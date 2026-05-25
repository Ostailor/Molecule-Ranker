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
    enable_literature: bool = True
    strict_literature: bool = False
    literature_sources: list[str] = Field(default_factory=lambda: ["pubmed"])
    enable_openalex_enrichment: bool = True
    max_literature_queries: int = Field(default=100, ge=1)
    max_papers_per_query: int = Field(default=10, ge=1)
    max_targets_for_literature: int = Field(default=10, ge=1)
    max_candidates_for_literature: int = Field(default=20, ge=1)
    ncbi_tool: str = "molecule-ranker"
    ncbi_email: str | None = None
    ncbi_api_key: str | None = None
    literature_request_timeout_seconds: float = Field(default=20.0, gt=0)
    literature_max_retries: int = Field(default=3, ge=0)
    literature_cache_ttl_seconds: int = Field(default=24 * 60 * 60, ge=1)
    max_literature_queries_per_candidate: int = Field(default=3, ge=1)
    max_literature_results_per_query: int = Field(default=5, ge=1)
    literature_failure_policy: str = "skip"
    enable_openalex_metadata: bool = False
    request_timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)
    strict_enrichment: bool = False
    allow_cached_real_data: bool = False

    def trace_metadata(self) -> dict[str, Any]:
        metadata = self.model_dump(mode="json")
        if metadata.get("ncbi_api_key"):
            metadata["ncbi_api_key"] = "***"
        return metadata

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
            "max_literature_queries": self.max_literature_queries,
            "max_papers_per_query": self.max_papers_per_query,
            "max_targets_for_literature": self.max_targets_for_literature,
            "max_candidates_for_literature": self.max_candidates_for_literature,
            "max_literature_queries_per_candidate": self.max_literature_queries_per_candidate,
            "max_literature_results_per_query": self.max_literature_results_per_query,
            "enable_literature": self.enable_literature,
            "strict_literature": self.strict_literature,
            "literature_sources": list(self.literature_sources),
            "literature_failure_policy": (
                "fail" if self.strict_literature else self.literature_failure_policy
            ),
            "enable_openalex_enrichment": self.enable_openalex_enrichment,
            "enable_openalex_metadata": self.enable_openalex_metadata,
            "ncbi_tool": self.ncbi_tool,
            "ncbi_email": self.ncbi_email,
            "literature_request_timeout_seconds": self.literature_request_timeout_seconds,
            "literature_max_retries": self.literature_max_retries,
            "literature_cache_ttl_seconds": self.literature_cache_ttl_seconds,
            "strict_enrichment": self.strict_enrichment,
            "ranker_config": trace_metadata,
        }
