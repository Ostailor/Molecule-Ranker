from __future__ import annotations

import json

import pytest

from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
    TargetDiscoveryError,
)
from molecule_ranker.generation.chemistry import canonicalize_smiles
from molecule_ranker.literature.errors import LiteratureParsingError, LiteratureRetrievalError
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator

pytestmark = [pytest.mark.live, pytest.mark.network]


def test_live_tiny_generation_job_structural_properties(tmp_path) -> None:
    config = RankerConfig(
        results_dir=tmp_path,
        use_cache=False,
        allow_cached_real_data=False,
        request_timeout_seconds=15,
        literature_request_timeout_seconds=10,
        max_retries=1,
        literature_max_retries=1,
        retry_backoff_seconds=0.25,
        default_target_limit=3,
        target_source_limit=5,
        max_molecules_per_target=2,
        max_activity_records_per_target=2,
        max_indications_per_molecule=1,
        max_warnings_per_molecule=1,
        enable_literature=True,
        strict_literature=False,
        enable_openalex_enrichment=False,
        max_literature_queries=2,
        max_papers_per_query=1,
        max_targets_for_literature=1,
        max_candidates_for_literature=1,
        enable_generation=True,
        strict_generation=False,
        include_generated_in_main_ranking=False,
        generation_random_seed=17,
        max_seed_molecules=3,
        max_generation_objectives=2,
        generated_per_objective=3,
        max_generated_before_filtering=60,
        max_retained_generated=3,
        max_generation_rounds=1,
        max_mutations_per_child=2,
    )
    orchestrator = MoleculeRankerOrchestrator(config=config)

    try:
        result = orchestrator.rank("Parkinson disease", top_n=3, output_dir=tmp_path)
    except (
        DiseaseResolutionError,
        ExternalDataUnavailableError,
        TargetDiscoveryError,
        MoleculeRetrievalError,
        LiteratureParsingError,
        LiteratureRetrievalError,
        NoCandidatesFoundError,
    ) as exc:
        pytest.skip(f"Public data source unavailable for live generation smoke: {exc}")

    generation_trace = next(
        trace for trace in result.traces if trace.agent_name == "NovelMoleculeAgent"
    )
    assert generation_trace.metadata["generation_enabled"] is True
    assert "generation_run" in generation_trace.metadata

    output_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert output_dirs
    report_path = output_dirs[0] / "report.md"
    generated_path = output_dirs[0] / "generated_candidates.json"
    assert report_path.exists()
    assert generated_path.exists()

    generated_payload = json.loads(generated_path.read_text())
    assert generated_payload["generation_enabled"] is True
    assert "objectives" in generated_payload
    assert "seeds" in generated_payload

    retained = generated_payload["retained_generated_molecules"]
    for molecule in retained:
        canonical_smiles = molecule["canonical_smiles"]
        assert canonicalize_smiles(canonical_smiles) is not None
        assert molecule["origin"] == "generated"
        assert "evidence" not in molecule

    for hypothesis in result.generated_candidates:
        assert canonicalize_smiles(hypothesis.canonical_smiles) is not None
        assert hypothesis.evidence == []
        assert hypothesis.trace.get("origin") == "generated"

    assert all(candidate.origin == "existing" for candidate in result.candidates)

    report = report_path.read_text()
    assert "## Generated Molecule Hypotheses" in report
    assert "Generated molecules are computational structures." in report
    assert "Generated molecules have no direct experimental evidence." in report
    assert "generation-prioritization scores, not efficacy predictions" in report
