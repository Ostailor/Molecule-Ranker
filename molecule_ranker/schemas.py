from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Disease(BaseModel):
    """Normalized disease query with identifiers and descriptive context."""

    input_name: str
    canonical_name: str
    synonyms: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    description: str | None = None


class DiseaseMatch(BaseModel):
    """Candidate disease search match considered during resolution."""

    id: str
    name: str
    entity: str
    score: float | None = None
    synonyms: list[str] = Field(default_factory=list)
    description: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)
    match_reason: str


class EvidenceItem(BaseModel):
    """Traceable evidence item from a public biomedical source."""

    source: str
    source_record_id: str | None = None
    title: str
    url: str | None = None
    evidence_type: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    retrieval_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class Target(BaseModel):
    """Disease-associated biological target hypothesis."""

    symbol: str
    name: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)
    organism: str = "human"
    target_class: str | None = None
    tractability: list[dict[str, Any]] = Field(default_factory=list)
    safety: list[dict[str, Any]] = Field(default_factory=list)
    disease_relevance_score: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    mechanism: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    """Transparent score components for a molecule ranking decision."""

    disease_target_relevance: float = Field(ge=0.0, le=1.0)
    molecule_target_evidence: float = Field(ge=0.0, le=1.0)
    mechanism_plausibility: float = Field(ge=0.0, le=1.0)
    clinical_precedence: float = Field(ge=0.0, le=1.0)
    safety_prior: float = Field(ge=0.0, le=1.0)
    data_quality: float = Field(ge=0.0, le=1.0)
    novelty_or_repurposing_value: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str


class MoleculeCandidate(BaseModel):
    """Existing molecule candidate with evidence, warnings, and optional score details."""

    name: str
    molecule_type: str
    identifiers: dict[str, str] = Field(default_factory=dict)
    known_targets: list[str] = Field(default_factory=list)
    development_status: str | None = None
    mechanism_of_action: str | None = None
    chemical_metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_breakdown: ScoreBreakdown | None = None
    warnings: list[str] = Field(default_factory=list)


class AgentTrace(BaseModel):
    """Compact trace record for an agent step in a ranking run."""

    agent_name: str
    input_summary: str
    output_summary: str
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RankingRun(BaseModel):
    """Complete ranking run artifact containing inputs, outputs, trace, and limitations."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    disease: Disease
    targets: list[Target]
    candidates: list[MoleculeCandidate]
    traces: list[AgentTrace]
    limitations: list[str] = Field(default_factory=list)
