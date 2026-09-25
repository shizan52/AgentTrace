"""AgentTrace — AI-powered browser automation & network/HAR capture module.

Part 1 exposes the core browser automation engine built on Playwright.
"""

from __future__ import annotations

from .agent import (
    AGENT_TOOLS,
    ActionResult,
    AgentSession,
    ElementInfo,
    PageObservation,
    SessionSummary,
    dispatch_tool,
    tool_catalog,
)
from .auth import (
    AuthStateInfo,
    auth_state_available,
    clear_auth_state,
    inspect_auth_state,
    save_auth_state,
    storage_state_arg,
)
from .browser import (
    BrowserEngine,
    BrowserError,
    PageSnapshot,
)
from .crawl import CrawlPageResult, CrawlSummary, crawl_site
from .har import HarCaptureResult
from .interaction import BeforeAfterResult, capture_click
from .stealth import (
    DETECTION_PROBES_JS,
    STEALTH_INIT_SCRIPT,
    StealthProbe,
    analyse_probe,
    probe_summary,
    stealth_context_options,
    stealth_launch_args,
    stealth_user_agent,
)
from .verification import (
    CaptureVerification,
    IdleWaitResult,
    NetworkActivityTracker,
    VerifiedCaptureResult,
    capture_verified,
    verify_capture,
)

__version__ = "0.1.0"
__all__ = [
    "BrowserEngine",
    "BrowserError",
    "PageSnapshot",
    "HarCaptureResult",
    "BeforeAfterResult",
    "capture_click",
    "CaptureVerification",
    "IdleWaitResult",
    "NetworkActivityTracker",
    "VerifiedCaptureResult",
    "capture_verified",
    "verify_capture",
    "CrawlPageResult",
    "CrawlSummary",
    "crawl_site",
    "AGENT_TOOLS",
    "ActionResult",
    "AgentSession",
    "ElementInfo",
    "PageObservation",
    "SessionSummary",
    "dispatch_tool",
    "tool_catalog",
    "AuthStateInfo",
    "auth_state_available",
    "clear_auth_state",
    "inspect_auth_state",
    "save_auth_state",
    "storage_state_arg",
    "DETECTION_PROBES_JS",
    "STEALTH_INIT_SCRIPT",
    "StealthProbe",
    "analyse_probe",
    "probe_summary",
    "stealth_context_options",
    "stealth_launch_args",
    "stealth_user_agent",
    "__version__",
]