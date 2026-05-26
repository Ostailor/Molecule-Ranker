from __future__ import annotations

import json

from molecule_ranker.experimental import (
    ActiveLearningAgent,
    ExperimentalEvidenceAgent,
    ExperimentalResultStore,
    import_assay_results,
    render_experiment_summary_markdown,
    validate_assay_results,
)
from molecule_ranker.review.schemas import ReviewItem
from molecule_ranker.schemas import MoleculeCandidate


def _candidate(
    name: str = "Rasagiline",
    score: float = 0.62,
    chembl_id: str = "CHEMBL887",
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": chembl_id},
        known_targets=["MAOB"],
        score=score,
    )


def _review_item() -> ReviewItem:
    return ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbols=["MAOB"],
        score=0.62,
        confidence=0.7,
        priority_bucket="high_priority",
        review_status="pending",
    )


def test_importer_normalizes_csv_and_marks_incomplete_rows(tmp_path):
    csv_path = tmp_path / "assay_results.csv"
    csv_path.write_text(
        "\n".join(
            [
                "experiment_id,assay_name,molecule_name,target_symbol,disease_name,outcome,value,unit",
                "exp-1,Binding screen,Rasagiline,MAOB,Parkinson disease,hit,0.82,relative_activity",
                (
                    "exp-1,Binding screen,Missing Outcome,MAOB,Parkinson disease,,"
                    "0.12,relative_activity"
                ),
            ]
        )
        + "\n"
    )

    imported = import_assay_results(csv_path)
    report = validate_assay_results(imported.results)

    assert imported.source_path == str(csv_path)
    assert imported.results[0].outcome == "positive"
    assert imported.results[0].validation_status == "valid"
    assert imported.results[0].provenance["source_type"] == "user_imported_file"
    assert imported.results[1].outcome is None
    assert imported.results[1].validation_status == "incomplete"
    assert "outcome is required" in imported.results[1].validation_issues
    assert report.valid_count == 1
    assert report.incomplete_count == 1
    assert report.invalid_count == 0


def test_store_links_results_and_preserves_experimental_separation(tmp_path):
    imported = import_assay_results(
        tmp_path / "results.json",
        payload=[
            {
                "experiment_id": "exp-2",
                "assay_name": "Functional readout",
                "candidate_id": "CHEMBL887",
                "molecule_name": "Rasagiline",
                "target_symbol": "MAOB",
                "disease_name": "Parkinson disease",
                "review_item_id": _review_item().review_item_id,
                "outcome": "negative",
                "value": 0.1,
                "unit": "relative_activity",
            }
        ],
    )
    result = ExperimentalEvidenceAgent().link_results(
        imported.results,
        candidates=[_candidate()],
        review_items=[_review_item()],
    )[0]
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results([result], actor="unit-test")

    loaded = store.list_results(candidate_id="CHEMBL887")
    summary = store.summarize()

    assert loaded[0].linked_candidate_name == "Rasagiline"
    assert loaded[0].linked_review_item_id == _review_item().review_item_id
    assert loaded[0].evidence_channel == "experimental"
    assert summary.outcome_counts["negative"] == 1
    assert summary.review_link_count == 1


def test_experimental_recalibration_uses_only_imported_valid_results():
    candidate = _candidate(score=0.62)
    results = import_assay_results(
        "inline.json",
        payload=[
            {
                "experiment_id": "exp-3",
                "assay_name": "Orthogonal assay",
                "candidate_id": "CHEMBL887",
                "molecule_name": "Rasagiline",
                "target_symbol": "MAOB",
                "outcome": "positive",
                "value": 0.9,
                "unit": "relative_activity",
            },
            {
                "experiment_id": "exp-3",
                "assay_name": "Incomplete assay",
                "candidate_id": "CHEMBL887",
                "molecule_name": "Rasagiline",
                "target_symbol": "MAOB",
                "value": 0.4,
                "unit": "relative_activity",
            },
        ],
    ).results

    report = ExperimentalEvidenceAgent().recalibrate_candidates([candidate], results)
    recalibrated = report.recalibrations[0]

    assert recalibrated.candidate_id == "CHEMBL887"
    assert recalibrated.original_score == 0.62
    assert recalibrated.recalibrated_score is not None
    assert recalibrated.recalibrated_score > 0.62
    assert recalibrated.outcome_counts == {"positive": 1}
    assert "Incomplete or invalid assay rows were excluded" in report.limitations


def test_active_learning_prioritizes_candidates_without_claiming_validation():
    candidates = [
        _candidate("Rasagiline", 0.7, "CHEMBL887"),
        _candidate("Safinamide", 0.66, "CHEMBL2103830"),
    ]
    results = import_assay_results(
        "inline.json",
        payload=[
            {
                "experiment_id": "exp-4",
                "assay_name": "Primary screen",
                "molecule_name": "Rasagiline",
                "candidate_id": "CHEMBL887",
                "target_symbol": "MAOB",
                "outcome": "positive",
                "value": 0.8,
                "unit": "relative_activity",
            }
        ],
    ).results

    recommendations = ActiveLearningAgent().recommend_next_candidates(
        candidates,
        results,
        top=2,
    )

    assert recommendations.recommendations[0].candidate_name == "Safinamide"
    assert "No lab protocol" in " ".join(recommendations.limitations)
    assert not any(
        "clinical efficacy" in item.rationale.lower()
        for item in recommendations.recommendations
    )


def test_experiment_summary_markdown_has_integrity_disclaimers(tmp_path):
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results(
        import_assay_results(
            "inline.json",
            payload=[
                {
                    "experiment_id": "exp-5",
                    "assay_name": "Counter screen",
                    "molecule_name": "Rasagiline",
                    "candidate_id": "CHEMBL887",
                    "target_symbol": "MAOB",
                    "outcome": "failed",
                }
            ],
        ).results
    )

    markdown = render_experiment_summary_markdown(store.summarize())
    payload = json.loads(store.summarize().model_dump_json())

    assert "does not establish clinical efficacy" in markdown
    assert "Expert review decisions are tracked separately" in markdown
    assert payload["outcome_counts"]["failed"] == 1
