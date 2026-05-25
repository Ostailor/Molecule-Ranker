from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from molecule_ranker.evidence import is_molecule_target_evidence, is_safety_warning
from molecule_ranker.evidence.normalizer import normalize_evidence_type
from molecule_ranker.generation.chemistry import canonicalize_inchi, canonicalize_smiles
from molecule_ranker.generation.schemas import GenerationConfig, SeedMolecule
from molecule_ranker.schemas import Disease, EvidenceItem, MoleculeCandidate, Target


class SeedSelector:
    """Select evidence-backed existing molecules as generation seeds."""

    def __init__(self) -> None:
        self.trace_metadata: dict[str, Any] = {
            "selected_seeds": [],
            "rejected_seed_candidates": [],
            "target_coverage": {},
        }

    def select(
        self,
        *,
        disease: Disease,
        targets: list[Target],
        candidates: list[MoleculeCandidate],
        literature_evidence: Mapping[str, Any] | None,
        config: GenerationConfig,
    ) -> list[SeedMolecule]:
        evidence_backed_targets = self._evidence_backed_targets(targets)
        target_symbols = set(evidence_backed_targets)
        selected: list[tuple[float, SeedMolecule]] = []
        rejected: list[dict[str, Any]] = []

        for candidate in candidates:
            canonical_smiles = self._canonical_structure(candidate)
            matched_targets = [
                target
                for target in candidate.known_targets
                if target.upper() in target_symbols
            ]
            target_relevance = max(
                (
                    evidence_backed_targets[target.upper()].disease_relevance_score
                    for target in matched_targets
                ),
                default=0.0,
            )
            target_evidence = self._real_molecule_target_evidence(candidate)
            serious_warnings = self._serious_warning_items(candidate)
            rejection_reasons = self._rejection_reasons(
                candidate=candidate,
                canonical_smiles=canonical_smiles,
                matched_targets=matched_targets,
                target_evidence=target_evidence,
                target_relevance=target_relevance,
                serious_warnings=serious_warnings,
                config=config,
            )
            seed_score = self._seed_score(
                candidate=candidate,
                target_relevance=target_relevance,
                target_evidence=target_evidence,
                literature_evidence=literature_evidence,
                serious_warnings=serious_warnings,
            )
            if seed_score < config.min_seed_score:
                rejection_reasons.append("below_min_seed_score")

            if rejection_reasons:
                rejected.append(
                    {
                        "name": candidate.name,
                        "rejection_reasons": sorted(set(rejection_reasons)),
                        "seed_score": round(seed_score, 3),
                        "matched_targets": sorted(set(matched_targets)),
                    }
                )
                continue

            seed = SeedMolecule(
                name=candidate.name,
                canonical_smiles=canonical_smiles or "",
                identifiers=dict(candidate.identifiers),
                known_targets=list(candidate.known_targets),
                source_candidate_name=candidate.name,
                evidence_count=self._real_evidence_count(candidate),
                best_evidence_confidence=self._best_evidence_confidence(candidate),
                target_relevance_score=round(target_relevance, 3),
                seed_selection_reason=self._selection_reason(
                    matched_targets=matched_targets,
                    target_evidence=target_evidence,
                    candidate=candidate,
                ),
                metadata={
                    "seed_score": round(seed_score, 3),
                    "matched_targets": sorted(set(matched_targets)),
                    "molecule_target_evidence_score": round(
                        self._molecule_target_evidence_score(candidate, target_evidence),
                        3,
                    ),
                    "literature_support_score": round(
                        self._literature_support_score(candidate, literature_evidence),
                        3,
                    ),
                    "serious_warning_count": len(serious_warnings),
                    "has_pubchem_structure_metadata": self._has_pubchem_structure_metadata(
                        candidate
                    ),
                },
            )
            selected.append((seed_score, seed))

        selected.sort(key=lambda item: item[0], reverse=True)
        seeds = [seed for _, seed in selected[: config.max_seed_molecules]]
        self.trace_metadata = {
            "disease": disease.canonical_name,
            "selected_seeds": [
                {
                    "name": seed.name,
                    "canonical_smiles": seed.canonical_smiles,
                    "seed_score": seed.metadata.get("seed_score"),
                    "matched_targets": seed.metadata.get("matched_targets", []),
                }
                for seed in seeds
            ],
            "rejected_seed_candidates": rejected,
            "target_coverage": self._target_coverage(seeds),
        }
        return seeds

    def _evidence_backed_targets(self, targets: list[Target]) -> dict[str, Target]:
        return {
            target.symbol.upper(): target
            for target in targets
            if any(item.source and item.source_record_id for item in target.evidence)
        }

    def _canonical_structure(self, candidate: MoleculeCandidate) -> str | None:
        for field in ("canonical_smiles", "isomeric_smiles", "smiles", "canonical_smile"):
            value = candidate.chemical_metadata.get(field) or candidate.identifiers.get(field)
            if value not in (None, ""):
                canonical = canonicalize_smiles(str(value))
                if canonical is not None:
                    return canonical
        for field in ("inchi", "standard_inchi"):
            value = candidate.chemical_metadata.get(field) or candidate.identifiers.get(field)
            if value not in (None, ""):
                canonical = canonicalize_inchi(str(value))
                if canonical is not None:
                    return canonical
        return None

    def _real_molecule_target_evidence(
        self,
        candidate: MoleculeCandidate,
    ) -> list[EvidenceItem]:
        return [
            item
            for item in candidate.evidence
            if item.source
            and item.source_record_id
            and is_molecule_target_evidence(item)
        ]

    def _rejection_reasons(
        self,
        *,
        candidate: MoleculeCandidate,
        canonical_smiles: str | None,
        matched_targets: list[str],
        target_evidence: list[EvidenceItem],
        target_relevance: float,
        serious_warnings: list[EvidenceItem],
        config: GenerationConfig,
    ) -> list[str]:
        reasons: list[str] = []
        if config.require_structure_for_seed and canonical_smiles is None:
            reasons.append("missing_valid_structure")
        if not self._has_real_retrieved_evidence(candidate):
            reasons.append("no_real_source_evidence")
        if not matched_targets:
            reasons.append("no_evidence_backed_target_overlap")
        if not target_evidence:
            reasons.append("no_real_molecule_target_evidence")
        if target_relevance < config.min_seed_target_relevance:
            reasons.append("below_min_seed_target_relevance")
        if config.exclude_seed_with_serious_warnings and serious_warnings:
            reasons.append("serious_safety_warning")
        if self._only_mention_literature_without_database_evidence(candidate):
            reasons.append("mention_only_literature_without_database_evidence")
        return reasons

    def _has_real_retrieved_evidence(self, candidate: MoleculeCandidate) -> bool:
        return any(item.source and item.source_record_id for item in candidate.evidence)

    def _only_mention_literature_without_database_evidence(
        self,
        candidate: MoleculeCandidate,
    ) -> bool:
        if not candidate.evidence:
            return False
        has_database_evidence = any(
            item.source.lower() not in {"pubmed", "openalex"}
            and item.source_record_id
            for item in candidate.evidence
        )
        has_literature = any(
            item.source.lower() in {"pubmed", "openalex"}
            or str(item.evidence_type).startswith("literature_")
            for item in candidate.evidence
        )
        return has_literature and not has_database_evidence

    def _seed_score(
        self,
        *,
        candidate: MoleculeCandidate,
        target_relevance: float,
        target_evidence: list[EvidenceItem],
        literature_evidence: Mapping[str, Any] | None,
        serious_warnings: list[EvidenceItem],
    ) -> float:
        molecule_target_score = self._molecule_target_evidence_score(candidate, target_evidence)
        chembl_score = self._chembl_mechanism_activity_score(target_evidence)
        structure_score = 1.0 if self._has_pubchem_structure_metadata(candidate) else 0.65
        literature_score = self._literature_support_score(candidate, literature_evidence)
        safety_score = max(0.0, 1.0 - 0.35 * len(serious_warnings))
        score = (
            0.30 * target_relevance
            + 0.30 * molecule_target_score
            + 0.15 * chembl_score
            + 0.10 * structure_score
            + 0.10 * literature_score
            + 0.05 * safety_score
        )
        return max(0.0, min(score, 1.0))

    def _molecule_target_evidence_score(
        self,
        candidate: MoleculeCandidate,
        target_evidence: list[EvidenceItem],
    ) -> float:
        if candidate.score_breakdown is not None:
            return candidate.score_breakdown.molecule_target_evidence
        return max((item.confidence for item in target_evidence), default=0.0)

    def _chembl_mechanism_activity_score(self, target_evidence: list[EvidenceItem]) -> float:
        if not target_evidence:
            return 0.0
        mechanism_or_activity = [
            item
            for item in target_evidence
            if item.source.lower() == "chembl"
            and normalize_evidence_type(item.evidence_type)
            in {"molecule_target_mechanism", "molecule_target_activity"}
        ]
        return min(len(mechanism_or_activity) / 2.0, 1.0)

    def _has_pubchem_structure_metadata(self, candidate: MoleculeCandidate) -> bool:
        if any(
            item.source.lower() == "pubchem"
            and normalize_evidence_type(item.evidence_type) == "chemical_annotation"
            for item in candidate.evidence
        ):
            return True
        metadata_keys = {key.lower() for key in candidate.chemical_metadata}
        identifier_keys = {key.lower() for key in candidate.identifiers}
        return bool(
            {"cid", "pubchem_cid", "inchikey", "canonical_smiles"}
            & (metadata_keys | identifier_keys)
        )

    def _literature_support_score(
        self,
        candidate: MoleculeCandidate,
        literature_evidence: Mapping[str, Any] | None,
    ) -> float:
        external = self._external_literature_score(candidate, literature_evidence)
        bundle_score = (
            candidate.literature_evidence.quality_score
            if candidate.literature_evidence is not None
            else 0.0
        )
        return max(float(external or 0.0), float(bundle_score or 0.0))

    def _external_literature_score(
        self,
        candidate: MoleculeCandidate,
        literature_evidence: Mapping[str, Any] | None,
    ) -> float:
        if not literature_evidence:
            return 0.0
        value = literature_evidence.get(candidate.name)
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return max(0.0, min(float(value), 1.0))
        quality = getattr(value, "quality_score", None)
        if isinstance(quality, (int, float)):
            return max(0.0, min(float(quality), 1.0))
        if isinstance(value, Mapping):
            raw_quality = value.get("quality_score")
            if isinstance(raw_quality, (int, float)):
                return max(0.0, min(float(raw_quality), 1.0))
        return 0.0

    def _serious_warning_items(self, candidate: MoleculeCandidate) -> list[EvidenceItem]:
        return [
            item
            for item in candidate.evidence
            if is_safety_warning(item) and self._is_serious_warning(item)
        ]

    def _is_serious_warning(self, item: EvidenceItem) -> bool:
        text = " ".join(
            [
                str(item.metadata.get("warning_type") or ""),
                str(item.metadata.get("warning_class") or ""),
                item.summary,
                item.title,
            ]
        ).lower()
        return any(
            term in text
            for term in ("black box", "boxed", "contraindication", "withdrawn", "fatal")
        )

    def _real_evidence_count(self, candidate: MoleculeCandidate) -> int:
        return sum(1 for item in candidate.evidence if item.source and item.source_record_id)

    def _best_evidence_confidence(self, candidate: MoleculeCandidate) -> float:
        return max(
            (
                item.confidence
                for item in candidate.evidence
                if item.source and item.source_record_id
            ),
            default=0.0,
        )

    def _selection_reason(
        self,
        *,
        matched_targets: list[str],
        target_evidence: list[EvidenceItem],
        candidate: MoleculeCandidate,
    ) -> str:
        sources = sorted({item.source for item in target_evidence})
        source_text = ", ".join(sources) if sources else "retrieved"
        targets = ", ".join(sorted(set(matched_targets)))
        reason = f"Selected for overlap with evidence-backed target(s) {targets}"
        if "ChEMBL" in sources:
            reason += " and real ChEMBL molecule-target evidence"
        else:
            reason += f" and real {source_text} molecule-target evidence"
        if self._has_pubchem_structure_metadata(candidate):
            reason += "; structure metadata available"
        return reason + "."

    def _target_coverage(self, seeds: list[SeedMolecule]) -> dict[str, list[str]]:
        coverage: dict[str, list[str]] = {}
        for seed in seeds:
            for target in seed.metadata.get("matched_targets", []):
                coverage.setdefault(str(target), []).append(seed.name)
        return {target: sorted(names) for target, names in sorted(coverage.items())}
