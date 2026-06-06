from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.biologics import (
    AntibodyDesignObjective,
    AntibodyDevelopabilityAssessment,
    AntibodyNoveltyAssessment,
    AntibodyNumbering,
    AntibodySequence,
    AntigenContext,
    BiologicCandidate,
    CDRAnnotation,
    ConservativeCDRMutator,
    ExternalAntibodyGeneratorPlugin,
    GeneratedAntibodyHypothesis,
    NullAntibodyGenerator,
    annotate_antibody_numbering,
    antigen_generation_guardrails,
    assess_antibody_developability,
    assess_antibody_novelty,
    build_antibody_design_objective,
    build_antigen_contexts,
    build_biologic_report_card,
    build_biologics_dashboard_summary,
    build_generation_hypothesis,
    configure_numbering_adapter,
    is_antibody_like,
    number_antibody_sequence,
    rank_biologic_candidates,
    rank_generated_antibody_hypotheses,
    rank_retrieved_biologics,
    retrieve_existing_biologics,
    score_biologic_candidate,
    score_biologic_candidate_components,
    score_generated_antibody_hypothesis,
    validate_antibody_sequence,
    validate_antibody_sequences,
    validate_cdr_regions,
)
from molecule_ranker.biologics.numbering import annotate_cdrs

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def _sequence(sequence_id: str = "seq-1", sequence: str | None = None) -> AntibodySequence:
    amino_acid_sequence = sequence or "ACDEFGHIKLMNPQRSTVWY" * 5
    return AntibodySequence(
        sequence_id=sequence_id,
        biologic_id="bio-1",
        chain_type="heavy",
        amino_acid_sequence=amino_acid_sequence,
        sequence_length=len(amino_acid_sequence),
        species_origin="human",
        is_generated=False,
        parent_sequence_ids=[],
        source="public_database",
        source_record_id="PUB-1",
        created_at=NOW,
        metadata={},
    )


def test_biologic_candidate_schema_accepts_allowed_values() -> None:
    candidate = BiologicCandidate(
        biologic_id="bio-1",
        name="Existing antibody",
        biologic_type="monoclonal_antibody",
        origin="existing",
        target_symbols=["TNF"],
        antigen_names=["TNF antigen"],
        disease_name="Example disease",
        identifiers={"drugbank": "DB-1"},
        sequence_ids=["seq-1"],
        structure_ids=["struct-1"],
        evidence_item_ids=["ev-1"],
        direct_experimental_evidence=True,
        warnings=[],
        metadata={},
    )

    assert candidate.biologic_id == "bio-1"
    assert is_antibody_like(candidate) is True
    assert score_biologic_candidate(candidate) <= 1.0


def test_biologic_candidate_rejects_invalid_type_and_generated_direct_evidence() -> None:
    with pytest.raises(ValidationError):
        BiologicCandidate.model_validate(
            {
                "biologic_id": "bio-1",
                "name": "Bad type",
                "biologic_type": "small_molecule",
                "origin": "existing",
                "target_symbols": [],
                "antigen_names": [],
                "identifiers": {},
                "sequence_ids": [],
                "structure_ids": [],
                "evidence_item_ids": [],
                "direct_experimental_evidence": False,
                "warnings": [],
                "metadata": {},
            }
        )

    with pytest.raises(ValidationError, match="generated biologic"):
        BiologicCandidate(
            biologic_id="bio-2",
            name="Generated antibody",
            biologic_type="monoclonal_antibody",
            origin="generated",
            target_symbols=[],
            antigen_names=[],
            identifiers={},
            sequence_ids=[],
            structure_ids=[],
            evidence_item_ids=[],
            direct_experimental_evidence=True,
            warnings=[],
            metadata={},
        )


def test_antibody_sequence_schema_normalizes_and_checks_length() -> None:
    sequence = AntibodySequence(
        sequence_id="seq-1",
        biologic_id="bio-1",
        chain_type="light_kappa",
        amino_acid_sequence=" acd ef ",
        sequence_length=5,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )

    assert sequence.amino_acid_sequence == "ACDEF"

    with pytest.raises(ValidationError, match="sequence_length"):
        AntibodySequence(
            sequence_id="seq-bad",
            biologic_id=None,
            chain_type="heavy",
            amino_acid_sequence="ACDEF",
            sequence_length=4,
            species_origin=None,
            is_generated=False,
            parent_sequence_ids=[],
            source="public_database",
            source_record_id=None,
            created_at=NOW,
            metadata={},
        )


def test_antibody_numbering_and_cdr_annotation_bounds_confidence() -> None:
    numbering = AntibodyNumbering(
        numbering_id="num-1",
        sequence_id="seq-1",
        scheme="imgt",
        framework_regions={"fr1": (1, 26)},
        cdr_regions={"cdr1": (27, 38)},
        insertions={},
        numbering_tool="anarci",
        confidence=0.8,
        warnings=[],
        metadata={},
    )
    annotation = CDRAnnotation(
        annotation_id="cdr-1",
        sequence_id="seq-1",
        scheme="imgt",
        cdr1="AAAA",
        cdr2=None,
        cdr3="CCCC",
        cdr_lengths={"cdr1": 4, "cdr3": 4},
        unusual_motifs=[],
        warnings=[],
        metadata={},
    )

    assert numbering.confidence == 0.8
    assert annotation.cdr_lengths["cdr3"] == 4

    with pytest.raises(ValidationError):
        AntibodyNumbering(
            numbering_id="num-bad",
            sequence_id="seq-1",
            scheme="imgt",
            framework_regions={},
            cdr_regions={},
            insertions={},
            numbering_tool="anarci",
            confidence=1.1,
            warnings=[],
            metadata={},
        )


def test_antigen_context_requires_epitope_source_and_bounds_confidence() -> None:
    context = AntigenContext(
        antigen_context_id="ag-1",
        target_symbol="TNF",
        antigen_name="TNF antigen",
        antigen_identifiers={"uniprot": "P01375"},
        epitope_description=None,
        epitope_source=None,
        structure_context_ids=[],
        evidence_item_ids=["ev-1"],
        confidence=0.7,
        warnings=[],
        metadata={},
    )

    assert context.confidence == 0.7

    with pytest.raises(ValidationError, match="epitope_source"):
        AntigenContext(
            antigen_context_id="ag-2",
            target_symbol="TNF",
            antigen_name="TNF antigen",
            antigen_identifiers={},
            epitope_description="Reported epitope",
            epitope_source=None,
            structure_context_ids=[],
            evidence_item_ids=[],
            confidence=0.5,
            warnings=[],
            metadata={},
        )


def test_developability_and_novelty_schema_bounds_scores_and_identities() -> None:
    developability = AntibodyDevelopabilityAssessment(
        assessment_id="dev-1",
        biologic_id="bio-1",
        sequence_ids=["seq-1"],
        aggregation_risk="low",
        polyreactivity_risk="unknown",
        immunogenicity_risk="medium",
        viscosity_risk="unknown",
        stability_risk="low",
        expression_risk="unknown",
        sequence_liability_flags=[],
        cdr_liability_flags=[],
        overall_developability_score=0.8,
        confidence=0.6,
        warnings=[],
        metadata={},
    )
    novelty = AntibodyNoveltyAssessment(
        novelty_id="nov-1",
        biologic_id="bio-1",
        sequence_ids=["seq-1"],
        exact_sequence_match=False,
        nearest_sequence_identity=0.72,
        nearest_known_record="known-1",
        cdr3_exact_match=False,
        cdr3_nearest_identity=0.4,
        novelty_class="close_variant",
        sources_checked=["fixture"],
        warnings=[],
        metadata={},
    )

    assert developability.overall_developability_score == 0.8
    assert novelty.nearest_sequence_identity == 0.72

    with pytest.raises(ValidationError):
        AntibodyNoveltyAssessment(
            novelty_id="nov-bad",
            biologic_id="bio-1",
            sequence_ids=["seq-1"],
            exact_sequence_match=False,
            nearest_sequence_identity=1.2,
            nearest_known_record=None,
            cdr3_exact_match=None,
            cdr3_nearest_identity=None,
            novelty_class="unknown",
            sources_checked=[],
            warnings=[],
            metadata={},
        )


def test_generated_antibody_hypothesis_defaults_to_no_direct_evidence() -> None:
    hypothesis = GeneratedAntibodyHypothesis(
        generated_antibody_id="gab-1",
        biologic_id="bio-1",
        design_objective_id="obj-1",
        generated_sequence_ids=["seq-generated-1"],
        parent_sequence_ids=["seq-parent-1"],
        generation_method="approved_plugin",
        antigen_context_id="ag-1",
        target_symbols=["TNF"],
        score=0.4,
        confidence=0.5,
        warnings=[],
        metadata={},
    )

    assert hypothesis.direct_experimental_evidence is False
    assert "computational hypotheses only" in hypothesis.no_direct_evidence_warning

    with pytest.raises(ValidationError, match="direct experimental evidence"):
        GeneratedAntibodyHypothesis(
            generated_antibody_id="gab-2",
            biologic_id="bio-1",
            design_objective_id="obj-1",
            generated_sequence_ids=["seq-generated-1"],
            parent_sequence_ids=[],
            generation_method="approved_plugin",
            antigen_context_id=None,
            target_symbols=[],
            score=0.5,
            confidence=0.5,
            direct_experimental_evidence=True,
            warnings=[],
            metadata={},
        )


def test_biologics_helper_modules_return_schema_backed_outputs() -> None:
    sequence = _sequence()
    numbering, cdr = annotate_antibody_numbering(sequence)
    validation = validate_antibody_sequence(sequence)
    developability = assess_antibody_developability(
        assessment_id="dev-1",
        biologic_id="bio-1",
        sequences=[sequence],
    )
    novelty = assess_antibody_novelty(
        novelty_id="nov-1",
        biologic_id="bio-1",
        sequences=[sequence],
        known_sequences={"known-1": sequence.amino_acid_sequence},
        sources_checked=["fixture"],
    )
    hypothesis = build_generation_hypothesis(
        generated_antibody_id="gab-1",
        biologic_id="bio-1",
        design_objective_id="obj-1",
        generation_method="approved_plugin",
        generated_sequence_ids=["seq-generated-1"],
        confidence=0.5,
    )
    candidate = BiologicCandidate(
        biologic_id="bio-1",
        name="Existing antibody",
        biologic_type="monoclonal_antibody",
        origin="existing",
        target_symbols=["TNF"],
        antigen_names=["TNF antigen"],
        identifiers={},
        sequence_ids=[sequence.sequence_id],
        structure_ids=[],
        evidence_item_ids=["ev-1"],
        direct_experimental_evidence=True,
        warnings=[],
        metadata={},
    )
    report = build_biologic_report_card(
        candidate=candidate,
        developability=developability,
        novelty=novelty,
        hypothesis=hypothesis,
    )
    dashboard = build_biologics_dashboard_summary([candidate])

    assert numbering.scheme == "imgt"
    assert cdr.sequence_id == sequence.sequence_id
    assert validation["valid"] is True
    assert novelty.exact_sequence_match is True
    assert hypothesis.direct_experimental_evidence is False
    assert report["biologic_id"] == "bio-1"
    assert dashboard["candidate_count"] == 1
    assert rank_retrieved_biologics([candidate]) == [candidate]


def test_existing_antibody_with_evidence_is_ranked_above_weaker_candidate() -> None:
    supported = BiologicCandidate(
        biologic_id="bio-supported",
        name="Supported antibody",
        biologic_type="monoclonal_antibody",
        origin="existing",
        target_symbols=["TNF"],
        antigen_names=["TNF antigen"],
        identifiers={"chembl": "CHEMBL-MAB-1"},
        sequence_ids=["seq-supported"],
        structure_ids=[],
        evidence_item_ids=["ev-1", "ev-2"],
        direct_experimental_evidence=True,
        warnings=[],
        metadata={
            "sequence_validation": {"valid": True, "warnings": []},
            "novelty": {"novelty_class": "known"},
            "developability": {"overall_developability_score": 0.72},
        },
    )
    weak = BiologicCandidate(
        biologic_id="bio-weak",
        name="Weak antibody",
        biologic_type="monoclonal_antibody",
        origin="existing",
        target_symbols=["TNF"],
        antigen_names=[],
        identifiers={},
        sequence_ids=[],
        structure_ids=[],
        evidence_item_ids=[],
        direct_experimental_evidence=False,
        warnings=["Sequence unavailable from source."],
        metadata={},
    )

    ranked = rank_biologic_candidates([weak, supported])

    assert ranked[0] == supported
    assert score_biologic_candidate(supported) > score_biologic_candidate(weak)


def test_generated_antibody_scoring_keeps_direct_evidence_false() -> None:
    hypothesis = GeneratedAntibodyHypothesis(
        generated_antibody_id="gab-scored",
        biologic_id="bio-generated",
        design_objective_id="obj-1",
        generated_sequence_ids=["seq-generated"],
        parent_sequence_ids=["seq-parent"],
        generation_method="conservative_cdr_mutator",
        antigen_context_id="ag-1",
        target_symbols=["TNF"],
        score=0.9,
        confidence=0.9,
        warnings=[],
        metadata={
            "sequence_validation": {"valid": True, "warnings": []},
            "novelty": {"novelty_class": "novel_candidate"},
            "developability": {"overall_developability_score": 0.62},
        },
    )

    components = score_generated_antibody_hypothesis(hypothesis)

    assert hypothesis.direct_experimental_evidence is False
    assert components["experimental_support_score"] == 0.0
    assert components["effective_confidence"] <= 0.45


def test_antibody_liability_lowers_scoring() -> None:
    clean = BiologicCandidate(
        biologic_id="bio-clean",
        name="Cleaner candidate",
        biologic_type="monoclonal_antibody",
        origin="existing",
        target_symbols=["IL6"],
        antigen_names=["IL6"],
        identifiers={},
        sequence_ids=["seq-clean"],
        structure_ids=[],
        evidence_item_ids=["ev-clean"],
        direct_experimental_evidence=True,
        warnings=[],
        metadata={"sequence_validation": {"valid": True, "warnings": []}},
    )
    liability = clean.model_copy(
        update={
            "biologic_id": "bio-liability",
            "name": "Liability candidate",
            "warnings": ["Review sequence liabilities before prioritization."],
        }
    )
    clean_developability = AntibodyDevelopabilityAssessment(
        assessment_id="dev-clean",
        biologic_id="bio-clean",
        sequence_ids=["seq-clean"],
        aggregation_risk="low",
        polyreactivity_risk="unknown",
        immunogenicity_risk="unknown",
        viscosity_risk="unknown",
        stability_risk="low",
        expression_risk="unknown",
        sequence_liability_flags=[],
        cdr_liability_flags=[],
        overall_developability_score=0.8,
        confidence=0.45,
        warnings=[],
        metadata={},
    )
    liability_developability = AntibodyDevelopabilityAssessment(
        assessment_id="dev-liability",
        biologic_id="bio-liability",
        sequence_ids=["seq-liability"],
        aggregation_risk="high",
        polyreactivity_risk="medium",
        immunogenicity_risk="unknown",
        viscosity_risk="high",
        stability_risk="medium",
        expression_risk="unknown",
        sequence_liability_flags=["glycosylation motif", "oxidation motif"],
        cdr_liability_flags=["unusual cdr3 length"],
        overall_developability_score=0.25,
        confidence=0.4,
        warnings=[],
        metadata={},
    )

    clean_score = score_biologic_candidate_components(
        clean,
        developability=clean_developability,
    )
    liability_score = score_biologic_candidate_components(
        liability,
        developability=liability_developability,
    )

    assert liability_score["total_score"] < clean_score["total_score"]
    assert liability_score["risk_penalty"] > clean_score["risk_penalty"]


def test_seed_evidence_is_not_direct_generated_evidence() -> None:
    hypothesis = GeneratedAntibodyHypothesis(
        generated_antibody_id="gab-seed-context",
        biologic_id="bio-generated-seed-context",
        design_objective_id="obj-1",
        generated_sequence_ids=["seq-generated-seed-context"],
        parent_sequence_ids=["seq-parent"],
        generation_method="conservative_cdr_mutator",
        antigen_context_id=None,
        target_symbols=["EGFR"],
        score=None,
        confidence=0.7,
        warnings=[],
        metadata={
            "parent_evidence_item_ids": ["ev-parent-1"],
            "seed_evidence_item_ids": ["ev-seed-1"],
            "sequence_validation": {"valid": True, "warnings": []},
            "novelty": {"novelty_class": "close_variant"},
            "developability": {"overall_developability_score": 0.55},
        },
    )

    ranked = rank_generated_antibody_hypotheses([hypothesis])
    components = score_generated_antibody_hypothesis(hypothesis)

    assert ranked == [hypothesis]
    assert hypothesis.direct_experimental_evidence is False
    assert components["evidence_score"] > 0.0
    assert components["experimental_support_score"] == 0.0


def test_retrieves_mocked_chembl_biologic_candidate(tmp_path) -> None:
    result = retrieve_existing_biologics(
        target_symbols=["TNF"],
        disease_name="Rheumatoid arthritis",
        chembl_records=[
            {
                "molecule_chembl_id": "CHEMBL-MAB-1",
                "pref_name": "Source Backed Mab",
                "molecule_type": "monoclonal antibody",
                "target_symbols": ["TNF"],
                "antigen_names": ["TNF antigen"],
                "disease_name": "Rheumatoid arthritis",
                "amino_acid_sequence": "ACDEFGHIKLMNPQRSTVWY" * 6,
                "evidence_item_ids": ["ev-chembl-1"],
                "direct_experimental_evidence": True,
            }
        ],
        output_dir=tmp_path,
    )

    candidate = result.candidates[0]
    assert candidate.biologic_id == "bio-CHEMBL-MAB-1"
    assert candidate.biologic_type == "monoclonal_antibody"
    assert candidate.identifiers["chembl"] == "CHEMBL-MAB-1"
    assert candidate.evidence_item_ids == ["ev-chembl-1"]
    assert len(candidate.sequence_ids) == 1
    assert result.sequences[0].source == "public_database"
    assert (tmp_path / "biologic_candidates.json").exists()
    assert (tmp_path / "biologic_evidence.json").exists()


def test_builds_source_backed_antigen_context_from_literature_claim() -> None:
    contexts = build_antigen_contexts(
        literature_claims=[
            {
                "target_symbol": "TNF",
                "antigen_name": "Tumor necrosis factor",
                "epitope_description": "Source-reported extracellular antigen region",
                "pmid": "12345",
                "evidence_item_ids": ["ev-lit-1"],
                "confidence": 0.8,
            }
        ]
    )

    context = contexts[0]
    guardrails = antigen_generation_guardrails(context)
    enabled_guardrails = antigen_generation_guardrails(
        context,
        enable_epitope_specific_design=True,
    )

    assert context.epitope_description == "Source-reported extracellular antigen region"
    assert context.epitope_source == "literature_claim:12345"
    assert context.metadata["epitope_status"] == "source_backed"
    assert guardrails["generation_context_mode"] == "epitope_context"
    assert guardrails["epitope_specific_design_allowed"] is False
    assert enabled_guardrails["epitope_specific_design_allowed"] is True


def test_antigen_context_unknown_epitope_uses_broad_target_mode() -> None:
    contexts = build_antigen_contexts(
        target_records=[
            {
                "target_symbol": "IL6",
                "antigen_name": "Interleukin-6",
                "uniprot_id": "P05231",
            }
        ]
    )

    context = contexts[0]
    guardrails = antigen_generation_guardrails(context)

    assert context.epitope_description is None
    assert context.epitope_source is None
    assert context.metadata["epitope_status"] == "unknown"
    assert guardrails["generation_context_mode"] == "broad_target_context"
    assert guardrails["broad_target_context_allowed"] is True
    assert guardrails["epitope_context_available"] is False


def test_antigen_context_does_not_accept_unsourced_epitope_description() -> None:
    contexts = build_antigen_contexts(
        user_supplied_antigen_annotations=[
            {
                "target_symbol": "EGFR",
                "antigen_name": "EGFR",
                "epitope_description": "User text without source record",
            }
        ]
    )

    context = contexts[0]

    assert context.epitope_description is None
    assert context.epitope_source is None
    assert any("ignored" in warning.lower() for warning in context.warnings)


def test_antigen_context_merges_structure_registry_and_user_annotations() -> None:
    contexts = build_antigen_contexts(
        structure_records=[
            {
                "target_symbol": "BCMA",
                "structure_id": "PDB-1",
                "structure_context_ids": ["struct-bcma-1"],
            }
        ],
        external_registry_metadata=[
            {
                "target_symbol": "BCMA",
                "antigen_identifiers": {"registry": "REG-BCMA"},
                "evidence_item_ids": ["ev-reg-bcma"],
            }
        ],
        user_supplied_antigen_annotations=[
            {
                "target_symbol": "BCMA",
                "antigen_name": "B-cell maturation antigen",
                "epitope_description": "Imported domain-level antigen annotation",
                "epitope_source": "user-file:bcma-antigen.json",
            }
        ],
    )

    context = contexts[0]

    assert context.antigen_name == "B-cell maturation antigen"
    assert context.antigen_identifiers["registry"] == "REG-BCMA"
    assert context.structure_context_ids == ["struct-bcma-1"]
    assert context.evidence_item_ids == ["ev-reg-bcma"]
    assert context.epitope_source == "user_supplied_annotation:user-file:bcma-antigen.json"


def test_retrieves_mocked_external_registry_biologic_candidate() -> None:
    result = retrieve_existing_biologics(
        target_symbols=["CD3"],
        external_registry_records=[
            {
                "registry_id": "REG-BISPECIFIC-1",
                "name": "Registry bispecific",
                "modality": "bispecific antibody",
                "target_symbols": ["CD3", "BCMA"],
                "heavy_chain": "ACDEFGHIKLMNPQRSTVWY" * 6,
                "light_chain": "YWVTSRQPNMLKIHGFEDCA" * 5,
                "evidence": [
                    {
                        "evidence_item_id": "ev-registry-1",
                        "source_record_id": "registry-evidence-1",
                    }
                ],
            }
        ],
    )

    candidate = result.candidates[0]
    assert candidate.origin == "external"
    assert candidate.biologic_type == "bispecific_antibody"
    assert candidate.identifiers["registry"] == "REG-BISPECIFIC-1"
    assert len(candidate.sequence_ids) == 2
    assert {sequence.chain_type for sequence in result.sequences} == {
        "heavy",
        "light_kappa",
    }
    assert result.evidence_items[0]["source_record_id"] == "registry-evidence-1"


def test_missing_sequence_is_handled_without_fake_sequence(tmp_path) -> None:
    result = retrieve_existing_biologics(
        target_symbols=["IL6"],
        chembl_records=[
            {
                "molecule_chembl_id": "CHEMBL-NOSEQ-1",
                "pref_name": "Sequence unavailable mab",
                "molecule_type": "antibody",
                "target_symbols": ["IL6"],
                "evidence_item_ids": ["ev-no-seq-1"],
                "direct_experimental_evidence": True,
            }
        ],
        output_dir=tmp_path,
    )

    candidate = result.candidates[0]
    payload = json.loads((tmp_path / "biologic_candidates.json").read_text())
    assert candidate.sequence_ids == []
    assert payload["antibody_sequences"] == []
    assert any("sequence unavailable" in warning.lower() for warning in candidate.warnings)
    assert candidate.direct_experimental_evidence is True


def test_no_fake_sequence_created_for_sequence_unavailable_antibody() -> None:
    result = retrieve_existing_biologics(
        target_symbols=["EGFR"],
        user_candidate_records=[
            {
                "biologic_id": "user-antibody-no-seq",
                "name": "User antibody without sequence",
                "biologic_type": "monoclonal_antibody",
                "target_symbols": ["EGFR"],
                "evidence_item_ids": ["ev-user-1"],
                "sequence_expected": True,
            }
        ],
    )

    candidate = result.candidates[0]
    assert candidate.sequence_ids == []
    assert result.sequences == []
    assert "ACDEFGHIKLMNPQRSTVWY" not in json.dumps(
        [candidate.model_dump(mode="json") for candidate in result.candidates]
    )


def test_antibody_developability_flags_sequence_and_cdr_liabilities() -> None:
    sequence = AntibodySequence(
        sequence_id="seq-liability",
        biologic_id="bio-liability",
        chain_type="heavy",
        amino_acid_sequence=("ACDEFGHIKLMNPQRSTVWY" * 4)
        + "NST"
        + "M"
        + "DG"
        + "DP"
        + "AILMFWVY"
        + "C",
        sequence_length=97,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={
            "cdr_lengths": {"cdr3": 34},
            "cdr_sequences": {"cdr3": "RRRRRAILMFWVYNST"},
        },
    )

    assessment = assess_antibody_developability(
        assessment_id="dev-liability",
        biologic_id="bio-liability",
        sequences=[sequence],
    )

    flags = " ".join(
        assessment.sequence_liability_flags + assessment.cdr_liability_flags
    ).lower()
    assert "glycosylation" in flags
    assert "deamidation" in flags
    assert "oxidation" in flags
    assert "hydrophobic" in flags
    assert "unpaired cysteine" in flags
    assert "unusual cdr3 length" in flags
    assert assessment.aggregation_risk in {"medium", "high"}
    assert assessment.stability_risk in {"medium", "high"}


def test_normal_looking_sequence_low_or_unknown_risk_but_not_safe_claim() -> None:
    sequence = AntibodySequence(
        sequence_id="seq-normal",
        biologic_id="bio-normal",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHILNPQSTVY" * 8,
        sequence_length=128,
        species_origin="human",
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={"cdr_lengths": {"cdr3": 12}},
    )

    assessment = assess_antibody_developability(
        assessment_id="dev-normal",
        biologic_id="bio-normal",
        sequences=[sequence],
    )

    assert assessment.aggregation_risk in {"low", "unknown"}
    assert assessment.polyreactivity_risk in {"low", "unknown"}
    assert assessment.immunogenicity_risk in {"low", "unknown"}
    assert "safe" not in json.dumps(assessment.model_dump(mode="json")).lower()
    assert "manufacturing outcomes" in " ".join(assessment.warnings)


def test_generated_sequence_developability_assessment_remains_review_gated() -> None:
    sequence = AntibodySequence(
        sequence_id="seq-generated",
        biologic_id="bio-generated",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHILNPQSTVY" * 8,
        sequence_length=128,
        species_origin=None,
        is_generated=True,
        parent_sequence_ids=[],
        source="generated",
        source_record_id=None,
        created_at=NOW,
        metadata={"cdr_lengths": {"cdr3": 12}},
    )

    assessment = assess_antibody_developability(
        assessment_id="dev-generated",
        biologic_id="bio-generated",
        sequences=[sequence],
    )

    assert any("generated sequence" in flag for flag in assessment.sequence_liability_flags)
    assert any("computational hypotheses" in warning for warning in assessment.warnings)
    assert assessment.confidence <= 0.5


def test_developability_confidence_conservative_without_validated_plugin() -> None:
    sequence = AntibodySequence(
        sequence_id="seq-confidence",
        biologic_id="bio-confidence",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHILNPQSTVY" * 8,
        sequence_length=128,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )

    heuristic = assess_antibody_developability(
        assessment_id="dev-confidence",
        biologic_id="bio-confidence",
        sequences=[sequence],
    )
    unvalidated_external = assess_antibody_developability(
        assessment_id="dev-unvalidated",
        biologic_id="bio-confidence",
        sequences=[sequence],
        external_model_assessment={"liability_flags": ["model flag"]},
    )
    validated_external = assess_antibody_developability(
        assessment_id="dev-validated",
        biologic_id="bio-confidence",
        sequences=[sequence],
        external_model_assessment={
            "validated_external_model": True,
            "liability_flags": ["validated model flag"],
        },
    )

    assert heuristic.confidence <= 0.5
    assert unvalidated_external.confidence <= 0.5
    assert validated_external.confidence > heuristic.confidence


def test_generated_exact_antibody_duplicate_is_rejected_by_default() -> None:
    sequence = AntibodySequence(
        sequence_id="seq-generated-duplicate",
        biologic_id="bio-generated-duplicate",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHIKLMNPQRSTVWY" * 6,
        sequence_length=120,
        species_origin=None,
        is_generated=True,
        parent_sequence_ids=[],
        source="generated",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )

    novelty = assess_antibody_novelty(
        novelty_id="nov-generated-duplicate",
        biologic_id="bio-generated-duplicate",
        sequences=[sequence],
        known_sequences={"existing-record-1": sequence.amino_acid_sequence},
        sources_checked=["internal_candidate_registry"],
    )

    assert novelty.exact_sequence_match is True
    assert novelty.nearest_sequence_identity == 1.0
    assert novelty.novelty_class == "known"
    assert novelty.metadata["generated_exact_duplicate_rejected"] is True
    assert any("rejected" in warning.lower() for warning in novelty.warnings)
    assert novelty.metadata["global_novelty_claimed"] is False


def test_heavy_and_light_chain_duplicates_are_reported() -> None:
    heavy = _sequence(sequence_id="seq-heavy", sequence="ACDEFGHIKLMNPQRSTVWY" * 6)
    light = AntibodySequence(
        sequence_id="seq-light",
        biologic_id="bio-1",
        chain_type="light_kappa",
        amino_acid_sequence="YWVTSRQPNMLKIHGFEDCA" * 5,
        sequence_length=100,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )
    known_heavy = AntibodySequence(
        **{
            **heavy.model_dump(),
            "sequence_id": "known-heavy",
            "source_record_id": "known-heavy-record",
        }
    )
    known_light = AntibodySequence(
        **{
            **light.model_dump(),
            "sequence_id": "known-light",
            "source_record_id": "known-light-record",
        }
    )

    novelty = assess_antibody_novelty(
        novelty_id="nov-chain-duplicates",
        biologic_id="bio-1",
        sequences=[heavy, light],
        internal_candidate_registry=[known_heavy, known_light],
    )

    assert novelty.metadata["heavy_chain_duplicate_record_ids"] == ["known-heavy-record"]
    assert novelty.metadata["light_chain_duplicate_record_ids"] == ["known-light-record"]
    assert novelty.exact_sequence_match is True


def test_cdr3_duplicate_and_nearest_cdr3_identity_require_review() -> None:
    sequence = AntibodySequence(
        sequence_id="seq-cdr3",
        biologic_id="bio-cdr3",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHIKLMNPQRSTVWY" * 6,
        sequence_length=120,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={"cdr_sequences": {"cdr3": "CARDRSTYWYFDV"}},
    )

    novelty = assess_antibody_novelty(
        novelty_id="nov-cdr3",
        biologic_id="bio-cdr3",
        sequences=[sequence],
        imported_external_registry=[
            {
                "record_id": "external-cdr3-match",
                "sequence": "YWVTSRQPNMLKIHGFEDCA" * 6,
                "chain_type": "heavy",
                "cdr3": "CARDRSTYWYFDV",
            },
            {
                "record_id": "external-cdr3-near",
                "sequence": "YWVTSRQPNMLKIHGFEDCA" * 6,
                "chain_type": "heavy",
                "cdr3": "CARDRSTYWYFAV",
            },
        ],
    )

    assert novelty.cdr3_exact_match is True
    assert novelty.cdr3_nearest_identity == 1.0
    assert novelty.metadata["cdr3_exact_duplicate_record_ids"] == ["external-cdr3-match"]
    assert novelty.metadata["review_required"] is True
    assert novelty.novelty_class == "near_duplicate"


def test_nearest_sequence_identity_parent_similarity_and_lineage_are_reported() -> None:
    parent_sequence = "ACDEFGHIKLMNPQRSTVWY" * 6
    generated_variant = parent_sequence[:-1] + "A"
    sequence = AntibodySequence(
        sequence_id="seq-parent-variant",
        biologic_id="bio-parent-variant",
        chain_type="heavy",
        amino_acid_sequence=generated_variant,
        sequence_length=len(generated_variant),
        species_origin=None,
        is_generated=True,
        parent_sequence_ids=["parent-heavy-1"],
        source="generated",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )

    novelty = assess_antibody_novelty(
        novelty_id="nov-parent-similarity",
        biologic_id="bio-parent-variant",
        sequences=[sequence],
        parent_sequences={"parent-heavy-1": parent_sequence},
        internal_candidate_registry=[
            {"record_id": "existing-near", "sequence": parent_sequence, "chain_type": "heavy"}
        ],
        generated_sequence_archive=[
            {"record_id": "generated-archive-near", "sequence": parent_sequence}
        ],
    )

    assert novelty.exact_sequence_match is False
    assert novelty.nearest_sequence_identity is not None
    assert novelty.nearest_sequence_identity > 0.98
    assert novelty.metadata["parent_sequence_similarity"] > 0.98
    assert novelty.metadata["nearest_parent_record"] == "parent-heavy-1"
    assert novelty.metadata["generated_vs_existing_lineage"]["generated_query"] is True
    assert novelty.novelty_class == "near_duplicate"


def test_optional_public_antibody_database_plugin_is_source_limited() -> None:
    sequence = _sequence(sequence_id="seq-plugin", sequence="ACDEFGHIKLMNPQRSTVWY" * 6)

    def adapter(**kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "record_id": "plugin-record-1",
                "sequence": "ACDEFGHIKLMNPQRSTVWY" * 5 + "ACDEFGHIKLMNPQRSTVWA",
                "chain_type": "heavy",
            }
        ]

    novelty = assess_antibody_novelty(
        novelty_id="nov-plugin",
        biologic_id="bio-plugin",
        sequences=[sequence],
        public_antibody_database_adapters=[adapter],
    )

    assert "public_antibody_database_plugin" in novelty.sources_checked
    assert novelty.nearest_known_record == "plugin-record-1"
    assert novelty.novelty_class in {"near_duplicate", "close_variant"}
    assert any("do not establish global novelty" in warning for warning in novelty.warnings)


def test_null_antibody_generator_returns_no_hypotheses() -> None:
    objective = AntibodyDesignObjective(
        objective_id="obj-null",
        target_symbol="TNF",
        metadata={"biologic_id": "bio-null"},
    )
    generator = NullAntibodyGenerator()

    assert generator.generate(objective, seeds=[_sequence()], antigen_context=None, config={}) == []


def test_conservative_cdr_mutator_produces_validated_generated_sequence() -> None:
    seed = AntibodySequence(
        sequence_id="seq-seed-cdr",
        biologic_id="bio-seed",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHIKLMNPQRSTVWY" * 6,
        sequence_length=120,
        species_origin="human",
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id="seed-record",
        created_at=NOW,
        metadata={"cdr_regions": {"cdr1": (27, 38), "cdr2": (56, 65), "cdr3": (105, 117)}},
    )
    objective = AntibodyDesignObjective(
        objective_id="obj-cdr-mut",
        target_symbol="TNF",
        design_mode="cdr_mutation",
        metadata={"biologic_id": "bio-generated"},
    )
    context = AntigenContext(
        antigen_context_id="ag-tnf",
        target_symbol="TNF",
        antigen_name="TNF antigen",
        antigen_identifiers={},
        epitope_description=None,
        epitope_source=None,
        structure_context_ids=[],
        evidence_item_ids=[],
        confidence=0.4,
        warnings=[],
        metadata={},
    )

    generator = ConservativeCDRMutator(random_seed=7)
    hypotheses = generator.generate(
        objective,
        seeds=[seed],
        antigen_context=context,
        config={"max_outputs": 1},
    )

    assert len(hypotheses) == 1
    hypothesis = hypotheses[0]
    generated_sequence = hypothesis.metadata["generated_sequences"][0]
    assert hypothesis.direct_experimental_evidence is False
    assert hypothesis.parent_sequence_ids == [seed.sequence_id]
    assert generated_sequence["is_generated"] is True
    assert generated_sequence["amino_acid_sequence"] != seed.amino_acid_sequence
    assert hypothesis.metadata["validation"]["valid"] is True
    assert hypothesis.metadata["numbering"]["sequence_id"] == generated_sequence["sequence_id"]
    assert hypothesis.metadata["novelty"]["metadata"]["global_novelty_claimed"] is False
    assert hypothesis.metadata["developability"]["metadata"]["assessment_type"]
    assert hypothesis.metadata["no_binding_activity_claim"] is True
    assert "binding" in " ".join(hypothesis.warnings).lower()


def test_conservative_cdr_mutator_rejects_invalid_generated_sequence() -> None:
    seed = AntibodySequence(
        sequence_id="seq-seed-invalid-generation",
        biologic_id="bio-seed",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHIKLMNPQRSTVWY" * 6,
        sequence_length=120,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={"cdr_regions": {"cdr1": (27, 38)}},
    )
    objective = AntibodyDesignObjective(
        objective_id="obj-invalid-generation",
        target_symbol="TNF",
        design_mode="cdr_mutation",
        metadata={"biologic_id": "bio-invalid-generation"},
    )
    generator = ConservativeCDRMutator(random_seed=1)

    hypotheses = generator.generate(
        objective,
        seeds=[seed],
        antigen_context=None,
        config={"candidate_sequence_override": "ACGT" * 30},
    )

    assert hypotheses == []
    assert generator.last_rejections
    assert generator.last_rejections[0]["validation"]["rejected"] is True


def test_external_antibody_generator_plugin_disabled_by_default() -> None:
    objective = AntibodyDesignObjective(
        objective_id="obj-external",
        target_symbol="TNF",
        metadata={"biologic_id": "bio-external"},
    )
    generator = ExternalAntibodyGeneratorPlugin()

    with pytest.raises(RuntimeError, match="disabled by default"):
        generator.generate(objective, seeds=[], antigen_context=None, config={})


def test_build_antibody_design_objective_existing_ranking_defaults() -> None:
    objective = build_antibody_design_objective(
        objective_id="obj-existing-ranking",
        disease_name="Example disease",
        target_symbol="TNF",
        design_mode="existing_antibody_ranking",
    )

    assert objective.objective_id == "obj-existing-ranking"
    assert objective.target_symbol == "TNF"
    assert objective.biologic_type == "monoclonal_antibody"
    assert objective.hard_constraints["no_binding_activity_claims"] is True
    assert "novelty_check" in " ".join(objective.review_requirements)


def test_epitope_specific_objective_requires_source_backed_epitope() -> None:
    unknown_context = AntigenContext(
        antigen_context_id="ag-unknown",
        target_symbol="TNF",
        antigen_name="TNF antigen",
        antigen_identifiers={},
        epitope_description=None,
        epitope_source=None,
        structure_context_ids=[],
        evidence_item_ids=[],
        confidence=0.4,
        warnings=[],
        metadata={},
    )
    source_backed_context = AntigenContext(
        antigen_context_id="ag-source-backed",
        target_symbol="TNF",
        antigen_name="TNF antigen",
        antigen_identifiers={},
        epitope_description="Imported extracellular region annotation",
        epitope_source="literature_claim:12345",
        structure_context_ids=[],
        evidence_item_ids=["ev-epitope"],
        confidence=0.7,
        warnings=[],
        metadata={},
    )

    with pytest.raises(ValueError, match="source-backed epitope"):
        build_antibody_design_objective(
            objective_id="obj-epitope-fail",
            target_symbol="TNF",
            design_mode="epitope_context_design",
            antigen_context=unknown_context,
        )

    objective = build_antibody_design_objective(
        objective_id="obj-epitope-ok",
        target_symbol="TNF",
        design_mode="epitope_context_design",
        antigen_context=source_backed_context,
    )

    assert objective.antigen_context_id == "ag-source-backed"
    assert objective.hard_constraints["source_backed_epitope_context"] is True


def test_inverse_folding_objective_requires_approved_structure_model_plugin() -> None:
    with pytest.raises(ValueError, match="Inverse folding"):
        build_antibody_design_objective(
            objective_id="obj-inverse-fail",
            target_symbol="TNF",
            design_mode="inverse_folding_plugin",
            approved_tool_packages=["external_antibody_generator"],
        )

    objective = build_antibody_design_objective(
        objective_id="obj-inverse-ok",
        target_symbol="TNF",
        design_mode="inverse_folding_plugin",
        approved_tool_packages=["inverse_folding_plugin", "external_antibody_generator"],
    )

    assert "inverse_folding_plugin" in objective.metadata["approved_tool_packages"]


def test_external_generator_objective_requires_tool_package_approval() -> None:
    with pytest.raises(ValueError, match="External antibody generation"):
        build_antibody_design_objective(
            objective_id="obj-inpainting-fail",
            target_symbol="TNF",
            design_mode="sequence_inpainting_plugin",
        )

    objective = build_antibody_design_objective(
        objective_id="obj-inpainting-ok",
        target_symbol="TNF",
        design_mode="sequence_inpainting_plugin",
        approved_tool_packages=["external_antibody_generator"],
    )

    assert objective.design_mode == "sequence_inpainting_plugin"


def test_cdr_mutation_objective_requires_source_backed_seed_sequences() -> None:
    generated_seed = AntibodySequence(
        sequence_id="seq-generated-seed",
        biologic_id="bio-generated-seed",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHIKLMNPQRSTVWY" * 6,
        sequence_length=120,
        species_origin=None,
        is_generated=True,
        parent_sequence_ids=[],
        source="generated",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )
    imported_seed = AntibodySequence(
        sequence_id="seq-imported-seed",
        biologic_id="bio-imported-seed",
        chain_type="heavy",
        amino_acid_sequence="ACDEFGHIKLMNPQRSTVWY" * 6,
        sequence_length=120,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id="registry-seed-1",
        created_at=NOW,
        metadata={},
    )

    with pytest.raises(ValueError, match="source-backed seed"):
        build_antibody_design_objective(
            objective_id="obj-cdr-fail",
            target_symbol="TNF",
            design_mode="cdr_mutation",
            seed_sequences=[generated_seed],
        )

    objective = build_antibody_design_objective(
        objective_id="obj-cdr-ok",
        target_symbol="TNF",
        design_mode="cdr_mutation",
        seed_sequences=[imported_seed],
    )

    assert objective.seed_sequence_ids == ["seq-imported-seed"]
    assert objective.hard_constraints["source_backed_seed_sequences"] is True


def test_number_antibody_sequence_uses_mocked_external_adapter() -> None:
    sequence = _sequence(sequence="ACDEFGHIKLMNPQRSTVWY" * 6)

    def adapter(seq: AntibodySequence, scheme: str) -> dict[str, object]:
        return {
            "numbering_id": "num-external",
            "scheme": scheme,
            "framework_regions": {"fr1": (1, 26), "fr2": (39, 55), "fr3": (66, 104)},
            "cdr_regions": {"cdr1": (27, 38), "cdr2": (56, 65), "cdr3": (105, 117)},
            "insertions": {"112A": "present"},
            "numbering_tool": "mock_anarci",
            "tool_version": "1.0-test",
            "confidence": 0.92,
            "warnings": [],
        }

    configure_numbering_adapter(adapter)
    try:
        numbering = number_antibody_sequence(sequence, scheme="imgt")
        annotation = annotate_cdrs(sequence, numbering)
    finally:
        configure_numbering_adapter(None)

    assert numbering.numbering_tool == "mock_anarci"
    assert numbering.metadata["tool_version"] == "1.0-test"
    assert numbering.confidence == 0.92
    assert annotation.cdr1 == sequence.amino_acid_sequence[26:38]
    assert annotation.cdr3 == sequence.amino_acid_sequence[104:117]
    assert validate_cdr_regions(annotation) == []


def test_low_confidence_fallback_withholds_precise_cdrs() -> None:
    sequence = _sequence(sequence="ACDEFGHIKLMNPQRSTVWY" * 6)

    numbering = number_antibody_sequence(sequence, scheme="imgt")
    annotation = annotate_cdrs(sequence, numbering)
    findings = validate_cdr_regions(annotation)

    assert numbering.numbering_tool == "internal_heuristic"
    assert numbering.confidence < 0.6
    assert annotation.cdr1 is None
    assert annotation.metadata["precise_cdrs_withheld"] is True
    assert "cdr1 missing or withheld" in findings


def test_numbering_failure_marks_unknown_and_requires_review() -> None:
    sequence = _sequence(sequence="ACDEFGHIKLMNPQRSTVWY" * 6)

    def failing_adapter(seq: AntibodySequence, scheme: str) -> dict[str, object]:
        raise RuntimeError("adapter unavailable")

    configure_numbering_adapter(failing_adapter)
    try:
        numbering = number_antibody_sequence(sequence, scheme="imgt")
    finally:
        configure_numbering_adapter(None)

    assert numbering.scheme == "unknown"
    assert numbering.confidence == 0.0
    assert numbering.metadata["review_required"] is True
    assert any("failed" in warning.lower() for warning in numbering.warnings)


def test_validate_cdr_regions_reports_mismatches_and_unusual_lengths() -> None:
    annotation = CDRAnnotation.model_construct(
        annotation_id="cdr-bad",
        sequence_id="seq-1",
        scheme="imgt",
        cdr1="AAAA",
        cdr2=None,
        cdr3="C" * 31,
        cdr_lengths={"cdr1": 5, "cdr3": 31},
        unusual_motifs=[],
        warnings=[],
        metadata={},
    )

    findings = validate_cdr_regions(annotation)

    assert "cdr1 length metadata does not match sequence" in findings
    assert "cdr2 missing or withheld" in findings
    assert "cdr3 length is outside broad antibody review bounds" in findings


def test_valid_heavy_chain_validation_passes() -> None:
    sequence = _sequence(sequence="ACDEFGHIKLMNPQRSTVWY" * 6)

    result = validate_antibody_sequence(sequence)

    assert result["valid"] is True
    assert result["rejected"] is False
    assert result["errors"] == []
    assert "does not prove safety" in result["limitations"][0]


def test_invalid_characters_fail_validation() -> None:
    sequence = AntibodySequence.model_construct(
        sequence_id="seq-invalid",
        biologic_id="bio-1",
        chain_type="heavy",
        amino_acid_sequence="ACDEF!",
        sequence_length=6,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )

    result = validate_antibody_sequence(sequence)

    assert result["valid"] is False
    assert any("invalid amino acid characters" in error for error in result["errors"])


def test_nucleotide_like_sequence_rejected() -> None:
    sequence = AntibodySequence.model_construct(
        sequence_id="seq-nucleotide",
        biologic_id="bio-1",
        chain_type="heavy",
        amino_acid_sequence="ACGT" * 30,
        sequence_length=120,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )

    result = validate_antibody_sequence(sequence)

    assert result["valid"] is False
    assert any("nucleotide-like" in error for error in result["errors"])


def test_ambiguous_residue_warning_when_allowed() -> None:
    sequence = AntibodySequence.model_construct(
        sequence_id="seq-ambiguous",
        biologic_id="bio-1",
        chain_type="heavy",
        amino_acid_sequence=("ACDEFGHIKLMNPQRSTVWY" * 5) + "X",
        sequence_length=101,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )

    result = validate_antibody_sequence(sequence, allow_ambiguous=True)

    assert result["valid"] is True
    assert any("ambiguous residues" in warning for warning in result["warnings"])


def test_liability_motifs_are_flagged() -> None:
    sequence = _sequence(
        sequence="ACDEFGHIKLMNPQRSTVWY" * 4 + "NST" + "M" + "DG" + "DP" + "AILMFWVY"
    )

    result = validate_antibody_sequence(sequence)

    warnings = " ".join(result["warnings"]).lower()
    assert "glycosylation" in warnings
    assert "deamidation" in warnings
    assert "oxidation" in warnings
    assert "clipping" in warnings
    assert "hydrophobic" in warnings
    assert result["liability_flags"]


def test_generated_invalid_sequence_is_rejected() -> None:
    sequence = AntibodySequence.model_construct(
        sequence_id="seq-generated-invalid",
        biologic_id="bio-1",
        chain_type="heavy",
        amino_acid_sequence="ACDEF!",
        sequence_length=6,
        species_origin=None,
        is_generated=True,
        parent_sequence_ids=[],
        source="generated",
        source_record_id=None,
        created_at=NOW,
        metadata={},
    )

    result = validate_antibody_sequence(sequence)

    assert result["valid"] is False
    assert result["rejected"] is True


def test_duplicate_and_paired_sequence_warnings() -> None:
    sequence = AntibodySequence(
        sequence_id="seq-paired",
        biologic_id="bio-1",
        chain_type="paired_heavy_light",
        amino_acid_sequence="ACDEFGHIKLMNPQRSTVWY" * 10,
        sequence_length=200,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={"cdr_lengths": {"cdr3": 35}},
    )
    duplicate = AntibodySequence(
        sequence_id="seq-duplicate",
        biologic_id="bio-2",
        chain_type="paired_heavy_light",
        amino_acid_sequence=sequence.amino_acid_sequence,
        sequence_length=sequence.sequence_length,
        species_origin=None,
        is_generated=False,
        parent_sequence_ids=[],
        source="imported",
        source_record_id=None,
        created_at=NOW,
        metadata={"heavy_sequence_id": "h1", "light_sequence_id": "l1"},
    )

    result = validate_antibody_sequence(sequence, existing_sequences=[duplicate])
    batch = validate_antibody_sequences([sequence, duplicate])

    assert result["duplicated_sequence_ids"] == ["seq-duplicate"]
    assert any("pairing metadata" in warning for warning in result["warnings"])
    assert any("Unusual CDR3" in warning for warning in result["warnings"])
    assert batch[0]["duplicated_sequence_ids"] == ["seq-duplicate"]
