from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.knowledge_graph.schemas import GraphProvenance


def provenance_id(source_type: str, source_record_id: str | None, transformation: str) -> str:
    raw = f"{source_type}:{source_record_id or ''}:{transformation}"
    return f"prov:{uuid5(NAMESPACE_URL, raw).hex[:16]}"


def graph_provenance(
    *,
    source_type: str,
    transformation: str,
    confidence: float,
    source_artifact_id: str | None = None,
    source_record_id: str | None = None,
    source_url: str | None = None,
) -> GraphProvenance:
    return GraphProvenance(
        provenance_id=provenance_id(source_type, source_record_id, transformation),
        source_type=source_type,
        source_artifact_id=source_artifact_id,
        source_record_id=source_record_id,
        source_url=source_url,
        retrieved_at=datetime.now(UTC),
        transformation=transformation,
        confidence=confidence,
    )


__all__ = ["GraphProvenance", "graph_provenance", "provenance_id"]
