from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.agents import BiologicsDiscoveryAgent
from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.biologics.generation import NullAntibodyGenerator
from molecule_ranker.schemas import Disease, EvidenceItem, Target


def test_biologics_discovery_disabled_noop(tmp_path: Path) -> None:
    context = PipelineContext(
        disease_input="Rheumatoid arthritis",
        disease=_disease(),
        targets=[_target("TNF")],
        output_dir=tmp_path,
        config={"enable_biologics": False},
    )

    result = BiologicsDiscoveryAgent().run(context)

    assert result.config["biologics"]["enabled"] is False
    assert result.config["biologics"]["candidates"] == []
    assert not (tmp_path / "biologic_candidates.json").exists()
    assert result.traces[-1].agent_name == "BiologicsDiscoveryAgent"
    assert result.traces[-1].metadata["enabled"] is False


def test_existing_biologic_candidate_is_ranked() -> None:
    context = _context(
        config={
            "enable_biologics": True,
            "chembl_biologic_records": [_chembl_record("CHEMBL-MAB-1", evidence=True)],
        }
    )

    result = BiologicsDiscoveryAgent().run(context)

    candidates = result.config["biologics"]["candidates"]
    assert len(candidates) == 1
    assert candidates[0].biologic_id == "bio-CHEMBL-MAB-1"
    assert candidates[0].metadata["biologics_score"] > 0.0
    assert result.traces[-1].metadata["ranked_candidate_count"] == 1


def test_generated_antibodies_disabled_by_default() -> None:
    context = _context(
        config={
            "enable_biologics": True,
            "chembl_biologic_records": [_chembl_record("CHEMBL-MAB-1", evidence=True)],
            "max_generated_antibodies": 2,
        }
    )

    result = BiologicsDiscoveryAgent().run(context)

    assert result.config["biologics"]["generated_antibodies"] == []
    assert result.traces[-1].metadata["generation_enabled"] is False


def test_generation_enabled_with_null_generator_returns_no_hypotheses() -> None:
    context = _context(
        config={
            "enable_biologics": True,
            "enable_antibody_generation": True,
            "antibody_generation_method": "null",
            "max_generated_antibodies": 2,
            "chembl_biologic_records": [_chembl_record("CHEMBL-MAB-1", evidence=True)],
        }
    )

    result = BiologicsDiscoveryAgent(generator=NullAntibodyGenerator()).run(context)

    assert result.config["biologics"]["generated_antibodies"] == []
    assert result.traces[-1].metadata["generation_enabled"] is True
    assert result.traces[-1].metadata["generated_count"] == 0


def test_generation_enabled_with_conservative_generator(tmp_path: Path) -> None:
    context = _context(
        output_dir=tmp_path,
        config={
            "enable_biologics": True,
            "enable_antibody_generation": True,
            "antibody_generation_method": "conservative_cdr_mutator",
            "max_generated_antibodies": 2,
            "reject_generated_sequence_liabilities": False,
            "antibody_generation_random_seed": 11,
            "chembl_biologic_records": [_chembl_record("CHEMBL-MAB-1", evidence=True)],
            "biologics_sequence_metadata": {
                "CHEMBL-MAB-1": {
                    "source_backed": True,
                    "cdr_regions": {"cdr1": (27, 38), "cdr2": (56, 65), "cdr3": (105, 112)},
                }
            },
        },
    )

    result = BiologicsDiscoveryAgent().run(context)

    generated = result.config["biologics"]["generated_antibodies"]
    assert len(generated) == 1
    assert generated[0].direct_experimental_evidence is False
    assert generated[0].score is not None
    assert generated[0].metadata["binding_activity_claim"] is False
    assert result.traces[-1].metadata["generated_count"] == 1


def test_biologics_discovery_writes_artifacts(tmp_path: Path) -> None:
    context = _context(
        output_dir=tmp_path,
        config={
            "enable_biologics": True,
            "chembl_biologic_records": [_chembl_record("CHEMBL-MAB-1", evidence=True)],
        },
    )

    result = BiologicsDiscoveryAgent().run(context)
    artifacts = result.config["biologics"]["artifacts"]

    for filename in [
        "biologic_candidates.json",
        "antibody_sequences.json",
        "antibody_numbering.json",
        "antibody_developability.json",
        "antibody_novelty.json",
        "generated_antibodies.json",
        "biologics_report.md",
    ]:
        assert (tmp_path / filename).exists()
    candidates_payload = json.loads((tmp_path / "biologic_candidates.json").read_text())
    generated_payload = json.loads((tmp_path / "generated_antibodies.json").read_text())
    report = (tmp_path / "biologics_report.md").read_text()

    assert artifacts["biologic_candidates"].endswith("biologic_candidates.json")
    assert candidates_payload["ranked_biologic_ids"] == ["bio-CHEMBL-MAB-1"]
    assert generated_payload["generated_antibody_hypotheses"] == []
    assert "Generated antibodies are computational hypotheses only." in report


def _context(
    *,
    config: dict[str, object],
    output_dir: Path | None = None,
) -> PipelineContext:
    return PipelineContext(
        disease_input="Rheumatoid arthritis",
        disease=_disease(),
        targets=[_target("TNF")],
        output_dir=output_dir,
        config=config,
    )


def _disease() -> Disease:
    return Disease(
        input_name="Rheumatoid arthritis",
        canonical_name="Rheumatoid arthritis",
        identifiers={"mondo": "MONDO:0008383"},
    )


def _target(symbol: str) -> Target:
    return Target(
        symbol=symbol,
        name=f"{symbol} antigen",
        identifiers={"uniprot": "P01375"},
        disease_relevance_score=0.82,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id=f"ot-{symbol}",
                title=f"{symbol} disease association",
                evidence_type="target_disease_association",
                summary="Mocked disease target association.",
                confidence=0.82,
            )
        ],
    )


def _chembl_record(record_id: str, *, evidence: bool) -> dict[str, object]:
    return {
        "molecule_chembl_id": record_id,
        "pref_name": "Source Backed Mab",
        "molecule_type": "monoclonal antibody",
        "target_symbols": ["TNF"],
        "antigen_names": ["TNF antigen"],
        "disease_name": "Rheumatoid arthritis",
        "amino_acid_sequence": "ACDEFGHIKLMNPQRSTVWY" * 6,
        "evidence_item_ids": ["ev-chembl-1"] if evidence else [],
        "direct_experimental_evidence": evidence,
    }
