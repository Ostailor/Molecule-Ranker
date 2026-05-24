from __future__ import annotations

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.target_discovery import TargetDiscoveryAgent
from molecule_ranker.data_sources.errors import ExternalDataUnavailableError, TargetDiscoveryError
from molecule_ranker.schemas import Disease, EvidenceItem, Target


def _disease() -> Disease:
    return Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        synonyms=[],
        identifiers={"open_targets": "MONDO_0005180"},
        description=None,
    )


def _evidence(source_record_id: str = "MONDO_0005180:ENSG") -> EvidenceItem:
    return EvidenceItem(
        source="Open Targets",
        source_record_id=source_record_id,
        title="Target association",
        evidence_type="target_disease_association",
        summary="Mocked Open Targets target evidence.",
        confidence=0.8,
        metadata={"query": "disease.associatedTargets"},
    )


class SortingTargetSource:
    source_name = "Open Targets"

    def __init__(self) -> None:
        self.requested_limit: int | None = None

    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
        self.requested_limit = limit
        return [
            Target(
                symbol="LOW",
                name="Low score target",
                disease_relevance_score=0.2,
                evidence=[_evidence("low")],
                mechanism=None,
            ),
            Target(
                symbol="HIGH",
                name="High score target",
                disease_relevance_score=0.9,
                evidence=[_evidence("high")],
                mechanism=None,
            ),
            Target(
                symbol="MID",
                name="Middle score target",
                disease_relevance_score=0.5,
                evidence=[_evidence("mid")],
                mechanism=None,
            ),
        ]


class NoTargetsSource:
    source_name = "Open Targets"

    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
        return []


class NoEvidenceSource:
    source_name = "Open Targets"

    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
        return [
            Target(
                symbol="NOEVID",
                name="No evidence target",
                disease_relevance_score=0.9,
                evidence=[],
                mechanism=None,
            )
        ]


class UnavailableTargetSource:
    source_name = "Open Targets"

    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
        raise ExternalDataUnavailableError("Open Targets unavailable")


def test_target_discovery_sorts_filters_limits_and_traces():
    source = SortingTargetSource()
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        config={"target_limit": 2},
    )

    result = TargetDiscoveryAgent(source).run(context)

    assert source.requested_limit == 100
    assert [target.symbol for target in result.targets] == ["HIGH", "MID"]
    assert all(target.evidence for target in result.targets)
    trace = result.traces[-1]
    assert trace.agent_name == "TargetDiscoveryAgent"
    assert trace.metadata["disease_id"] == "MONDO_0005180"
    assert trace.metadata["source"] == "Open Targets"
    assert trace.metadata["targets_retrieved"] == 3
    assert trace.metadata["targets_retained"] == 2
    assert trace.metadata["top_target_symbols"] == ["HIGH", "MID"]


def test_target_discovery_requires_resolved_disease():
    context = PipelineContext(disease_input="Parkinson disease")

    with pytest.raises(TargetDiscoveryError):
        TargetDiscoveryAgent(SortingTargetSource()).run(context)

    assert context.traces[-1].warnings


def test_target_discovery_fails_when_no_targets_returned():
    context = PipelineContext(disease_input="Parkinson disease", disease=_disease())

    with pytest.raises(TargetDiscoveryError):
        TargetDiscoveryAgent(NoTargetsSource()).run(context)

    assert context.traces[-1].warnings


def test_target_discovery_rejects_targets_without_evidence():
    context = PipelineContext(disease_input="Parkinson disease", disease=_disease())

    with pytest.raises(TargetDiscoveryError):
        TargetDiscoveryAgent(NoEvidenceSource()).run(context)

    assert context.traces[-1].warnings


def test_target_discovery_propagates_external_unavailability():
    context = PipelineContext(disease_input="Parkinson disease", disease=_disease())

    with pytest.raises(ExternalDataUnavailableError):
        TargetDiscoveryAgent(UnavailableTargetSource()).run(context)

    assert context.traces[-1].warnings
