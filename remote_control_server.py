#!/usr/bin/env python3
"""
Claude Remote Control Server

An HTTP server that exposes Claude Code agent capabilities via a REST API,
allowing remote clients to submit prompts and receive responses.
"""

import anyio
import json
import logging
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse, parse_qs

try:
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        ResultMessage,
        SystemMessage,
        AssistantMessage,
        TextBlock,
        CLINotFoundError,
        CLIConnectionError,
    )
    AGENT_SDK_AVAILABLE = True
except ImportError:
    AGENT_SDK_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CWD = os.getcwd()


def _allowed_tools_for_mode(mode: str) -> list[str]:
    """Return the appropriate tool list for the given mode."""
    modes = {
        "readonly": ["Read", "Glob", "Grep"],
        "standard": ["Read", "Glob", "Grep", "Write", "Edit"],
        "full": ["Read", "Glob", "Grep", "Write", "Edit", "Bash"],
    }
    return modes.get(mode, modes["readonly"])


async def run_agent_query(
    prompt: str,
    cwd: str = DEFAULT_CWD,
    mode: str = "readonly",
    max_turns: int = 20,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """
    Execute a prompt via the Claude Agent SDK and return structured results.

    Returns a dict with keys:
      - result (str): The final text result from the agent
      - session_id (str | None): Session ID for resumption
      - stop_reason (str | None): Why the agent stopped
      - error (str | None): Error message if execution failed
    """
    if not AGENT_SDK_AVAILABLE:
        return {
            "result": None,
            "session_id": None,
            "stop_reason": None,
            "error": "claude-agent-sdk is not installed. Run: pip install claude-agent-sdk",
        }

    options = ClaudeAgentOptions(
        cwd=cwd,
        allowed_tools=_allowed_tools_for_mode(mode),
        permission_mode="acceptEdits" if mode in ("standard", "full") else "default",
        max_turns=max_turns,
        **({"system_prompt": system_prompt} if system_prompt else {}),
    )

    result_text: str | None = None
    session_id: str | None = None
    stop_reason: str | None = None
    error: str | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                session_id = message.data.get("session_id")
            elif isinstance(message, ResultMessage):
                result_text = message.result
                stop_reason = message.stop_reason
    except CLINotFoundError:
        error = "Claude Code CLI not found. Install with: pip install claude-agent-sdk"
    except CLIConnectionError as e:
        error = f"Connection error: {e}"
    except Exception as e:
        error = f"Unexpected error: {e}"
        logger.error(traceback.format_exc())

    return {
        "result": result_text,
        "session_id": session_id,
        "stop_reason": stop_reason,
        "error": error,
    }


class RemoteControlHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Claude remote control API."""

    server_version = "ClaudeRemoteControl/1.0"
    log_message_format = "%(address)s - %(method)s %(path)s %(status)s"

    # ------------------------------------------------------------------ #
    # Routing                                                              #
    # ------------------------------------------------------------------ #

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"status": "ok", "agent_sdk": AGENT_SDK_AVAILABLE})
        elif parsed.path == "/":
            self._send_json(self._api_info())
        else:
            self._send_error(404, f"Unknown path: {parsed.path}")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/query":
            self._handle_query()
        else:
            self._send_error(404, f"Unknown path: {parsed.path}")

    # ------------------------------------------------------------------ #
    # Handlers                                                             #
    # ------------------------------------------------------------------ #

    def _handle_query(self) -> None:
        body = self._read_body()
        if body is None:
            return  # error already sent

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._send_error(400, f"Invalid JSON: {e}")
            return

        prompt = data.get("prompt", "").strip()
        if not prompt:
            self._send_error(400, "Field 'prompt' is required and must not be empty")
            return

        cwd = data.get("cwd", DEFAULT_CWD)
        mode = data.get("mode", "readonly")
        if mode not in ("readonly", "standard", "full"):
            self._send_error(400, "Field 'mode' must be one of: readonly, standard, full")
            return

        max_turns = data.get("max_turns", 20)
        system_prompt = data.get("system_prompt")

        logger.info("Query received | mode=%s | cwd=%s | prompt=%.80s", mode, cwd, prompt)

        response_data = anyio.from_thread.run_sync(
            lambda: anyio.run(
                run_agent_query,
                prompt,
                cwd,
                mode,
                max_turns,
                system_prompt,
            )
        ) if False else self._run_sync(
            run_agent_query(prompt, cwd, mode, max_turns, system_prompt)
        )

        status = 200 if response_data.get("error") is None else 500
        self._send_json(response_data, status=status)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _run_sync(self, coro):
        """Run an async coroutine from within a sync HTTP handler."""
        return anyio.from_thread.run_sync(lambda: anyio.run(lambda: coro))  # type: ignore[arg-type]

    def _read_body(self) -> bytes | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._send_error(400, "Request body is empty")
            return None
        return self.rfile.read(length)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _api_info(self) -> dict:
        return {
            "name": "Claude Remote Control API",
            "version": "1.0",
            "endpoints": {
                "GET /health": "Health check",
                "GET /": "This API description",
                "POST /query": {
                    "description": "Submit a prompt to Claude Code agent",
                    "body": {
                        "prompt": "(required) The task or question for Claude",
                        "cwd": f"(optional) Working directory, default: {DEFAULT_CWD}",
                        "mode": "(optional) Tool access level: readonly | standard | full (default: readonly)",
                        "max_turns": "(optional) Max agent turns, default: 20",
                        "system_prompt": "(optional) Custom system prompt",
                    },
                    "response": {
                        "result": "Agent's final response text",
                        "session_id": "Session ID for context (informational)",
                        "stop_reason": "Why the agent stopped",
                        "error": "Error message if execution failed, else null",
                    },
                },
            },
        }

    def log_message(self, fmt, *args):  # suppress default noisy logging
        logger.debug("%s - " + fmt, self.address_string(), *args)


# ------------------------------------------------------------------ #
# Synchronous wrapper                                                  #
# ------------------------------------------------------------------ #

def _patch_handler_run_sync():
    """
    Replace the _run_sync stub with a real implementation that works
    from within a synchronous HTTP handler thread.
    """

    def _run_sync(self, coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    RemoteControlHandler._run_sync = _run_sync


def main():
    host = os.environ.get("RC_HOST", DEFAULT_HOST)
    port = int(os.environ.get("RC_PORT", DEFAULT_PORT))

    _patch_handler_run_sync()

    server = HTTPServer((host, port), RemoteControlHandler)
    logger.info("Claude Remote Control Server listening on http://%s:%d", host, port)
    logger.info("Agent SDK available: %s", AGENT_SDK_AVAILABLE)
    logger.info("Default working directory: %s", DEFAULT_CWD)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
