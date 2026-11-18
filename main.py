"""HTTP entry point for BotHost; the Telegram long-polling worker runs beside it."""

import os
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from bot import TOKEN, main as run_bot


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN in the hosting service environment.")
    worker = threading.Thread(target=run_bot, name="telegram-poller", daemon=True)
    worker.start()
    app.state.bot_worker = worker
    yield


app = FastAPI(title="Cat Moderator", lifespan=lifespan)


@app.get("/")
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.getenv("SERVER_PORT", os.getenv("PORT", "8000")))
    uvicorn.run(app, host="0.0.0.0", port=port)
