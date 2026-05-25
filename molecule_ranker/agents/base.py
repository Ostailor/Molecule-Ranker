from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    EvidenceRetrievalError,
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
    TargetDiscoveryError,
)
from molecule_ranker.literature.errors import LiteratureParsingError, LiteratureRetrievalError
from molecule_ranker.schemas import AgentTrace, Disease, MoleculeCandidate, Target
from molecule_ranker.utils.logging import get_logger

T = TypeVar("T")
class AgentExecutionError(RuntimeError):
    """Raised when an agent has an unexpected programming or runtime failure."""


DOMAIN_ERRORS = (
    ExternalDataUnavailableError,
    DiseaseResolutionError,
    TargetDiscoveryError,
    MoleculeRetrievalError,
    EvidenceRetrievalError,
    LiteratureRetrievalError,
    LiteratureParsingError,
    NoCandidatesFoundError,
    AgentExecutionError,
)


class AgentResult(BaseModel, Generic[T]):
    """Generic container for optional structured agent outputs."""

    value: T | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineContext(BaseModel):
    """Mutable pipeline state passed from one agent to the next."""

    disease_input: str
    disease: Disease | None = None
    targets: list[Target] = Field(default_factory=list)
    candidates: list[MoleculeCandidate] = Field(default_factory=list)
    traces: list[AgentTrace] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    output_dir: Path | None = None


class BaseAgent(ABC):
    """Base class for deterministic, traceable pipeline agents."""

    name = "BaseAgent"

    def __init__(self) -> None:
        self.logger = get_logger(self.name)

    def run(self, context: PipelineContext) -> PipelineContext:
        input_summary = self.summarize_input(context)
        warnings: list[str] = []
        caught: Exception | None = None
        try:
            updated = self.process(context)
            output_summary = self.summarize_output(updated)
            self.logger.info("%s completed", self.name)
        except Exception as exc:  # pragma: no cover - exercised through behavior, not type
            updated = context
            warning = f"{self.name} failed gracefully: {exc}"
            warnings.append(warning)
            output_summary = warning
            self.logger.warning(warning)
            caught = exc

        updated.traces.append(
            AgentTrace(
                agent_name=self.name,
                input_summary=input_summary,
                output_summary=output_summary,
                warnings=warnings,
                metadata=self.trace_metadata(updated),
            )
        )
        if caught is not None:
            if isinstance(caught, DOMAIN_ERRORS):
                raise caught
            raise AgentExecutionError(f"{self.name} failed unexpectedly: {caught}") from caught
        return updated

    @abstractmethod
    def process(self, context: PipelineContext) -> PipelineContext:
        """Override in subclasses to mutate or replace the pipeline context."""
        raise NotImplementedError

    def summarize_input(self, context: PipelineContext) -> str:
        return f"PipelineContext(disease_input={context.disease_input!r})"

    def summarize_output(self, context: PipelineContext) -> str:
        return "Agent completed successfully."

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        return {}
