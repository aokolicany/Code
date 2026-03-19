#!/usr/bin/env python3
"""
Claude Remote Control Client

A command-line client for submitting prompts to a running
Claude Remote Control Server (remote_control_server.py).

Usage:
    python remote_control_client.py "Your prompt here"
    python remote_control_client.py --mode standard "Refactor utils.py"
    python remote_control_client.py --host 192.168.1.10 --port 8765 "List files"
    python remote_control_client.py --health
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from urllib.parse import urljoin


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def health_check(base_url: str) -> int:
    """GET /health — return exit code 0 if healthy, 1 otherwise."""
    try:
        url = urljoin(base_url, "/health")
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        print(json.dumps(data, indent=2))
        return 0
    except urllib.error.URLError as e:
        print(f"Error: could not reach server at {base_url} — {e.reason}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def send_query(
    base_url: str,
    prompt: str,
    *,
    cwd: str | None = None,
    mode: str = "readonly",
    max_turns: int = 20,
    system_prompt: str | None = None,
    raw: bool = False,
) -> int:
    """POST /query — return exit code 0 on success, 1 on error."""
    payload: dict = {"prompt": prompt, "mode": mode, "max_turns": max_turns}
    if cwd:
        payload["cwd"] = cwd
    if system_prompt:
        payload["system_prompt"] = system_prompt

    body = json.dumps(payload).encode()
    url = urljoin(base_url, "/query")

    try:
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode(errors="replace")
        try:
            err_data = json.loads(error_body)
            print(f"Server error ({e.code}): {err_data.get('error', error_body)}", file=sys.stderr)
        except json.JSONDecodeError:
            print(f"Server error ({e.code}): {error_body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Error: could not reach server at {base_url} — {e.reason}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1

    if data.get("error"):
        print(f"Agent error: {data['error']}", file=sys.stderr)
        return 1

    if raw:
        print(data.get("result", ""))
    else:
        print(json.dumps(data, indent=2))

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Claude Remote Control Client — send prompts to a Claude Code agent server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s "What files are in the current directory?"
  %(prog)s --mode standard "Add docstrings to all functions in utils.py"
  %(prog)s --cwd /path/to/project "Summarize the README"
  %(prog)s --host 192.168.1.10 --port 8765 "List open TODO comments"
  %(prog)s --health
  %(prog)s --raw "Explain what create_event_now.py does"
        """,
    )

    parser.add_argument("prompt", nargs="?", help="The prompt/task for Claude")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Server host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Server port (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--mode",
        choices=["readonly", "standard", "full"],
        default="readonly",
        help="Tool access level (default: readonly)",
    )
    parser.add_argument("--cwd", help="Working directory for the agent")
    parser.add_argument("--max-turns", type=int, default=20, dest="max_turns", help="Max agent turns (default: 20)")
    parser.add_argument("--system-prompt", dest="system_prompt", help="Custom system prompt")
    parser.add_argument("--health", action="store_true", help="Check server health and exit")
    parser.add_argument("--raw", action="store_true", help="Print only the result text (no JSON wrapper)")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_url = build_base_url(args.host, args.port)

    if args.health:
        sys.exit(health_check(base_url))

    if not args.prompt:
        print("Error: a prompt is required (or use --health)", file=sys.stderr)
        sys.exit(1)

    sys.exit(
        send_query(
            base_url,
            args.prompt,
            cwd=args.cwd,
            mode=args.mode,
            max_turns=args.max_turns,
            system_prompt=args.system_prompt,
            raw=args.raw,
        )
    )


if __name__ == "__main__":
    main()
