from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RankerConfig(BaseModel):
    results_dir: Path = Path("results")
    cache_dir: Path = Path(".cache/molecule-ranker")
    use_cache: bool = True
    cache_ttl_seconds: int = Field(default=24 * 60 * 60, ge=1)
    data_source: str = "public_adapters"
    default_top: int = Field(default=20, ge=1)
    default_target_limit: int = Field(default=20, ge=1)
    target_source_limit: int = Field(default=100, ge=2)
    max_molecules_per_target: int = Field(default=10, ge=1)
    max_activity_records_per_target: int = Field(default=10, ge=1)
    max_indications_per_molecule: int = Field(default=20, ge=1)
    max_warnings_per_molecule: int = Field(default=20, ge=1)
    request_timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)
    strict_enrichment: bool = False
    allow_cached_real_data: bool = False

    def trace_metadata(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def runtime_agent_config(self, *, top: int, results_dir: Path) -> dict[str, Any]:
        trace_metadata = {
            **self.trace_metadata(),
            "default_top": top,
            "results_dir": str(results_dir),
        }
        return {
            "top": top,
            "results_dir": str(results_dir),
            "default_target_limit": self.default_target_limit,
            "target_source_limit": self.target_source_limit,
            "max_molecules_per_target": self.max_molecules_per_target,
            "max_activity_records_per_target": self.max_activity_records_per_target,
            "max_indications_per_molecule": self.max_indications_per_molecule,
            "max_warnings_per_molecule": self.max_warnings_per_molecule,
            "strict_enrichment": self.strict_enrichment,
            "ranker_config": trace_metadata,
        }
