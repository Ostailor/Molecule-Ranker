from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.codex import create_llm_provider
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.generation.schemas import GeneratedMolecule, GenerationRun, SeedMolecule

RecommendedAction = Literal[
    "retain_for_review",
    "deprioritize",
    "reject",
    "needs_expert_review",
]


class MedChemCritique(BaseModel):
    molecule_id: str
    positives: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    likely_artifacts: list[str] = Field(default_factory=list)
    novelty_comment: str
    developability_comment: str
    risk_comment: str
    recommended_action: RecommendedAction
    confidence: float = Field(ge=0.0, le=1.0)
    codex_task_result_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MedChemCriticProvider(Protocol):
    def run_task(self, task: CodexTask) -> CodexTaskResult:
        """Run an optional grounded Codex critique task."""
        ...


UNSAFE_CRITIQUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(cures?|treats?|prevents?)\b", re.I), "therapeutic claim"),
    (re.compile(r"\bbinds?\b|\bbinding\b", re.I), "binding/activity claim"),
    (re.compile(r"\b(active|inactive|potent|inhibits?|activates?)\b", re.I), "activity claim"),
    (re.compile(r"\b(is|are|was|were)\s+safe\b", re.I), "safety claim"),
    (re.compile(r"\bsynthesi[sz](able|ability)\b", re.I), "synthesizability claim"),
    (re.compile(r"\bsynthesis\s+routes?\b", re.I), "synthesis route"),
    (re.compile(r"\breagents?\b|\breaction\s+conditions?\b", re.I), "reaction detail"),
    (re.compile(r"\blab\s+protocols?\b|\bprotocol\b", re.I), "lab protocol"),
    (re.compile(r"\bdos(e|ing)\b|\bpatient\b|\banimal\s+study\b", re.I), "medical protocol"),
)


class MedicinalChemistryCriticAgent(BaseAgent):
    """Rule-based and optional Codex-assisted critique for generated hypotheses."""

    name = "MedicinalChemistryCriticAgent"

    def __init__(
        self,
        provider: MedChemCriticProvider | None = None,
        *,
        working_directory: str | Path = ".",
    ) -> None:
        super().__init__()
        self.provider = provider
        self.working_directory = Path(working_directory)
        self._last_critiques: list[MedChemCritique] = []
        self._last_warning: str | None = None

    def process(self, context: PipelineContext) -> PipelineContext:
        self._last_critiques = []
        self._last_warning = None
        run = context.config.get("generation_run")
        if not isinstance(run, GenerationRun):
            self._last_warning = "No GenerationRun available for medicinal chemistry critique."
            return context

        codex_by_id: dict[str, dict[str, Any]] = {}
        codex_task_result_id: str | None = None
        codex_rejection: dict[str, Any] | None = None
        if bool(context.config.get("enable_codex_medchem_critique", False)):
            codex_by_id, codex_task_result_id, codex_rejection = self._codex_critiques(run)

        retained = self._critique_candidates(
            candidates=run.retained,
            run=run,
            codex_by_id=codex_by_id,
            codex_task_result_id=codex_task_result_id,
            codex_rejection=codex_rejection,
        )
        retained_by_id = {candidate.generated_id: candidate for candidate in retained}
        generated = [
            retained_by_id.get(candidate.generated_id, candidate) for candidate in run.generated
        ]
        updated_run = run.model_copy(
            update={
                "generated": generated,
                "retained": retained,
                "metadata": {
                    **run.metadata,
                    "medicinal_chemistry_critic_agent": {
                        "reviewed_count": len(retained),
                        "rule_based_always_run": True,
                        "codex_enabled": bool(
                            context.config.get("enable_codex_medchem_critique", False)
                        ),
                        "claim_boundary": "critique only; no activity, safety, or synthesis claims",
                    },
                },
            }
        )
        context.config["generation_run"] = updated_run
        context.config["generated_molecules"] = updated_run.retained
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if self._last_warning:
            return self._last_warning
        return f"Critiqued {len(self._last_critiques)} generated molecule hypotheses."

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        return {
            "reviewed_count": len(self._last_critiques),
            "recommended_actions": {
                critique.molecule_id: critique.recommended_action
                for critique in self._last_critiques
            },
            "rule_based_always_run": True,
            "claim_boundary": "no activity, safety, synthesis route, or protocol claims",
            **({"warning": self._last_warning} if self._last_warning else {}),
        }

    def critique_state(
        self,
        *,
        generated: list[GeneratedMolecule],
        objectives: list[Any],
        seeds: list[SeedMolecule],
    ) -> list[MedChemCritique]:
        run = GenerationRun(
            objectives=objectives,
            seeds=seeds,
            generated=generated,
            retained=generated,
        )
        return [
            self._rule_based_critique(candidate, run).model_copy()
            for candidate in generated
        ]

    def _critique_candidates(
        self,
        *,
        candidates: list[GeneratedMolecule],
        run: GenerationRun,
        codex_by_id: dict[str, dict[str, Any]],
        codex_task_result_id: str | None,
        codex_rejection: dict[str, Any] | None,
    ) -> list[GeneratedMolecule]:
        updated: list[GeneratedMolecule] = []
        for candidate in candidates:
            critique = self._rule_based_critique(candidate, run)
            codex_payload = codex_by_id.get(candidate.generated_id)
            if codex_payload is not None:
                critique = critique.model_copy(
                    update={
                        "codex_task_result_id": codex_task_result_id,
                        "metadata": {
                            **critique.metadata,
                            "codex_critique": codex_payload,
                        },
                    }
                )
            elif codex_rejection is not None:
                critique = critique.model_copy(
                    update={
                        "metadata": {
                            **critique.metadata,
                            "codex_rejected": True,
                            "codex_rejection": codex_rejection,
                        }
                    }
                )
            self._last_critiques.append(critique)
            updated.append(
                candidate.model_copy(
                    update={
                        "metadata": {
                            **candidate.metadata,
                            "medicinal_chemistry_critique": critique.model_dump(mode="json"),
                        },
                        "warnings": sorted(
                            {
                                *candidate.warnings,
                                *self._critique_warnings(critique),
                            }
                        ),
                    }
                )
            )
        return updated

    def _rule_based_critique(
        self,
        candidate: GeneratedMolecule,
        run: GenerationRun,
    ) -> MedChemCritique:
        positives = self._positives(candidate)
        concerns = self._concerns(candidate)
        required_checks = self._required_checks(candidate)
        likely_artifacts = self._likely_artifacts(candidate)
        novelty_comment = self._novelty_comment(candidate)
        developability_comment = self._developability_comment(candidate)
        risk_comment = self._risk_comment(candidate)
        recommended_action = self._recommended_action(candidate, concerns)
        if self._high_score_with_obvious_risk(candidate, concerns):
            concerns.append(
                "High score with obvious medicinal chemistry risk requires explicit review."
            )
            if recommended_action == "retain_for_review":
                recommended_action = "needs_expert_review"
        confidence = self._confidence(candidate, concerns)
        return MedChemCritique(
            molecule_id=candidate.generated_id,
            positives=positives,
            concerns=sorted(set(concerns)),
            required_checks=sorted(set(required_checks)),
            likely_artifacts=sorted(set(likely_artifacts)),
            novelty_comment=novelty_comment,
            developability_comment=developability_comment,
            risk_comment=risk_comment,
            recommended_action=recommended_action,
            confidence=round(confidence, 3),
            codex_task_result_id=None,
            metadata={
                "rule_based": True,
                "codex_enabled": False,
                "objective_id": candidate.objective_id,
                "parent_seed_ids": list(candidate.parent_seed_ids),
                "seed_scaffold_provenance": self._seed_scaffold_provenance(candidate, run),
                "no_activity_or_safety_claims": True,
                "no_synthesis_routes_or_protocols": True,
            },
        )

    def _positives(self, candidate: GeneratedMolecule) -> list[str]:
        positives: list[str] = []
        if candidate.validation.valid_rdkit_mol and candidate.validation.sanitization_ok:
            positives.append("Structure passed deterministic RDKit validity checks.")
        if candidate.novelty is not None and candidate.novelty.novelty_class in {
            "close_analog",
            "novel_analog",
        }:
            positives.append("Novelty assessment indicates non-duplicate structural context.")
        if candidate.parent_seed_ids:
            positives.append("Parent seed provenance is recorded.")
        if candidate.metadata.get("oracle_scoring"):
            positives.append("Oracle scoring artifact is available for inspection.")
        return positives or ["Generated hypothesis is traceable to deterministic artifacts."]

    def _concerns(self, candidate: GeneratedMolecule) -> list[str]:
        concerns: list[str] = []
        if candidate.validation.pains_or_alerts:
            concerns.append(
                "Structural alert flags require medicinal chemistry review: "
                + ", ".join(candidate.validation.pains_or_alerts)
            )
        if candidate.validation.rejection_reasons:
            concerns.append(
                "Validation rejection reasons are present: "
                + ", ".join(candidate.validation.rejection_reasons)
            )
        assessment = candidate.developability_assessment
        risk_level = ""
        if assessment is not None:
            risk_level = str(assessment.metadata.get("risk_level") or "").lower()
            if risk_level in {"critical", "high"}:
                concerns.append(f"Developability assessment has {risk_level} risk.")
            if assessment.developability_score < 0.35:
                concerns.append("Developability score is low.")
        uncertainty = self._metadata_mapping(candidate, "uncertainty")
        if uncertainty.get("applicability_domain") == "out_of_domain":
            concerns.append("Applicability domain is out_of_domain.")
        if uncertainty.get("uncertainty_class") == "uncontrolled_risk":
            concerns.append("Uncertainty assessment marks uncontrolled risk.")
        return concerns

    def _required_checks(self, candidate: GeneratedMolecule) -> list[str]:
        checks = [
            "Confirm generated-hypothesis label and absence of direct evidence claims.",
            "Review source seed, scaffold, oracle, and uncertainty artifacts.",
        ]
        if candidate.validation.pains_or_alerts:
            checks.append("Expert review of structural alert flags.")
        if candidate.validation.rejection_reasons:
            checks.append("Review deterministic validation rejection reasons.")
        if candidate.developability_assessment is None:
            checks.append("Run deterministic developability assessment before retention.")
        return checks

    def _likely_artifacts(self, candidate: GeneratedMolecule) -> list[str]:
        artifacts = ["generated_structure_record", "seed_scaffold_provenance"]
        if candidate.validation.pains_or_alerts:
            artifacts.append("structural_alert_flags")
        if candidate.validation.rejection_reasons:
            artifacts.append("validation_rejection_reasons")
        if candidate.metadata.get("oracle_scoring"):
            artifacts.append("oracle_scoring")
        if candidate.metadata.get("uncertainty"):
            artifacts.append("uncertainty_assessment")
        if candidate.developability_assessment is not None:
            artifacts.append("developability_assessment")
        return artifacts

    def _novelty_comment(self, candidate: GeneratedMolecule) -> str:
        if candidate.novelty is None:
            return "Novelty was not assessed; require duplicate and similarity review."
        return (
            f"Novelty class is {candidate.novelty.novelty_class}; this is structural "
            "context only and not a claim of biological value."
        )

    def _developability_comment(self, candidate: GeneratedMolecule) -> str:
        assessment = candidate.developability_assessment
        if assessment is None:
            return "No developability assessment is attached."
        risk_level = str(assessment.metadata.get("risk_level") or "unknown")
        return (
            f"Developability score is {assessment.developability_score:.3f} with "
            f"{risk_level} risk metadata; this is heuristic triage, not a safety claim."
        )

    def _risk_comment(self, candidate: GeneratedMolecule) -> str:
        risks = []
        risks.extend(candidate.validation.rejection_reasons)
        risks.extend([f"alert:{alert}" for alert in candidate.validation.pains_or_alerts])
        uncertainty = self._metadata_mapping(candidate, "uncertainty")
        if uncertainty.get("applicability_domain"):
            risks.append(f"domain:{uncertainty['applicability_domain']}")
        if not risks:
            return "No deterministic high-risk critique flags were found."
        return "Deterministic critique flags: " + ", ".join(str(item) for item in risks)

    def _recommended_action(
        self,
        candidate: GeneratedMolecule,
        concerns: list[str],
    ) -> RecommendedAction:
        if (
            not candidate.validation.valid_rdkit_mol
            or candidate.validation.rejection_reasons
            or self._critical_developability(candidate)
        ):
            return "reject"
        uncertainty = self._metadata_mapping(candidate, "uncertainty")
        if uncertainty.get("uncertainty_class") == "uncontrolled_risk":
            return "deprioritize"
        if candidate.validation.pains_or_alerts:
            return "needs_expert_review"
        if concerns:
            return "deprioritize"
        return "retain_for_review"

    def _high_score_with_obvious_risk(
        self,
        candidate: GeneratedMolecule,
        concerns: list[str],
    ) -> bool:
        score = candidate.generation_score
        oracle = self._metadata_mapping(candidate, "oracle_scoring")
        if isinstance(oracle.get("experiment_worthiness_score"), (int, float)):
            score = float(oracle["experiment_worthiness_score"])
        return bool(score is not None and score >= 0.75 and concerns)

    def _confidence(self, candidate: GeneratedMolecule, concerns: list[str]) -> float:
        confidence = 0.75
        if candidate.validation.rejection_reasons:
            confidence -= 0.25
        if candidate.validation.pains_or_alerts:
            confidence -= 0.15
        if self._critical_developability(candidate):
            confidence -= 0.20
        confidence -= min(0.20, 0.04 * len(concerns))
        return max(0.0, min(confidence, 1.0))

    def _critical_developability(self, candidate: GeneratedMolecule) -> bool:
        assessment = candidate.developability_assessment
        if assessment is None:
            return False
        risk_level = str(assessment.metadata.get("risk_level") or "").lower()
        return risk_level == "critical" or assessment.triage_recommendation == "high_risk_flags"

    def _seed_scaffold_provenance(
        self,
        candidate: GeneratedMolecule,
        run: GenerationRun,
    ) -> dict[str, Any]:
        seeds_by_id = {self._seed_id(seed): seed for seed in run.seeds}
        parent_seeds = [
            seeds_by_id[seed_id] for seed_id in candidate.parent_seed_ids if seed_id in seeds_by_id
        ]
        return {
            "parent_seed_ids": list(candidate.parent_seed_ids),
            "parent_seed_names": [seed.name for seed in parent_seeds],
            "seed_scaffold_ids": [
                seed.metadata.get("scaffold_id")
                for seed in parent_seeds
                if seed.metadata.get("scaffold_id")
            ],
            "candidate_scaffold_id": candidate.metadata.get("scaffold_id"),
        }

    def _codex_critiques(
        self,
        run: GenerationRun,
    ) -> tuple[dict[str, dict[str, Any]], str | None, dict[str, Any] | None]:
        task = self._build_codex_task(run)
        provider = self.provider or create_llm_provider(
            {
                "enable_codex_backbone": True,
                "codex_working_dir": self.working_directory,
                "codex_require_json": True,
                "codex_allow_shell_commands": False,
            }
        )
        result = provider.run_task(task)
        if result.status != "succeeded" or result.output_json is None:
            return {}, None, {"reason": "codex_task_failed", "status": result.status}
        unsafe = self._unsafe_reason(result.output_json)
        if unsafe is not None:
            return {}, None, {"reason": unsafe, "task_id": result.task_id}
        try:
            critiques = self._codex_payloads_by_id(result.output_json, run)
        except ValueError as exc:
            return {}, None, {"reason": str(exc), "task_id": result.task_id}
        return critiques, result.task_id, None

    def _build_codex_task(self, run: GenerationRun) -> CodexTask:
        payload = {
            "task": "medicinal_chemistry_critique",
            "instructions": [
                "Return JSON with a critiques array keyed by molecule_id.",
                "Ground every critique in the supplied generated molecules and artifacts.",
                "Do not claim activity, binding, efficacy, safety, or synthesizability.",
                "Do not include synthesis routes, reaction details, lab protocols, dosing, "
                "animal instructions, or patient guidance.",
                "Codex critique is advisory text only; rule-based critique remains primary.",
            ],
            "allowed_molecule_ids": [candidate.generated_id for candidate in run.retained],
            "artifacts": {
                "objectives": [item.model_dump(mode="json") for item in run.objectives],
                "seeds": [item.model_dump(mode="json") for item in run.seeds],
                "generated": [item.model_dump(mode="json") for item in run.retained],
            },
        }
        return CodexTask(
            task_id="codex-medchem-critique",
            task_type="inspect_artifacts",
            prompt=json.dumps(payload, indent=2, sort_keys=True),
            working_directory=str(self.working_directory),
            input_artifact_paths=[],
            allowed_commands=[],
            forbidden_commands=[],
            expected_output_format="json",
            timeout_seconds=300,
            require_json=True,
            metadata={"mode": "medchem_critique_v1_1", "no_shell_commands": True},
        )

    def _codex_payloads_by_id(
        self,
        output_json: dict[str, Any],
        run: GenerationRun,
    ) -> dict[str, dict[str, Any]]:
        raw_critiques = output_json.get("critiques")
        if not isinstance(raw_critiques, list):
            raise ValueError("codex_output_missing_critiques")
        allowed = {candidate.generated_id for candidate in run.retained}
        by_id: dict[str, dict[str, Any]] = {}
        for item in raw_critiques:
            if not isinstance(item, Mapping):
                continue
            molecule_id = str(item.get("molecule_id") or "")
            if molecule_id not in allowed:
                raise ValueError(f"codex_output_unknown_molecule:{molecule_id}")
            by_id[molecule_id] = {
                "molecule_id": molecule_id,
                "positives": self._string_list(item.get("positives")),
                "concerns": self._string_list(item.get("concerns")),
                "required_checks": self._string_list(item.get("required_checks")),
                "metadata": dict(item.get("metadata") or {})
                if isinstance(item.get("metadata"), Mapping)
                else {},
            }
        return by_id

    def _unsafe_reason(self, value: Any) -> str | None:
        text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
        for pattern, label in UNSAFE_CRITIQUE_PATTERNS:
            if pattern.search(text):
                return f"unsafe_codex_critique:{label}"
        return None

    def _metadata_mapping(
        self,
        candidate: GeneratedMolecule,
        key: str,
    ) -> dict[str, Any]:
        value = candidate.metadata.get(key)
        return dict(value) if isinstance(value, Mapping) else {}

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def _critique_warnings(self, critique: MedChemCritique) -> list[str]:
        warnings = ["medchem_critique_is_not_activity_or_safety_evidence"]
        if critique.recommended_action == "reject":
            warnings.append("medchem_critic_recommends_reject")
        elif critique.recommended_action == "deprioritize":
            warnings.append("medchem_critic_recommends_deprioritize")
        elif critique.recommended_action == "needs_expert_review":
            warnings.append("medchem_critic_requires_expert_review")
        return warnings

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name
