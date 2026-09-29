from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

from mlx_worker import MLXWorkerService
from session import SessionHub, TranscriptionSession
from transport import FRAME_TEXT, HEADER, UnixSocketTransport


IDLE_UNLOAD_SECONDS = max(
    5.0, float(os.getenv("MLX_WORKER_IDLE_UNLOAD_SECONDS", "45"))
)


class EngineHost:
    def __init__(self) -> None:
        self.worker = MLXWorkerService()
        self.hub = SessionHub()
        self._idle_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    async def start(self, socket_path: Path) -> None:
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            socket_path.unlink()

        server = await asyncio.start_unix_server(self._handle_client, path=str(socket_path))
        self._idle_task = asyncio.create_task(self._idle_unloader())
        print(json.dumps({"type": "engine_ready", "socket": str(socket_path)}), flush=True)

        async with server:
            await self._stopped.wait()
            server.close()
            await server.wait_closed()

        if self._idle_task is not None:
            self._idle_task.cancel()
            await asyncio.gather(self._idle_task, return_exceptions=True)
        await self.worker.stop()
        if socket_path.exists():
            socket_path.unlink()

    def stop(self) -> None:
        self._stopped.set()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        query_params = await self._read_hello(reader)
        transport = UnixSocketTransport(reader, writer, query_params)
        try:
            await TranscriptionSession(
                transport,
                self.worker,
                self.hub,
            ).run()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _read_hello(self, reader: asyncio.StreamReader) -> dict[str, str]:
        try:
            header = await reader.readexactly(HEADER.size)
            frame_type, length = HEADER.unpack(header)
            payload = await reader.readexactly(length)
            if frame_type != FRAME_TEXT:
                return {}
            hello = json.loads(payload.decode("utf-8"))
            if hello.get("type") != "hello":
                return {}
            session = str(hello.get("session") or "")
            return {"session": session} if session else {}
        except Exception:
            return {}

    async def _idle_unloader(self) -> None:
        idle_since: float | None = None
        while True:
            await asyncio.sleep(2)
            if await self.hub.active_producer_count() > 0:
                idle_since = None
                continue
            if idle_since is None:
                idle_since = time.monotonic()
                continue
            if time.monotonic() - idle_since >= IDLE_UNLOAD_SECONDS:
                await self.worker.stop()
                idle_since = time.monotonic()


async def main() -> None:
    socket_env = os.getenv("LIVETR3_ENGINE_SOCKET")
    if not socket_env and len(sys.argv) > 1:
        socket_env = sys.argv[1]
    if not socket_env:
        raise SystemExit("LIVETR3_ENGINE_SOCKET is required")

    host = EngineHost()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, host.stop)
    await host.start(Path(socket_env))


if __name__ == "__main__":
    asyncio.run(main())
