from __future__ import annotations

import asyncio
import json
import struct

from transport import FRAME_BINARY, FRAME_TEXT, HEADER, UnixSocketTransport


def frame(frame_type: int, payload: bytes) -> bytes:
    return HEADER.pack(frame_type, len(payload)) + payload


def test_header_codec_round_trip() -> None:
    encoded = frame(FRAME_TEXT, b'{"type":"start"}')
    frame_type, length = HEADER.unpack(encoded[: HEADER.size])
    assert frame_type == FRAME_TEXT
    assert length == len(b'{"type":"start"}')
    assert encoded[HEADER.size :] == b'{"type":"start"}'


def test_unix_socket_transport_receive_text_and_binary() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = _MemoryWriter()
        transport = UnixSocketTransport(reader, writer, {"session": "abc"})
        reader.feed_data(frame(FRAME_TEXT, b'{"type":"start"}'))
        reader.feed_data(frame(FRAME_BINARY, b"\x00\x01"))
        assert await transport.receive() == {"text": '{"type":"start"}'}
        assert await transport.receive() == {"bytes": b"\x00\x01"}
        await transport.send_json({"type": "status", "state": "ready"})
        assert writer.payloads
        out_type, out_length = HEADER.unpack(writer.payloads[0][: HEADER.size])
        assert out_type == FRAME_TEXT
        payload = writer.payloads[0][HEADER.size :]
        assert out_length == len(payload)
        assert json.loads(payload) == {"type": "status", "state": "ready"}

    asyncio.run(run())


def test_transport_preserves_session_query_params() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        transport = UnixSocketTransport(
            reader,
            _MemoryWriter(),
            {"session": "session-123", "role": "viewer"},
        )
        assert transport.query_params == {"session": "session-123", "role": "viewer"}

    asyncio.run(run())


def test_incomplete_frame_becomes_disconnect_instead_of_crashing() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        transport = UnixSocketTransport(reader, _MemoryWriter(), {})
        reader.feed_data(b"\x01\x00")
        reader.feed_eof()
        assert await transport.receive() == {"type": "websocket.disconnect"}

    asyncio.run(run())


def test_unknown_frame_type_is_treated_as_disconnect() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        transport = UnixSocketTransport(reader, _MemoryWriter(), {})
        reader.feed_data(frame(0x7F, b"unexpected"))
        assert await transport.receive() == {"type": "websocket.disconnect"}

    asyncio.run(run())


def test_send_json_preserves_unicode() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = _MemoryWriter()
        transport = UnixSocketTransport(reader, writer, {})
        await transport.send_json({"type": "final", "translation": "Buenos días 👋"})
        out_type, out_length = HEADER.unpack(writer.payloads[0][: HEADER.size])
        payload = writer.payloads[0][HEADER.size :]
        assert out_type == FRAME_TEXT
        assert out_length == len(payload)
        assert json.loads(payload) == {"type": "final", "translation": "Buenos días 👋"}

    asyncio.run(run())


class _MemoryWriter:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.payloads.append(data)

    async def drain(self) -> None:
        return
