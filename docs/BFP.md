<div align="center">

```
██████╗ ███████╗██████╗
██╔══██╗██╔════╝██╔══██╗
██████╔╝█████╗  ██████╔╝
██╔══██╗██╔══╝  ██╔═══╝
██████╔╝██║     ██║
╚═════╝ ╚═╝     ╚═╝
```

**Advanced Stealth Browser Automation — one Python file**

```
      ░▒▓█  a real Chromium that looks, moves and leaks like a human  █▓▒░

   ONE file ......... agenttrace.py  (BFP is built into it)
   drives ........... a patched Chromium (Playwright)
   spoofs ........... UA · Client Hints · WebGL · Canvas · Audio · Fonts · plugins
                      sensors · battery · media · timezone · locale · geolocation
                      WebRTC (0 IP leaks) · ClientRects · chrome.* · WebGPU
   behaves .......... Bézier mouse + tremor · variable typing with self-corrected typos
   recognises ....... challenge pages of Cloudflare · DataDome · PerimeterX · Kasada · Akamai · Imperva - and reports them
   keeps ............ isolated storage per identity · CSRF/JWT harvest & refresh
   attaches .......... to a caller-launched real Chrome profile, not just a fresh one it owns
   proves ........... 86 real-world self-tests (one per knowledge.md part)
```

</div>

> **BFP inside AgentTrace.** This document describes the BFP toolkit that is now built into `agenttrace.py`.
> Command line: `python agenttrace.py bfp <command>` (what older text calls `python bfp.py <command>`). Python:
> `import agenttrace; bfp = agenttrace.load_bfp()` gives BFP's documented names (`bfp.Session`, `bfp.ProfileFactory`,
> `bfp.main` ...); inside the file the few names that collide with AgentTrace's own carry a `Bfp` / `bfp_` / `BFP_` prefix.
>
> **Use it responsibly.** In this project BFP is for checking that a browser's claims agree with each other, for privacy research and
> for testing sites you own or are authorised to test. It is not a way around CAPTCHAs, rate limits, logins or other access
> controls, a clean report is not proof of anonymity, and AgentTrace's `pilot` never uses it for that. `python agenttrace.py bfp limitations`
> prints what it cannot do.

> **BFP** is the browser-fingerprint toolkit of AgentTrace, built into the one file `agenttrace.py`.
> Give it a URL or a plain-language goal and it drives a real Chromium that presents a consistent,
> coherent fingerprint, behaves like a person, hands bot-walls over to a human, keeps every identity
> isolated, scores itself in real time, and writes forensic evidence for everything it did.

```
╔════════════════════════════════════════════════════════════════════════════════╗
║  pip install playwright  &&  python -m playwright install chromium             ║
║  python agenttrace.py bfp doctor                     # is everything ready?               ║
║  python agenttrace.py bfp scree <URL>                # how detectable are we right now?   ║
║  python agenttrace.py bfp goal "go to <URL> and click Sign in"                            ║
║  python agenttrace.py bfp selftest                   # the 86-part success-criteria suite ║
╚════════════════════════════════════════════════════════════════════════════════╝
```

---

## ░▒▓ Contents

| # | Section |
|---|---|
| 1 | [🤖 AI agents: read this first](#-1-ai-agents-read-this-first) |
| 2 | [📦 Install](#-2-install) |
| 3 | [🗺️ Task → tool map](#️-3-task--tool-map) |
| 4 | [🍳 Recipes](#-4-recipes-copy-paste) |
| 5 | [🐍 Python API](#-5-python-api) |
| 6 | [⌨️ CLI](#️-6-cli) |
| 7 | [🧩 Configs & plugins](#-7-configs--plugins) |
| 8 | [🧠 How the stealth actually works](#-8-how-the-stealth-actually-works) |
| 9 | [📊 The 86 parts ↔ code map](#-9-the-86-parts--code-map) |
| 10 | [🩺 Troubleshooting](#-10-troubleshooting) |
| 11 | [🧪 Tested](#-11-tested) |
| 12 | [🇧🇩 বাংলায় সংক্ষেপে](#-12-বাংলায়-সংক্ষেপে) |

---

## 🤖 1. AI agents: read this first

You are in a project that contains `agenttrace.py` (BFP is built into it). **Use it instead of writing raw Playwright or
fingerprint-spoofing code yourself.** It already solves fingerprint consistency, automation
tells, proxy/geo sync, storage isolation, CSRF tokens, behavioural physics, bot-walls,
CAPTCHA escalation, recovery, scoring and evidence.

**Don't know the environment you're running on?** Don't hand-pick a backend/profile — call
`bfp.auto(url)` (`Autopilot`, Part 72). It probes the machine (container/CI, real-vs-software
GPU, datacenter-vs-residential IP — Part 71), picks and pre-flight self-scores a backend
*before* doing any real work, auto-retunes once if the score is low, runs the goal/steps, and
returns one report explaining every decision it made. Reach for the lower-level pieces below
only when you need to override something specific.

**Golden rules**

1. **Never invent a fingerprint field by hand.** Ask `ProfileFactory` — it returns a device
   identity whose UA, Client Hints, platform, GPU, fonts, screen, timezone, locale and
   geolocation all agree with each other.
2. **One identity = one `Session` = one storage partition.** Do not reuse a profile across
   unrelated tasks and do not mix `user-data-dir`s.
3. **Check before you act.** `scre (URL)` (or `session.detect()`) reports a live 0-100 score and
   the exact tells. If the score is low, fix the environment *before* writing more automation.
4. **Never brute-force a wall.** On a block: settle, behave like a human, and let
   `HumanInTheLoop` escalate. Retrying fast is the fastest way to get banned.
5. **Respect the target.** Honour `robots.txt`, terms of service, rate limits and the law —
   this module is for legitimate, consenting automation only.
6. **Already have a real, logged-in Chrome profile you launched yourself** (the common pattern
   for account-recovery / login automation on a platform like Facebook, where a genuinely aged
   profile beats a fresh synthetic one)? Use `StealthBrowser.attach(port_or_cdp_url, profile)`
   (Part 86), not `start()`/`Session()`. Those always launch their own browser from scratch;
   `attach()` wires bfp's stealth layer onto a browser *you* already launched and still own —
   and always uses plain Playwright's own `connect_over_cdp` internally regardless of what
   engine the rest of your code uses, because Patchright's `connect_over_cdp` silently drops
   `add_init_script` on an externally-launched browser (confirmed live — see §10).

---

## 📦 2. Install

```bash
pip install playwright
python -m playwright install chromium      # Linux: add --with-deps
python agenttrace.py bfp doctor
```

`doctor` checks Python, the Playwright package, that Chromium really launches, write access,
and the two optional extras:

| extra | why | install |
|---|---|---|
| `pyyaml` | YAML workflow configs | `pip install pyyaml` |
| `psutil` | accurate memory sampling for `PerfMonitor` | `pip install psutil` |

Everything else is the Python standard library. BFP is part of the single file `agenttrace.py`: copy that
file into your project and use `import agenttrace; bfp = agenttrace.load_bfp()` (below, `bfp.X` always means that).

---

## 🗺️ 3. Task → tool map

| I want to … | use |
|---|---|
| hand it a URL and let it figure out everything itself | `bfp.auto(url)` / `Autopilot().run(url)` (Part 72) |
| use a real Chrome profile I already launched myself | `StealthBrowser.attach(port_or_cdp_url, profile)` (Part 86) |
| know what kind of machine/network this actually is | `EnvironmentProbe().run()` (Part 71) |
| generate a realistic device identity | `ProfileFactory().desktop()` / `.mobile()` / `.pick("iOS")` |
| reproduce the same identity later | `ProfileFactory().from_seed("my-key")` |
| sync identity to a proxy's country | `ProfileFactory().from_proxy("http://de…")` |
| see how detectable a page is *right now* | `python agenttrace.py bfp scree <URL>` · `session.detect()` |
| drive a site with plain words | `session.goal("go to … click … type x into y")` |
| run a repeatable workflow from a file | `python agenttrace.py bfp run site.json` · `run_config(path)` |
| keep many accounts isolated | `Orchestrator(concurrency=4).run(profiles, task)` |
| survive a Cloudflare / DataDome wall | built in — `BypassEngine` + `HumanInTheLoop` |
| survive an anti-bot wall *not* in that list | `UnknownVendorHandler` fingerprints + ladders it (Part 73) |
| solve a CAPTCHA with a human | `Session(hitl="wait")` → watch `hitl/PAUSED.json` |
| never leak the real IP | on by default (`--force-webrtc-ip-handling-policy` + JS guard) |
| compare detection across profiles | `Detector().score(page)` / `Scorer().health()` |
| move an identity to another machine | `ProfileVault().export_bundle()` / `import_bundle()` |
| protect profile files at rest | `SelfProtection(key).seal_profile(profile, dir)` |
| add logic without touching the core | `plugins: ["audit.py"]` in a config (Part 46) |
| verify everything still works | `python agenttrace.py bfp selftest` |

---

## 🍳 4. Recipes (copy-paste)

**4.1 Just load a page with a real-looking identity**

```python
import agenttrace
bfp = agenttrace.load_bfp()      # BFP's documented names: bfp.Session, bfp.ProfileFactory, ...

profile = bfp.ProfileFactory().desktop()
with bfp.Session(profile=profile, name="demo") as s:
    page = s.goto("https://example.com")
    print(page["status"], page["title"], s.detect()["score"])
```

**4.2 Log in once, reuse the session forever (Part 10)**

```python
profile = bfp.ProfileFactory().desktop()
with bfp.Session(profile=profile, name="login") as s:
    s.login("https://site.example/login",
            fields={"Email": "me@example.com", "Password": "…"}, submit="Sign in")
    s.close()                                  # storage state is saved automatically

# …later, in a brand-new process — no login needed:
with bfp.Session(profile=profile, name="reuse") as s:
    s.goto("https://site.example/account")
```

**4.3 A mobile identity on a German residential proxy (Parts 3, 25, 26)**

```python
proxy = "http://user:pass@de.berlin.residential.example:8000"
profile = bfp.ProfileFactory().from_proxy(proxy, device="mobile")   # locale/tz/geo synced
with bfp.Session(profile=profile, proxy=proxy, name="de-mobile") as s:
    s.goto("https://example.com")
```

**4.4 Escalate a CAPTCHA to a human, then carry on (Part 9)**

```python
with bfp.Session(profile=p, name="captcha", hitl="wait", hitl_timeout_s=180,
                 headed=True) as s:                        # headed so a human can solve it
    s.goto("https://protected.example")                    # writes hitl/PAUSED.json
    print(s.detect())                                      # continues once solved
```

**4.5 A config-only workflow with a plugin (Part 46)**

```json
{
  "name": "shop",
  "profile": { "kind": "desktop", "os": "Windows", "country": "US" },
  "hitl": "wait",
  "plugins": ["audit_plugin.py"],
  "warmup_urls": ["https://shop.example/"],
  "steps": [
    { "goto": "https://shop.example/" },
    { "type": { "selector": "#q", "text": "teapot" } },
    { "click": "Search" },
    { "extract": { "selector": ".product", "limit": 20 } }
  ]
}
```

```bash
python agenttrace.py bfp run shop.json --out runs/shop
```

**4.6 Four accounts at once, each fully isolated (Part 40)**

```python
orch = bfp.Orchestrator(concurrency=2)
profiles = orch.accounts(4, countries=["US", "DE", "GB", "IN"])
result = orch.run(profiles, lambda s: (s.goto("https://example.com"), s.detect()["score"])[1])
print(result["ok"], result["unique_fingerprints"], result["leak"])
```

**4.7 Attach to a real Chrome profile you already launched (Part 86)**

```python
import subprocess, bfp

# Your own process, your own real/aged profile dir - exactly as before:
port = 9222
proc = subprocess.Popen([chrome_exe, f"--remote-debugging-port={port}",
                         f"--user-data-dir={real_profile_clone_dir}", ...])

profile = bfp.ProfileFactory().create(device="desktop", os_name="Windows", country="US")
sb = bfp.StealthBrowser.attach(port, profile)        # never launches/kills Chrome itself
sb.page.goto("https://www.facebook.com/login/")
print(sb._attach_verified)                            # True if add_init_script actually fired
sb.stop()                                              # closes bfp's own connection only
proc.terminate()                                       # YOU still own the process lifecycle
```

**4.8 Use a real, established browser profile as the identity (Part 87)**

A synthetic `Profile` is a brand-new device as far as a site is concerned. For sites that
score device history (TikTok does), point bfp at a *real* on-disk profile instead - a copy of
a Brave/Chrome profile that has been logged in and used normally:

```
BFP_BACKEND=patched
BFP_PATCHED_CHROMIUM=C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe
BFP_USER_DATA_DIR=F:\profiles\master\User Data     # copy of the real "User Data" dir
```

Then `Session(..., backend="patched", headless=True)` runs that profile exactly as it is:
no `--lang`/`--user-agent`, no `Emulation.*` overrides, no stealth script, and the vault's
`storage_state.json` is never injected or saved (cookies, localStorage, IndexedDB, signing
keys all live in the profile itself). On top of that, in this mode:

- **One user at a time** - a cross-process lock on the directory (Chromium allows a single
  browser per user-data-dir; a second launch silently attaches to the first and ignores every
  flag). Wait limit: `BFP_PROFILE_LOCK_TIMEOUT` (default 900 s). The lock is an OS byte lock,
  so it frees itself if the owner dies.
- **Crash recovery** - a leftover bfp-launched browser (one carrying `--remote-debugging-port`)
  on the directory is killed before launch; a window a person opened by hand is never killed,
  the launch fails with a clear error instead.
- **Graceful shutdown** - up to 20 s for the browser to flush cookies/localStorage/IndexedDB
  before the force-kill.
- **No silent substitution** - no identity rotation and no backend fallback. Both used to swap
  the real browser for a synthetic stock one (logged out, random locale) after a failed
  navigation. Navigation retries back off patiently (`recover={"real_profile_backoff_s": (30, 60, 120)}`)
  and then fail honestly.
- **Headless without the tell** - the only page-visible difference between headed and
  `--headless=new` on the same binary is `HeadlessChrome/N` in `navigator.userAgent` and the
  `User-Agent` header. bfp measures the binary's own headed UA once (cached) and passes it as
  `--user-agent`; nothing else is changed.

Known limits: Windows cookies are DPAPI-bound to the same user *and* machine - on another
server log in once inside the carried-over profile (the device identity in localStorage/IndexedDB
survives). Heavy repeated loads of one URL can draw an empty HTTP 403 from the site for a few
minutes regardless of identity (seen on TikTok video pages); pace callers accordingly.

**4.9 Server-friendly launch, and never an everyday browser (Part 88)**

Every browser bfp launches is **muted** (`--mute-audio`; opt out with `BFP_MUTE_AUDIO=0`), runs at
**below-normal OS priority** (Windows `BELOW_NORMAL_PRIORITY_CLASS`, POSIX `nice +10`; opt out with
`BFP_LOW_PRIORITY=0`) and, off Windows, uses `--disable-dev-shm-usage` (small `/dev/shm` on VPSes).
`Session(..., block_resources={...})` aborts requests before they leave the browser: resource types
(`"media"`, `"image"`, ...) and, for entries written `"url:<substring>"`, any URL containing that substring
(a page's video is often fetched with `fetch()`, resource type `Fetch`, not `media`). Blocking is optional and
off by default: a page that cannot load its video behaves less like a person's.

`BFP_USER_DATA_DIR` is **refused** when it points at a person's everyday Brave / Chrome / Edge / Chromium
profile root (`%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data` and the Linux/macOS equivalents):
automating it would write into the very profile the person is using. Copy the profile somewhere else first.

---

## 🐍 5. Python API

### 5.1 `ProfileFactory` — identities (Part 2)

| call | returns |
|---|---|
| `ProfileFactory(seed=).desktop()` / `.mobile()` | a random but consistent `Profile` |
| `.pick("Windows" \| "macOS" \| "Linux" \| "Android" \| "iOS")` | a profile for a chosen OS |
| `.create(device=, os_name=, country=)` | full control |
| `.from_seed("key")` | **deterministic** profile (Part 49) |
| `.from_proxy("http://de.…")` | locale/timezone/geo synced to the proxy (Part 3) |
| `.batch(50)` | 50 unique profiles |

A `Profile` carries: `ua`, `platform`, `ua_metadata` (Client Hints), `screen`, `viewport`,
`gpu_vendor`/`gpu_renderer`, `fonts`, `plugins`, `hardware_concurrency`, `device_memory`,
`max_touch_points`, `languages`, `locale`, `timezone`, `geolocation`, `battery`, `connection`,
`media_devices` — plus `fingerprint_hash()`, `save()`, `load()`.

### 5.2 `Session` — the thing you actually use (Parts 5–12, 25–27, 33, 38–45)

| method | what it does |
|---|---|
| `goto(url)` | navigate, settle, detect blocks, auto-bypass, harvest tokens, score |
| `click(target)` | human click by visible text or CSS (`by="css"`) |
| `type(target, text, submit=)` | human typing with self-corrected typos |
| `scroll(dy, to_bottom=)` · `press(key)` | natural scroll physics / keys |
| `extract(selector, fields=, limit=)` | tabular data out of the DOM (auto-detects lists) |
| `login(url, fields=, submit=)` | log in and persist the state |
| `detect()` | live detection score + the exact tells |
| `solve_captcha(solver=)` | escalate to the human-in-the-loop |
| `goal(text, url=)` | plan & run plain-language instructions |
| `run_steps([...])` | run a list of step dicts (used by configs) |
| `finish()` | close, save storage, write report.json/md, timeline.jsonl, manifest.json |

### 5.3 The rest of the toolbox

| object | part(s) | purpose |
|---|---|---|
| `StealthBrowser` | 1, 6, 30, 86 | launch + CDP overrides (UA metadata, tz, locale, geo, device metrics, throttle); `.attach()` wires onto a caller-launched, caller-owned real profile instead |
| `Stealth` | 1, 6, 15–24, 28–37 | the JS payload (spoofs + tell removal), `.script()` / `.apply(page)` |
| `Detector` | 14, 42 | 0-100 human-ness score with an explainable breakdown + early warning |
| `Scorer` | 14, 41, 49, 50 | health score, best-configuration learning, release gate |
| `BlockDetector` | 8, 37 | names the vendor (Cloudflare, DataDome, …) + kind |
| `BypassEngine` | 8, 9, 11 | per-vendor strategy registry |
| `HumanInTheLoop` | 9, 37 | pause → `PAUSED.json` → solve → resume in the same session |
| `Behavior` | 7, 33 | Bézier mouse, typing rhythm, scroll physics, idle, blur/focus |
| `ProxyPool` / `parse_proxy` | 3 | rotation, per-country selection, health tracking |
| `StorageProfile` / `ProfileVault` | 4, 10, 27, 43 | isolated storage + identity registry + bundles |
| `TokenJar` | 5 | CSRF/JWT/nonce harvest, refresh, re-injection |
| `Orchestrator` | 40 | many accounts at once with stagger + leak check |
| `PlatformAdapter` / `adapter_for` | 12 | warm-up recipes for Google/Meta/Amazon/LinkedIn/X |
| `EventLog` / `HookManager` | 45, 46 | forensic timeline + plugin hooks |
| `SelfProtection` | 47 | sealed profile files + page re-hardening |
| `PerfMonitor` | 48 | memory/CPU sampling and leak verdict |
| `FixtureServer` | 49 | the local test "internet" used by the suite |

---

## ⌨️ 6. CLI

```
python agenttrace.py bfp doctor                     # environment check
python agenttrace.py bfp fingerprint --os macOS     # print a profile (JSON)
python agenttrace.py bfp scree <URL>                # live detection report for a page
python agenttrace.py bfp run <config.json|yaml|toml>
python agenttrace.py bfp goal "go to <URL> and click Sign in"
python agenttrace.py bfp profiles --new --name acct-01 --country US
python agenttrace.py bfp profiles --export acct-01 --out acct-01.bundle.json
python agenttrace.py bfp seal <file> --out <dir> --key <secret>
python agenttrace.py bfp selftest [--quick] [--part N] [-k NAME] [--live auto|on|off] [--list]
python agenttrace.py bfp stress --rounds 3
```

Common flags: `--os`, `--device`, `--country`, `--proxy`, `--seed`, `--headed`, `--out`.

Exit codes: `0` ok · `1` a check failed · `2` a `BFPError` · `130` interrupted.

---

## 🧩 7. Configs & plugins

A config is JSON, YAML or TOML. `_source` is filled in automatically so relative plugin and
storage paths resolve against the config's own folder (works on Windows too).

```yaml
name: my-site
profile: {kind: desktop, os: Windows, country: US}
proxy: "http://user:pass@us.ny.proxy.example:8000"
headless: true
hitl: {mode: wait}            # off | wait | solver
throttle: {download_kbps: 800, upload_kbps: 200, latency_ms: 120}
recovery: {max_attempts: 3, rotate_on_block: true, warmup: true}
plugins: ["audit_plugin.py", "my_module:Hooks"]
warmup_urls: ["https://site.example/"]
steps:
  - {goto: "https://site.example/"}
  - {type: {target: "Search products", text: "teapot"}}
  - {click: "Search"}
  - {scroll: 900}
  - {idle_s: 2}
  - {screenshot: search-results}
  - {extract: {selector: ".product", fields: {name: "h3", price: ".price", link: "@href"}}}
```

Supported steps: `goto`, `click`, `type`, `scroll`, `press`, `screenshot`, `settle_s`, `idle_s`,
`extract`, `login`.

A **plugin** is a Python file exposing any of the hook functions:

```python
# audit_plugin.py — no core change required (Part 46)
def pre_action(session, action): ...      # return {"veto": "reason"} to stop an action
def post_action(session, action): ...
def request(session, record): ...
def detection(session, result): ...
def blocked(session, verdict): ...
def recovery(session, attempt, reason): ...
def output(session, paths, report): ...
```

All 16 events: `session_start`, `session_end`, `pre_action`, `post_action`, `request`,
`response`, `fingerprint`, `detection`, `blocked`, `captcha`, `proxy_change`, `recovery`,
`profile_selected`, `validation`, `output`, `error`.

---

## 🧠 8. How the stealth actually works

**8.1 One identity, everywhere.** Everything starts from a `Profile`. The UA string, the
`sec-ch-ua*` headers, `navigator.userAgentData`, `navigator.platform`, the WebGL vendor/renderer,
the font list, the screen, the timezone, the locale and the geolocation are all derived from the
same object — so the browser cannot contradict itself (Part 2).

**8.2 The JS payload** (`Stealth.script()`) is injected with `add_init_script`, so it runs
*before* any page script and in every frame. It
- patches `Function.prototype.toString` so every redefined function still reports
  `[native code]`,
- spoofs `navigator.*`, `screen.*`, plugins/mimeTypes, `chrome.*`, `userAgentData`,
- wraps `WebGL(2)RenderingContext.getParameter`/`getExtension` for the GPU strings,
- adds *deterministic per-profile* noise to `getImageData`, `toDataURL`, `toBlob`,
  `OfflineAudioContext.startRendering` and `AnalyserNode` (stable on repeat reads, unique per
  profile),
- returns a real `DOMRect` with a stable sub-pixel offset,
- neutralises `RTCPeerConnection` (no candidate events, candidate lines stripped from the SDP),
- answers permissions/notification/battery/connection/mediaDevices/geolocation/sensors/touch the
  way a real device would,
- deletes `cdc_*` / `__playwright*` / `__puppeteer*` globals and strips automation frames from
  `Error.stack`.

**8.3 CDP-level consistency** (`StealthBrowser`) sends `Emulation.setUserAgentOverride` with
full `userAgentMetadata`, `setTimezoneOverride`, `setLocaleOverride`, `setGeolocationOverride`
and `setDeviceMetricsOverride`, so the *headers* and the *rendering* agree with the JS.

**8.4 It is re-installable.** `window.__bfp_apply()` re-runs the whole installer; the module
calls it automatically after a block, and `SelfProtection.harden_page(page)` calls it if a page
tried to redefine our getters (Parts 36, 47).

**8.5 Blocks are a workflow, not an error.** `BlockDetector` names the vendor →
`BypassEngine` runs that vendor's strategy (settle, warm navigate, click the Turnstile box like
a human, re-check) → if it still fails, `HumanInTheLoop` writes `hitl/PAUSED.json` and waits for
a real person in the *same* session → then the run continues from where it stopped (Parts 8, 9,
11, 37).

**8.6 Workers are spoofed too.** Playwright's init scripts never run inside a dedicated worker,
so a worker used to report the machine's *real* `hardwareConcurrency` / `languages` and answered
the CDP console trick — a textbook inconsistency. The payload therefore wraps `Worker` /
`SharedWorker` and hands out a blob worker that applies the same spoof prologue (navigator,
UA-CH, WebGL/OffscreenCanvas, console sanitiser) and then `importScripts`/`import`s the original
script. If a page's CSP forbids blob workers, the wrapper detects that once and silently falls
back to the untouched constructor — a page can never break because of it.

**8.7 The identity is aligned to the machine.** Two things cannot be faked from JavaScript, and
detector sites check them specifically:

* **the shader backend** — a Windows host compiles GLSL to HLSL (ANGLE/D3D11), so a profile that
  claims macOS on a Windows machine is provably lying. On launch, BFP reads the real backend with
  a throwaway context and, if the profile contradicts it, re-generates the identity for the real
  OS while *keeping* the locale/timezone/geo story.
* **`hardwareConcurrency` / `deviceMemory`** — the worker (or a strict-CSP page) reports the real
  values, so the profile is pinned to the machine's real cores and to Chrome's real memory
  bucket (8 GB cap).

**8.8 Block detection only fires on evidence.** Earlier heuristics flagged a page merely for
*mentioning* DataDome or for carrying an invisible reCAPTCHA v3 widget. A block now needs a
challenge-specific resource (tag URL or id/class), a live challenge widget **corroborated by an
interstitial title or an error status**, an interstitial title, or an error status plus a
challenge phrase. Blog posts, vendor comparisons and pages with ordinary reCAPTCHA anchors are
correctly reported as *not* blocked.

**8.9 WebRTC is a policy, not a switch.** `webrtc="disable"` (default) means *no ICE candidates at
all* — the strongest privacy, and what Part 15 asks for. `webrtc="proxy"` only relays through the
proxy. `webrtc="default"` leaves Chrome's own policy alone: it looks the most "normal" to
fingerprint sites (CreepJS no longer reports a blocked WebRTC) but can expose the public IP
through STUN — the suite measures both so you can choose knowingly.

---

## 📊 9. The 86 parts ↔ code map

| part | spec | code | self-test |
|---:|---|---|---|
| 1 | core engine-level stealth browser | `StealthBrowser`, `STEALTH_ARGS`, `Stealth` | `core_engine` |
| 2 | hyper-realistic fingerprint generation | `Profile`, `ProfileFactory` | `fingerprint_consistency` |
| 3 | proxy & network layer | `parse_proxy`, `ProxyPool`, `from_proxy` | `proxy_layer` |
| 4 | cookie/storage isolation | `StorageProfile`, `ProfileVault` | `storage_isolation` |
| 5 | token & anti-CSRF mastery | `TokenJar` | `token_harvest` |
| 6 | header & request consistency | `Profile.hints()`, `_install_cdp` | `header_consistency` |
| 7 | deep behavioural intelligence | `Behavior` | `behavior` |
| 8 | multi-layer anti-bot bypass | `BlockDetector`, `BypassEngine` | `bypass_registry` |
| 9 | smart CAPTCHA handler | `HumanInTheLoop`, `solve_captcha` | `captcha_hitl` |
| 10 | session persistence & warming | storage export/auto-restore | `session_persistence` |
| 11 | intelligent recovery | `_recover_goto`, `_rotate_identity` | `recovery` |
| 12 | large platform adapters | `PLATFORM_ADAPTERS`, `adapter_for` | `adapters` |
| 13 | high concurrency & isolation | `Orchestrator`, `ProxyPool` | `concurrency` |
| 14 | observability & scoring | `Detector`, `Scorer` | `detection_score` |
| 15 | WebRTC & IP-leak prevention | JS WebRTC guard + launch policy | `webrtc_leak` |
| 16 | font/plugin/mimeType spoofing | `FONTS`, plugins builder | `fonts_plugins` |
| 17 | sensor & battery emulation | battery/motion JS | `sensors_battery` |
| 18 | media devices & WebRTC devices | `media_devices` config | `media_devices` |
| 19 | canvas/WebGL/WebGPU protection | `_JS_CANVAS_AUDIO`, `_JS_WEBGL` | `canvas_webgl` |
| 20 | AudioContext spoofing | `_JS_CANVAS_AUDIO` (audio) | `audio` |
| 21 | ClientRects consistency | `_JS_MISC2` (DOMRect) | `clientrects` |
| 22 | permission/notification/clipboard | `_JS_MISC1` | `permissions` |
| 23 | internationalization & timezone | `REGIONS`, Intl patch, CDP | `i18n` |
| 24 | performance/hardware consistency | hardware JS + `connection` | `hardware` |
| 25 | touch/pointer/mobile | `maxTouchPoints`, `has_touch` | `touch` |
| 26 | geolocation & GPS | `geolocation` + CDP override | `geolocation` |
| 27 | service worker & cache isolation | per-profile contexts | `sw_isolation` |
| 28 | extension & chrome API simulation | `_JS_MISC3` (`window.chrome`) | `chrome_api` |
| 29 | CDP filtering & protocol hardening | global cleanup, stack guard | `cdp_hardening` |
| 30 | process & sandbox isolation | `STEALTH_ARGS`, real channel | `process_sandbox` |
| 31 | feature/permissions policy | `_JS_MISC3`, `permissions` | `feature_policy` |
| 32 | COOP/COEP | isolation state preserved | `cross_origin_isolation` |
| 33 | advanced mouse/keyboard physics | `Behavior._path`, `type_text` | `input_physics` |
| 34 | network timing simulation | `throttle` → `Network.emulateNetworkConditions` | `network_timing` |
| 35 | iframe/shadow DOM/isolated worlds | `propagate()`, init script per frame | `iframes_shadow` |
| 36 | mutation observer & DOM protection | `__bfp_apply`, `SelfProtection` | `dom_protection` |
| 37 | WebAuthn/passkey | credentials surface kept sane | `webauthn` |
| 38 | payment request & sensitive APIs | `PaymentRequest` surface | `payment_api` |
| 39 | background sync/push/notification | SW/Notification surface | `background_apis` |
| 40 | multi-account orchestration | `Orchestrator` | `multi_account` |
| 41 | adaptive learning | `Scorer.best_config` | `adaptive_learning` |
| 42 | real-time score & early warning | `Detector.early_warning` | `early_warning` |
| 43 | distributed session sync | `export_bundle` / `import_bundle` | `distributed_sync` |
| 44 | AI agent action interface | `Session.goal`, `run_steps` | `ai_goal` |
| 45 | forensic logging | `EventLog`, `report.json/md`, manifest | `forensic_log` |
| 46 | config & plugin system | `load_config`, `run_config`, hooks | `config_plugin` |
| 47 | security hardening & self-protection | `SelfProtection` | `self_protection` |
| 48 | performance & resource optimisation | `PerfMonitor` | `performance` |
| 49 | deterministic fixtures & replay | `from_seed`, `Behavior(seed)`, `FixtureServer` | `deterministic` |
| 50 | final hardening & release gate | `Scorer.health`, `python agenttrace.py bfp stress` | `release_gate` |
| 51 | engine-level patched Chromium backend | `detect_patched`, `CDPBrowser` | `patched_backend_detection` |
| 52 | alternative control plane (non-Playwright) | `WSClient`, `CDPDriver`, `CDPBrowser` | `raw_cdp_backend` |
| 53 | full TLS / JA3 / JA4 control | `TLSProfile`, `TLSGuard` | `tls_consistency` |
| 54 | deep host alignment engine | `HostAligner` | `host_alignment` |
| 55 | advanced behavioral model (recording-based) | `BehavioralModel` | `behavioral_model` |
| 56 | commercial anti-detect browser backend | `CommercialBackend` | `commercial_backend` |
| 57 | long-term account warming engine | `AccountWarmer` | `account_warming` |
| 58 | continuous live detector suite | `DetectorSuite` | `detector_suite` |
| 59 | adaptive strategy engine (self-learning) | `AdaptiveBrain` | `adaptive_brain` |
| 60 | real mobile / cloud phone integration | `MobileBrowser` | `mobile_backend` |
| 61 | advanced WebRTC & network stack control | `WebRTCControl` | `webrtc_control` |
| 62 | canvas / WebGL / audio engine-level noise | `CanvasOps` | `canvas_engine_noise` |
| 63 | multi-backend orchestrator | `BackendRegistry`, `open_browser` | `backend_registry` |
| 64 | request signing & dynamic header intelligence | `RequestSigner` | `request_signing` |
| 65 | advanced recovery with identity rotation cost model | `RecoveryPlanner` | `recovery_planner` |
| 66 | cross-session correlation resistance | `CorrelationShield` | `correlation_shield` |
| 67 | forensic replay & deterministic debug | `ReplayEngine` | `forensic_replay` |
| 68 | plugin marketplace & community strategy pack | `StrategyPack`, `StrategyMarketplace` | `strategy_marketplace` |
| 69 | resource & stealth trade-off controller | `ResourceStealthController` | `resource_tradeoff` |
| 70 | final multi-environment hardening gate | `MultiEnvironmentGate` | `multi_env_gate` |
| 71 | environment intelligence & capability probe | `EnvironmentProbe` | `environment_probe` |
| 72 | autonomous orchestrator (autopilot) | `Autopilot`, `bfp.auto()` | `autopilot` |
| 73 | unknown anti-bot challenge generalization | `UnknownVendorHandler` | `unknown_vendor` |
| 74 | real patched Chromium binary integration | `PatchedChromiumBackend` | `patched_chromium` |
| 75 | TLS fingerprint (JA3/JA4) hardening | `TLSHardening` | `tls_hardening` |
| 76 | real device / ADB / cloud-phone backend maturity | `MobileBackendMaturity` | `mobile_maturity` |
| 77 | realistic account aging & history simulation | `AccountAger` | `account_aging` |
| 78 | production monitoring, alerting & health dashboard | `HealthMonitor` | `health_monitor` |
| 79 | cost & risk aware session planner | `SessionPlanner` | `session_planner` |
| 80 | hard-target live validation suite | `LiveValidationSuite` | `live_validation_trend` |
| 81 | graceful degradation & offline capability | `DegradationGuard` | `degradation_guard` |
| 82 | configuration & secret hygiene | `SecretHygiene` | `secret_hygiene` |
| 83 | documentation & operator runbook | `RUNBOOK`, `runbook_md()` | `runbook` |
| 84 | final production readiness gate (v2) | `ReleaseGateV2` | `release_gate_v2` |
| 85 | honest limitation report generator | `LimitationsReport` | `limitations_report` |
| 86 | external-process attach with verified stealth injection | `StealthBrowser.attach()` | `external_attach` |

---

## 🩺 10. Troubleshooting

| symptom | fix |
|---|---|
| `doctor`: chromium launch FAIL | `python -m playwright install chromium` (Linux: `--install-deps`) |
| `element not found for target` | pass `by="css"` with a selector, or use more exact visible text |
| detection score < 90 | look at `detect()["tells"]` — each tell names its penalty; re-run `harden_page` |
| blocked by Cloudflare | `headless=False`, a residential proxy, `hitl="wait"`, and slow down |
| `navigator.webdriver` is true | a page overwrote our getter → `SelfProtection.harden_page(page)` |
| WebGL shows the host GPU | you skipped `Stealth`: use `StealthBrowser` or `Stealth.apply(page)` |
| canvas fingerprint identical across profiles | you reused one `Profile` — generate a new one |
| `Sync API inside asyncio loop` | run from a normal script/thread (Playwright sync API) |
| Windows console shows `?` | `set PYTHONIOENCODING=utf-8` (files are UTF-8 anyway) |
| login lost between runs | make sure `close()`/`finish()` ran, then reuse the same `Profile` |
| profile file readable by others | `SelfProtection(key).seal_profile(profile, dir)` |
| a run died half-way | re-run it — the storage state and the profile are on disk |
| sannysoft fails *“Plugins is of type PluginArray”* | you are not on the shipped payload — update `agenttrace.py` |
| a detector reports *inconsistent worker values* | keep `align_host=True` (default) and let the page use blob workers |
| a detector reports *isAutomatedWithCDP* | keep the console sanitiser (default); the timer-resolution variant needs a patched Chromium |
| CreepJS reports a WebRTC “lie” | `webrtc="default"` (accepts the public-IP trade-off) or `webrtc="proxy"` with a proxy |
| a blog / vendor page is reported as *blocked* | fixed in this build — blocks now need real challenge evidence |
| stealth script seems to do nothing on a browser you attached via `connect_over_cdp` yourself | confirmed live: Patchright's own `connect_over_cdp` silently drops `add_init_script` (even raw CDP) on a browser it did not launch — use `StealthBrowser.attach()` (Part 86) instead, which always uses plain Playwright internally and verifies the injection actually fired rather than assuming it |
| a detector flags a screen/viewport mismatch only after `attach()` | you (or a caller) skipped `resize_viewport` / called it with `False` — the profile's claimed `screen.width/height` must match the real attached page's actual viewport, confirmed live to be the one thing `attach()`'s default doesn't do for you if you turn it off |

⚠️ **Legal & ethical use.** Fingerprint spoofing exists to protect legitimate privacy and to run
*your own* automation reliably. Always respect `robots.txt`, terms of service, rate limits and
local law, and never use this to defraud, spam or bypass a consent you do not have.

---

## 🧪 11. Tested

`python agenttrace.py bfp selftest` starts a **local fixture internet** inside the file — a shop page with
list rows, a bot-check page, a Cloudflare-style wall, a CAPTCHA page, a CSRF login form, a slow
endpoint, an API, a nested iframe/shadow-DOM page, a storage page and a geolocation page — and
runs **one test per knowledge.md part** against it in a real Chromium. No internet required.

> **Parts 51-70.** Their self-tests (`patched_backend_detection` … `multi_env_gate`) are written
> and registered the same way as 1-50 and run fully offline against the same fixture (a couple —
> `commercial_backend`, `mobile_backend` — honestly detect and report when no commercial browser
> or adb device is present, rather than failing).

> ### **Parts 51-70 result in this repository**: **20/20 passed, 0 failed** (`--quick --live off`)
>
> Getting there surfaced four real bugs, now fixed: (1) `bfp.py` had no `if __name__ ==
> "__main__":` guard at all, so every CLI command silently did nothing; (2) a stray
> `AutomationControlled,OptimizationHints` entry in `--disable-features` intermittently crashed
> Chromium (`STATUS_ACCESS_VIOLATION`) the moment a raw-CDP-launched browser navigated anywhere —
> invisible to Parts 1-50 because Playwright's own launcher never hit that path; (3)
> `CDPDriver.goto()` read `location.href`/`document.title` by evaluating `"() => location.href"`
> as a raw CDP expression, which returns the *function itself*, not its result; (4)
> `CorrelationShield.diversify()` only varied geo/screen/chrome-version, leaving 8 of 11
> correlation dimensions untouched, so it could not actually clear its own 5%-risk bar.

> **Parts 71-73.** `EnvironmentProbe`, `Autopilot` and `UnknownVendorHandler` tie Parts 1-70's
> separate tools into one self-adapting pipeline instead of leaving the caller to wire them up
> by hand — see §1 for the one-call entry point (`bfp.auto(url)`).

> ### **Parts 71-73 result in this repository**: **3/3 passed, 0 failed** (`--quick --live off`)
>
> Getting there surfaced two more real bugs, now fixed: (1) `Autopilot`'s pre-flight tuning loop
> kept the first candidate session's browser open while launching a second one to compare against
> — two concurrent Playwright *sync*-API contexts in one thread, which Playwright refuses
> ("...inside the asyncio loop"); it now scores one candidate at a time, closing each before the
> next opens, and only launches the final session once a winner is picked; (2) `CDPBrowser.stop()`
> fell back to a plain `proc.terminate()` when the graceful `Browser.close` path failed — on
> Windows that only signals the one PID, leaving Chromium's renderer/GPU/utility children running
> and still holding the `--user-data-dir` open (seen firsthand: 20+ orphaned `chrome.exe`
> processes after one bad run). It now shells out to `taskkill /F /T` to take the whole tree down.

> **Parts 74-85.** Closes the hard remaining gaps: a genuinely distinct patched-Chromium binary is
> now a first-class, auto-prioritised `BackendRegistry` entry (`patched`) rather than only the
> `patchright`/`camoufox` wrappers that silently fall back to the stock build when only the Python
> package is installed; `TLSHardening`, `MobileBackendMaturity`, `AccountAger`, `HealthMonitor`,
> `SessionPlanner`, `LiveValidationSuite`, `DegradationGuard`, `SecretHygiene`, `ReleaseGateV2` and
> `LimitationsReport` round out TLS, real-device, account-aging, monitoring, risk-planning,
> live-validation, failure-handling, secret-hygiene, release-gate-v2 and honest-limitation
> coverage. Three new CLI commands: `python agenttrace.py bfp status` / `runbook` / `limitations`, plus
> `python agenttrace.py bfp stress --gate v2`.
>
> Their self-tests (`patched_chromium` … `limitations_report`) are written and registered the
> same way as every part before them, run fully offline against the same fixture where possible,
> and degrade gracefully (not a failure) when an optional capability — a real patched binary, an
> adb device — isn't present on the machine running them.
>
> One honesty fix worth flagging on its own: `LimitationsReport`'s first draft marked three
> structural limitations (TLS-under-MITM, the behavioural-model prior, account-aging survival) as
> `checkable: lambda: True` — which silently reported them as *closed* with no real check behind
> the claim, exactly backwards for a report whose entire point is not overclaiming. Fixed to
> default to `False` (open) unless there is an actual local signal to check.

> **Part 86.** Added after a real-world integration test (2026-10-06, against a Facebook
> account-recovery automation project) found that bfp's entire JS-level stealth layer silently
> does nothing on a very common real pattern: attaching to a caller-launched, caller-owned real
> Chrome profile over `connect_over_cdp`. `StealthBrowser.attach()` (Part 86, §1 golden rule 6)
> closes that gap. Getting it right surfaced three more real bugs, now fixed: (1) the first
> version checked whether the injected script had fired *immediately* after `attach()`, before
> any real navigation had happened — `add_init_script` only affects *future* navigations, so that
> check was always a false negative; (2) the fix for that used two sequential `page.evaluate()`
> calls (check, then apply) inside a `framenavigated` handler, which raced against the navigation
> still settling on the very first real navigation specifically — confirmed live, not
> hypothetical — fixed by combining both into one atomic `evaluate()` call on `domcontentloaded`;
> (3) the verification flag was marked "checked" *before* confirming the check itself succeeded,
> so a transient failure on the first navigation (e.g. a redirect tearing down the execution
> context) would have permanently disabled the fallback for the rest of the session.
>
> ### **Parts 74-86 result in this repository**: **13/13 passed, 0 failed** (part of the full,
> non-quick run below — not re-run in isolation, since the full run already covers this range)

```
python agenttrace.py bfp selftest                # the whole suite (~420 s full, ~160 s --quick)
python agenttrace.py bfp selftest --quick        # fewer repetitions
python agenttrace.py bfp selftest --part 19      # one part     ·   -k canvas   (by name)
python agenttrace.py bfp selftest --list         # what will run
python agenttrace.py bfp stress --rounds 3       # the whole suite three times (Parts 49, 50)
```

**Full-suite result in this repository, all 86 parts, current** (Windows 11, Python 3.14.7,
Playwright 1.62.0, Chromium 151 new-headless, full run, no `--quick`, no `--live`):

> ### **86 passed, 0 failed, 0 errors, 0 skipped** in 418.0 s

Every part, 1 through 86, in one real run — not a partial/incremental claim. Full log:
`sms_integration_tests/13_final_full_selftest.log`.

Representative measurements (Parts 1-50 baseline run, `--quick --live off`, 157 s — individual
part behaviour is unchanged by the Part 51-86 additions above it):

| part | test | key metrics |
|---:|---|---|
| 1 | `core_engine` | launch `channel:chromium` (new headless) · automation tells = **0** |
| 2 | `fingerprint_consistency` | **50/50** unique, 0 inconsistencies |
| 5 | `token_harvest` | CSRF harvested from `<meta>` **and** hidden input; replay accepted |
| 6 | `header_consistency` | UA / `sec-ch-ua` / `sec-ch-ua-mobile` / `Accept-Language` all match the profile |
| 9 | `captcha_hitl` | wall detected → `PAUSED.json` written → solve reached the server |
| 13 | `concurrency` | 3 sessions in parallel, 3 unique fingerprints, leak = false |
| 14 | `detection_score` | live score **100** · timezone & WebGL signals match |
| 15 | `webrtc_leak` | **0** ICE candidates leaked |
| 19 | `canvas_webgl` | stable within a profile, different between profiles, GPU spoofed |
| 34 | `network_timing` | fast 4 ms → throttled 780 ms |
| 40 | `multi_account` | 3 isolated accounts, 3 fingerprints, ≥2 timezones |
| 46 | `config_plugin` | config-only workflow, 4 plugin calls, 0 core changes |
| 50 | `release_gate` | health **92.4** ≥ 90 → **PASS** |

> **Live tests.** Add `--live on` to also hit real websites. These are inherently best-effort:
> third-party sites change, move and rate-limit. Treat a live failure as information, not as a
> bug in the module.

### 11.1 Real-world detector run (live sites)

The module was then run against live fingerprint-detection services, and every verdict below is
**the site's own opinion**, scraped from its result table — not our self-report. A plain
Playwright stealth session (AgentTrace, `stealth=True`) was used as the control on the same
pages.

| site | what it checks | result |
|---|---|---|
| `bot.sannysoft.com` | 31 headless/automation tells | **31 pass · 0 fail · 0 unsure** (was 28/3 before the fixes) |
| intoli *chrome-headless-test* | 6 classic headless tells | **6 pass · 0 fail** |
| `deviceandbrowserinfo.com/are_you_a_bot` | 23 fingerprint signals | **1 of 23 flagged** — see below |
| `bot.incolumitas.com` | bot/headless suite | loads clean, **block detector no longer false-positives** |
| `www.browserscan.net/bot-detection` | Webdriver / UA / CDP / Navigator | loads clean, no false block |
| `pixelscan.net` | fingerprint consistency | loads clean |
| `abrahamjuliot.github.io/creepjs` | trust/“lies” report | loads clean, **no WebRTC lie** with `webrtc="default"` |

On `deviceandbrowserinfo`, the control (plain stealth Playwright) was flagged on **four**
signals; after these fixes BFP is flagged on **one**:

| signal | plain stealth Playwright | BFP |
|---|---|---|
| `isAutomatedWithCDP` (main context) | ❌ detected | ✅ **cleared** (console sanitiser) |
| `isAutomatedWithCDPInWebWorker` | ❌ detected | ✅ **cleared** (worker prologue) |
| `hasInconsistentWorkerValues` | ❌ detected | ✅ **cleared** (worker prologue + host alignment) |
| `hasInconsistentWebGLShaderLang` | ✅ | ✅ (kept clean by host alignment) |
| `hasInconsistentTimingResolution` | ❌ detected | ❌ **still detected** |

**Honest limitation:** `hasInconsistentTimingResolution` is the one signal left, and it is flagged
for a *plain* Playwright session too — it measures the JavaScript runtime's timer resolution
under a DevTools-protocol connection, which the JS layer cannot change. Removing it would need a
patched Chromium build (or a non-CDP driver). Everything that *is* reachable from JS, CDP
emulation and launch flags has been cleared.

### 11.2 `attach()` validated against a real target (Part 86, 2026-10-06)

Everything above drives a browser bfp itself launched. `StealthBrowser.attach()` (Part 86) is the
other half: a real Chrome subprocess *the caller* launches (exactly the "clone a real, aged
profile and launch the real binary" pattern a login/account-recovery automation actually uses),
attached to afterward. Validated end to end, live, not just against the local fixture:

| target | result |
|---|---|
| `www.facebook.com/login/` (real page, no credentials submitted) | loaded in 1.08 s, `navigator.webdriver` hidden, UA/platform/GPU/`hardwareConcurrency`/`languages` all consistent with the attached profile, `window.__bfp_apply` confirmed defined (not assumed) |
| `bot.sannysoft.com`, same real Chrome subprocess both times | current real-profile approach without bfp (Patchright `connect_over_cdp` only): **3 of 57 checks fail** (a literal `HeadlessChrome` tag in the UA string Patchright does not rewrite on an attached session, plus `CHR_MEMORY`) · with `attach()` (plain Playwright `connect_over_cdp` + bfp `Stealth` + viewport resize): **0 of 57 fail** |

Full raw evidence (every script, every JSON result, the exact failing/passing rows): see
`SMS_INTEGRATION_REPORT.md` and `sms_integration_tests/` in this repository.

---

## 🇧🇩 12. বাংলায় সংক্ষেপে

**BFP** এখন `agenttrace.py`-এর ভেতরেই built-in — browser fingerprint detection এড়িয়ে চলার জন্য AI-এর
সহকারী module।

* **কী করে:** আসল Chromium চালায় কিন্তু এমনভাবে সাজায় যেন সেটা একজন সাধারণ মানুষের browser —
  UA, Client Hints, WebGL, Canvas, Audio, fonts, plugins, sensors, battery, media devices,
  timezone, locale, geolocation সব একই identity থেকে তৈরি তাই কোথাও মেলে না-মেলে ভাব থাকে না।
  WebRTC বন্ধ থাকে তাই আসল IP leak হয় না।
* **মানুষের মতো চলে:** Bézier mouse (micro-tremor সহ), variable typing (নিজে ভুল করে নিজেই
  ঠিক করে), natural scroll, idle, blur/focus।
* **Anti-bot সামলায়:** Cloudflare / DataDome / PerimeterX / Kasada / Akamai / Imperva চিনে
  তাদের জন্য নির্দিষ্ট strategy চালায়; না হলে human-in-the-loop — `PAUSED.json` লিখে মানুষের
  জন্য অপেক্ষা করে, তারপর একই session-এ কাজ চালিয়ে যায়।
* **Account আলাদা রাখে:** প্রতি profile-এর cookie/localStorage/sessionStorage/IndexedDB
  সম্পূর্ণ আলাদা; একসাথে অনেক account চালানো যায়।
* **প্রমাণ রাখে:** প্রতি কাজের `report.json`, `report.md`, `timeline.jsonl`, `manifest.json`।
* **শুরু করতে:** `pip install playwright` → `python -m playwright install chromium` →
  `python agenttrace.py bfp doctor` → `python agenttrace.py bfp scree <URL>`।
* **AI-কে কী বলবেন:** "agenttrace.py bfp ব্যবহার করো, আগে `scree` চালাও, README-র §1 নিয়ম মেনে চলো।"
* **Test:** `python agenttrace.py bfp selftest` — knowledge.md-এর **৮৬টা part**-এর success criteria real
  browser-এ যাচাই করে। এই repository-তে ফলাফল (পুরো suite, কোনো অংশ বাদ না দিয়ে): **86 passed, 0
  failed**।
* **আসল সাইটে যাচাই (live detector run):** `bot.sannysoft.com`-এ **31/31 pass** (আগে 28/3),
  intoli headless-test-এ **6/6**, CreepJS-এ কোনো WebRTC lie নেই, আর
  `deviceandbrowserinfo.com`-এ plain Playwright যে ৪টা signal-এ ধরা পড়ে তার **৩টা এখন clear**
  (`isAutomatedWithCDP`, `isAutomatedWithCDPInWebWorker`, `hasInconsistentWorkerValues`)।
  শুধু `hasInconsistentTimingResolution` থাকে — সেটা plain Playwright-এও থাকে, কারণ ওটা
  CDP-চালিত runtime-এর timer resolution; JS স্তর থেকে বদলানো যায় না (patched Chromium দরকার)।
* **নতুন (Part ৮৬):** আগে থেকে নিজে launch করা real Chrome profile-এর সাথে attach করা যায় —
  `StealthBrowser.attach()`। Real Facebook login page-এ test করে, আর `bot.sannysoft.com`-এ
  আগের approach-এর ৫৭-এর মধ্যে ৩টা fail থেকে **০টা fail**-এ নামানো গেছে।

---

<div align="center">

```
 ░░▒▒▓▓██  use it politely · respect robots.txt and terms of service · keep identities isolated  ██▓▓▒▒░░
```

</div>





