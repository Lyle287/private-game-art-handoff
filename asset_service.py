"""HTTP entry point for the moderated game-asset handoff."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from game_asset_handoff import (
    AssetRequest,
    InfraiAssetBackend,
    InfraiError,
    ModeratedAssetWorkflow,
)


class GenerateAssetBody(BaseModel):
    player_id: str = Field(min_length=1, max_length=80)
    live_event: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")
    prompt: str = Field(min_length=3, max_length=1000)
    moderation: Literal["approved", "pending", "rejected"]
    link_ttl_seconds: int = Field(default=900, ge=60, le=86400)


class AssetResponse(BaseModel):
    asset_id: str
    state: Literal["ready", "queued", "rejected"]
    live_event: str
    private_url: str | None = None
    expires_seconds: int | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    backend = InfraiAssetBackend(
        api_key=os.environ["INFRAI_API_KEY"],
        bucket=os.environ.get("INFRAI_BUCKET", "player-event-assets"),
    )
    cleanup = os.environ.get("INFRAI_DEMO_CLEANUP") == "1"
    try:
        backend.ensure_bucket(require_new=cleanup)
        app.state.workflow = ModeratedAssetWorkflow(backend)
        yield
    finally:
        try:
            if cleanup:
                backend.cleanup()
        finally:
            backend.close()


app = FastAPI(title="Private game asset handoff", lifespan=lifespan)


@app.exception_handler(InfraiError)
async def infrai_error_handler(_: Request, error: InfraiError):
    status = error.status_code if 400 <= error.status_code < 500 else 502
    return __import__("fastapi").responses.JSONResponse(
        status_code=status, content={"detail": error.detail, "code": error.code}
    )


@app.post("/assets", response_model=AssetResponse)
def submit_asset(body: GenerateAssetBody, request: Request) -> AssetResponse:
    result = request.app.state.workflow.submit(AssetRequest(**body.model_dump()))
    if result.state == "rejected":
        raise HTTPException(status_code=422, detail="Asset was rejected by moderation")
    return AssetResponse(**result.__dict__)
