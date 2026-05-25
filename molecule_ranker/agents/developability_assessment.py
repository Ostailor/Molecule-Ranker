from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.developability import DevelopabilityAssessor
from molecule_ranker.developability.admet import predict_rule_based_admet
from molecule_ranker.developability.descriptors import compute_physchem_profile
from molecule_ranker.developability.filters import detect_chemistry_alerts
from molecule_ranker.developability.schemas import (
    ADMETPrediction,
    ChemistryAlert,
    DevelopabilityRun,
)
from molecule_ranker.developability.schemas import (
    DevelopabilityAssessment as StructuredDevelopabilityAssessment,
)
from molecule_ranker.developability.scoring import score_developability
from molecule_ranker.developability.synthesizability import assess_synthesizability
from molecule_ranker.generation.chemistry import canonicalize_inchi, canonicalize_smiles
from molecule_ranker.generation.schemas import GeneratedMolecule, GenerationRun
from molecule_ranker.generation.scoring import GeneratedMoleculeScorer
from molecule_ranker.schemas import (
    DevelopabilityAssessment as LegacyDevelopabilityAssessment,
)
from molecule_ranker.schemas import MoleculeCandidate

VALID_FILTER_MODES = {
    "report_only",
    "deprioritize",
    "filter_generated_only",
    "filter_all",
}


class DevelopabilityAssessmentAgent(BaseAgent):
    """Assess V0.4 developability for existing and generated molecule hypotheses."""

    name = "DevelopabilityAssessmentAgent"

    def __init__(self, assessor: DevelopabilityAssessor | None = None) -> None:
        super().__init__()
        self._assessor = assessor or DevelopabilityAssessor()
        self._generation_scorer = GeneratedMoleculeScorer()
        self._last_metadata: dict[str, Any] = {}

    def process(self, context: PipelineContext) -> PipelineContext:
        if not bool(context.config.get("enable_developability", True)):
            self._last_metadata = self._empty_metadata("developability disabled")
            context.config["developability_run"] = DevelopabilityRun(
                enabled=False,
                assessed_existing_count=0,
                assessed_generated_count=0,
                retained_count=0,
                deprioritized_count=0,
                rejected_count=0,
                assessments=[],
                warnings=["Developability assessment disabled by configuration."],
                metadata=self._last_metadata,
            )
            return context

        config = _AgentConfig.from_mapping(context.config)
        assessments: list[StructuredDevelopabilityAssessment] = []
        warnings: list[str] = []
        retained_count = 0
        deprioritized_count = 0
        rejected_count = 0
        assessed_existing_count = 0
        assessed_generated_count = 0

        next_candidates: list[MoleculeCandidate] = []
        for candidate in context.candidates:
            if candidate.origin == "generated":
                next_candidates.append(candidate)
                continue
            if not config.assess_existing_molecules:
                next_candidates.append(candidate)
                continue

            assessed_existing_count += 1
            candidate, assessment, warning = self._assess_candidate(candidate, config)
            if warning is not None:
                warnings.append(warning)
            assessments.append(assessment)

            reject = self._should_reject(assessment, config, generated=False)
            deprioritize = self._should_deprioritize(assessment, reject)
            if reject and config.filter_mode == "filter_all":
                rejected_count += 1
                continue
            if deprioritize and config.filter_mode == "deprioritize":
                deprioritized_count += 1
                candidate = self._warn_candidate(
                    candidate,
                    "Developability computational triage recommends deprioritization.",
                )
            else:
                retained_count += 1
            next_candidates.append(candidate)
        context.candidates = next_candidates

        generation_run = self._generation_run(context)
        generated_assessments_by_id: dict[str, StructuredDevelopabilityAssessment] = {}
        if generation_run is not None and config.assess_generated_molecules:
            (
                generation_run,
                generated_assessments_by_id,
                generated_warnings,
                generated_counts,
            ) = self._assess_generation_run(generation_run, config)
            context.config["generation_run"] = generation_run
            context.config["generated_molecules"] = generation_run.retained
            assessments.extend(generated_assessments_by_id.values())
            warnings.extend(generated_warnings)
            assessed_generated_count += generated_counts["assessed"]
            retained_count += generated_counts["retained"]
            deprioritized_count += generated_counts["deprioritized"]
            rejected_count += generated_counts["rejected"]

        context.generated_candidates = self._update_generated_hypotheses(
            context.generated_candidates,
            generated_assessments_by_id,
            config,
        )

        run = DevelopabilityRun(
            enabled=True,
            assessed_existing_count=assessed_existing_count,
            assessed_generated_count=assessed_generated_count,
            retained_count=retained_count,
            deprioritized_count=deprioritized_count,
            rejected_count=rejected_count,
            assessments=assessments,
            warnings=sorted(set(warnings)),
            metadata=self._run_metadata(
                assessments=assessments,
                config=config,
                assessed_existing_count=assessed_existing_count,
                assessed_generated_count=assessed_generated_count,
                retained_count=retained_count,
                deprioritized_count=deprioritized_count,
                rejected_count=rejected_count,
                warnings=warnings,
            ),
        )
        context.config["developability_run"] = run
        context.config["developability_assessments"] = [
            self._legacy_assessment_from_structured(assessment).model_dump(mode="json")
            for assessment in assessments
        ]
        context.config["developability_assessments_by_molecule_id"] = {
            assessment.molecule_id: assessment.model_dump(mode="json")
            for assessment in assessments
        }
        self._last_metadata = dict(run.metadata)
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        return (
            "Assessed developability for "
            f"{self._last_metadata.get('assessed_existing_count', 0)} existing and "
            f"{self._last_metadata.get('assessed_generated_count', 0)} generated molecules."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        return dict(self._last_metadata)

    def _assess_candidate(
        self,
        candidate: MoleculeCandidate,
        config: _AgentConfig,
    ) -> tuple[MoleculeCandidate, StructuredDevelopabilityAssessment, str | None]:
        molecule_id = candidate.identifiers.get("chembl") or candidate.name
        smiles = _structure_from_mappings(
            candidate.chemical_metadata,
            candidate.identifiers,
            candidate.generation_metadata,
        )
        try:
            assessment = self._build_structured_assessment(
                molecule_id=molecule_id,
                molecule_name=candidate.name,
                origin="existing",
                smiles=smiles,
                descriptors=candidate.chemical_metadata,
                warning_evidence=[*candidate.evidence, *candidate.warnings],
                direct_evidence_available=candidate.direct_evidence_available,
                config=config,
            )
            legacy = self._legacy_assess_mapping(
                name=candidate.name,
                origin=candidate.origin,
                chemical_metadata=candidate.chemical_metadata,
                identifiers=candidate.identifiers,
                descriptors=candidate.chemical_metadata,
                warnings=candidate.warnings,
                config=config,
            )
            warning = None
        except Exception as exc:
            if config.strict_developability:
                raise
            assessment = self._unknown_assessment(
                molecule_id=molecule_id,
                molecule_name=candidate.name,
                origin="existing",
                smiles=smiles,
                reason=str(exc),
            )
            legacy = self._legacy_unknown(candidate.name, "existing", smiles, str(exc))
            warning = f"Developability assessment failed for {candidate.name}: {exc}"

        chemical_metadata = {
            **candidate.chemical_metadata,
            "developability_assessment": assessment.model_dump(mode="json"),
        }
        warnings = list(candidate.warnings)
        if assessment.recommendation in {"deprioritize", "reject", "expert_review_required"}:
            warnings.append(
                "Developability computational triage found risk flags requiring expert review."
            )
        if assessment.risk_level == "unknown":
            warnings.append("Developability computational triage is incomplete or unknown.")
        return (
            candidate.model_copy(
                update={
                    "chemical_metadata": chemical_metadata,
                    "developability_assessment": legacy,
                    "warnings": sorted(set(warnings)),
                }
            ),
            assessment,
            warning,
        )

    def _assess_generation_run(
        self,
        run: GenerationRun,
        config: _AgentConfig,
    ) -> tuple[
        GenerationRun,
        dict[str, StructuredDevelopabilityAssessment],
        list[str],
        dict[str, int],
    ]:
        warnings: list[str] = []
        assessments_by_id: dict[str, StructuredDevelopabilityAssessment] = {}
        retained: list[GeneratedMolecule] = []
        rejected: list[GeneratedMolecule] = []
        assessed_by_id: dict[str, GeneratedMolecule] = {}
        counts = {"assessed": 0, "retained": 0, "deprioritized": 0, "rejected": 0}

        for molecule in run.retained:
            counts["assessed"] += 1
            molecule, assessment, warning = self._assess_generated_molecule(molecule, config)
            if warning is not None:
                warnings.append(warning)
            assessments_by_id[molecule.generated_id] = assessment
            assessed_by_id[molecule.generated_id] = molecule

            reject = self._should_reject(assessment, config, generated=True)
            if (
                assessment.risk_level == "unknown"
                and config.require_developability_for_generated
            ):
                reject = True
            if reject and config.generated_filtering_enabled:
                counts["rejected"] += 1
                rejected_molecule = self._mark_generated_rejected(molecule, assessment)
                rejected.append(rejected_molecule)
                assessed_by_id[molecule.generated_id] = rejected_molecule
                continue
            if (
                self._should_deprioritize(assessment, reject)
                and config.filter_mode == "deprioritize"
            ):
                counts["deprioritized"] += 1
                molecule = self._warn_generated(
                    molecule,
                    "Developability computational triage recommends deprioritization.",
                )
            else:
                counts["retained"] += 1
            retained.append(molecule)
            assessed_by_id[molecule.generated_id] = molecule

        for molecule in run.rejected:
            if molecule.generated_id in assessed_by_id:
                rejected.append(assessed_by_id[molecule.generated_id])
                counts["rejected"] += 1
                continue

            counts["assessed"] += 1
            molecule, assessment, warning = self._assess_generated_molecule(molecule, config)
            if warning is not None:
                warnings.append(warning)
            assessments_by_id[molecule.generated_id] = assessment

            if self._should_reject(assessment, config, generated=True):
                molecule = self._mark_generated_rejected(molecule, assessment)
            rejected.append(molecule)
            assessed_by_id[molecule.generated_id] = molecule
            counts["rejected"] += 1

        generated = [
            assessed_by_id.get(molecule.generated_id, molecule)
            for molecule in run.generated
        ]
        updated_run = run.model_copy(
            update={
                "generated": generated,
                "retained": retained,
                "rejected": rejected,
                "metadata": {
                    **run.metadata,
                    "developability_assessed_generated_count": counts["assessed"],
                    "developability_assessed_retained_count": len(run.retained),
                    "developability_assessed_preexisting_rejected_count": len(run.rejected),
                },
            }
        )
        return updated_run, assessments_by_id, warnings, counts

    def _assess_generated_molecule(
        self,
        molecule: GeneratedMolecule,
        config: _AgentConfig,
    ) -> tuple[GeneratedMolecule, StructuredDevelopabilityAssessment, str | None]:
        try:
            assessment = self._build_structured_assessment(
                molecule_id=molecule.generated_id,
                molecule_name=molecule.generated_id,
                origin="generated",
                smiles=molecule.canonical_smiles,
                descriptors=molecule.descriptors,
                warning_evidence=molecule.warnings,
                direct_evidence_available=False,
                config=config,
            )
            legacy = self._legacy_assess_mapping(
                name=molecule.generated_id,
                origin="generated",
                chemical_metadata={"canonical_smiles": molecule.canonical_smiles},
                identifiers={"inchikey": molecule.inchi_key} if molecule.inchi_key else {},
                descriptors=molecule.descriptors,
                warnings=molecule.warnings,
                config=config,
            )
            warning = None
        except Exception as exc:
            if config.strict_developability:
                raise
            assessment = self._unknown_assessment(
                molecule_id=molecule.generated_id,
                molecule_name=molecule.generated_id,
                origin="generated",
                smiles=molecule.canonical_smiles,
                reason=str(exc),
            )
            legacy = self._legacy_unknown(
                molecule.generated_id,
                "generated",
                molecule.canonical_smiles,
                str(exc),
            )
            warning = f"Developability assessment failed for {molecule.generated_id}: {exc}"

        metadata = {
            **molecule.metadata,
            "developability_assessment": assessment.model_dump(mode="json"),
        }
        warnings = list(molecule.warnings)
        if assessment.recommendation in {"deprioritize", "reject", "expert_review_required"}:
            warnings.append("Generated molecule has developability risk flags for expert review.")
        updated = molecule.model_copy(
            update={
                "metadata": metadata,
                "developability_assessment": legacy,
                "warnings": sorted(set(warnings)),
            }
        )
        updated = self._generation_scorer.apply_developability_modifier(updated)
        return (
            updated,
            assessment,
            warning,
        )

    def _build_structured_assessment(
        self,
        *,
        molecule_id: str,
        molecule_name: str,
        origin: str,
        smiles: str | None,
        descriptors: Mapping[str, Any],
        warning_evidence: Sequence[Any],
        direct_evidence_available: bool,
        config: _AgentConfig,
    ) -> StructuredDevelopabilityAssessment:
        if not smiles:
            raise ValueError("No parseable structure was available for developability triage.")
        profile = compute_physchem_profile(smiles)
        alerts = detect_chemistry_alerts(profile.canonical_smiles)
        admet_predictions = (
            predict_rule_based_admet(
                profile,
                alerts,
                origin,
                warning_evidence=warning_evidence,
            )
            if config.enable_rule_based_admet
            else []
        )
        synthesizability = (
            assess_synthesizability(profile.canonical_smiles, dict(descriptors))
            if config.enable_synthesizability
            else None
        )
        return score_developability(
            molecule_id=molecule_id,
            molecule_name=molecule_name,
            origin=origin,
            canonical_smiles=profile.canonical_smiles,
            physchem=profile,
            alerts=alerts,
            admet_predictions=admet_predictions,
            synthesizability=synthesizability,
            docking=[],
            warning_evidence=warning_evidence,
            direct_evidence_available=direct_evidence_available,
        )

    def _legacy_assess_mapping(
        self,
        *,
        name: str,
        origin: str,
        chemical_metadata: Mapping[str, Any],
        identifiers: Mapping[str, Any],
        descriptors: Mapping[str, Any],
        warnings: list[str],
        config: _AgentConfig,
    ) -> LegacyDevelopabilityAssessment:
        return self._assessor.assess_mapping(
            name=name,
            origin=origin,
            chemical_metadata=chemical_metadata,
            identifiers=identifiers,
            descriptors=descriptors,
            warnings=warnings,
            structure_filter_enabled=config.structure_filter_enabled,
            min_developability_score=config.min_score,
        )

    def _update_generated_hypotheses(
        self,
        hypotheses: list[Any],
        assessments_by_id: dict[str, StructuredDevelopabilityAssessment],
        config: _AgentConfig,
    ) -> list[Any]:
        updated = []
        for hypothesis in hypotheses:
            assessment = assessments_by_id.get(hypothesis.name)
            if assessment is None:
                updated.append(hypothesis)
                continue
            if (
                self._should_reject(assessment, config, generated=True)
                and config.generated_filtering_enabled
            ):
                continue
            legacy = self._legacy_assessment_from_structured(assessment)
            trace = {
                **hypothesis.trace,
                "developability_assessment": assessment.model_dump(mode="json"),
            }
            updated.append(
                hypothesis.model_copy(
                    update={
                        "developability_assessment": legacy,
                        "trace": trace,
                    }
                )
            )
        return updated

    def _should_reject(
        self,
        assessment: StructuredDevelopabilityAssessment,
        config: _AgentConfig,
        *,
        generated: bool,
    ) -> bool:
        if assessment.risk_level == "unknown":
            return generated and config.require_developability_for_generated
        if config.reject_critical_alerts and (
            assessment.risk_level == "critical"
            or any(alert.severity == "critical" for alert in assessment.alerts)
        ):
            return True
        if config.reject_high_toxicity_risk and _has_high_toxicity_risk(
            assessment.alerts,
            assessment.admet_predictions,
        ):
            return True
        return False

    def _should_deprioritize(
        self,
        assessment: StructuredDevelopabilityAssessment,
        rejected: bool,
    ) -> bool:
        return not rejected and assessment.recommendation in {
            "deprioritize",
            "expert_review_required",
        }

    def _mark_generated_rejected(
        self,
        molecule: GeneratedMolecule,
        assessment: StructuredDevelopabilityAssessment,
    ) -> GeneratedMolecule:
        validation = molecule.validation.model_copy(
            update={
                "rejection_reasons": sorted(
                    set(
                        [
                            *molecule.validation.rejection_reasons,
                            "developability_filter_failed",
                        ]
                    )
                )
            }
        )
        return self._warn_generated(
            molecule.model_copy(update={"validation": validation}),
            f"Generated molecule rejected by developability triage: {assessment.risk_level}.",
        )

    def _warn_generated(self, molecule: GeneratedMolecule, warning: str) -> GeneratedMolecule:
        return molecule.model_copy(update={"warnings": sorted(set([*molecule.warnings, warning]))})

    def _warn_candidate(self, candidate: MoleculeCandidate, warning: str) -> MoleculeCandidate:
        return candidate.model_copy(
            update={"warnings": sorted(set([*candidate.warnings, warning]))}
        )

    def _unknown_assessment(
        self,
        *,
        molecule_id: str,
        molecule_name: str,
        origin: str,
        smiles: str | None,
        reason: str,
    ) -> StructuredDevelopabilityAssessment:
        return StructuredDevelopabilityAssessment(
            molecule_id=molecule_id,
            molecule_name=molecule_name,
            origin="generated" if origin == "generated" else "existing",
            canonical_smiles=smiles or "",
            physchem=None,
            alerts=[],
            admet_predictions=[],
            synthesizability=None,
            docking=[],
            overall_developability_score=0.0,
            risk_summary="unknown developability risk; computational triage failed.",
            risk_level="unknown",
            confidence=0.0,
            recommendation="expert_review_required",
            warnings=[
                "Developability assessment failed for this molecule; requires expert review.",
                "No safety or practical synthesizability conclusion should be drawn.",
            ],
            metadata={
                "assessment_policy": "v0.4_developability_weighted_triage",
                "assessment_error": reason,
            },
        )

    def _legacy_unknown(
        self,
        molecule_name: str,
        origin: str,
        smiles: str | None,
        reason: str,
    ) -> LegacyDevelopabilityAssessment:
        return LegacyDevelopabilityAssessment(
            molecule_name=molecule_name,
            origin="generated" if origin == "generated" else "existing",
            structure_available=False,
            canonical_smiles=smiles,
            developability_score=0.0,
            triage_recommendation="insufficient_structure",
            limitations=[
                "Developability assessment is computational triage only.",
                "Assessment failure prevents structure-aware conclusions.",
            ],
            metadata={"assessment_error": reason},
        )

    def _legacy_assessment_from_structured(
        self,
        assessment: StructuredDevelopabilityAssessment,
    ) -> LegacyDevelopabilityAssessment:
        return LegacyDevelopabilityAssessment(
            molecule_name=assessment.molecule_name,
            origin=assessment.origin,
            structure_available=assessment.physchem is not None,
            canonical_smiles=assessment.canonical_smiles or None,
            descriptors=_numeric_descriptors_from_profile(assessment),
            admet_properties={
                "admet_predictions": [
                    prediction.model_dump(mode="json")
                    for prediction in assessment.admet_predictions
                ],
                "risk_summary": assessment.risk_summary,
            },
            synthetic_accessibility_score=(
                assessment.synthesizability.sa_score
                if assessment.synthesizability is not None
                else None
            ),
            structure_filter_pass=assessment.recommendation != "reject",
            developability_score=assessment.overall_developability_score,
            triage_recommendation=_legacy_recommendation(assessment),
            limitations=assessment.metadata.get("limitations", []),
            metadata={
                "structured_developability_assessment": assessment.model_dump(mode="json"),
                "risk_level": assessment.risk_level,
                "recommendation": assessment.recommendation,
            },
        )

    def _generation_run(self, context: PipelineContext) -> GenerationRun | None:
        value = context.config.get("generation_run")
        return value if isinstance(value, GenerationRun) else None

    def _run_metadata(
        self,
        *,
        assessments: list[StructuredDevelopabilityAssessment],
        config: _AgentConfig,
        assessed_existing_count: int,
        assessed_generated_count: int,
        retained_count: int,
        deprioritized_count: int,
        rejected_count: int,
        warnings: list[str],
    ) -> dict[str, Any]:
        alert_counts: Counter[str] = Counter()
        admet_risk_counts: Counter[str] = Counter()
        for assessment in assessments:
            for alert in assessment.alerts:
                alert_counts[f"severity:{alert.severity}"] += 1
                alert_counts[f"type:{alert.alert_type}"] += 1
            for prediction in assessment.admet_predictions:
                admet_risk_counts[prediction.risk_level] += 1
                admet_risk_counts[f"{prediction.endpoint}:{prediction.risk_level}"] += 1
        return {
            "enabled": True,
            "filter_mode": config.filter_mode,
            "assessed_existing_count": assessed_existing_count,
            "assessed_generated_count": assessed_generated_count,
            "retained_count": retained_count,
            "deprioritized_count": deprioritized_count,
            "rejected_count": rejected_count,
            "alert_mode": config.alert_mode,
            "enable_rule_based_admet": config.enable_rule_based_admet,
            "enable_local_admet_models": config.enable_local_admet_models,
            "allow_rule_based_admet_fallback": config.allow_rule_based_admet_fallback,
            "enable_synthesizability": config.enable_synthesizability,
            "enable_structure_retrieval": config.enable_structure_retrieval,
            "enable_docking": config.enable_docking,
            "strict_structure_mode": config.strict_structure_mode,
            "write_docking_artifacts": config.write_docking_artifacts,
            "max_structures_per_target": config.max_structures_per_target,
            "max_docked_molecules": config.max_docked_molecules,
            "alert_counts": dict(sorted(alert_counts.items())),
            "admet_risk_counts": dict(sorted(admet_risk_counts.items())),
            "warnings": sorted(set(warnings)),
        }

    def _empty_metadata(self, warning: str) -> dict[str, Any]:
        return {
            "enabled": False,
            "assessed_existing_count": 0,
            "assessed_generated_count": 0,
            "retained_count": 0,
            "deprioritized_count": 0,
            "rejected_count": 0,
            "alert_counts": {},
            "admet_risk_counts": {},
            "warnings": [warning],
        }


class _AgentConfig:
    def __init__(
        self,
        *,
        strict_developability: bool,
        assess_existing_molecules: bool,
        assess_generated_molecules: bool,
        filter_mode: str,
        reject_critical_alerts: bool,
        reject_high_toxicity_risk: bool,
        alert_mode: str,
        enable_rule_based_admet: bool,
        enable_local_admet_models: bool,
        allow_rule_based_admet_fallback: bool,
        enable_synthesizability: bool,
        enable_structure_retrieval: bool,
        require_developability_for_generated: bool,
        structure_filter_enabled: bool,
        enable_docking: bool,
        strict_structure_mode: bool,
        write_docking_artifacts: bool,
        max_structures_per_target: int,
        max_docked_molecules: int,
        min_score: float,
    ) -> None:
        self.strict_developability = strict_developability
        self.assess_existing_molecules = assess_existing_molecules
        self.assess_generated_molecules = assess_generated_molecules
        self.filter_mode = filter_mode
        self.reject_critical_alerts = reject_critical_alerts
        self.reject_high_toxicity_risk = reject_high_toxicity_risk
        self.alert_mode = alert_mode
        self.enable_rule_based_admet = enable_rule_based_admet
        self.enable_local_admet_models = enable_local_admet_models
        self.allow_rule_based_admet_fallback = allow_rule_based_admet_fallback
        self.enable_synthesizability = enable_synthesizability
        self.enable_structure_retrieval = enable_structure_retrieval
        self.require_developability_for_generated = require_developability_for_generated
        self.structure_filter_enabled = structure_filter_enabled
        self.enable_docking = enable_docking
        self.strict_structure_mode = strict_structure_mode
        self.write_docking_artifacts = write_docking_artifacts
        self.max_structures_per_target = max_structures_per_target
        self.max_docked_molecules = max_docked_molecules
        self.min_score = min_score

    @property
    def generated_filtering_enabled(self) -> bool:
        return self.filter_mode in {"filter_generated_only", "filter_all"}

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> _AgentConfig:
        filter_mode = str(config.get("developability_filter_mode", "filter_generated_only"))
        if filter_mode not in VALID_FILTER_MODES:
            raise ValueError(f"Unsupported developability_filter_mode: {filter_mode!r}")
        return cls(
            strict_developability=bool(config.get("strict_developability", False)),
            assess_existing_molecules=bool(config.get("assess_existing_molecules", True)),
            assess_generated_molecules=bool(config.get("assess_generated_molecules", True)),
            filter_mode=filter_mode,
            reject_critical_alerts=bool(config.get("reject_critical_alerts", True)),
            reject_high_toxicity_risk=bool(config.get("reject_high_toxicity_risk", False)),
            alert_mode=str(config.get("alert_mode", "deprioritize")),
            enable_rule_based_admet=bool(config.get("enable_rule_based_admet", True)),
            enable_local_admet_models=bool(config.get("enable_local_admet_models", False)),
            allow_rule_based_admet_fallback=bool(
                config.get("allow_rule_based_admet_fallback", True)
            ),
            enable_synthesizability=bool(config.get("enable_synthesizability", True)),
            enable_structure_retrieval=bool(config.get("enable_structure_retrieval", False)),
            require_developability_for_generated=bool(
                config.get("require_developability_for_generated", True)
            ),
            structure_filter_enabled=bool(config.get("enable_structure_filtering", False)),
            enable_docking=bool(config.get("enable_docking", False)),
            strict_structure_mode=bool(config.get("strict_structure_mode", False)),
            write_docking_artifacts=bool(config.get("write_docking_artifacts", False)),
            max_structures_per_target=int(config.get("max_structures_per_target", 5) or 5),
            max_docked_molecules=int(config.get("max_docked_molecules", 20) or 20),
            min_score=float(config.get("min_developability_score", 0.25)),
        )


def _structure_from_mappings(*mappings: Mapping[str, Any]) -> str | None:
    first_unparsed_structure: str | None = None
    for mapping in mappings:
        for field in ("canonical_smiles", "isomeric_smiles", "smiles", "canonical_smile"):
            value = mapping.get(field)
            if value not in (None, ""):
                raw_value = str(value)
                canonical = canonicalize_smiles(raw_value)
                if canonical is not None:
                    return canonical
                first_unparsed_structure = first_unparsed_structure or raw_value
    for mapping in mappings:
        for field in ("inchi", "standard_inchi"):
            value = mapping.get(field)
            if value not in (None, ""):
                canonical = canonicalize_inchi(str(value))
                if canonical is not None:
                    return canonical
                first_unparsed_structure = first_unparsed_structure or str(value)
    if first_unparsed_structure is not None:
        return first_unparsed_structure
    return None


def _has_high_toxicity_risk(
    alerts: Sequence[ChemistryAlert],
    predictions: Sequence[ADMETPrediction],
) -> bool:
    toxic_alert = any(
        alert.severity == "high"
        and alert.alert_type in {"toxicophore", "reactive_functionality", "structural_liability"}
        for alert in alerts
    )
    toxic_prediction = any(
        prediction.risk_level == "high"
        and prediction.endpoint
        in {
            "herg_liability_risk",
            "ames_mutagenicity_risk",
            "dili_risk",
            "general_toxicity_risk",
        }
        for prediction in predictions
    )
    return toxic_alert or toxic_prediction


def _numeric_descriptors_from_profile(
    assessment: StructuredDevelopabilityAssessment,
) -> dict[str, float]:
    if assessment.physchem is None:
        return {}
    payload = assessment.physchem.model_dump(mode="json", exclude_none=True)
    return {
        key: float(value)
        for key, value in payload.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }


def _legacy_recommendation(
    assessment: StructuredDevelopabilityAssessment,
) -> Literal[
    "favorable_hypothesis",
    "review_flags",
    "high_risk_flags",
    "insufficient_structure",
]:
    if assessment.risk_level == "unknown":
        return "insufficient_structure"
    if assessment.risk_level in {"critical", "high"}:
        return "high_risk_flags"
    if assessment.recommendation in {"deprioritize", "expert_review_required"}:
        return "review_flags"
    return "favorable_hypothesis"
