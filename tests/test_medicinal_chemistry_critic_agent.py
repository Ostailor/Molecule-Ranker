from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.medicinal_chemistry_critic import (
    MedicinalChemistryCriticAgent,
)
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GenerationObjective,
    GenerationRun,
    NoveltyAssessment,
    SeedMolecule,
)
from molecule_ranker.schemas import DevelopabilityAssessment


class FakeCodexCriticProvider:
    def __init__(self, output_json: dict[str, Any]) -> None:
        self.output_json = output_json
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        now = datetime.now(UTC)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=json.dumps(self.output_json, sort_keys=True),
            output_json=self.output_json,
            stdout=json.dumps(self.output_json, sort_keys=True),
            stderr="",
            return_code=0,
            started_at=now,
            completed_at=now,
        )


def _objective() -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        seed_molecule_names=["Seed A"],
        seed_molecule_ids=["CHEMBL_A"],
        objective_type="target_conditioned_analog_generation",
        metadata={"target_relevance_score": 0.8},
    )


def _seed() -> SeedMolecule:
    return SeedMolecule(
        name="Seed A",
        canonical_smiles="CCOc1ccccc1",
        identifiers={"chembl": "CHEMBL_A"},
        known_targets=["MAOB"],
        source_candidate_name="Seed A",
        evidence_count=2,
        best_evidence_confidence=0.9,
        target_relevance_score=0.8,
        seed_selection_reason="Retrieved molecule-target evidence.",
        metadata={"scaffold_id": "seed-scaffold"},
    )


def _developability(score: float = 0.75, risk_level: str = "low") -> DevelopabilityAssessment:
    return DevelopabilityAssessment(
        molecule_name="gen-1",
        origin="generated",
        structure_available=True,
        canonical_smiles="CCOc1ccccn1",
        developability_score=score,
        triage_recommendation="high_risk_flags"
        if risk_level == "critical"
        else "favorable_hypothesis",
        metadata={"risk_level": risk_level},
    )


def _generated(
    *,
    validation: ChemicalValidationResult | None = None,
    generation_score: float = 0.65,
    developability: DevelopabilityAssessment | None = None,
    metadata: dict[str, Any] | None = None,
) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id="gen-1",
        smiles="CCOc1ccccn1",
        canonical_smiles="CCOc1ccccn1",
        generation_method="fragment_grower",
        parent_seed_ids=["CHEMBL_A"],
        conditioned_targets=["MAOB"],
        objective_id="objective-1",
        generation_round=1,
        descriptors={"molecular_weight": 200.0, "logp": 2.0, "tpsa": 30.0},
        fingerprints={},
        validation=validation
        or ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
        ),
        novelty=NoveltyAssessment(
            duplicate_of_existing=False,
            duplicate_of_generated=False,
            max_similarity_to_existing=0.4,
            max_similarity_to_seed=0.62,
            novelty_class="novel_analog",
        ),
        generation_score=generation_score,
        developability_assessment=developability or _developability(),
        warnings=["in_silico_hypothesis_only"],
        metadata=metadata or {},
    )


def _run(candidate: GeneratedMolecule) -> GenerationRun:
    return GenerationRun(
        objectives=[_objective()],
        seeds=[_seed()],
        generated=[candidate],
        retained=[candidate],
        rejected=[],
    )


def test_rule_based_critique_flags_alerts() -> None:
    candidate = _generated(
        validation=ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
            pains_or_alerts=["nitro_group"],
        )
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        config={"generation_run": _run(candidate)},
    )

    result = MedicinalChemistryCriticAgent().run(context)

    critique = result.config["generation_run"].retained[0].metadata[
        "medicinal_chemistry_critique"
    ]
    assert critique["molecule_id"] == "gen-1"
    assert any("alert" in concern.lower() for concern in critique["concerns"])
    assert "structural_alert_flags" in critique["likely_artifacts"]
    assert critique["codex_task_result_id"] is None


def test_codex_critique_stored_separately() -> None:
    provider = FakeCodexCriticProvider(
        {
            "critiques": [
                {
                    "molecule_id": "gen-1",
                    "positives": ["Grounded structure metadata is inspectable."],
                    "concerns": ["Review scaffold novelty context."],
                    "required_checks": ["Expert review of source artifacts."],
                    "metadata": {"codex_note": "artifact grounded"},
                }
            ]
        }
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        config={"generation_run": _run(_generated()), "enable_codex_medchem_critique": True},
    )

    result = MedicinalChemistryCriticAgent(provider=provider).run(context)

    critique = result.config["generation_run"].retained[0].metadata[
        "medicinal_chemistry_critique"
    ]
    assert provider.tasks
    assert provider.tasks[0].allowed_commands == []
    assert critique["codex_task_result_id"] == "codex-medchem-critique"
    assert critique["metadata"]["codex_critique"]["concerns"] == [
        "Review scaffold novelty context."
    ]
    assert "codex_critique" in critique["metadata"]
    assert "Review scaffold novelty context." not in critique["concerns"]


def test_unsafe_codex_critique_rejected() -> None:
    provider = FakeCodexCriticProvider(
        {
            "critiques": [
                {
                    "molecule_id": "gen-1",
                    "positives": ["This molecule binds the target."],
                    "concerns": ["Use synthesis routes and a lab protocol."],
                }
            ]
        }
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        config={"generation_run": _run(_generated()), "enable_codex_medchem_critique": True},
    )

    result = MedicinalChemistryCriticAgent(provider=provider).run(context)

    critique = result.config["generation_run"].retained[0].metadata[
        "medicinal_chemistry_critique"
    ]
    assert critique["codex_task_result_id"] is None
    assert "codex_critique" not in critique["metadata"]
    assert critique["metadata"]["codex_rejected"] is True


def test_recommended_action_follows_risk_policy() -> None:
    high_score_risky = _generated(
        generation_score=0.91,
        developability=_developability(0.9, "critical"),
        metadata={
            "oracle_scoring": {"experiment_worthiness_score": 0.91},
            "uncertainty": {"applicability_domain": "out_of_domain"},
        },
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        config={"generation_run": _run(high_score_risky)},
    )

    result = MedicinalChemistryCriticAgent().run(context)

    critique = result.config["generation_run"].retained[0].metadata[
        "medicinal_chemistry_critique"
    ]
    assert critique["recommended_action"] == "reject"
    assert any("critical" in concern.lower() for concern in critique["concerns"])
    assert any("high score" in concern.lower() for concern in critique["concerns"])
