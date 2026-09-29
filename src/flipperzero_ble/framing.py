"""Length-delimited framing for PB.Main messages: each message is prefixed with its size as a varint."""

from ._proto import flipper_pb2


def encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(buffer: bytes | bytearray, offset: int = 0) -> tuple[int, int] | None:
    """Returns (value, offset after the varint), or None if the buffer ends mid-varint."""
    value = 0
    shift = 0
    while offset < len(buffer):
        byte = buffer[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    return None


def encode(message: flipper_pb2.Main) -> bytes:
    body = message.SerializeToString()
    return encode_varint(len(body)) + body


class Decoder:
    """Reassembles messages from arbitrarily split transport chunks."""

    def __init__(self):
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[flipper_pb2.Main]:
        self._buffer.extend(data)
        messages = []
        while True:
            header = decode_varint(self._buffer)
            if header is None:
                break
            length, start = header
            if len(self._buffer) < start + length:
                break
            message = flipper_pb2.Main()
            message.ParseFromString(bytes(self._buffer[start : start + length]))
            del self._buffer[: start + length]
            messages.append(message)
        return messages
