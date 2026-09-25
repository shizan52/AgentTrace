"""Anti-detection / stealth layer (Part 9).

Headless Chromium advertises several automation tells (``navigator.webdriver``,
an empty ``navigator.plugins``, a ``HeadlessChrome`` user-agent, a missing
``window.chrome`` object, …). Sites use those tells to serve CAPTCHAs or block
traffic outright.

This module provides:

* :func:`stealth_launch_args` — Chromium flags that switch automation signals off
* :data:`STEALTH_INIT_SCRIPT` — an init script (runs before every page script)
  that normalises the most common fingerprinting surfaces
* :func:`stealth_user_agent` — a realistic, non-headless Chrome UA string
* :func:`stealth_context_options` — viewport/locale/timezone/header profile
* :data:`DETECTION_PROBES_JS` + :func:`analyse_probe` — the *same* checks bot
  detectors use, so the stealth layer can be measured instead of assumed

The layer is deliberately conservative: it only patches well-known tells and
never breaks page scripts (every patch is wrapped in try/except).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ----------------------------------------------------------------------
# Launch flags
# ----------------------------------------------------------------------
def stealth_launch_args() -> list:
    """Chromium flags that remove the loudest automation signals."""
    return [
        # The single most important flag: stops Blink from exposing
        # navigator.webdriver = true and related automation behaviour.
        "--disable-blink-features=AutomationControlled",
        # Cosmetic: no "Chrome is being controlled by automated test software".
        "--disable-infobars",
        "--no-default-browser-check",
        "--no-first-run",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]


def stealth_user_agent(browser_version: str | None = None) -> str:
    """Return a realistic desktop Chrome UA (never ``HeadlessChrome``)."""
    major = "126"
    if browser_version:
        match = re.match(r"(\d+)", str(browser_version))
        if match:
            major = match.group(1)
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def stealth_context_options(user_agent: str | None = None) -> dict:
    """Context options that make the profile look like a normal desktop user."""
    return {
        "viewport": {"width": 1280, "height": 800},
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "user_agent": user_agent or stealth_user_agent(),
        "extra_http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
        },
    }


# ----------------------------------------------------------------------
# Init script (runs before any page script)
# ----------------------------------------------------------------------
STEALTH_INIT_SCRIPT = r"""
(() => {
  const safe = (fn) => { try { fn(); } catch (e) { /* never break the page */ } };

  safe(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true });
  });

  safe(() => {
    Object.defineProperty(navigator, 'languages', {
      get: () => ['en-US', 'en'], configurable: true,
    });
  });

  safe(() => {
    const make = (name, filename, description) => ({
      name, filename, description, length: 1,
    });
    const plugins = [
      make('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      make('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      make('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
    ];
    const mimeTypes = [
      { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    ];
    Object.defineProperty(navigator, 'plugins', { get: () => plugins, configurable: true });
    Object.defineProperty(navigator, 'mimeTypes', { get: () => mimeTypes, configurable: true });
  });

  safe(() => {
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
  });

  safe(() => {
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8, configurable: true });
  });

  safe(() => {
    if (!('platform' in navigator) || !navigator.platform) {
      Object.defineProperty(navigator, 'platform', { get: () => 'Win32', configurable: true });
    }
  });

  safe(() => {
    const chrome = window.chrome || {};
    chrome.runtime = chrome.runtime || {};
    chrome.app = chrome.app || { isInstalled: false, InstallState: {}, RunningState: {} };
    chrome.csi = chrome.csi || (() => ({
      onloadT: Date.now(), startE: Date.now(), pageT: 0, tran: 15,
    }));
    chrome.loadTimes = chrome.loadTimes || (() => {
      const now = Date.now() / 1000;
      return {
        requestTime: now, startLoadTime: now, commitLoadTime: now,
        finishDocumentLoadTime: now, finishLoadTime: now, firstPaintTime: now,
        firstPaintAfterLoadTime: 0, navigationType: 'Other',
        wasFetchedViaSpdy: true, wasNpnNegotiated: true,
        npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false,
        connectionInfo: 'h2',
      };
    });
    Object.defineProperty(window, 'chrome', { get: () => chrome, configurable: true });
  });

  safe(() => {
    // WebGL vendor/renderer strings (headless exposes "Google SwiftShader").
    const patch = (proto) => {
      if (!proto || !proto.getParameter) return;
      const original = proto.getParameter;
      proto.getParameter = function (parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return original.apply(this, [parameter]);
      };
    };
    patch(window.WebGLRenderingContext && window.WebGLRenderingContext.prototype);
    patch(window.WebGL2RenderingContext && window.WebGL2RenderingContext.prototype);
  });

  safe(() => {
    // Notification.permission is "denied" in headless; make it "default".
    if (window.Notification && Notification.permission === 'denied') {
      Object.defineProperty(Notification, 'permission', {
        get: () => 'default', configurable: true,
      });
    }
  });

  safe(() => {
    // Headless reports a zero-height outer window.
    if (window.outerHeight === 0) {
      Object.defineProperty(window, 'outerHeight', { get: () => 800, configurable: true });
    }
    if (window.outerWidth === 0) {
      Object.defineProperty(window, 'outerWidth', { get: () => 1280, configurable: true });
    }
  });

  safe(() => {
    // Permissions.query({name:'notifications'}) reports "denied" in headless.
    const permissions = navigator.permissions;
    if (permissions && permissions.query) {
      const original = permissions.query.bind(permissions);
      permissions.query = (parameters) => (
        parameters && parameters.name === 'notifications'
          ? Promise.resolve({ state: 'default', onchange: null })
          : original(parameters)
      );
    }
  });
})();
"""


# ----------------------------------------------------------------------
# Detection probes (the same checks bot detectors run)
# ----------------------------------------------------------------------
DETECTION_PROBES_JS = r"""
() => {
  const probe = {};
  const safe = (fn, fallback) => { try { return fn(); } catch (e) { return fallback; } };

  probe.userAgent = safe(() => navigator.userAgent, '');
  probe.headlessUserAgent = /HeadlessChrome|Headless/i.test(probe.userAgent);
  probe.webdriver = safe(() => navigator.webdriver, null);
  probe.plugins = safe(() => navigator.plugins.length, -1);
  probe.mimeTypes = safe(() => navigator.mimeTypes.length, -1);
  probe.languages = safe(() => navigator.languages.length, -1);
  probe.platform = safe(() => navigator.platform, '');
  probe.hardwareConcurrency = safe(() => navigator.hardwareConcurrency, -1);
  probe.hasChrome = safe(() => !!window.chrome, false);
  probe.chromeRuntime = safe(() => !!(window.chrome && window.chrome.runtime), false);
  probe.outerHeight = safe(() => window.outerHeight, -1);

  const webgl = (which) => safe(() => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (!gl) return '';
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    if (info) {
      return which === 'vendor'
        ? gl.getParameter(info.UNMASKED_VENDOR_WEBGL)
        : gl.getParameter(info.UNMASKED_RENDERER_WEBGL);
    }
    return which === 'vendor' ? gl.getParameter(gl.VENDOR) : gl.getParameter(gl.RENDERER);
  }, '');

  probe.webglVendor = webgl('vendor');
  probe.webglRenderer = webgl('renderer');

  probe.iframeWebdriver = safe(() => {
    const frame = document.createElement('iframe');
    frame.style.display = 'none';
    document.body.appendChild(frame);
    const value = frame.contentWindow && frame.contentWindow.navigator
      ? frame.contentWindow.navigator.webdriver
      : null;
    frame.remove();
    return value;
  }, null);

  return probe;
}
"""


@dataclass(frozen=True)
class StealthProbe:
    """Raw detection signals collected from a page."""

    data: dict

    @property
    def problems(self) -> tuple:
        return tuple(analyse_probe(self.data))

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return dict(self.data)


def analyse_probe(data: dict) -> list:
    """Return the list of detection problems for a raw probe dict.

    An empty list means the browser looks like a normal desktop Chrome.
    """
    problems: list[str] = []

    if data.get("headlessUserAgent"):
        problems.append(f"user-agent advertises headless: {data.get('userAgent')!r}")
    if data.get("webdriver"):
        problems.append(f"navigator.webdriver is {data.get('webdriver')!r} (expected falsy)")
    if data.get("iframeWebdriver"):
        problems.append("navigator.webdriver is truthy inside an iframe")
    if (data.get("plugins") or 0) < 1:
        problems.append(f"navigator.plugins.length is {data.get('plugins')!r} (expected >= 1)")
    if (data.get("languages") or 0) < 1:
        problems.append(f"navigator.languages is empty ({data.get('languages')!r})")
    if not data.get("hasChrome"):
        problems.append("window.chrome is missing")
    if not data.get("chromeRuntime"):
        problems.append("window.chrome.runtime is missing")
    renderer = f"{data.get('webglVendor', '')} {data.get('webglRenderer', '')}".lower()
    if "swiftshader" in renderer:
        problems.append(
            f"WebGL renderer exposes the headless software stack: {renderer.strip()!r}"
        )
    if (data.get("outerHeight") or 0) <= 0:
        problems.append(f"window.outerHeight is {data.get('outerHeight')!r} (headless tell)")

    return problems


def probe_summary(data: dict) -> str:
    """One-line, log-friendly summary of a probe result."""
    return (
        f"webdriver={data.get('webdriver')!r} plugins={data.get('plugins')} "
        f"languages={data.get('languages')} chrome={data.get('hasChrome')} "
        f"headlessUA={data.get('headlessUserAgent')} "
        f"webgl={(data.get('webglRenderer') or '')[:40]!r}"
    )