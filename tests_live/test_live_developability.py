from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources.structure_adapters import AlphaFoldDBAdapter, RCSBPDBAdapter
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator
from molecule_ranker.schemas import Target
from molecule_ranker.utils import slugify

pytestmark = [
    pytest.mark.live,
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("MOLECULE_RANKER_RUN_LIVE") != "1",
        reason="Set MOLECULE_RANKER_RUN_LIVE=1 to run live network smoke tests.",
    ),
]


def test_live_existing_molecule_ranking_with_developability(tmp_path: Path) -> None:
    config = _live_config(tmp_path, enable_generation=False)

    result = MoleculeRankerOrchestrator(config=config).rank(
        "Parkinson disease",
        top_n=2,
        output_dir=tmp_path,
    )

    output_dir = tmp_path / slugify(result.disease.canonical_name)
    developability_payload = json.loads((output_dir / "developability.json").read_text())
    trace_payload = json.loads((output_dir / "trace.json").read_text())
    assessed_structured_candidates = [
        candidate
        for candidate in result.candidates
        if candidate.developability_assessment is not None
        and candidate.developability_assessment.structure_available
    ]

    assert developability_payload["enabled"] is True
    assert trace_payload["developability_run"]["enabled"] is True
    assert assessed_structured_candidates
    assert developability_payload["assessed_existing_count"] >= len(assessed_structured_candidates)
    assert any(assessment.get("physchem") for assessment in developability_payload["assessments"])
    _assert_no_actionable_synthesis_text(
        (output_dir / "report.md").read_text(),
        json.dumps(developability_payload),
    )


def test_live_generation_with_developability(tmp_path: Path) -> None:
    config = _live_config(tmp_path, enable_generation=True)

    result = MoleculeRankerOrchestrator(config=config).rank(
        "Parkinson disease",
        top_n=2,
        output_dir=tmp_path,
    )

    output_dir = tmp_path / slugify(result.disease.canonical_name)
    generated_payload = json.loads((output_dir / "generated_candidates.json").read_text())
    developability_payload = json.loads((output_dir / "developability.json").read_text())
    report = (output_dir / "report.md").read_text()
    retained = generated_payload.get("retained_generated_molecules", [])
    rejected = generated_payload.get("rejected_generated_molecules", [])
    generated_records = [
        *retained,
        *[
            item.get("generated_molecule", item)
            for item in rejected
            if isinstance(item, dict)
        ],
    ]

    for record in generated_records:
        assert record.get("developability") or record.get("developability_summary")

    for record in generated_records:
        summary = record.get("developability_summary") or {}
        if summary.get("risk_level") == "critical":
            rejected_ids = {
                (item.get("generated_molecule") or {}).get("generated_id")
                for item in rejected
                if isinstance(item, dict)
            }
            warnings = " ".join(record.get("warnings", []))
            assert record.get("generated_id") in rejected_ids or "developability" in warnings

    assert developability_payload["enabled"] is True
    assert "Developability scores are computational triage heuristics." in report
    assert "Generated molecules have no direct experimental evidence." in report
    _assert_no_actionable_synthesis_text(report)


def test_live_structure_retrieval_health_when_enabled() -> None:
    if os.getenv("MOLECULE_RANKER_LIVE_STRUCTURE_RETRIEVAL") != "1":
        pytest.skip(
            "Set MOLECULE_RANKER_LIVE_STRUCTURE_RETRIEVAL=1 to smoke-test structure APIs."
        )
    target = Target(
        symbol="TP53",
        name="tumor protein p53",
        identifiers={"uniprot": "P04637"},
        disease_relevance_score=0.5,
    )

    rcsb_records = RCSBPDBAdapter(timeout_seconds=15.0).retrieve_target_structures(
        target,
        limit=1,
    )
    alphafold_records = AlphaFoldDBAdapter(timeout_seconds=15.0).retrieve_target_structures(
        target,
        limit=1,
    )

    assert rcsb_records or alphafold_records
    for record in [*rcsb_records, *alphafold_records]:
        assert record.structure_id
        assert record.source in {"RCSB PDB", "AlphaFold DB"}
        assert record.target_symbol == "TP53"


def _live_config(tmp_path: Path, *, enable_generation: bool) -> RankerConfig:
    return RankerConfig(
        results_dir=tmp_path,
        cache_dir=tmp_path / ".cache",
        use_cache=True,
        allow_cached_real_data=False,
        request_timeout_seconds=20.0,
        max_retries=1,
        retry_backoff_seconds=0.25,
        enable_literature=False,
        default_target_limit=3,
        target_source_limit=10,
        max_molecules_per_target=2,
        max_activity_records_per_target=2,
        max_indications_per_molecule=2,
        max_warnings_per_molecule=2,
        enable_developability=True,
        strict_developability=False,
        assess_existing_molecules=True,
        assess_generated_molecules=True,
        developability_filter_mode="filter_generated_only",
        reject_critical_alerts=True,
        enable_generation=enable_generation,
        strict_generation=False,
        generation_random_seed=7,
        max_seed_molecules=2,
        max_generation_objectives=1,
        generated_per_objective=2,
        max_generated_before_filtering=8,
        max_retained_generated=2,
        max_generation_rounds=1,
        enable_structure_retrieval=False,
        enable_docking=False,
    )


def _assert_no_actionable_synthesis_text(*values: str) -> None:
    text = "\n".join(values).lower()
    forbidden = (
        " add ",
        " stir ",
        " reflux",
        " quench",
        " purify",
        " chromatograph",
        " heat to ",
        " cool to ",
    )
    assert not any(term in text for term in forbidden)
