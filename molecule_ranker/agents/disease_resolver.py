from __future__ import annotations

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.data_sources.base import DiseaseResolverDataSource
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter


class DiseaseResolverAgent(BaseAgent):
    name = "DiseaseResolverAgent"

    def __init__(self, data_source: DiseaseResolverDataSource | None = None) -> None:
        super().__init__()
        self._data_source = data_source or OpenTargetsAdapter()

    def process(self, context: PipelineContext) -> PipelineContext:
        query = context.disease_input.strip()
        disease = self._data_source.resolve_disease(query)
        context.disease = disease
        context.config[f"{self.name}.summary"] = (
            f"Resolved canonical disease: {disease.canonical_name}"
        )
        context.config[f"{self.name}.source"] = getattr(
            self._data_source, "source_name", self._data_source.__class__.__name__
        )
        return context

    def summarize_input(self, context: PipelineContext) -> str:
        return f"Original disease input: {context.disease_input}"

    def summarize_output(self, context: PipelineContext) -> str:
        return str(context.config.get(f"{self.name}.summary", "Resolved disease."))

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        identifiers = context.disease.identifiers if context.disease else {}
        return {
            "source": context.config.get(f"{self.name}.source"),
            "canonical_name": context.disease.canonical_name if context.disease else None,
            "identifiers": identifiers,
        }
