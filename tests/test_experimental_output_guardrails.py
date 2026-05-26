from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.experimental_evidence import ExperimentalEvidenceAgent
from molecule_ranker.agents.report_writer import ReportWriterAgent
from molecule_ranker.experiments.active_learning import suggest_next_experiments
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.experiments.store import ExperimentalResultStore
from molecule_ranker.review.dossier import DossierWriterAgent, render_dossier_json
from molecule_ranker.review.schemas import ReviewItem, ReviewWorkspace
from molecule_ranker.review.validation_handoff import build_validation_handoff
from molecule_ranker.schemas import (
    Disease,
    EvidenceItem,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
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


def _assert_guardrail_clean(payload: object) -> None:
    serialized = json.dumps(payload, default=str).lower()
    offenders = [
        pattern
        for pattern in FORBIDDEN_OUTPUT_PATTERNS
        if re.search(pattern, serialized)
    ]
    assert offenders == []


def _context() -> AssayContext:
    return AssayContext(
        assay_context_id="context-example",
        assay_name="Example assay name",
        assay_type="other",
        target_symbol="EXAMPLE",
        disease_name="Example condition",
        endpoint=AssayEndpoint(
            endpoint_id="endpoint-example",
            name="Example endpoint",
            endpoint_category="other",
            unit=None,
            directionality="neutral",
        ),
    )


def _result(
    result_id: str,
    *,
    candidate_id: str | None = "EXAMPLE-A",
    candidate_name: str = "ExampleCandidateA",
    candidate_origin: str = "existing",
    canonical_smiles: str | None = "CC",
    outcome_label: str = "positive",
    notes: str | None = None,
) -> AssayResult:
    return AssayResult(
        result_id=result_id,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_origin=candidate_origin,  # type: ignore[arg-type]
        canonical_smiles=canonical_smiles,
        disease_name="Example condition",
        target_symbol="EXAMPLE",
        assay_context=_context(),
        measured_value=12.3,
        measured_value_numeric=12.3,
        unit="example_unit",
        normalized_value=12.3,
        normalized_unit="example_unit",
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction="active",
        confidence=0.8,
        qc_status="passed",
        source="csv_import",
        source_record_id=f"row-{result_id}",
        imported_at=FIXED_TIME,
        notes=notes,
    )


def _candidate() -> MoleculeCandidate:
    return MoleculeCandidate(
        name="ExampleCandidateA",
        molecule_type="small_molecule",
        identifiers={"id": "EXAMPLE-A"},
        chemical_metadata={"canonical_smiles": "CC"},
        known_targets=["EXAMPLE"],
        score=0.62,
        evidence=[
            EvidenceItem(
                source="Example source",
                source_record_id="example-record",
                title="Example record",
                evidence_type="mechanism",
                summary="Cautious example evidence.",
                confidence=0.7,
                retrieval_timestamp=FIXED_TIME,
            )
        ],
    )


def _generated() -> GeneratedMoleculeHypothesis:
    return GeneratedMoleculeHypothesis(
        name="GeneratedExampleA",
        canonical_smiles="CCC",
        target_symbol="EXAMPLE",
        generation_score=0.55,
        min_seed_similarity=0.2,
        max_seed_similarity=0.5,
        mean_seed_similarity=0.35,
    )


def _workspace_with_malicious_experimental_summary() -> ReviewWorkspace:
    item = ReviewItem(
        run_id="run-guardrail-v06",
        disease_name="Example condition",
        candidate_id="EXAMPLE-A",
        candidate_name="ExampleCandidateA",
        candidate_origin="existing",
        target_symbols=["EXAMPLE"],
        canonical_smiles="CC",
        score=0.62,
        confidence=0.55,
        evidence_summary={
            "target_evidence_count": 1,
            "molecule_evidence_count": 1,
            "experimental_results": {
                "result_count": 1,
                "positive_count": 1,
                "negative_count": 0,
                "inconclusive_count": 0,
                "failed_qc_count": 0,
                "results": [
                    {
                        "result_id": "result-1",
                        "measured_value": 12.3,
                        "notes": "This proves efficacy and cures. It treats disease.",
                        "procedure": (
                            "step-by-step protocol with reagents list, reaction conditions, "
                            "incubation time, temperature instructions, animal dosing, "
                            "human dosing, and patient treatment recommendation."
                        ),
                    }
                ],
                "interpretation": "Exact imported result still does not prove efficacy.",
            },
        },
        literature_summary={},
        developability_summary={},
        risk_flags=[],
        warnings=[],
        priority_bucket="needs_review",
        review_status="pending",
    )
    return ReviewWorkspace(
        run_id="run-guardrail-v06",
        disease_name="Example condition",
        created_at=FIXED_TIME,
        review_items=[item],
    )


def test_active_learning_suggestions_do_not_contain_protocol_or_dosing_language():
    batch = suggest_next_experiments(
        [_candidate()],
        [_generated()],
        [],
        [],
        {"strategy": "balanced", "top_k": 2, "endpoint_name": "Example endpoint"},
    )

    _assert_guardrail_clean(batch.model_dump(mode="json"))
    assert batch.suggestions
    assert all(
        suggestion.metadata["suggested_assay_class"].startswith("high_level_")
        for suggestion in batch.suggestions
    )


def test_validation_handoff_and_dossier_strip_procedural_and_overclaiming_text():
    workspace = _workspace_with_malicious_experimental_summary()
    item = workspace.review_items[0]

    handoff = build_validation_handoff(workspace, item.review_item_id)
    dossier = DossierWriterAgent().build_dossier(workspace, item.review_item_id)

    _assert_guardrail_clean(handoff.model_dump(mode="json"))
    _assert_guardrail_clean(json.loads(render_dossier_json(dossier)))


def test_experimental_report_sanitizes_overclaims_but_keeps_result_metadata():
    context = PipelineContext(
        disease_input="Example condition",
        disease=Disease(
            input_name="Example condition",
            canonical_name="Example condition",
            synonyms=[],
            identifiers={},
            description=None,
        ),
        candidates=[_candidate()],
        generated_candidates=[_generated()],
        config={
            "experimental_evidence": {
                "results": [
                    {
                        "result_id": "result-1",
                        "candidate_name": "ExampleCandidateA",
                        "measured_value": 12.3,
                        "outcome_label": "positive",
                        "assay_context": {
                            "assay_name": "Example assay name",
                            "endpoint": {"name": "Example endpoint"},
                        },
                        "notes": "This proves safety and cures.",
                        "procedure": "exact lab procedure instructions and synthesis route",
                    }
                ],
                "linked_result_ids": ["result-1"],
                "candidate_summaries": {
                    "ExampleCandidateA": {
                        "result_count": 1,
                        "positive_count": 1,
                        "negative_count": 0,
                        "inconclusive_count": 0,
                        "failed_qc_count": 0,
                        "endpoint_summaries": {
                            "Example endpoint": {
                                "result_count": 1,
                                "outcome_counts": {"positive": 1},
                            }
                        },
                        "best_supporting_results": ["result-1"],
                        "key_negative_results": [],
                        "safety_concerns": [],
                        "confidence": 0.8,
                        "interpretation": "Imported result does not prove efficacy.",
                        "warnings": [],
                        "metadata": {"direct_evidence_result_ids": ["result-1"]},
                    }
                },
                "generated_summaries": {},
                "unlinked_result_ids": [],
            },
            "active_learning_batch": {
                "strategy": "balanced",
                "suggestions": [
                    {
                        "candidate_name": "ExampleCandidateA",
                        "candidate_origin": "existing",
                        "acquisition_score": 0.5,
                        "rationale": "No step-by-step protocol or human dosing.",
                        "metadata": {
                            "suggested_assay_class": "high_level_target_or_activity_assay",
                        },
                    }
                ],
            },
        },
    )

    writer = ReportWriterAgent()
    payload = writer._experimental_report_payload(context)
    report = writer._render_experimental_report(context)

    _assert_guardrail_clean(payload)
    _assert_guardrail_clean(report)
    assert payload["experimental_results"]["results"][0]["measured_value"] == 12.3
    assert "Example endpoint" in report


def test_generated_molecule_direct_evidence_requires_exact_imported_result(tmp_path):
    db_path = tmp_path / "experiments.sqlite"
    generated = _generated()
    store = ExperimentalResultStore(db_path)
    store.import_results(
        [
            _result(
                "seed-result",
                candidate_id="EXAMPLE-A",
                candidate_name="ExampleCandidateA",
                candidate_origin="existing",
                canonical_smiles="CC",
            )
        ],
        actor="test",
    )
    context = PipelineContext(
        disease_input="Example condition",
        candidates=[_candidate()],
        generated_candidates=[generated],
        config={"enable_experimental_evidence": True, "experimental_db_path": str(db_path)},
    )

    without_exact = ExperimentalEvidenceAgent().run(context)

    assert without_exact.config["experimental_evidence"]["generated_summaries"] == {}

    store.import_results(
        [
            _result(
                "generated-exact-result",
                candidate_id=None,
                candidate_name=generated.name,
                candidate_origin="generated",
                canonical_smiles=generated.canonical_smiles,
                notes="Exact imported result does not imply clinical efficacy.",
            )
        ],
        actor="test",
    )
    with_exact = ExperimentalEvidenceAgent().run(
        context.model_copy(update={"config": dict(context.config)})
    )
    generated_summary = with_exact.config["experimental_evidence"]["generated_summaries"][
        generated.name
    ]

    assert generated_summary["metadata"]["direct_evidence_result_ids"] == [
        "generated-exact-result"
    ]
    assert "clinical efficacy" not in generated_summary["interpretation"].lower()
    assert "seed-result" not in generated_summary["metadata"]["direct_evidence_result_ids"]
