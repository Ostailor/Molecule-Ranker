from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class RankerConfig(BaseModel):
    results_dir: Path = Path("results")
    cache_dir: Path = Path(".cache/molecule-ranker")
    data_source: str = "public_adapters"
    default_top: int = Field(default=20, ge=1)
