from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.generation.schemas import GeneratedMolecule
from molecule_ranker.review.schemas import ReviewItem
from molecule_ranker.schemas import MoleculeCandidate


class LinkingConfig(BaseModel):
    allow_fuzzy_name_matching: bool = False
    fuzzy_min_confidence: float = Field(default=0.92, ge=0.0, le=1.0)
    allow_generated_name_linking: bool = False
    allow_analog_context: bool = False


def link_assay_results(
    results: list[AssayResult],
    *,
    candidates: list[MoleculeCandidate] | None = None,
    generated_molecules: list[Any] | None = None,
    review_items: list[ReviewItem] | None = None,
    artifact_paths: list[str | Path] | None = None,
    config: LinkingConfig | None = None,
) -> list[AssayResult]:
    loaded_candidates, loaded_generated, loaded_review_items = _load_artifacts(artifact_paths or [])
    return [
        link_assay_result(
            result,
            candidates=[*(candidates or []), *loaded_candidates],
            generated_molecules=[*(generated_molecules or []), *loaded_generated],
            review_items=[*(review_items or []), *loaded_review_items],
            artifact_paths=artifact_paths,
            config=config,
        )
        for result in results
    ]


def link_assay_result(
    result: AssayResult,
    *,
    candidates: list[MoleculeCandidate] | None = None,
    generated_molecules: list[Any] | None = None,
    review_items: list[ReviewItem] | None = None,
    artifact_paths: list[str | Path] | None = None,
    config: LinkingConfig | None = None,
) -> AssayResult:
    cfg = config or LinkingConfig()
    candidates = candidates or []
    generated_molecules = generated_molecules or []
    review_items = review_items or []
    loaded_candidates, loaded_generated, loaded_review_items = _load_artifacts(artifact_paths or [])
    candidates = [*candidates, *loaded_candidates]
    generated_molecules = [*generated_molecules, *loaded_generated]
    review_items = [*review_items, *loaded_review_items]

    generated_match = _match_generated(result, generated_molecules, cfg)
    if generated_match is not None:
        metadata = _base_link_metadata(result)
        metadata.update(generated_match)
        metadata["generated_direct_experimental_evidence"] = True
        metadata["direct_evidence_result_id"] = result.result_id
        return result.model_copy(update={"metadata": metadata})
    if result.candidate_origin == "generated" and generated_molecules:
        return _unlinked(
            result,
            "Generated molecule results require exact generated_id, InChIKey, or canonical SMILES.",
        )

    match = _match_existing_candidate(result, candidates, cfg)
    if match is None:
        review_match = _match_review_item(result, review_items)
        if review_match is not None:
            match = review_match
        else:
            return _unlinked(result)
    if match.get("link_method") == "ambiguous":
        return result.model_copy(update={"metadata": {**_base_link_metadata(result), **match}})

    metadata = {**_base_link_metadata(result), **match}
    linked_review_item = _review_item_for_match(metadata, review_items, result)
    if linked_review_item is not None:
        metadata["linked_review_item_id"] = linked_review_item.review_item_id
        metadata.setdefault("matched_identifiers", {})["review_item_id"] = (
            linked_review_item.review_item_id
        )
    return result.model_copy(update={"metadata": metadata})


def _match_existing_candidate(
    result: AssayResult,
    candidates: list[MoleculeCandidate],
    config: LinkingConfig,
) -> dict[str, Any] | None:
    priorities = [
        ("candidate_id", _candidate_id_matches),
        ("inchi_key", _inchi_key_matches),
        ("canonical_smiles", _smiles_matches),
        ("normalized_name", _name_matches),
    ]
    for method, matcher in priorities:
        matches = [candidate for candidate in candidates if matcher(result, candidate)]
        resolved = _resolve_matches(matches, method, result)
        if resolved is not None:
            return resolved
    if config.allow_fuzzy_name_matching:
        fuzzy = _fuzzy_name_matches(result, candidates, config.fuzzy_min_confidence)
        if fuzzy is not None:
            return fuzzy
    return None


def _match_generated(
    result: AssayResult,
    generated_molecules: list[Any],
    config: LinkingConfig,
) -> dict[str, Any] | None:
    priorities = [
        ("generated_id", _generated_id_matches),
        ("generated_inchi_key", _generated_inchi_key_matches),
        ("generated_canonical_smiles", _generated_smiles_matches),
    ]
    if config.allow_generated_name_linking:
        priorities.append(("generated_name", _generated_name_matches))
    for method, matcher in priorities:
        matches = [generated for generated in generated_molecules if matcher(result, generated)]
        if not matches:
            continue
        if len(matches) > 1:
            return _ambiguous(method, [_generated_id(match) for match in matches])
        generated = matches[0]
        generated_id = _generated_id(generated)
        return {
            "linked_candidate_id": generated_id,
            "linked_generated_id": generated_id,
            "linked_review_item_id": None,
            "link_method": method,
            "link_confidence": 1.0,
            "ambiguity_warning": None,
            "matched_identifiers": _generated_identifiers(generated, result),
        }
    return None


def _match_review_item(
    result: AssayResult,
    review_items: list[ReviewItem],
) -> dict[str, Any] | None:
    if not result.review_item_id:
        return None
    matches = [item for item in review_items if item.review_item_id == result.review_item_id]
    if not matches:
        return None
    if len(matches) > 1:
        return _ambiguous("review_item_id", [item.review_item_id for item in matches])
    item = matches[0]
    return {
        "linked_candidate_id": item.candidate_id,
        "linked_review_item_id": item.review_item_id,
        "link_method": "review_item_id",
        "link_confidence": 0.95,
        "ambiguity_warning": None,
        "matched_identifiers": {
            "candidate_id": item.candidate_id,
            "review_item_id": item.review_item_id,
        },
    }


def _resolve_matches(
    matches: list[MoleculeCandidate],
    method: str,
    result: AssayResult,
) -> dict[str, Any] | None:
    if not matches:
        return None
    if len(matches) > 1:
        return _ambiguous(method, [_candidate_id(candidate) for candidate in matches])
    candidate = matches[0]
    return {
        "linked_candidate_id": _candidate_id(candidate),
        "linked_review_item_id": None,
        "link_method": method,
        "link_confidence": 1.0 if method != "normalized_name" else 0.9,
        "ambiguity_warning": None,
        "matched_identifiers": _candidate_identifiers(candidate, result),
    }


def _candidate_id_matches(result: AssayResult, candidate: MoleculeCandidate) -> bool:
    if not result.candidate_id:
        return False
    return _clean(result.candidate_id) in {
        _clean(value) for value in candidate.identifiers.values()
    }


def _inchi_key_matches(result: AssayResult, candidate: MoleculeCandidate) -> bool:
    if not result.inchi_key:
        return False
    return _clean(result.inchi_key) == _clean(_candidate_inchi_key(candidate))


def _smiles_matches(result: AssayResult, candidate: MoleculeCandidate) -> bool:
    if not result.canonical_smiles:
        return False
    return _normalize_smiles(result.canonical_smiles) == _normalize_smiles(
        _candidate_smiles(candidate)
    )


def _name_matches(result: AssayResult, candidate: MoleculeCandidate) -> bool:
    return _normalize_name(result.candidate_name) == _normalize_name(candidate.name)


def _fuzzy_name_matches(
    result: AssayResult,
    candidates: list[MoleculeCandidate],
    min_confidence: float,
) -> dict[str, Any] | None:
    scored = [
        (
            difflib.SequenceMatcher(
                None,
                _normalize_name(result.candidate_name),
                _normalize_name(c.name),
            ).ratio(),
            c,
        )
        for c in candidates
    ]
    high = [(score, candidate) for score, candidate in scored if score >= min_confidence]
    if not high:
        return None
    high.sort(key=lambda item: item[0], reverse=True)
    if len(high) > 1 and abs(high[0][0] - high[1][0]) < 0.02:
        return _ambiguous("fuzzy_name", [_candidate_id(candidate) for _, candidate in high])
    score, candidate = high[0]
    return {
        "linked_candidate_id": _candidate_id(candidate),
        "linked_review_item_id": None,
        "link_method": "fuzzy_name",
        "link_confidence": round(score, 3),
        "ambiguity_warning": None,
        "matched_identifiers": {"candidate_name": candidate.name},
    }


def _review_item_for_match(
    metadata: dict[str, Any],
    review_items: list[ReviewItem],
    result: AssayResult,
) -> ReviewItem | None:
    if result.review_item_id:
        for item in review_items:
            if item.review_item_id == result.review_item_id:
                return item
    linked_candidate_id = metadata.get("linked_candidate_id")
    if linked_candidate_id is None:
        return None
    matches = [item for item in review_items if item.candidate_id == linked_candidate_id]
    return matches[0] if len(matches) == 1 else None


def _generated_id_matches(result: AssayResult, generated: Any) -> bool:
    return bool(
        result.candidate_id and _clean(result.candidate_id) == _clean(_generated_id(generated))
    )


def _generated_inchi_key_matches(result: AssayResult, generated: Any) -> bool:
    return bool(
        result.inchi_key and _clean(result.inchi_key) == _clean(_get(generated, "inchi_key"))
    )


def _generated_smiles_matches(result: AssayResult, generated: Any) -> bool:
    return bool(
        result.canonical_smiles
        and _normalize_smiles(result.canonical_smiles)
        == _normalize_smiles(_get(generated, "canonical_smiles"))
    )


def _generated_name_matches(result: AssayResult, generated: Any) -> bool:
    return _normalize_name(result.candidate_name) == _normalize_name(
        _get(generated, "name") or _generated_id(generated)
    )


def _candidate_id(candidate: MoleculeCandidate) -> str:
    if candidate.identifiers:
        for key in ["chembl", "pubchem", "drugbank", "id"]:
            if key in candidate.identifiers:
                return candidate.identifiers[key]
        return next(iter(candidate.identifiers.values()))
    return candidate.name


def _candidate_inchi_key(candidate: MoleculeCandidate) -> str | None:
    return _metadata_value(candidate, "inchi_key")


def _candidate_smiles(candidate: MoleculeCandidate) -> str | None:
    return _metadata_value(candidate, "canonical_smiles") or _metadata_value(candidate, "smiles")


def _metadata_value(candidate: MoleculeCandidate, key: str) -> str | None:
    value = candidate.chemical_metadata.get(key)
    if value:
        return str(value)
    if candidate.developability_assessment is not None and key == "canonical_smiles":
        return candidate.developability_assessment.canonical_smiles
    return None


def _candidate_identifiers(candidate: MoleculeCandidate, result: AssayResult) -> dict[str, str]:
    identifiers = dict(candidate.identifiers)
    if result.candidate_id:
        identifiers["candidate_id"] = result.candidate_id
    if result.inchi_key:
        identifiers["inchi_key"] = result.inchi_key
    if result.canonical_smiles:
        identifiers["canonical_smiles"] = result.canonical_smiles
    return identifiers


def _generated_id(generated: Any) -> str:
    return str(
        _get(generated, "generated_id")
        or _get(generated, "candidate_id")
        or _get(generated, "name")
        or ""
    )


def _generated_identifiers(generated: Any, result: AssayResult) -> dict[str, str]:
    identifiers: dict[str, str] = {"generated_id": _generated_id(generated)}
    if result.inchi_key:
        identifiers["inchi_key"] = result.inchi_key
    if result.canonical_smiles:
        identifiers["canonical_smiles"] = result.canonical_smiles
    return identifiers


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _ambiguous(method: str, ids: list[str]) -> dict[str, Any]:
    return {
        "linked_candidate_id": None,
        "linked_review_item_id": None,
        "link_method": "ambiguous",
        "link_confidence": 0.0,
        "ambiguity_warning": f"ambiguous {method} match: {', '.join(sorted(set(ids)))}",
        "matched_identifiers": {"ambiguous_ids": sorted(set(ids))},
    }


def _unlinked(result: AssayResult, warning: str | None = None) -> AssayResult:
    metadata = _base_link_metadata(result)
    metadata.update(
        {
            "linked_candidate_id": None,
            "linked_review_item_id": None,
            "link_method": "unlinked",
            "link_confidence": 0.0,
            "ambiguity_warning": warning,
            "matched_identifiers": {},
        }
    )
    return result.model_copy(update={"metadata": metadata})


def _base_link_metadata(result: AssayResult) -> dict[str, Any]:
    metadata = dict(result.metadata)
    metadata.setdefault("linking", {})
    return metadata


def _load_artifacts(
    artifact_paths: list[str | Path],
) -> tuple[list[MoleculeCandidate], list[GeneratedMolecule], list[ReviewItem]]:
    candidates: list[MoleculeCandidate] = []
    generated: list[GeneratedMolecule] = []
    review_items: list[ReviewItem] = []
    for artifact_path in artifact_paths:
        path = Path(artifact_path)
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            continue
        candidates.extend(
            MoleculeCandidate.model_validate(item)
            for item in payload.get("candidates", [])
            if isinstance(item, dict)
        )
        generated.extend(
            GeneratedMolecule.model_validate(item)
            for item in payload.get("generated_molecules", [])
            if isinstance(item, dict)
        )
        raw_review_items = payload.get("review_items") or payload.get("items") or []
        review_items.extend(
            ReviewItem.model_validate(item) for item in raw_review_items if isinstance(item, dict)
        )
    return candidates, generated, review_items


def _clean(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value))


def _normalize_smiles(value: object) -> str:
    return str(value or "").strip()
