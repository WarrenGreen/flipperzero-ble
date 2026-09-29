"""Control a Flipper Zero over Bluetooth LE using its protobuf RPC protocol."""

from .client import Flipper, connect
from .discovery import FlipperDevice, find, scan
from .errors import FlipperError, FlipperNotFoundError
from .gui import Key, Screen
from .storage import FileEntry

__all__ = [
    "FileEntry",
    "Flipper",
    "FlipperDevice",
    "FlipperError",
    "FlipperNotFoundError",
    "Key",
    "Screen",
    "connect",
    "find",
    "scan",
]
__version__ = "0.1.0"
