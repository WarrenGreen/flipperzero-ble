from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._proto import flipper_pb2


class FlipperError(Exception):
    """A command was rejected by the Flipper. `status` is the CommandStatus name, e.g. ERROR_STORAGE_EXIST.

    `response` is the Flipper's final reply, for commands that return details alongside an error status.
    """

    def __init__(self, status: str, message: str | None = None, response: "flipper_pb2.Main | None" = None):
        super().__init__(message or status)
        self.status = status
        self.response = response


class AppError(FlipperError):
    """An app running in RPC mode rejected a command. `code` and `text` are the app's own error details."""

    def __init__(self, status: str, code: int, text: str):
        message = f"{text} (app error {code})" if text else f"The app rejected the command without saying why ({status})"
        super().__init__(status, message)
        self.code = code
        self.text = text


class FlipperNotFoundError(Exception):
    """No matching Flipper was found while scanning."""


class FlipperDisconnectedError(ConnectionError):
    """The Bluetooth connection to the Flipper was lost or closed."""
