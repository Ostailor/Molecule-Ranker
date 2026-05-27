from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["experiments"])


@router.get("/experiments/health")
def experiments_health() -> dict[str, object]:
    return {"ok": True, "module": "experiments"}
