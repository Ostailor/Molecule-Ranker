from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.experimental_evidence import ExperimentalEvidenceAgent
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.experiments.store import ExperimentalResultStore
from molecule_ranker.schemas import GeneratedMoleculeHypothesis, MoleculeCandidate

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _context() -> AssayContext:
    return AssayContext(
        assay_context_id="context-binding",
        assay_name="Binding screen",
        assay_type="biochemical",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        endpoint=AssayEndpoint(
            endpoint_id="endpoint-binding",
            name="binding_affinity",
            endpoint_category="potency",
            unit="nM",
            directionality="lower_is_better",
        ),
    )


def _assay_result(
    result_id: str = "result-1",
    *,
    candidate_id: str | None = "CHEMBL887",
    candidate_name: str = "Rasagiline",
    candidate_origin: str = "existing",
    canonical_smiles: str | None = "C#CCN1CCC2=CC=CC=C21",
    inchi_key: str | None = "RUYUTDCTDCBNSZ-UHFFFAOYSA-N",
    outcome_label: str = "positive",
    activity_direction: str = "active",
    qc_status: str = "passed",
    source_record_id: str | None = "row-1",
) -> AssayResult:
    return AssayResult(
        result_id=result_id,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_origin=candidate_origin,  # type: ignore[arg-type]
        canonical_smiles=canonical_smiles,
        inchi_key=inchi_key,
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        assay_context=_context(),
        measured_value=12.5,
        measured_value_numeric=12.5,
        unit="nM",
        normalized_value=12.5,
        normalized_unit="nM",
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        confidence=0.8,
        qc_status=qc_status,  # type: ignore[arg-type]
        source="csv_import",
        source_record_id=source_record_id,
        imported_at=FIXED_TIME,
    )


def _candidate() -> MoleculeCandidate:
    return MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        chemical_metadata={
            "canonical_smiles": "C#CCN1CCC2=CC=CC=C21",
            "inchi_key": "RUYUTDCTDCBNSZ-UHFFFAOYSA-N",
        },
    )


def _generated_hypothesis() -> GeneratedMoleculeHypothesis:
    return GeneratedMoleculeHypothesis(
        name="Generated-MAOB-001",
        canonical_smiles="C#CCN(C)CCc1ccccn1",
        target_symbol="MAOB",
        generation_score=0.7,
        min_seed_similarity=0.4,
        max_seed_similarity=0.6,
        mean_seed_similarity=0.5,
    )


def test_experimental_evidence_agent_disabled_mode_is_noop(tmp_path):
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        config={
            "enable_experimental_evidence": False,
            "experimental_db_path": str(tmp_path / "experiments.sqlite"),
        },
    )

    updated = ExperimentalEvidenceAgent().run(context)

    assert updated.candidates[0].evidence == []
    assert "experimental_evidence" not in updated.config
    assert updated.traces[-1].agent_name == "ExperimentalEvidenceAgent"
    assert updated.traces[-1].metadata["enabled"] is False


def test_experimental_evidence_agent_loads_links_and_attaches_evidence(tmp_path):
    db_path = tmp_path / "experiments.sqlite"
    ExperimentalResultStore(db_path).import_results([_assay_result()], actor="test")
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        config={
            "enable_experimental_evidence": True,
            "experimental_db_path": str(db_path),
        },
    )

    updated = ExperimentalEvidenceAgent().run(context)

    evidence = updated.candidates[0].evidence[-1]
    summary = updated.config["experimental_evidence"]["candidate_summaries"]["Rasagiline"]
    assert evidence.source == "Imported experimental result"
    assert evidence.source_record_id == "result-1"
    assert evidence.metadata["result_id"] == "result-1"
    assert evidence.metadata["source_record_id"] == "row-1"
    assert summary["positive_count"] == 1
    assert updated.traces[-1].metadata["results_loaded"] == 1
    assert updated.traces[-1].metadata["results_linked"] == 1
    assert updated.traces[-1].metadata["positive_count"] == 1


def test_failed_qc_and_inconclusive_results_are_recorded_but_not_added_as_promoting_evidence(
    tmp_path,
):
    db_path = tmp_path / "experiments.sqlite"
    ExperimentalResultStore(db_path).import_results(
        [
            _assay_result(
                "result-failed",
                outcome_label="failed_qc",
                activity_direction="ambiguous",
                qc_status="failed",
                source_record_id="row-failed",
            ),
            _assay_result(
                "result-inconclusive",
                outcome_label="inconclusive",
                activity_direction="ambiguous",
                qc_status="passed",
                source_record_id="row-inconclusive",
            ),
        ]
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        config={
            "enable_experimental_evidence": True,
            "experimental_db_path": str(db_path),
            "include_inconclusive_results": True,
        },
    )

    updated = ExperimentalEvidenceAgent().run(context)

    summary = updated.config["experimental_evidence"]["candidate_summaries"]["Rasagiline"]
    assert updated.candidates[0].evidence == []
    assert summary["failed_qc_count"] == 1
    assert summary["inconclusive_count"] == 1
    assert "failed_qc results are recorded but not score-promoting" in summary["warnings"]


def test_generated_molecule_direct_evidence_requires_exact_linked_result(tmp_path):
    generated = _generated_hypothesis()
    db_path = tmp_path / "experiments.sqlite"
    ExperimentalResultStore(db_path).import_results(
        [
            _assay_result(
                "generated-result",
                candidate_id=None,
                candidate_name=generated.name,
                candidate_origin="generated",
                canonical_smiles=generated.canonical_smiles,
                inchi_key=None,
                source_record_id="gen-row-1",
            ),
            _assay_result(
                "seed-result",
                candidate_id="CHEMBL887",
                candidate_name="Rasagiline",
                candidate_origin="existing",
                canonical_smiles="C#CCN1CCC2=CC=CC=C21",
                source_record_id="seed-row-1",
            ),
        ]
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        generated_candidates=[generated],
        config={
            "enable_experimental_evidence": True,
            "experimental_db_path": str(db_path),
        },
    )

    updated = ExperimentalEvidenceAgent().run(context)

    generated_summary = updated.config["experimental_evidence"]["generated_summaries"][
        generated.name
    ]
    assert generated_summary["result_count"] == 1
    assert generated_summary["best_supporting_results"] == ["generated-result"]
    assert generated_summary["metadata"]["direct_evidence_result_ids"] == ["generated-result"]
    assert "seed-result" not in generated_summary["metadata"]["direct_evidence_result_ids"]


def test_unlinked_results_are_reported(tmp_path):
    db_path = tmp_path / "experiments.sqlite"
    ExperimentalResultStore(db_path).import_results(
        [
            _assay_result(
                "unlinked-result",
                candidate_id="CHEMBL-NOT-IN-CONTEXT",
                candidate_name="Unlinked molecule",
                canonical_smiles=None,
                inchi_key=None,
            )
        ]
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        config={
            "enable_experimental_evidence": True,
            "experimental_db_path": str(db_path),
        },
    )

    updated = ExperimentalEvidenceAgent().run(context)

    assert updated.config["experimental_evidence"]["unlinked_result_ids"] == ["unlinked-result"]
    assert updated.traces[-1].metadata["results_unlinked"] == 1
