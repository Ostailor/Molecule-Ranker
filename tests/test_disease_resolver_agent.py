from __future__ import annotations

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.disease_resolver import DiseaseResolverAgent
from molecule_ranker.data_sources.errors import DiseaseResolutionError, ExternalDataUnavailableError
from molecule_ranker.schemas import Disease


class SuccessfulDiseaseSource:
    source_name = "Open Targets"

    def resolve_disease(self, disease_name: str) -> Disease:
        self.last_resolution_metadata = {
            "search_hit_count": 3,
            "selected_disease_id": "MONDO_0005180",
            "selected_disease_name": "Parkinson disease",
            "match_reason": "exact_canonical_match",
            "ambiguity": False,
        }
        assert disease_name == "Parkinson disease"
        return Disease(
            input_name=disease_name,
            canonical_name="Parkinson disease",
            synonyms=["Parkinson's disease"],
            identifiers={"open_targets": "MONDO_0005180", "mondo": "MONDO:0005180"},
            description="Resolved from a mocked public source.",
        )


class UnresolvedDiseaseSource:
    source_name = "Open Targets"

    def resolve_disease(self, disease_name: str) -> Disease:
        raise DiseaseResolutionError(f"No disease for {disease_name}")


class UnavailableDiseaseSource:
    source_name = "Open Targets"

    def resolve_disease(self, disease_name: str) -> Disease:
        raise ExternalDataUnavailableError("network unavailable")


def test_disease_resolver_sets_real_disease_and_trace_metadata():
    context = PipelineContext(disease_input="  Parkinson disease  ")

    result = DiseaseResolverAgent(SuccessfulDiseaseSource()).run(context)

    assert result.disease is not None
    assert result.disease.input_name == "Parkinson disease"
    assert result.disease.canonical_name == "Parkinson disease"
    assert result.disease.identifiers["mondo"] == "MONDO:0005180"
    assert result.disease.synonyms == ["Parkinson's disease"]
    assert result.traces[-1].agent_name == "DiseaseResolverAgent"
    assert result.traces[-1].input_summary == "Original disease input:   Parkinson disease  "
    assert result.traces[-1].output_summary == "Resolved canonical disease: Parkinson disease"
    assert result.traces[-1].metadata["source"] == "Open Targets"
    assert result.traces[-1].metadata["identifiers"] == {
        "open_targets": "MONDO_0005180",
        "mondo": "MONDO:0005180",
    }
    assert result.traces[-1].metadata["search_hit_count"] == 3
    assert result.traces[-1].metadata["selected_disease_id"] == "MONDO_0005180"
    assert result.traces[-1].metadata["selected_disease_name"] == "Parkinson disease"
    assert result.traces[-1].metadata["match_reason"] == "exact_canonical_match"
    assert result.traces[-1].metadata["ambiguity"] is False


def test_disease_resolver_raises_when_unresolved_and_does_not_set_disease():
    context = PipelineContext(disease_input="unknown disease")

    with pytest.raises(DiseaseResolutionError):
        DiseaseResolverAgent(UnresolvedDiseaseSource()).run(context)

    assert context.disease is None
    assert context.traces[-1].agent_name == "DiseaseResolverAgent"
    assert context.traces[-1].warnings


def test_disease_resolver_raises_when_external_source_unavailable():
    context = PipelineContext(disease_input="Parkinson disease")

    with pytest.raises(ExternalDataUnavailableError):
        DiseaseResolverAgent(UnavailableDiseaseSource()).run(context)

    assert context.disease is None
    assert context.traces[-1].warnings
