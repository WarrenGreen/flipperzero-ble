class FlipperError(Exception):
    """A command was rejected by the Flipper. `status` is the CommandStatus name, e.g. ERROR_STORAGE_EXIST."""

    def __init__(self, status: str, message: str | None = None):
        super().__init__(message or status)
        self.status = status


class FlipperNotFoundError(Exception):
    """No matching Flipper was found while scanning."""
