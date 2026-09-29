"""Control a Flipper Zero over Bluetooth LE using its protobuf RPC protocol."""

from .apps import AppSession, AppState
from .client import Flipper, Subscription, connect
from .discovery import FlipperDevice, find, scan
from .errors import AppError, FlipperDisconnectedError, FlipperError, FlipperNotFoundError
from .gpio import Pin, PinMode, Pull
from .gui import InputEvent, InputType, Key, Screen
from .storage import FileEntry
from .system import RebootMode

__all__ = [
    "AppError",
    "AppSession",
    "AppState",
    "FileEntry",
    "Flipper",
    "FlipperDevice",
    "FlipperDisconnectedError",
    "FlipperError",
    "FlipperNotFoundError",
    "InputEvent",
    "InputType",
    "Key",
    "Pin",
    "PinMode",
    "Pull",
    "RebootMode",
    "Screen",
    "Subscription",
    "connect",
    "find",
    "scan",
]
__version__ = "0.2.0"
