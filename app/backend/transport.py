from __future__ import annotations

import asyncio
import json
import struct
from typing import Any, Protocol


FRAME_TEXT = 0x01
FRAME_BINARY = 0x02
HEADER = struct.Struct(">BI")


class SessionTransport(Protocol):
    query_params: dict[str, str]

    async def accept(self) -> None:
        ...

    async def receive(self) -> dict[str, Any]:
        ...

    async def send_json(self, payload: dict[str, Any]) -> None:
        ...


class UnixSocketTransport:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: Any,
        query_params: dict[str, str],
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.query_params = query_params

    async def accept(self) -> None:
        return

    async def receive(self) -> dict[str, Any]:
        try:
            header = await self.reader.readexactly(HEADER.size)
            frame_type, length = HEADER.unpack(header)
            payload = await self.reader.readexactly(length)
        except (asyncio.IncompleteReadError, ConnectionError):
            return {"type": "websocket.disconnect"}

        if frame_type == FRAME_TEXT:
            return {"text": payload.decode("utf-8")}
        if frame_type == FRAME_BINARY:
            return {"bytes": payload}
        return {"type": "websocket.disconnect"}

    async def send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.writer.write(HEADER.pack(FRAME_TEXT, len(data)) + data)
        await self.writer.drain()
