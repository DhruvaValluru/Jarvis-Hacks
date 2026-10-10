"""Tool registry shared by the Claude loop, the fast-path router and the MCP server.

Each tool declares a risk tier:
  read     - only looks at things; always runs
  act      - low-risk action (open an app, change volume); runs without asking
  confirm  - consequential (shell commands, sending, deleting); asks the user first
"""

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import config


class ToolError(Exception):
    """A tool failed in a way worth telling the model about."""


@dataclass
class Image:
    """A tool result that is a picture (base64 PNG) plus a caption."""

    png_b64: str
    caption: str = ""


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    params: dict = field(default_factory=dict)
    required: tuple = ()
    risk: str = "act"
    # Optional per-call check: returns a reason string when this call needs confirmation.
    confirm_if: Callable[[dict], str | None] | None = None
    hidden: bool = False  # callable by name (e.g. from another PC) but not shown to Claude
    remote: bool = False  # proxy for a tool on another PC; func(args, confirm)

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.params,
                "required": list(self.required),
            },
        }

    def confirmation_reason(self, args: dict) -> str | None:
        if self.risk == "confirm":
            return f"{self.name}({_short(args)})"
        if self.confirm_if:
            return self.confirm_if(args)
        return None


TOOLS: dict[str, Tool] = {}

# Hook for tools that need to talk to the user later (timers). Set by the app.
announce: Callable[[str], None] = print
# Hook for speaking out loud on this PC (the say tool). Set by the app.
speak: Callable[[str], None] = print

_call = threading.local()


def reply_to(send: Callable[[str], None] | None) -> None:
    """While a call from another PC runs on this thread, send its later messages back there."""
    _call.reply_to = send


def announcer() -> Callable[[str], None]:
    """Where a later message from the current tool call should go: the PC that asked, or here."""
    return getattr(_call, "reply_to", None) or (lambda text: announce(text))


def tool(name: str, description: str, params: dict | None = None, required=(), risk="act", confirm_if=None, hidden=False):
    def deco(fn):
        TOOLS[name] = Tool(name, description, fn, params or {}, tuple(required), risk, confirm_if, hidden)
        return fn

    return deco


def schemas() -> list[dict]:
    return [t.schema() for t in list(TOOLS.values()) if not t.hidden]


def _short(args: dict, limit: int = 300) -> str:
    text = json.dumps(args, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def audit(name: str, args: dict, ok: bool, result: Any) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": name,
        "args": args,
        "ok": ok,
        "result": result.caption if isinstance(result, Image) else str(result)[:500],
    }
    try:
        with open(config.data_dir() / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run(name: str, args: dict, confirm: Callable[[str], bool] | None = None) -> tuple[bool, Any]:
    """Run a tool by name. Returns (ok, result). Never raises."""
    t = TOOLS.get(name)
    if t is None:
        return False, f"Unknown tool: {name}"
    reason = t.confirmation_reason(args)
    if reason and not (confirm and confirm(reason)):
        audit(name, args, False, "declined by user")
        return False, "The user declined this action. Do not retry it; ask what they want instead."
    try:
        result = t.func(args, confirm) if t.remote else t.func(**args)
        ok = True
    except ToolError as e:
        ok, result = False, str(e)
    except TypeError as e:
        ok, result = False, f"Bad arguments for {name}: {e}"
    except Exception as e:  # noqa: BLE001 - report any tool crash back to the model
        ok, result = False, f"{type(e).__name__}: {e}"
    if result is None:
        result = "Done."
    audit(name, args, ok, result)
    return ok, result
