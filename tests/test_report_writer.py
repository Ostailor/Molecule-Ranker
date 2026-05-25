from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.report_writer import ReportWriterAgent
from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    GenerationObjective,
    GenerationRun,
    NoveltyAssessment,
    SeedMolecule,
)
from molecule_ranker.schemas import (
    AgentTrace,
    Disease,
    EvidenceItem,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
    ScoreBreakdown,
    Target,
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


def _generated_molecule(
    generated_id: str,
    *,
    rejected: bool = False,
) -> GeneratedMolecule:
    validation = ChemicalValidationResult(
        valid_rdkit_mol=not rejected,
        sanitization_ok=not rejected,
        canonicalization_ok=not rejected,
        allowed_elements_ok=True,
        descriptor_bounds_ok=True,
        pains_or_alerts=[],
        rejection_reasons=["rdkit_parse_failed"] if rejected else [],
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
        warnings=["in_silico_hypothesis_only"],
        metadata={"operation": "mutation"},
    )


def _generation_run() -> GenerationRun:
    retained = _generated_molecule("GEN-MAOB-0001")
    rejected = _generated_molecule("GEN-MAOB-REJECTED", rejected=True)
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
                },
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
            )
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
            "generation_run": _generation_run(),
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

    candidates_payload = json.loads((output_dir / "candidates.json").read_text())
    assert candidates_payload["success"] is True
    assert (
        candidates_payload["candidates"][0]["score_breakdown"]["final_score"]
        == pytest.approx(0.835)
    )
    assert candidates_payload["generated_molecule_hypotheses"][0]["name"] == "GEN-MAOB-0001"
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
    assert generated_payload["rejected_generated_molecules"][0]["rejection_reasons"] == [
        "rdkit_parse_failed"
    ]
    assert generated_payload["generation_config"]["generation_method"] == "selfies_mutation"
    assert "synthesis" not in json.dumps(generated_payload).lower()

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

    trace_payload = json.loads((output_dir / "trace.json").read_text())
    assert trace_payload["traces"][-1]["agent_name"] == "ReportWriterAgent"
    assert trace_payload["artifacts"]["report_md"].endswith("report.md")
    assert trace_payload["artifacts"]["generated_molecules_json"].endswith(
        "generated_molecules.json"
    )
    assert trace_payload["artifacts"]["generated_candidates_json"].endswith(
        "generated_candidates.json"
    )
    assert trace_payload["artifacts"]["generation_trace_json"].endswith(
        "generation_trace.json"
    )

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


def test_report_writer_does_not_create_generation_artifacts_when_generation_disabled(
    tmp_path,
):
    context = _scored_context(tmp_path)
    context.generated_candidates = []
    context.config.pop("generation_run")
    context.config["enable_generation"] = False
    context.config["ranker_config"]["enable_generation"] = False
    context.traces = [
        trace for trace in context.traces if trace.agent_name != "NovelMoleculeAgent"
    ]

    ReportWriterAgent().run(context)

    output_dir = tmp_path / "parkinson-disease"
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trace.json").exists()
    assert not (output_dir / "generated_candidates.json").exists()
    assert not (output_dir / "generation_trace.json").exists()
