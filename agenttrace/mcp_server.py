"""MCP (Model Context Protocol) server wrapper (Part 7).

Exposes the Part 6 agent tools over the MCP **stdio transport** so that
Claude Desktop / Cline / any MCP client can call them as native tools:

    "navigate to https://example.com and capture the network traffic"
        -> tool call ``capture_page`` -> returns the HAR file path

Transport & protocol
--------------------
* newline-delimited JSON-RPC 2.0 messages on stdin/stdout
* methods: ``initialize``, ``notifications/initialized``, ``ping``,
  ``tools/list``, ``tools/call``
* logs go to **stderr only** (stdout is reserved for the protocol)

This is deliberately dependency-free (stdlib only) so the server starts even
in a bare Python environment; the tool definitions themselves come from the
single source of truth in :mod:`agenttrace.agent`.

Run it directly::

    python -m agenttrace.mcp_server --out-dir artifacts

Claude Desktop config (``claude_desktop_config.json``)::

    {
      "mcpServers": {
        "agenttrace": {
          "command": "python",
          "args": ["-m", "agenttrace.mcp_server", "--out-dir", "F:/AgentTrace/artifacts"],
          "cwd": "F:/AgentTrace"
        }
      }
    }
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from .agent import AGENT_TOOLS, AgentSession, dispatch_tool
from .browser import BrowserError

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agenttrace"

_PARSE_ERROR = -32700
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _tool_schemas() -> list:
    """MCP-formatted tool list (note: MCP uses ``inputSchema``)."""
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["input_schema"],
        }
        for tool in AGENT_TOOLS
    ]


class MCPServer:
    """A minimal, spec-compliant MCP server exposing the AgentTrace tools."""

    def __init__(
        self,
        *,
        out_dir: str | Path = "artifacts",
        headless: bool = True,
        log_dir: str | Path = "logs",
    ) -> None:
        self.out_dir = Path(out_dir)
        self.headless = headless
        self.log_dir = Path(log_dir)

        self._logger = logging.getLogger("agenttrace.mcp")
        self._session: Optional[AgentSession] = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    def _get_session(self) -> AgentSession:
        """Return the shared agent session, starting the browser on demand."""
        if self._session is None:
            self._logger.info("starting agent session (out_dir=%s)", self.out_dir)
            session = AgentSession(out_dir=self.out_dir, headless=self.headless)
            session.start()
            self._session = session
        return self._session

    def _drop_session(self) -> None:
        """Forget the current session (called after the ``finish`` tool)."""
        if self._session is not None:
            self._session = None
            self._logger.info("agent session finished and released")

    def shutdown(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("error while closing session: %s", exc)
            self._session = None

    # ------------------------------------------------------------------
    # Protocol handling
    # ------------------------------------------------------------------
    def handle(self, message: dict) -> Optional[dict]:
        """Handle one JSON-RPC message; returns a response (None for notifications)."""
        if not isinstance(message, dict):
            return self._error_response(None, _PARSE_ERROR, "message must be an object")

        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        is_notification = msg_id is None

        self._logger.info("<- %s (id=%s)", method, msg_id)

        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "initialize":
            result = self._handle_initialize(params)
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": _tool_schemas()}
        elif method == "tools/call":
            result, error = self._handle_tools_call(params)
            if error is not None:
                if is_notification:
                    return None
                return self._error_response(msg_id, _INVALID_PARAMS, error)
        else:
            if is_notification:
                return None
            return self._error_response(
                msg_id, _METHOD_NOT_FOUND, f"unknown method: {method}"
            )

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _handle_initialize(self, params: dict) -> dict:
        # Echo the client's requested protocol version when it asks for one we
        # understand; otherwise advertise our default. This keeps both legacy
        # and current clients happy.
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) and requested else PROTOCOL_VERSION
        self._initialized = True
        client = params.get("clientInfo") or {}
        self._logger.info(
            "initialize: client=%s protocol=%s", client.get("name"), version
        )
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": self._server_version()},
        }

    @staticmethod
    def _server_version() -> str:
        try:
            from . import __version__

            return __version__
        except Exception:  # noqa: BLE001
            return "0.0.0"

    def _handle_tools_call(self, params: dict) -> tuple:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            return {}, "tools/call requires a 'name'"
        if not isinstance(arguments, dict):
            return {}, "tools/call 'arguments' must be an object"

        try:
            if name == "finish":
                session = self._get_session()
                payload = dispatch_tool(session, "finish", arguments)
                self._drop_session()
            else:
                session = self._get_session()
                payload = dispatch_tool(session, name, arguments)
        except BrowserError as exc:
            self._logger.error("tool %s failed: %s", name, exc)
            return {}, f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("tool %s crashed", name)
            return {}, f"{type(exc).__name__}: {exc}"

        ok = bool(payload.get("ok", True))
        text = json.dumps(payload, ensure_ascii=False, default=str)
        self._logger.info("-> tools/call %s ok=%s", name, ok)
        return {"content": [{"type": "text", "text": text}], "isError": not ok}, None

    @staticmethod
    def _error_response(msg_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    # ------------------------------------------------------------------
    # Transport (stdio)
    # ------------------------------------------------------------------
    def serve_forever(self, in_stream=None, out_stream=None) -> int:
        """Read newline-delimited JSON-RPC from stdin; write replies to stdout."""
        in_stream = in_stream or sys.stdin
        out_stream = out_stream or sys.stdout
        self._logger.info("MCP server ready (stdio) - waiting for requests")

        try:
            for raw_line in in_stream:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    response = self._error_response(
                        None, _PARSE_ERROR, f"invalid JSON: {exc}"
                    )
                else:
                    try:
                        response = self.handle(message)
                    except Exception as exc:  # noqa: BLE001
                        self._logger.exception("internal error while handling message")
                        response = self._error_response(
                            message.get("id") if isinstance(message, dict) else None,
                            _INTERNAL_ERROR,
                            f"{type(exc).__name__}: {exc}",
                        )
                if response is not None:
                    out_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
                    out_stream.flush()
        except (KeyboardInterrupt, EOFError):
            self._logger.info("MCP server interrupted")
        finally:
            self.shutdown()
        self._logger.info("MCP server stopped")
        return 0


def main(argv: Optional[list] = None) -> int:
    """Run the MCP server over stdio (entry point for MCP clients)."""
    parser = argparse.ArgumentParser(
        description="AgentTrace MCP server (stdio transport)."
    )
    parser.add_argument("--out-dir", default="artifacts", help="Where HAR files are written.")
    parser.add_argument("--log-dir", default="logs", help="Where the MCP server log is written.")
    parser.add_argument(
        "--headed", action="store_true", help="Run the browser with a visible window."
    )
    args = parser.parse_args(argv)

    # Logs must never touch stdout - it carries the MCP protocol.
    from .logging_utils import setup_logging

    setup_logging(Path(args.log_dir), filename="mcp_server.log", console=True)

    server = MCPServer(
        out_dir=args.out_dir, headless=not args.headed, log_dir=args.log_dir
    )
    try:
        return server.serve_forever()
    finally:
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())