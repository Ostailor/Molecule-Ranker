from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["review"])


@router.get("/review/health")
def review_health() -> dict[str, object]:
    return {"ok": True, "module": "review"}
