import pytest

from flipperzero_ble._proto import flipper_pb2
from flipperzero_ble.framing import Decoder, decode_varint, encode, encode_varint


@pytest.mark.parametrize("value", [0, 1, 127, 128, 300, 16_383, 16_384, 2**32 - 1])
def test_varint_round_trip(value):
    encoded = encode_varint(value)
    assert decode_varint(encoded) == (value, len(encoded))


def test_decode_varint_incomplete():
    assert decode_varint(bytes([0x80])) is None


def test_decoder_reassembles_split_messages():
    messages = [
        flipper_pb2.Main(command_id=i, storage_read_response={"file": {"data": bytes(range(i * 40 % 256)) * 3}})
        for i in range(1, 6)
    ]
    stream = b"".join(encode(m) for m in messages)
    decoder = Decoder()
    received = []
    for i in range(0, len(stream), 5):
        received.extend(decoder.feed(stream[i : i + 5]))
    assert received == messages
