from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from molecule_ranker.integrations.webhooks import (
    WebhookError,
    WebhookIngestionConfig,
    WebhookIngestionService,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.server.dependencies import platform_database

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/{external_system_id}")
@router.post("/webhooks/{external_system_id}/ingest")
async def ingest_webhook(
    external_system_id: str,
    request: Request,
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    connector = database.get_integration_connector(external_system_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Integration connector not found.")
    raw_payload = await request.body()
    try:
        event = WebhookIngestionService(database).ingest(
            raw_payload=raw_payload,
            headers=dict(request.headers),
            config=WebhookIngestionConfig.from_connector(connector),
            actor_user_id=None,
        )
    except WebhookError as exc:
        status_code = 413 if "maximum size" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"accepted": True, "webhook_event": event.model_dump(mode="json")}
