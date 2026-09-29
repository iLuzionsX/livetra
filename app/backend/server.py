from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from mlx_worker import MLXWorkerService, MODEL_PATH
from session import SessionHub, TranscriptionSession


worker = MLXWorkerService()
hub = SessionHub()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await worker.start()
    try:
        yield
    finally:
        await worker.stop()


app = FastAPI(title="LiveTR3 Local Transcribe + Translate", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "transcription_engine": "gemma",
        "asr_model": MODEL_PATH,
        "asr_state": worker.status.state,
        "translation_model": MODEL_PATH,
        "translation_state": worker.status.state,
    }


@app.websocket("/")
async def websocket_root(websocket: WebSocket) -> None:
    await TranscriptionSession(
        websocket,
        worker,
        hub,
    ).run()


@app.websocket("/ws")
async def websocket_ws(websocket: WebSocket) -> None:
    await TranscriptionSession(
        websocket,
        worker,
        hub,
    ).run()
