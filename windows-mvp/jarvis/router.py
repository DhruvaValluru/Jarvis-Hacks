"""Fast path: common commands handled instantly, without calling Claude.

Returns (tool_name, args) or None. Anything compound ("open X and do Y") or
unrecognised goes to Claude instead.
"""

import re

WAKE = re.compile(r"^\s*(hey|ok|okay|yo)?[\s,]*jarvis\b[\s,.!?]*", re.I)
COMPOUND = re.compile(r"\b(and|then|after that|also)\b", re.I)


def clean(text: str) -> str:
    text = WAKE.sub("", text)
    text = re.sub(r"\b(please|can you|could you|would you)\b", "", text, flags=re.I)
    text = text.strip(" .!?,")
    return re.sub(r"\s+", " ", text).strip()


def _single(arg: str) -> str | None:
    """The captured argument, unless the command is compound."""
    arg = arg.strip(" .!?,")
    return None if not arg or COMPOUND.search(arg) else arg


def route(text: str) -> tuple[str, dict] | None:
    t = clean(text).lower()
    if not t:
        return None

    if m := re.fullmatch(r"(?:open|launch|start|run) (.+)", t):
        if (arg := _single(m.group(1))) is None:
            return None
        if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", arg):
            return "open_url", {"url": arg}
        return "open_app", {"name": arg}
    if m := re.fullmatch(r"(?:close|quit|exit) (.+)", t):
        arg = _single(m.group(1))
        return ("close_window", {"window": arg}) if arg else None
    if m := re.fullmatch(r"(?:switch to|focus(?: on)?|go to|show me) (.+)", t):
        arg = _single(m.group(1))
        return ("focus_window", {"window": arg}) if arg else None

    if re.fullmatch(r"(?:play|pause|resume|stop)(?: (?:the )?(?:music|song|video|it))?|play music", t):
        return "media_control", {"action": "play_pause"}
    if re.fullmatch(r"(?:next|skip)(?: (?:the )?(?:song|track|one|video))?|skip (?:this|it)", t):
        return "media_control", {"action": "next"}
    if re.fullmatch(r"(?:previous|last|go back)(?: (?:a )?(?:song|track|one))?", t):
        return "media_control", {"action": "previous"}
    if re.fullmatch(r"(?:un)?mute(?: (?:the )?(?:sound|audio|volume|it))?", t):
        return "media_control", {"action": "mute"}
    if m := re.fullmatch(r"(?:set (?:the )?)?volume (?:to |at )?(\d{1,3})(?: ?%| percent)?", t):
        return "set_volume", {"percent": int(m.group(1))}
    if re.fullmatch(r"(?:turn (?:it|the volume|the sound) up|volume up|louder|turn up the volume)", t):
        return "media_control", {"action": "volume_up", "times": 5}
    if re.fullmatch(r"(?:turn (?:it|the volume|the sound) down|volume down|quieter|turn down the volume)", t):
        return "media_control", {"action": "volume_down", "times": 5}

    if m := re.fullmatch(r"(?:search(?: the web)?(?: for)?|google|look up) (.+)", t):
        return "web_search", {"query": m.group(1)}
    if re.fullmatch(r"what(?:'s| is) (?:the )?(?:time|date|day)(?: is it)?(?: today)?|what time is it|what day is it", t):
        return "system_status", {}
    if re.fullmatch(r"(?:what(?:'s| is) (?:open|running)|list (?:my )?windows)", t):
        return "list_windows", {}
    if re.fullmatch(r"lock (?:the |my )?(?:pc|computer|screen|laptop)", t):
        return "lock_pc", {}
    if m := re.fullmatch(r"(?:set a )?timer (?:for )?(\d+) (second|minute|hour)s?", t):
        mult = {"second": 1, "minute": 60, "hour": 3600}[m.group(2)]
        return "set_timer", {"seconds": int(m.group(1)) * mult, "message": "Your timer is done."}
    return None
