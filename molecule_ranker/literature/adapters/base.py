from __future__ import annotations

from typing import Protocol

from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.literature.schemas import LiteraturePaper, LiteratureQuery


class LiteratureSearchAdapter(Protocol):
    """Interface for public literature search adapters."""

    source_name: str

    def search(self, query: LiteratureQuery) -> list[LiteraturePaper]:
        """Retrieve paper records for a literature query."""
        ...


class LiteratureMetadataAdapter(Protocol):
    """Interface for optional paper metadata enrichment adapters."""

    source_name: str

    def enrich(self, papers: list[LiteraturePaper]) -> list[LiteraturePaper]:
        """Return papers with additional public metadata."""
        ...


class LiteratureHealthCheckAdapter(Protocol):
    """Interface for public literature adapter health checks."""

    def health_check(
        self,
        timeout_seconds: float | None = None,
    ) -> AdapterHealthStatus:
        """Check whether the public source is reachable."""
        ...
