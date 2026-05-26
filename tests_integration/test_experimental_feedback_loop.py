from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.evidence_scoring import EvidenceScoringAgent
from molecule_ranker.agents.experimental_evidence import ExperimentalEvidenceAgent
from molecule_ranker.agents.report_writer import ReportWriterAgent
from molecule_ranker.experiments.active_learning import suggest_next_experiments
from molecule_ranker.experiments.importers import import_assay_results_csv
from molecule_ranker.experiments.linking import LinkingConfig, link_assay_results
from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.experiments.store import ExperimentalResultStore
from molecule_ranker.review.experimental_results import (
    apply_experimental_results_to_review_workspace,
)
from molecule_ranker.review.queue_builder import build_review_workspace
from molecule_ranker.schemas import (
    Disease,
    EvidenceItem,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
    RankingRun,
    Target,
)

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

FORBIDDEN_OUTPUT_PATTERNS = [
    r"step-by-step protocol",
    r"exact lab procedure instructions",
    r"synthesis route",
    r"reagents list",
    r"reaction conditions",
    r"incubation time",
    r"temperature instructions",
    r"animal dosing",
    r"human dosing",
    r"patient treatment recommendation",
    r"proves efficacy",
    r"proves safety",
    r"\bcures\b",
    r"treats disease",
    r"\bprotocols?\b",
    r"\breagents?\b",
    r"\bmg/kg\b",
]


def test_experimental_feedback_loop_integration(tmp_path):
    csv_path = tmp_path / "mock_assay_results.csv"
    db_path = tmp_path / "experimental.sqlite"
    csv_path.write_text(_assay_csv())
    disease = Disease(
        input_name="Example condition",
        canonical_name="Example condition",
        synonyms=[],
        identifiers={"example": "EXAMPLE-CONDITION"},
        description=None,
    )
    target = _target()
    existing_positive = _candidate("ExistingPositive", "EX-POS", "CCO")
    existing_failed_qc = _candidate("ExistingFailedQC", "EX-FAIL", "CCN")
    generated_scored = MoleculeCandidate(
        name="GeneratedExact",
        molecule_type="small_molecule",
        origin="generated",
        chemical_metadata={"canonical_smiles": "CCC"},
        known_targets=["EXAMPLE"],
        generation_metadata={"generation_score": 0.5},
        score=0.5,
        warnings=["Generated hypothesis; no direct activity evidence."],
    )
    generated_exact = _generated("GeneratedExact", "CCC")
    generated_other = _generated("GeneratedOther", "CCCC")

    baseline_context = PipelineContext(
        disease_input=disease.input_name,
        disease=disease,
        targets=[target],
        candidates=[existing_positive, existing_failed_qc, generated_scored],
        generated_candidates=[generated_exact, generated_other],
        config={"top": 10, "results_dir": str(tmp_path)},
    )
    baseline_scored = EvidenceScoringAgent().run(baseline_context.model_copy(deep=True))
    baseline_scores = {
        candidate.name: candidate.score or 0.0 for candidate in baseline_scored.candidates
    }

    imported_results = import_assay_results_csv(csv_path, imported_by="integration-test")
    store = ExperimentalResultStore(db_path)
    store.import_results(imported_results, actor="integration-test")

    linked_results = link_assay_results(
        store.list_results(),
        candidates=[existing_positive, existing_failed_qc],
        generated_molecules=[generated_exact, generated_other],
        config=LinkingConfig(),
    )
    store.import_results(linked_results, actor="integration-test", update=True)

    experimental_context = baseline_context.model_copy(deep=True)
    experimental_context.config.update(
        {
            "enable_experimental_evidence": True,
            "experimental_db_path": str(db_path),
            "results_dir": str(tmp_path),
            "top": 10,
        }
    )
    experimental_context = ExperimentalEvidenceAgent().run(experimental_context)
    rescored_context = EvidenceScoringAgent().run(experimental_context)
    rescored_by_name = {candidate.name: candidate for candidate in rescored_context.candidates}

    batch = suggest_next_experiments(
        [candidate for candidate in rescored_context.candidates if candidate.origin != "generated"],
        [generated_exact, generated_other],
        [AssayResult.model_validate(result) for result in store.list_results()],
        [],
        {"strategy": "balanced", "top_k": 3, "endpoint_name": "Example endpoint"},
    )
    store.save_active_learning_batch(batch)
    rescored_context.config["active_learning_batch"] = batch

    ranking_run = RankingRun(
        disease=disease,
        targets=[target],
        candidates=[
            candidate
            for candidate in rescored_context.candidates
            if candidate.origin != "generated"
        ],
        generated_candidates=[generated_exact, generated_other],
        traces=rescored_context.traces,
    )
    workspace = build_review_workspace(ranking_run, config={"run_id": "integration-run"})
    updated_workspace = apply_experimental_results_to_review_workspace(
        workspace,
        store.list_results(),
        config={"actor": "integration-test"},
    )

    reported_context = rescored_context.model_copy(deep=True)
    reported_context.output_dir = None
    ReportWriterAgent().run(reported_context)
    output_dir = tmp_path / "example-condition"
    experimental_report = (output_dir / "experimental_report.md").read_text()
    experimental_results_payload = json.loads(
        (output_dir / "experimental_results.json").read_text()
    )
    active_learning_payload = json.loads((output_dir / "active_learning_batch.json").read_text())

    linked = store.list_results()
    generated_summary = rescored_context.config["experimental_evidence"]["generated_summaries"]
    generated_result = next(
        result for result in linked if result.source_record_id == "generated-exact-result"
    )

    assert len(imported_results) == 3
    assert len(linked) == 3
    assert all(result.metadata.get("linked_candidate_id") for result in linked)
    positive_score = rescored_by_name["ExistingPositive"].score
    failed_qc_score = rescored_by_name["ExistingFailedQC"].score
    failed_qc_breakdown = rescored_by_name["ExistingFailedQC"].score_breakdown
    assert positive_score is not None
    assert failed_qc_score is not None
    assert failed_qc_breakdown is not None
    assert positive_score > baseline_scores["ExistingPositive"]
    assert failed_qc_score == baseline_scores["ExistingFailedQC"]
    assert failed_qc_breakdown.experimental_evidence_score == 0
    assert generated_summary["GeneratedExact"]["metadata"]["direct_evidence_result_ids"] == [
        generated_result.result_id
    ]
    assert generated_result.source_record_id == "generated-exact-result"
    assert "GeneratedOther" not in generated_summary
    assert active_learning_payload["suggestions"]
    assert store.get_active_learning_batch(batch.batch_id).batch_id == batch.batch_id
    assert any(
        item.evidence_summary.get("experimental_results", {}).get("result_count", 0) > 0
        for item in updated_workspace.review_items
    )
    assert experimental_results_payload["summary"]["linked_results"] == 3
    _assert_no_forbidden_output(
        {
            "experimental_report": experimental_report,
            "experimental_results": experimental_results_payload,
            "active_learning": active_learning_payload,
            "review_workspace": updated_workspace.model_dump(mode="json"),
        }
    )


def _target() -> Target:
    return Target(
        symbol="EXAMPLE",
        name="Example target",
        identifiers={"example": "EXAMPLE-TARGET"},
        disease_relevance_score=0.8,
        evidence=[
            EvidenceItem(
                source="Example target source",
                source_record_id="target-record",
                title="Example target record",
                evidence_type="target_disease",
                summary="Example target rationale for integration testing.",
                confidence=0.8,
                retrieval_timestamp=FIXED_TIME,
            )
        ],
        mechanism="Example target mechanism.",
    )


def _candidate(name: str, candidate_id: str, smiles: str) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"id": candidate_id},
        known_targets=["EXAMPLE"],
        development_status="research",
        mechanism_of_action="Example target modulation.",
        chemical_metadata={"canonical_smiles": smiles},
        evidence=[
            EvidenceItem(
                source="Example molecule source",
                source_record_id=f"{candidate_id}-mechanism",
                title=f"{name} example evidence",
                evidence_type="mechanism",
                summary="Example molecule-target evidence for integration testing.",
                confidence=0.8,
                retrieval_timestamp=FIXED_TIME,
                metadata={"target_symbol": "EXAMPLE"},
            )
        ],
    )


def _generated(name: str, smiles: str) -> GeneratedMoleculeHypothesis:
    return GeneratedMoleculeHypothesis(
        name=name,
        canonical_smiles=smiles,
        target_symbol="EXAMPLE",
        generation_score=0.5,
        min_seed_similarity=0.2,
        max_seed_similarity=0.6,
        mean_seed_similarity=0.4,
        warnings=["Generated hypothesis; no direct activity evidence."],
    )


def _assay_csv() -> str:
    header = (
        "candidate_name,candidate_id,candidate_origin,canonical_smiles,disease_name,"
        "target_symbol,assay_name,assay_type,endpoint_name,endpoint_category,"
        "measured_value,unit,outcome_label,activity_direction,qc_status,source_record_id"
    )
    rows = [
        (
            "ExistingPositive,EX-POS,existing,CCO,Example condition,EXAMPLE,"
            "Example assay name,other,Example endpoint,other,12.3,example_unit,"
            "positive,active,passed,row-positive"
        ),
        (
            "ExistingFailedQC,EX-FAIL,existing,CCN,Example condition,EXAMPLE,"
            "Example assay name,other,Example endpoint,other,99.0,example_unit,"
            "failed_qc,ambiguous,failed,row-failed-qc"
        ),
        (
            "GeneratedExact,GeneratedExact,generated,CCC,Example condition,EXAMPLE,"
            "Example assay name,other,Example endpoint,other,8.1,example_unit,"
            "positive,active,passed,generated-exact-result"
        ),
    ]
    return "\n".join([header, *rows]) + "\n"


def _assert_no_forbidden_output(payload: object) -> None:
    serialized = json.dumps(payload, default=str).lower()
    offenders = [
        pattern
        for pattern in FORBIDDEN_OUTPUT_PATTERNS
        if re.search(pattern, serialized)
    ]
    assert offenders == []
