"""The in-process API the Alfred desktop app uses. See core.py; fake.py stands in without Windows."""

from .bridge import TelegramController, bridge_command
from .core import STATES, AlfredService, CommandResult, NotReady, QueueFull, Status, engine_command
from .events import EventBus

__all__ = [
    "STATES", "AlfredService", "CommandResult", "EventBus", "NotReady", "QueueFull", "Status",
    "TelegramController", "bridge_command", "engine_command",
]
