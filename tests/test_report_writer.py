from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.report_writer import ReportWriterAgent
from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.developability.schemas import (
    ADMETPrediction,
    ChemistryAlert,
    DevelopabilityRun,
    DockingAssessment,
    PhysChemProfile,
    SynthesizabilityAssessment,
)
from molecule_ranker.developability.schemas import (
    DevelopabilityAssessment as StructuredDevelopabilityAssessment,
)
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    GenerationObjective,
    GenerationRun,
    NoveltyAssessment,
    SeedMolecule,
)
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.queue_builder import build_review_workspace
from molecule_ranker.review.schemas import FollowupRequest, Reviewer
from molecule_ranker.review.workspace import ReviewWorkspaceStore, create_validation_handoff
from molecule_ranker.schemas import (
    AgentTrace,
    Disease,
    EvidenceItem,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
    RankingRun,
    ScoreBreakdown,
    Target,
)
from molecule_ranker.schemas import (
    DevelopabilityAssessment as LegacyDevelopabilityAssessment,
)

RETRIEVED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _evidence(source: str, record_id: str, evidence_type: str) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        source_record_id=record_id,
        title=f"{source} record",
        url=f"https://example.org/{record_id}",
        evidence_type=evidence_type,
        summary=f"Retrieved {source} evidence.",
        confidence=0.8,
        retrieval_timestamp=RETRIEVED_AT,
        metadata={
            "query": "Parkinson disease",
            "response_provenance": {
                "mode": "cached-real-data",
                "cache_key": "cache-test-key",
                "retrieved_at": RETRIEVED_AT.isoformat(),
                "source": source,
                "endpoint": f"https://example.org/{record_id}",
            },
        },
    )


def _generation_objective() -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        target_identifiers={"ensembl": "ENSG00000000001"},
        mechanism_hint="Retrieved target mechanism.",
        seed_molecule_names=["Evidence-backed candidate"],
        seed_molecule_ids=["CHEMBL_TEST"],
        objective_type="target_conditioned_analog_generation",
        constraints={"molecular_weight": {"min": 100, "max": 400}},
        metadata={"target_relevance_score": 0.8},
    )


def _generation_seed() -> SeedMolecule:
    return SeedMolecule(
        name="Evidence-backed candidate",
        canonical_smiles="CCO",
        identifiers={"chembl": "CHEMBL_TEST"},
        known_targets=["MAOB"],
        source_candidate_name="Evidence-backed candidate",
        evidence_count=2,
        best_evidence_confidence=0.9,
        target_relevance_score=0.8,
        seed_selection_reason="Selected from retrieved ChEMBL target evidence.",
        metadata={"matched_targets": ["MAOB"], "seed_score": 0.82},
    )


def _physchem(smiles: str) -> PhysChemProfile:
    return PhysChemProfile(
        canonical_smiles=smiles,
        inchi_key="TEST-INCHIKEY",
        molecular_weight=123.1,
        logp=2.1,
        tpsa=35.5,
        hbd=1,
        hba=2,
        rotatable_bonds=3,
        aromatic_rings=1,
        heavy_atom_count=9,
        formal_charge=0,
        fraction_csp3=0.4,
        qed=0.62,
        lipinski_violations=0,
        veber_violations=0,
        ghose_violations=1,
        egan_violations=0,
        muegge_violations=0,
        metadata={"assumptions": ["RDKit descriptor snapshot for reporting tests."]},
    )


AlertSeverity = Literal["low", "medium", "high", "critical"]
ADMETRiskLevel = Literal["low", "medium", "high", "unknown"]
DevelopabilityRiskLevel = Literal["low", "medium", "high", "critical", "unknown"]
DevelopabilityRecommendation = Literal[
    "retain",
    "deprioritize",
    "reject",
    "expert_review_required",
]
MoleculeOrigin = Literal["existing", "generated"]


def _alert(severity: AlertSeverity = "critical") -> ChemistryAlert:
    return ChemistryAlert(
        alert_id=f"local-alert-{severity}",
        alert_type="toxicophore",
        alert_name="Test toxicophore-like alert",
        severity=severity,
        matched_smarts="[N+](=O)[O-]",
        description="Transparent test alert used as a risk flag, not proof of toxicity.",
        source="local_smarts_test_alerts",
        metadata={"risk_flag_only": True},
    )


def _admet(endpoint: str, risk_level: ADMETRiskLevel = "high") -> ADMETPrediction:
    return ADMETPrediction(
        endpoint=endpoint,
        value=None,
        probability=None,
        risk_level=risk_level,
        model_name="rule_based_admet_baseline",
        model_version="0.4",
        prediction_method="rule_based",
        applicability_domain="unknown",
        confidence=0.45,
        metadata={
            "rules_used": ["test reporting risk flag"],
            "limitations": ["Computational triage only."],
        },
    )


def _synth(risk_level: ADMETRiskLevel = "medium") -> SynthesizabilityAssessment:
    return SynthesizabilityAssessment(
        sa_score=0.58,
        retrosynthesis_available=False,
        route_count=None,
        estimated_complexity="medium",
        starting_material_availability="unknown",
        risk_level=risk_level,
        method="descriptor_based_fallback",
        confidence=0.35,
        warnings=["Coarse computational triage only."],
        metadata={"fallback": True},
    )


def _docking(enabled: bool = True) -> DockingAssessment:
    return DockingAssessment(
        enabled=enabled,
        target_symbol="MAOB",
        structure_source="RCSB PDB" if enabled else None,
        structure_id="1GOS" if enabled else None,
        ligand_id="GEN-MAOB-0001",
        docking_engine="mock_vina" if enabled else None,
        docking_score=0.42 if enabled else None,
        score_units="normalized_test_score" if enabled else None,
        binding_site_method="known_ligand_site" if enabled else "skipped",
        pose_file=None,
        confidence=0.3,
        warnings=["Docking score is a weak computational heuristic and does not prove binding."]
        if enabled
        else ["Docking disabled."],
        metadata={"artifact": "not_written"},
    )


def _structured_assessment(
    molecule_id: str,
    molecule_name: str,
    *,
    origin: MoleculeOrigin,
    risk_level: DevelopabilityRiskLevel,
    recommendation: DevelopabilityRecommendation,
    score: float,
    alerts: list[ChemistryAlert] | None = None,
    docking: list[DockingAssessment] | None = None,
) -> StructuredDevelopabilityAssessment:
    smiles = "CCOc1ccccc1N" if origin == "generated" else "CCO"
    return StructuredDevelopabilityAssessment(
        molecule_id=molecule_id,
        molecule_name=molecule_name,
        origin=origin,
        canonical_smiles=smiles,
        physchem=_physchem(smiles),
        alerts=alerts or [],
        admet_predictions=[
            _admet(
                "ames_mutagenicity_risk",
                "high" if risk_level in {"high", "critical"} else "medium",
            ),
            _admet("herg_liability_risk", "medium"),
        ],
        synthesizability=_synth("high" if risk_level == "critical" else "medium"),
        docking=docking or [],
        overall_developability_score=score,
        risk_summary=f"{risk_level} computational developability risk flags.",
        risk_level=risk_level,
        confidence=0.4 if origin == "generated" else 0.55,
        recommendation=recommendation,
        warnings=["Requires expert review."],
        metadata={"reporting_test_data": True},
    )


def _legacy_assessment(
    structured: StructuredDevelopabilityAssessment,
) -> LegacyDevelopabilityAssessment:
    return LegacyDevelopabilityAssessment(
        molecule_name=structured.molecule_name,
        origin=structured.origin,
        structure_available=structured.physchem is not None,
        canonical_smiles=structured.canonical_smiles,
        descriptors={
            "molecular_weight": structured.physchem.molecular_weight or 0.0,
            "logp": structured.physchem.logp or 0.0,
        }
        if structured.physchem
        else {},
        synthetic_accessibility_score=structured.synthesizability.sa_score
        if structured.synthesizability
        else None,
        developability_score=structured.overall_developability_score,
        triage_recommendation=(
            "high_risk_flags"
            if structured.risk_level in {"high", "critical"}
            else "review_flags"
        ),
        limitations=["Developability assessment is computational triage only."],
        metadata={
            "risk_level": structured.risk_level,
            "structured_developability_assessment": structured.model_dump(mode="json"),
        },
    )


def _generated_molecule(
    generated_id: str,
    *,
    rejected: bool = False,
    developability: LegacyDevelopabilityAssessment | None = None,
    structured_developability: StructuredDevelopabilityAssessment | None = None,
) -> GeneratedMolecule:
    validation = ChemicalValidationResult(
        valid_rdkit_mol=not rejected,
        sanitization_ok=not rejected,
        canonicalization_ok=not rejected,
        allowed_elements_ok=True,
        descriptor_bounds_ok=True,
        pains_or_alerts=[],
        rejection_reasons=(
            ["rdkit_parse_failed", "developability_filter_failed"] if rejected else []
        ),
        metadata={},
    )
    novelty = NoveltyAssessment(
        duplicate_of_existing=False,
        duplicate_of_generated=False,
        max_similarity_to_existing=0.4,
        nearest_existing_name="Evidence-backed candidate",
        max_similarity_to_seed=0.62,
        nearest_seed_name="Evidence-backed candidate",
        novelty_class="novel_analog",
        metadata={},
    )
    score = GeneratedMoleculeScoreBreakdown(
        target_conditioning_score=0.7,
        seed_evidence_score=0.8,
        novelty_score=0.9,
        diversity_score=0.8,
        chemical_validity_score=0.0 if rejected else 1.0,
        property_profile_score=0.7,
        literature_context_score=0.0,
        final_generation_score=0.61,
        confidence=0.4,
        explanation="Generated hypothesis scored from seed context only.",
    )
    return GeneratedMolecule(
        generated_id=generated_id,
        smiles="CCOc1ccccc1N",
        canonical_smiles="CCOc1ccccc1N",
        selfies="[C][C][O][C][=C][C][=C][C][=C][Ring1][=Branch1][N]",
        inchi_key=f"{generated_id.upper()}-INCHIKEY",
        generation_method="selfies_mutation",
        parent_seed_ids=["CHEMBL_TEST"],
        conditioned_targets=["MAOB"],
        objective_id="objective-1",
        generation_round=1,
        descriptors={"molecular_weight": 123.1, "heavy_atom_count": 9},
        fingerprints={"morgan": {"representation": "not_serialized"}},
        validation=validation,
        novelty=novelty,
        diversity_cluster="cluster-1",
        generation_score=0.61,
        score_breakdown=score,
        developability_assessment=developability,
        warnings=["in_silico_hypothesis_only"],
        metadata={
            "operation": "mutation",
            **(
                {
                    "developability_assessment": structured_developability.model_dump(
                        mode="json"
                    )
                }
                if structured_developability is not None
                else {}
            ),
        },
    )


def _generation_run(
    retained_developability: LegacyDevelopabilityAssessment | None = None,
    rejected_developability: LegacyDevelopabilityAssessment | None = None,
    retained_structured: StructuredDevelopabilityAssessment | None = None,
    rejected_structured: StructuredDevelopabilityAssessment | None = None,
) -> GenerationRun:
    retained = _generated_molecule(
        "GEN-MAOB-0001",
        developability=retained_developability,
        structured_developability=retained_structured,
    )
    rejected = _generated_molecule(
        "GEN-MAOB-REJECTED",
        rejected=True,
        developability=rejected_developability,
        structured_developability=rejected_structured,
    )
    return GenerationRun(
        objectives=[_generation_objective()],
        seeds=[_generation_seed()],
        generated=[retained, rejected],
        retained=[retained],
        rejected=[rejected],
        warnings=["Generated molecules are in-silico research hypotheses only."],
        metadata={
            "generation_method": "selfies_mutation",
            "generator_version": "v0.3",
            "run_timestamp": "2026-01-02T03:04:05+00:00",
        },
    )


def _scored_context(tmp_path) -> PipelineContext:
    existing_structured = _structured_assessment(
        "CHEMBL_TEST",
        "Evidence-backed candidate",
        origin="existing",
        risk_level="critical",
        recommendation="expert_review_required",
        score=0.32,
        alerts=[_alert("critical")],
        docking=[_docking(True)],
    )
    generated_structured = _structured_assessment(
        "GEN-MAOB-0001",
        "GEN-MAOB-0001",
        origin="generated",
        risk_level="medium",
        recommendation="deprioritize",
        score=0.56,
        alerts=[_alert("medium")],
        docking=[_docking(True)],
    )
    rejected_generated_structured = _structured_assessment(
        "GEN-MAOB-REJECTED",
        "GEN-MAOB-REJECTED",
        origin="generated",
        risk_level="critical",
        recommendation="reject",
        score=0.18,
        alerts=[_alert("critical")],
        docking=[_docking(True)],
    )
    existing_legacy = _legacy_assessment(existing_structured)
    generated_legacy = _legacy_assessment(generated_structured)
    rejected_generated_legacy = _legacy_assessment(rejected_generated_structured)
    generation_run = _generation_run(
        generated_legacy,
        rejected_generated_legacy,
        generated_structured,
        rejected_generated_structured,
    )
    developability_run = DevelopabilityRun(
        enabled=True,
        assessed_existing_count=1,
        assessed_generated_count=2,
        retained_count=1,
        deprioritized_count=1,
        rejected_count=1,
        assessments=[
            existing_structured,
            generated_structured,
            rejected_generated_structured,
        ],
        warnings=["Developability outputs are computational triage heuristics."],
        metadata={
            "alert_counts": {"critical": 2, "medium": 1},
            "admet_risk_counts": {"high": 2, "medium": 4},
        },
    )
    disease = Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        synonyms=[],
        identifiers={"open_targets": "MONDO_0005180"},
        description=None,
    )
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        identifiers={"open_targets": "ENSG00000000001", "ensembl": "ENSG00000000001"},
        disease_relevance_score=0.8,
        evidence=[_evidence("Open Targets", "MONDO_0005180:ENSG1", "target_disease")],
        mechanism="Retrieved target mechanism.",
        metadata={
            "chembl_target_mapping": {
                "chembl_target_id": "CHEMBL_T_MAOB",
                "mapping_method": "uniprot_accession",
                "confidence": 0.95,
            }
        },
    )
    breakdown = ScoreBreakdown(
        disease_target_relevance=0.8,
        molecule_target_evidence=0.9,
        mechanism_plausibility=0.8,
        clinical_precedence=1.0,
        safety_prior=0.8,
        data_quality=0.8,
        novelty_or_repurposing_value=0.7,
        final_score=0.835,
        confidence=0.7,
        explanation="Retrieved evidence links the candidate to MAOB.",
    )
    candidate = MoleculeCandidate(
        name="Evidence-backed candidate",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL_TEST"},
        chemical_metadata={
            "inchikey": "TEST-INCHIKEY",
            "canonical_smiles": "CCO",
        },
        known_targets=["MAOB"],
        development_status="approved",
        mechanism_of_action="MAOB inhibitor",
        evidence=[
            _evidence("ChEMBL", "mec-1", "mechanism"),
            EvidenceItem(
                source="ChEMBL",
                source_record_id="act-1",
                title="ChEMBL activity",
                evidence_type="activity",
                summary="ChEMBL reports IC50 activity.",
                confidence=0.85,
                retrieval_timestamp=RETRIEVED_AT,
                metadata={
                    "standard_type": "IC50",
                    "standard_value": 12.5,
                    "standard_units": "nM",
                    "pchembl_value": 8.1,
                    "target_chembl_id": "CHEMBL_T_MAOB",
                    "mapping_confidence": 0.95,
                    "response_provenance": {
                        "mode": "live",
                        "endpoint": "https://example.org/activity",
                    },
                },
            ),
            EvidenceItem(
                source="ChEMBL",
                source_record_id="ind-1",
                title="ChEMBL indication",
                evidence_type="indication",
                summary="ChEMBL lists Parkinson Disease as an indication record.",
                confidence=0.7,
                retrieval_timestamp=RETRIEVED_AT,
                metadata={
                    "indication": "Parkinson Disease",
                    "mesh_id": "D010300",
                    "max_phase_for_ind": 3.0,
                    "response_provenance": {
                        "mode": "live",
                        "endpoint": "https://example.org/ind-1",
                    },
                },
            ),
            EvidenceItem(
                source="ChEMBL",
                source_record_id="warn-1",
                title="ChEMBL warning",
                evidence_type="safety_warning",
                summary="Black box warning.",
                confidence=0.8,
                retrieval_timestamp=RETRIEVED_AT,
                metadata={
                    "warning_type": "Black Box Warning",
                    "country": "US",
                    "year": 2020,
                    "warning_class": "boxed_warning",
                    "response_provenance": {
                        "mode": "live",
                        "endpoint": "https://example.org/warn-1",
                    },
                },
            ),
        ],
        score=0.835,
        score_breakdown=breakdown,
        developability_assessment=existing_legacy,
        warnings=[
            "Scores are heuristic and require experimental validation.",
            (
                "Low-confidence normalized-name-only deduplication used; "
                "stable chemistry identifiers were unavailable."
            ),
        ],
    )
    return PipelineContext(
        disease_input="Parkinson disease",
        disease=disease,
        targets=[target],
        candidates=[candidate],
        generated_candidates=[
            GeneratedMoleculeHypothesis(
                name="GEN-MAOB-0001",
                canonical_smiles="CCOc1ccccc1N",
                molecule_type="small_molecule",
                target_symbol="MAOB",
                seed_molecule_names=["Evidence-backed candidate"],
                seed_identifiers=[{"chembl": "CHEMBL_TEST"}],
                generation_score=0.63,
                min_seed_similarity=0.42,
                max_seed_similarity=0.74,
                mean_seed_similarity=0.58,
                descriptors={"molecular_weight": 123.1, "heavy_atom_count": 9},
                trace={
                    "generator": "selfies_mutation_crossover",
                    "operation": "mutation",
                    "parent_smiles": ["CCO"],
                    "developability_assessment": generated_structured.model_dump(
                        mode="json"
                    ),
                },
                developability_assessment=generated_legacy,
                warnings=["hypothesis_only"],
            )
        ],
        traces=[
            AgentTrace(
                agent_name="NovelMoleculeAgent",
                input_summary="Generation input.",
                output_summary="Generated one hypothesis.",
                warnings=[],
                metadata={
                    "seed_selection": {"selected_seeds": ["Evidence-backed candidate"]},
                    "objective_building": {"created_objectives": ["objective-1"]},
                    "generator_trace": {
                        "method": "selfies_mutation",
                        "random_seed": 17,
                        "raw_generated_count": 2,
                    },
                    "validation_filtering_trace": {
                        "retained_count": 1,
                        "rejected_count": 1,
                    },
                    "scoring_trace": {"scored_count": 1},
                    "generation_run": {
                        "raw_generated_count": 2,
                        "retained_count": 1,
                        "rejected_count": 1,
                    },
                },
            ),
            AgentTrace(
                agent_name="EvidenceScoringAgent",
                input_summary="Scoring input.",
                output_summary="Scored one candidate.",
                warnings=[],
                metadata={},
            ),
        ],
        config={
            "results_dir": str(tmp_path),
            "ranker_config": {
                "cache_dir": ".cache/molecule-ranker",
                "use_cache": True,
                "allow_cached_real_data": True,
                "request_timeout_seconds": 20,
                "enable_generation": True,
                "generation_random_seed": 17,
                "generation_method": "selfies_mutation",
            },
            "enable_generation": True,
            "enable_docking": True,
            "generation_run": generation_run,
            "developability_run": developability_run,
        },
    )


def test_report_writer_creates_success_artifacts(tmp_path):
    context = _scored_context(tmp_path)

    updated = ReportWriterAgent().run(context)

    output_dir = tmp_path / "parkinson-disease"
    assert updated.output_dir == output_dir
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trace.json").exists()
    assert (output_dir / "generated_molecules.json").exists()
    assert (output_dir / "generated_candidates.json").exists()
    assert (output_dir / "generation_trace.json").exists()
    assert (output_dir / "developability_report.md").exists()
    assert (output_dir / "developability_assessments.json").exists()
    assert (output_dir / "developability.json").exists()

    candidates_payload = json.loads((output_dir / "candidates.json").read_text())
    assert candidates_payload["success"] is True
    assert candidates_payload["candidates"][0]["score_breakdown"]["final_score"] == pytest.approx(
        0.835
    )
    assert candidates_payload["generated_molecule_hypotheses"][0]["name"] == "GEN-MAOB-0001"
    assert (
        candidates_payload["candidates"][0]["developability"]["metadata"]["risk_level"]
        == "critical"
    )
    assert (
        candidates_payload["candidates"][0]["developability_summary"]["risk_level"]
        == "critical"
    )
    assert (
        candidates_payload["generated_molecule_hypotheses"][0]["developability"][
            "metadata"
        ]["risk_level"]
        == "medium"
    )
    generated_payload = json.loads((output_dir / "generated_molecules.json").read_text())
    assert generated_payload["success"] is True
    assert generated_payload["generation_enabled"] is True
    assert generated_payload["generated_count"] == 2
    assert generated_payload["retained_count"] == 1
    assert generated_payload["rejected_count"] == 1
    assert generated_payload["objectives"][0]["target_symbol"] == "MAOB"
    assert generated_payload["seeds"][0]["name"] == "Evidence-backed candidate"
    assert generated_payload["retained_generated_molecules"][0]["canonical_smiles"]
    assert generated_payload["retained_generated_molecules"][0]["inchi_key"]
    assert (
        generated_payload["retained_generated_molecules"][0]["developability_summary"][
            "risk_level"
        ]
        == "medium"
    )
    assert generated_payload["rejected_generated_molecules"][0]["rejection_reasons"] == [
        "developability_filter_failed",
        "rdkit_parse_failed",
    ]
    assert (
        generated_payload["rejected_generated_molecules"][0]["developability"]["metadata"][
            "risk_level"
        ]
        == "critical"
    )
    assert generated_payload["generation_config"]["generation_method"] == "selfies_mutation"
    assert "synthesis routes" not in json.dumps(generated_payload).lower()
    assert "procedures" not in json.dumps(generated_payload).lower()

    generated_candidates_payload = json.loads(
        (output_dir / "generated_candidates.json").read_text()
    )
    assert generated_candidates_payload == generated_payload

    generation_trace_payload = json.loads((output_dir / "generation_trace.json").read_text())
    assert generation_trace_payload["seed_selection_trace"]["selected_seeds"] == [
        "Evidence-backed candidate"
    ]
    assert generation_trace_payload["objective_building_trace"]["created_objectives"] == [
        "objective-1"
    ]
    assert generation_trace_payload["generator_trace"]["method"] == "selfies_mutation"
    assert generation_trace_payload["validation_filtering_trace"]["rejected_count"] == 1
    assert generation_trace_payload["scoring_trace"]["scored_count"] == 1
    assert generation_trace_payload["random_seed"] == 17
    assert generation_trace_payload["generator_method"] == "selfies_mutation"
    assert generation_trace_payload["generator_version"] == "v0.3"
    assert generation_trace_payload["run_timestamp"] == "2026-01-02T03:04:05+00:00"
    assert generation_trace_payload["developability_filtering_trace"][
        "assessed_generated_count"
    ] == 2
    assert generation_trace_payload["developability_filtering_trace"]["rejected_count"] == 1

    trace_payload = json.loads((output_dir / "trace.json").read_text())
    assert trace_payload["traces"][-1]["agent_name"] == "ReportWriterAgent"
    assert trace_payload["artifacts"]["report_md"].endswith("report.md")
    assert trace_payload["artifacts"]["generated_molecules_json"].endswith(
        "generated_molecules.json"
    )
    assert trace_payload["artifacts"]["generated_candidates_json"].endswith(
        "generated_candidates.json"
    )
    assert trace_payload["artifacts"]["generation_trace_json"].endswith("generation_trace.json")
    assert trace_payload["artifacts"]["developability_report_md"].endswith(
        "developability_report.md"
    )
    assert trace_payload["artifacts"]["developability_assessments_json"].endswith(
        "developability_assessments.json"
    )
    assert trace_payload["artifacts"]["developability_json"].endswith("developability.json")
    assert trace_payload["developability_run"]["assessed_existing_count"] == 1
    assert trace_payload["developability_run"]["assessed_generated_count"] == 2

    developability_payload = json.loads((output_dir / "developability.json").read_text())
    assert developability_payload["success"] is True
    assert developability_payload["enabled"] is True
    assert developability_payload["assessed_existing_count"] == 1
    assert developability_payload["assessed_generated_count"] == 2
    assert developability_payload["retained_count"] == 1
    assert developability_payload["deprioritized_count"] == 1
    assert developability_payload["rejected_count"] == 1
    assert developability_payload["risk_distribution"]["critical"] == 2
    assert "toxicophore:critical" in developability_payload["alert_distribution"]
    assert "ames_mutagenicity_risk" in developability_payload["admet_endpoint_coverage"]
    assert developability_payload["assessments"][1]["molecule_id"] == "GEN-MAOB-0001"
    assert developability_payload["warnings"] == [
        "Developability outputs are computational triage heuristics."
    ]
    assert "No synthesis routes" in " ".join(developability_payload["limitations"])
    assert developability_payload["config"]["enable_docking"] is True
    assert "generated_at" in developability_payload

    report = (output_dir / "report.md").read_text()
    assert "# Molecule Ranking Report: Parkinson disease" in report
    assert "## Research-use disclaimer" in report
    assert "## Data provenance" in report
    assert "## Data Sources and Retrieval" in report
    assert "Open Targets endpoint: https://example.org/MONDO_0005180:ENSG1" in report
    assert "ChEMBL endpoint: https://example.org/mec-1" in report
    assert "PubChem endpoint: unavailable" in report
    assert "Cache usage: enabled; cached-real-data fallback allowed" in report
    assert "Source versions/status: unavailable" in report
    assert "## Disease Resolution" in report
    assert "Selected disease entity: Parkinson disease" in report
    assert "Match reason: unavailable" in report
    assert "Ambiguity handling result: unavailable" in report
    assert "## Target Mapping" in report
    assert "Open Targets ID: ENSG00000000001" in report
    assert "ChEMBL target mapping: CHEMBL_T_MAOB" in report
    assert "Mapping method: uniprot_accession" in report
    assert "Mapping confidence: 0.950" in report
    assert "Molecules found: yes" in report
    assert "## Evidence Coverage" in report
    assert "## Developability Summary" in report
    assert "Developability scores are computational triage heuristics." in report
    assert "They do not establish safety, efficacy, or synthesizability." in report
    assert "medicinal chemistry, toxicology, pharmacology" in report
    assert "No synthesis instructions are provided." in report
    assert "Risk-level distribution" in report
    assert "Alert distribution" in report
    assert "ADMET endpoint coverage" in report
    assert "Synthesizability method coverage" in report
    assert "Structure/docking availability" in report
    assert "Docking scores, when present, are weak computational heuristics" in report
    assert "Disease-target evidence count: 1" in report
    assert "Mechanism evidence count: 1" in report
    assert "Activity evidence count: 1" in report
    assert "Indication evidence count: 1" in report
    assert "Safety warning evidence count: 1" in report
    assert "Chemical annotation count: 0" in report
    assert "Molecule-target evidence: 2" in report
    assert "Activity evidence summary:" in report
    assert "IC50=12.5 nM; pChEMBL=8.1" in report
    assert "Indication evidence summary:" in report
    assert "Safety warnings:" in report
    assert "Chemical identifiers:" in report
    assert "inchikey: TEST-INCHIKEY" in report
    assert "Deduplication metadata:" in report
    assert "MONDO_0005180" in report
    assert "Open Targets" in report
    assert "ChEMBL" in report
    assert "2026-01-02T03:04:05+00:00" in report
    assert "## Ranked Candidates" in report
    assert "## Generated Molecule Hypotheses" in report
    generated_section = report.split("## Generated Molecule Hypotheses", 1)[1].split(
        "## Ranked Candidates",
        1,
    )[0]
    assert "Generated molecules are computational structures." in generated_section
    assert "Generated molecules have no direct experimental evidence." in generated_section
    assert "generation-prioritization scores, not efficacy predictions" in generated_section
    assert "chemical review, synthesis feasibility review, ADMET review" in generated_section
    assert "No synthesis instructions are provided." in generated_section
    assert "Generated attempted" in generated_section
    assert "Valid retained" in generated_section
    assert "Rejected invalid" in generated_section
    assert "Rejected duplicate/near-duplicate" in generated_section
    assert "Rejected distant/unconditioned" in generated_section
    assert "Retained By Target" in generated_section
    assert "Generated ID" in generated_section
    assert "GEN-MAOB-0001" in generated_section
    assert "Canonical SMILES" in generated_section
    assert "GEN-MAOB-0001-INCHIKEY" in generated_section
    assert "Conditioned target(s)" in generated_section
    assert "Parent seed molecule(s)" in generated_section
    assert "Evidence-backed candidate" in generated_section
    assert "Generation method" in generated_section
    assert "Final generation score" in generated_section
    assert "Confidence" in generated_section
    assert "Score breakdown" in generated_section
    assert "Descriptor table" in generated_section
    assert "Novelty assessment" in generated_section
    assert "Validation status" in generated_section
    assert "Developability triage" in generated_section
    assert "Explanation:" in generated_section
    forbidden_generated_phrases = (
        " active",
        " cure",
        " treats",
        " binds",
        " inhibits",
        " activates",
    )
    lowered_generated_section = generated_section.lower()
    assert not any(phrase in lowered_generated_section for phrase in forbidden_generated_phrases)
    assert "in-silico research hypotheses only" in report
    assert "GEN-MAOB-0001" in report
    assert "selfies_mutation" in report
    assert "Parent seed molecule(s) | Evidence-backed candidate" in report
    assert "No invented evidence is attached to generated molecules." in report
    assert "| Disease-target relevance | 0.800 |" in report
    assert "Known indications and warnings" in report
    assert "Parkinson Disease" in report
    assert "Black Box Warning" in report
    assert "record_id=warn-1" in report
    assert "## Targets Considered" in report
    assert "## Pipeline Trace" in report
    assert "ReportWriterAgent" in report
    assert "Generated molecule hypotheses are opt-in" in report
    assert "## Literature Evidence" in report
    assert "Literature evidence is absent" in report
    assert "Record-level evidence provenance" in report
    assert "response_mode=cached-real-data" in report
    assert "cache_key=cache-test-key" in report
    assert "fixture" not in report.lower()

    developability_report = (output_dir / "developability_report.md").read_text()
    assert "## Developability Summary" in developability_report
    assert "Assessed existing molecules: 1" in developability_report
    assert "Assessed generated molecules: 2" in developability_report
    assert "Test toxicophore-like alert [CRITICAL]" in developability_report
    rejection_reason = (
        "Rejection/deprioritization reason: "
        "developability_filter_failed, rdkit_parse_failed"
    )
    assert rejection_reason in developability_report
    assert "Docking score does not prove binding." in developability_report
    assert "Docking scores are weak computational heuristics and do not prove binding." in (
        developability_report
    )
    forbidden_developability_phrases = (
        "synthesis route",
        "synthetic route",
        "retrosynthesis route",
        "add reagent",
        "stir at",
        "reflux",
        "reaction temperature",
        "laboratory protocol",
    )
    lowered_developability_report = developability_report.lower()
    assert not any(
        phrase in lowered_developability_report
        for phrase in forbidden_developability_phrases
    )


def test_report_writer_failed_run_does_not_create_success_report(tmp_path):
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=Disease(
            input_name="Parkinson disease",
            canonical_name="Parkinson disease",
            synonyms=[],
            identifiers={"open_targets": "MONDO_0005180"},
            description=None,
        ),
        targets=[],
        candidates=[],
        config={"results_dir": str(tmp_path)},
        output_dir=tmp_path / "parkinson-disease",
    )

    with pytest.raises(NoCandidatesFoundError):
        ReportWriterAgent().run(context)

    assert not (tmp_path / "parkinson-disease" / "report.md").exists()
    assert not (tmp_path / "parkinson-disease" / "candidates.json").exists()
    assert not (tmp_path / "parkinson-disease" / "trace.json").exists()
    assert not (tmp_path / "parkinson-disease" / "generated_molecules.json").exists()
    assert not (tmp_path / "parkinson-disease" / "generated_candidates.json").exists()
    assert not (tmp_path / "parkinson-disease" / "generation_trace.json").exists()
    assert not (tmp_path / "parkinson-disease" / "developability_report.md").exists()
    assert not (tmp_path / "parkinson-disease" / "developability_assessments.json").exists()
    assert not (tmp_path / "parkinson-disease" / "developability.json").exists()


def test_report_writer_does_not_create_generation_artifacts_when_generation_disabled(
    tmp_path,
):
    context = _scored_context(tmp_path)
    context.generated_candidates = []
    context.config.pop("generation_run")
    context.config["enable_generation"] = False
    context.config["ranker_config"]["enable_generation"] = False
    context.traces = [trace for trace in context.traces if trace.agent_name != "NovelMoleculeAgent"]

    ReportWriterAgent().run(context)

    output_dir = tmp_path / "parkinson-disease"
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trace.json").exists()
    assert (output_dir / "developability_report.md").exists()
    assert (output_dir / "developability_assessments.json").exists()
    assert (output_dir / "developability.json").exists()
    assert not (output_dir / "generated_candidates.json").exists()
    assert not (output_dir / "generation_trace.json").exists()


def test_report_includes_expert_review_workflow_when_enabled(tmp_path):
    context = _scored_context(tmp_path)
    assert context.disease is not None
    workspace = build_review_workspace(
        RankingRun(
            disease=context.disease,
            targets=context.targets,
            candidates=context.candidates,
            generated_candidates=context.generated_candidates,
            traces=context.traces,
        ),
        config={"run_id": "run-review"},
        reviewer=Reviewer(
            reviewer_id="expert-1",
            name="Local Reviewer",
            role="medicinal_chemist",
        ),
    )
    existing_item = workspace.review_items[0]
    reviewer = Reviewer(reviewer_id="expert-1", name="Local Reviewer", role="medicinal_chemist")
    ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=existing_item.review_item_id,
        reviewer=reviewer,
        decision="needs_more_data",
        rationale="Review disease-specific evidence separately from scientific evidence.",
        confidence=0.7,
        decision_factors=["weak_literature"],
    )
    ReviewDecisionEngine().add_comment(
        workspace,
        review_item_id=existing_item.review_item_id,
        reviewer=reviewer,
        comment_text="Expert comment remains separate from evidence.",
        comment_type="evidence_question",
    )
    workspace.followup_requests.append(
        FollowupRequest(
            review_item_id=existing_item.review_item_id,
            requested_by=reviewer,
            request_type="rerun_with_more_literature",
            request_text="Repeat literature review with disease aliases.",
            priority="high",
            status="open",
        )
    )
    handoff = create_validation_handoff(
        workspace,
        review_item_id=existing_item.review_item_id,
        evidence_packet_paths={"dossier": "dossier.md"},
    )
    workspace.metadata["validation_handoffs"] = [handoff.model_dump(mode="json")]
    db_path = tmp_path / "review.sqlite"
    ReviewWorkspaceStore(db_path).create_workspace(workspace)
    output_dir = tmp_path / "parkinson-disease"
    output_dir.mkdir()
    queue_path = output_dir / "review_queue.json"
    queue_path.write_text(workspace.model_dump_json())
    context.config.update(
        {
            "enable_review_workflow": True,
            "review_workflow_enabled": True,
            "review_workspace_id": workspace.workspace_id,
            "review_db_path": str(db_path),
            "review_queue_json": str(queue_path),
            "review_dashboard_path": str(output_dir / "review_dashboard" / "index.html"),
            "reviewer_id": "expert-1",
            "reviewer_name": "Local Reviewer",
            "reviewer_role": "medicinal_chemist",
            "review_queue_summary": {
                "review_item_count": len(workspace.review_items),
                "priority_distribution": {"high_priority": 1, "medium_priority": 1},
                "status_distribution": {"pending": 1, "needs_more_data": 1},
            },
        }
    )

    ReportWriterAgent().run(context)

    report = (output_dir / "report.md").read_text()
    assert "## Expert Review Workflow" in report
    assert "Review workflow enabled: yes" in report
    assert f"Workspace ID: {workspace.workspace_id}" in report
    assert f"Review DB path: {db_path}" in report
    assert f"Review queue JSON: {queue_path}" in report
    assert "Dashboard: " in report
    assert "Reviewer ID: expert-1" in report
    assert "Review item count: 2" in report
    assert "high_priority: 1" in report
    assert "needs_more_data: 1" in report
    assert "Latest reviewer decisions" in report
    assert "needs_more_data by expert-1" in report
    candidate_section = report.split("### 1. Evidence-backed candidate", 1)[1].split(
        "## Targets Considered",
        1,
    )[0]
    assert "Review status:" in candidate_section
    assert "Reviewer decisions:" in candidate_section
    assert "Review decision evidence boundary:" in candidate_section
    assert "Reviewer comments summary:" in candidate_section
    assert "Follow-up requests:" in candidate_section
    assert "Validation handoff availability: available" in candidate_section
    evidence_section = candidate_section.split("Reviewer decisions:", 1)[0]
    assert "needs_more_data" not in evidence_section


def test_report_review_workflow_says_disabled_when_disabled(tmp_path):
    context = _scored_context(tmp_path)
    context.config["enable_review_workflow"] = False
    context.config["review_workflow_enabled"] = False

    ReportWriterAgent().run(context)

    report = (tmp_path / "parkinson-disease" / "report.md").read_text()
    assert "## Expert Review Workflow" in report
    assert "Review workflow enabled: no" in report


def test_generated_report_review_info_retains_no_direct_evidence_warning(tmp_path):
    context = _scored_context(tmp_path)
    assert context.disease is not None
    workspace = build_review_workspace(
        RankingRun(
            disease=context.disease,
            targets=context.targets,
            candidates=context.candidates,
            generated_candidates=context.generated_candidates,
            traces=context.traces,
        ),
        config={"run_id": "run-generated-review"},
    )
    generated_item = next(
        item for item in workspace.review_items if item.candidate_origin == "generated"
    )
    ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=generated_item.review_item_id,
        reviewer=Reviewer(reviewer_id="expert-2"),
        decision="hold",
        rationale="Generated hypothesis needs expert chemistry review.",
        confidence=0.5,
    )
    db_path = tmp_path / "review.sqlite"
    ReviewWorkspaceStore(db_path).create_workspace(workspace)
    context.config.update(
        {
            "enable_review_workflow": True,
            "review_workflow_enabled": True,
            "review_workspace_id": workspace.workspace_id,
            "review_db_path": str(db_path),
            "review_queue_summary": {
                "review_item_count": len(workspace.review_items),
                "priority_distribution": {"medium_priority": 1, "reject_suggested": 1},
                "status_distribution": {"pending": 1, "needs_more_data": 1},
            },
        }
    )

    ReportWriterAgent().run(context)

    report = (tmp_path / "parkinson-disease" / "report.md").read_text()
    generated_section = report.split("## Generated Molecule Hypotheses", 1)[1].split(
        "## Ranked Candidates",
        1,
    )[0]
    assert "Generated hypothesis; no direct experimental evidence" in generated_section
    assert "Review priority bucket:" in generated_section
    assert "Expert decision: hold" in generated_section


def test_report_writer_developability_artifact_marks_disabled(tmp_path):
    context = _scored_context(tmp_path)
    context.config["enable_developability"] = False
    context.config["developability_run"] = DevelopabilityRun(
        enabled=False,
        assessed_existing_count=0,
        assessed_generated_count=0,
        retained_count=0,
        deprioritized_count=0,
        rejected_count=0,
        assessments=[],
        warnings=["Developability assessment disabled by configuration."],
        metadata={"enabled": False},
    )

    ReportWriterAgent().run(context)

    output_dir = tmp_path / "parkinson-disease"
    payload = json.loads((output_dir / "developability.json").read_text())
    assert payload["success"] is False
    assert payload["enabled"] is False
    assert payload["warnings"] == ["Developability assessment disabled by configuration."]
