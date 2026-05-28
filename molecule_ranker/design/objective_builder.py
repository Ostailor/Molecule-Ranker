from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from molecule_ranker.design.constraints import (
    DEFAULT_FORBIDDEN_PATTERNS,
    default_hard_constraints,
    default_optimization_goals,
    default_soft_constraints,
)
from molecule_ranker.design.schemas import ActionSource, DesignObjectiveV2, DesiredAction
from molecule_ranker.generation.schemas import SeedMolecule
from molecule_ranker.schemas import Disease, Target
from molecule_ranker.utils import slugify

ACTION_PATTERNS: tuple[tuple[re.Pattern[str], DesiredAction], ...] = (
    (re.compile(r"\binhibit(or|ion)?\b|\binhibits?\b", re.I), "inhibitor"),
    (re.compile(r"\bagonist\b|\bactivat(es?|ion)\b", re.I), "agonist"),
    (re.compile(r"\bantagonist\b|\bblock(s|ade)?\b", re.I), "antagonist"),
    (re.compile(r"\bmodulat(or|ion)?\b|\bmodulates?\b", re.I), "modulator"),
)


class DesignObjectiveBuilderV2:
    """Build evidence-bounded V1.1 design objectives for generated hypotheses."""

    def __init__(self) -> None:
        self.trace_metadata: dict[str, Any] = {"created_objectives": [], "skipped_targets": []}

    def build(
        self,
        *,
        disease: Disease,
        targets: list[Target],
        seeds: list[SeedMolecule],
        literature_evidence: Mapping[str, Any] | None = None,
        review_decisions: Sequence[Mapping[str, Any] | Any] = (),
        max_objectives: int = 5,
    ) -> list[DesignObjectiveV2]:
        seeds_by_target = self._seeds_by_target(seeds)
        objectives: list[DesignObjectiveV2] = []
        skipped: list[dict[str, str]] = []
        for target in sorted(targets, key=lambda item: item.disease_relevance_score, reverse=True):
            if not self._is_evidence_backed_target(target):
                skipped.append(
                    {
                        "target_symbol": target.symbol,
                        "reason": "target_not_evidence_backed",
                    }
                )
                continue
            target_seeds = seeds_by_target.get(target.symbol.upper(), [])
            if not target_seeds:
                skipped.append(
                    {
                        "target_symbol": target.symbol,
                        "reason": "no_seed_for_target",
                    }
                )
                continue
            desired_action, action_source, action_ref = self._desired_action(
                target=target,
                literature_evidence=literature_evidence,
                review_decisions=review_decisions,
            )
            objectives.append(
                self._objective(
                    disease=disease,
                    target=target,
                    seeds=target_seeds,
                    desired_action=desired_action,
                    action_source=action_source,
                    action_ref=action_ref,
                    literature_evidence=literature_evidence,
                )
            )
            if len(objectives) >= max_objectives:
                break

        self.trace_metadata = {
            "created_objectives": [
                {
                    "objective_id": objective.objective_id,
                    "target_symbol": objective.target_symbol,
                    "desired_action": objective.desired_action,
                    "action_source": objective.action_source,
                }
                for objective in objectives
            ],
            "skipped_targets": skipped,
            "max_objectives": max_objectives,
        }
        return objectives

    def _objective(
        self,
        *,
        disease: Disease,
        target: Target,
        seeds: list[SeedMolecule],
        desired_action: DesiredAction,
        action_source: ActionSource,
        action_ref: str | None,
        literature_evidence: Mapping[str, Any] | None,
    ) -> DesignObjectiveV2:
        seed_ids = [self._seed_id(seed) for seed in seeds]
        scaffold_ids = sorted(
            {
                str(value)
                for seed in seeds
                if (value := seed.metadata.get("scaffold_id") or seed.metadata.get("scaffold"))
                not in (None, "")
            }
        )
        return DesignObjectiveV2(
            objective_id=f"{slugify(disease.canonical_name)}:{target.symbol}:v2",
            disease_name=disease.canonical_name,
            target_symbol=target.symbol,
            target_identifiers={
                str(key): str(value)
                for key, value in target.identifiers.items()
                if value not in (None, "")
            },
            desired_modality="small_molecule",
            desired_action=desired_action,
            action_source=action_source,
            seed_ids=seed_ids,
            scaffold_ids=scaffold_ids,
            optimization_goals=default_optimization_goals(),
            hard_constraints=default_hard_constraints(),
            soft_constraints=default_soft_constraints(
                target_relevance=target.disease_relevance_score
            ),
            forbidden_patterns=list(DEFAULT_FORBIDDEN_PATTERNS),
            target_context={
                "target_name": target.name,
                "target_symbol": target.symbol,
                "disease_relevance_score": target.disease_relevance_score,
                "organism": target.organism,
            },
            evidence_context={
                "target_evidence_count": self._target_evidence_count(target),
                "target_evidence_refs": self._target_evidence_refs(target),
                "action_evidence_source": action_ref,
                "literature_context_available": bool(literature_evidence),
                "seed_count": len(seeds),
            },
            uncertainty_context={
                "desired_action_uncertain": desired_action == "unknown",
                "action_source": action_source,
                "requires_expert_review": desired_action == "unknown",
            },
            metadata={
                "schema": "DesignObjectiveV2",
                "hypothesis_only": True,
                "no_invented_action": action_source == "unknown",
                "seed_names": [seed.name for seed in seeds],
            },
        )

    def _desired_action(
        self,
        *,
        target: Target,
        literature_evidence: Mapping[str, Any] | None,
        review_decisions: Sequence[Mapping[str, Any] | Any],
    ) -> tuple[DesiredAction, ActionSource, str | None]:
        if target.mechanism:
            action = self._action_from_text(target.mechanism)
            if action != "unknown":
                return action, "retrieved_mechanism", "target.mechanism"

        literature_action = self._action_from_literature(target, literature_evidence)
        if literature_action is not None:
            return literature_action

        review_action = self._action_from_review(target, review_decisions)
        if review_action is not None:
            return review_action

        return "unknown", "unknown", None

    def _action_from_literature(
        self,
        target: Target,
        literature_evidence: Mapping[str, Any] | None,
    ) -> tuple[DesiredAction, ActionSource, str | None] | None:
        if not literature_evidence:
            return None
        for item in self._walk(literature_evidence):
            if not isinstance(item, Mapping):
                continue
            text = " ".join(
                str(item.get(key) or "") for key in ("claim", "summary", "text", "title")
            )
            if target.symbol.upper() not in text.upper():
                continue
            action = self._action_from_text(text)
            source_ref = item.get("source_record_id") or item.get("pmid") or item.get("doi")
            if action != "unknown" and source_ref:
                return action, "literature_claim", str(source_ref)
        return None

    def _action_from_review(
        self,
        target: Target,
        review_decisions: Sequence[Mapping[str, Any] | Any],
    ) -> tuple[DesiredAction, ActionSource, str | None] | None:
        for item in self._walk(list(review_decisions)):
            if not isinstance(item, Mapping):
                continue
            raw_target = item.get("target_symbol") or item.get("target")
            if raw_target not in (None, "") and str(raw_target).upper() != target.symbol.upper():
                continue
            source_ref = item.get("review_decision_id") or item.get("decision_id")
            text = " ".join(str(item.get(key) or "") for key in ("desired_action", "rationale"))
            action = self._action_from_text(text)
            if action != "unknown" and source_ref:
                return action, "expert_review", str(source_ref)
        return None

    def _action_from_text(self, text: str) -> DesiredAction:
        for pattern, action in ACTION_PATTERNS:
            if pattern.search(text):
                return action
        return "unknown"

    def _seeds_by_target(self, seeds: list[SeedMolecule]) -> dict[str, list[SeedMolecule]]:
        grouped: dict[str, list[SeedMolecule]] = {}
        for seed in seeds:
            matched_targets = seed.metadata.get("matched_targets") or seed.known_targets
            for target in matched_targets:
                grouped.setdefault(str(target).upper(), []).append(seed)
        return grouped

    def _is_evidence_backed_target(self, target: Target) -> bool:
        return any(item.source and item.source_record_id for item in target.evidence)

    def _target_evidence_count(self, target: Target) -> int:
        return sum(1 for item in target.evidence if item.source and item.source_record_id)

    def _target_evidence_refs(self, target: Target) -> list[str]:
        return [
            str(item.source_record_id)
            for item in target.evidence
            if item.source and item.source_record_id
        ]

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name

    def _walk(self, value: Any) -> list[Any]:
        items = [value]
        if isinstance(value, Mapping):
            for child in value.values():
                items.extend(self._walk(child))
        elif isinstance(value, list | tuple):
            for child in value:
                items.extend(self._walk(child))
        return items
