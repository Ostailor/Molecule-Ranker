from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

QueryType = Literal[
    "disease_target",
    "molecule_target",
    "molecule_disease",
    "mechanism",
    "clinical",
    "safety",
]

ClaimType = Literal[
    "disease_target_association",
    "molecule_target_interaction",
    "molecule_disease_association",
    "mechanism_support",
    "clinical_support",
    "safety_concern",
    "negative_or_contradictory",
    "mention_only",
]

ClaimDirection = Literal[
    "supportive",
    "contradictory",
    "neutral",
    "safety_concern",
]


class LiteratureBaseModel(BaseModel):
    """Strict literature model base to prevent accidental article-body storage."""

    model_config = ConfigDict(extra="forbid")


class LiteratureQuery(LiteratureBaseModel):
    """Traceable literature search query generated from biomedical context."""

    query_id: str
    disease_name: str
    target_symbol: str | None = None
    target_name: str | None = None
    molecule_name: str | None = None
    molecule_identifiers: dict[str, str] = Field(default_factory=dict)
    query_text: str
    query_type: QueryType
    max_results: int = Field(ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(LiteratureBaseModel):
    """Bibliographic citation metadata extracted from a retrieved paper record."""

    title: str
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    publication_date: str | None = None
    year: int | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    openalex_id: str | None = None
    url: str | None = None
    citation_text: str


class LiteraturePaper(LiteratureBaseModel):
    """Retrieved paper metadata; stores source-provided abstracts, not full article text."""

    paper_id: str
    source: str
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    publication_date: str | None = None
    year: int | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    openalex_id: str | None = None
    publication_type: str | None = None
    is_review: bool
    is_clinical: bool
    is_preclinical: bool
    is_retracted: bool | None = None
    cited_by_count: int | None = Field(default=None, ge=0)
    url: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retrieved_at")
    @classmethod
    def retrieved_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value


class EvidenceClaim(LiteratureBaseModel):
    """Rule-extracted literature claim tied to a source-provided snippet."""

    claim_id: str
    paper_id: str
    candidate_name: str | None = None
    target_symbol: str | None = None
    disease_name: str | None = None
    claim_type: ClaimType
    claim_text: str
    supporting_snippet: str
    confidence: float = Field(ge=0.0, le=1.0)
    direction: ClaimDirection
    extraction_method: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiteratureEvidenceBundle(LiteratureBaseModel):
    """Papers and extracted claims returned for one literature query."""

    query: LiteratureQuery
    papers: list[LiteraturePaper] = Field(default_factory=list)
    claims: list[EvidenceClaim] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
