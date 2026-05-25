from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class LiteratureQuery(BaseModel):
    """Traceable literature search query generated from disease, target, and molecule context."""

    disease: str
    molecule: str | None = None
    target: str | None = None
    query_text: str
    max_results: int = Field(default=5, ge=1)
    source: str = "PubMed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiteraturePaper(BaseModel):
    """Real retrieved literature record with bibliographic metadata and provenance."""

    source: str
    source_record_id: str
    title: str
    abstract: str | None = None
    pmid: str | None = None
    doi: str | None = None
    journal: str | None = None
    publication_date: date | None = None
    publication_types: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    url: str | None = None
    is_open_access: bool | None = None
    is_retracted: bool = False
    citation_count: int | None = Field(default=None, ge=0)
    retrieval_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_citation(self) -> Citation:
        return Citation.from_paper(self)


class Citation(BaseModel):
    """Citation extracted from a retrieved paper record."""

    source: str
    source_record_id: str
    title: str
    pmid: str | None = None
    doi: str | None = None
    url: str | None = None
    journal: str | None = None
    publication_year: int | None = None
    authors: list[str] = Field(default_factory=list)
    formatted: str | None = None

    @classmethod
    def from_paper(cls, paper: LiteraturePaper) -> Citation:
        year = paper.publication_date.year if paper.publication_date else None
        identifiers = []
        if paper.pmid:
            identifiers.append(f"PMID:{paper.pmid}")
        if paper.doi:
            identifiers.append(f"doi:{paper.doi}")
        year_text = f" ({year})" if year else ""
        id_text = f" {'; '.join(identifiers)}" if identifiers else ""
        formatted = f"{paper.title}.{year_text}{id_text}".strip()
        return cls(
            source=paper.source,
            source_record_id=paper.source_record_id,
            title=paper.title,
            pmid=paper.pmid,
            doi=paper.doi,
            url=paper.url,
            journal=paper.journal,
            publication_year=year,
            authors=paper.authors,
            formatted=formatted,
        )


class EvidenceClaim(BaseModel):
    """Conservative rule-extracted claim tied directly to a retrieved paper record."""

    claim_type: str
    text: str
    matched_terms: list[str] = Field(default_factory=list)
    study_type: str
    support_level: str = "mentions"
    cautions: list[str] = Field(default_factory=list)


class LiteratureEvidenceItem(BaseModel):
    """Supported claim set extracted from one retrieved literature record."""

    query: LiteratureQuery
    paper: LiteraturePaper
    citation: Citation
    claims: list[EvidenceClaim] = Field(default_factory=list)
    quality_score: float = Field(ge=0.0, le=1.0)


class LiteratureEvidenceBundle(BaseModel):
    """Candidate-level literature evidence, including explicit absence state."""

    candidate_name: str
    query_count: int = Field(default=0, ge=0)
    items: list[LiteratureEvidenceItem] = Field(default_factory=list)
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    absent_reason: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
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
    literature_quality: float = Field(default=0.0, ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str


class MoleculeCandidate(BaseModel):
    """Existing molecule candidate with evidence, warnings, and optional score details."""

    name: str
    molecule_type: str
    origin: Literal["existing", "generated"] = "existing"
    identifiers: dict[str, str] = Field(default_factory=dict)
    known_targets: list[str] = Field(default_factory=list)
    development_status: str | None = None
    mechanism_of_action: str | None = None
    chemical_metadata: dict[str, Any] = Field(default_factory=dict)
    generation_metadata: dict[str, Any] = Field(default_factory=dict)
    direct_evidence_available: bool = True
    evidence: list[EvidenceItem] = Field(default_factory=list)
    literature_evidence: LiteratureEvidenceBundle | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_breakdown: ScoreBreakdown | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_generated_candidate_contract(self) -> MoleculeCandidate:
        if self.origin == "generated":
            self.direct_evidence_available = False
            if self.evidence:
                raise ValueError(
                    "Generated MoleculeCandidate records must not contain EvidenceItem "
                    "claims; use generation_metadata for generation trace data."
                )
        return self


class GeneratedMoleculeHypothesis(BaseModel):
    """In-silico generated structure hypothesis with generation trace, not evidence."""

    name: str
    canonical_smiles: str
    molecule_type: str = "small_molecule"
    source: str = "SELFIES_MUTATION_CROSSOVER"
    target_symbol: str
    target_name: str | None = None
    seed_molecule_names: list[str] = Field(default_factory=list)
    seed_identifiers: list[dict[str, str]] = Field(default_factory=list)
    generation_score: float = Field(ge=0.0, le=1.0)
    rank: int | None = Field(default=None, ge=1)
    min_seed_similarity: float = Field(ge=0.0, le=1.0)
    max_seed_similarity: float = Field(ge=0.0, le=1.0)
    mean_seed_similarity: float = Field(ge=0.0, le=1.0)
    descriptors: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


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
    generated_candidates: list[GeneratedMoleculeHypothesis] = Field(default_factory=list)
    traces: list[AgentTrace]
    limitations: list[str] = Field(default_factory=list)
