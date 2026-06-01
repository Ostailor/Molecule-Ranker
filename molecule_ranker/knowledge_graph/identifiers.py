from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.knowledge_graph.schemas import GraphRelation, make_entity_id

_PREFIX_ALIASES = {
    "opentargets": "OpenTargetsDisease",
    "opentargets_disease": "OpenTargetsDisease",
    "open_targets_disease": "OpenTargetsDisease",
    "efo": "EFO",
    "mondo": "MONDO",
    "umls": "UMLS",
    "mesh": "MeSH",
    "chembl_target": "ChEMBLTarget",
    "chembl_target_id": "ChEMBLTarget",
    "chembl": "ChEMBL",
    "ensembl": "Ensembl",
    "ensembl_gene": "Ensembl",
    "uniprot": "UniProt",
    "uniprotkb": "UniProt",
    "chembl_molecule": "ChEMBLMolecule",
    "chembl_molecule_id": "ChEMBLMolecule",
    "pubchem": "PubChemCID",
    "pubchem_cid": "PubChemCID",
    "inchi_key": "InChIKey",
    "inchikey": "InChIKey",
    "doi": "DOI",
    "pmid": "PMID",
    "pmcid": "PMCID",
    "openalex": "OpenAlex",
    "openalex_id": "OpenAlex",
    "candidate_id": "InternalCandidate",
    "internal_candidate_id": "InternalCandidate",
    "generated_molecule_id": "GeneratedMolecule",
    "project_id": "ProjectID",
    "run_id": "RunID",
    "artifact_id": "ArtifactID",
    "hgnc": "HGNC",
    "smiles": "SMILES",
    "canonical_smiles": "SMILES",
}

_ENTITY_IDENTIFIER_PRIORITY = {
    "disease": ["OpenTargetsDisease", "EFO", "MONDO", "UMLS", "MeSH"],
    "target": ["ChEMBLTarget", "Ensembl", "UniProt", "HGNC"],
    "molecule": ["InChIKey", "ChEMBLMolecule", "PubChemCID", "InternalCandidate"],
    "generated_molecule": ["GeneratedMolecule", "InChIKey", "InternalCandidate"],
    "literature_paper": ["DOI", "PMID", "PMCID", "OpenAlex"],
    "project": ["ProjectID", "RunID", "ArtifactID"],
    "program": ["ProjectID", "RunID", "ArtifactID"],
}


@dataclass(frozen=True)
class IdentifierConflict:
    prefix: str
    existing_value: str
    incoming_value: str
    warning: str
    review_required: bool = True


@dataclass(frozen=True)
class IdentifierMergeResult:
    identifiers: dict[str, str]
    conflicts: list[IdentifierConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    review_required: bool = False


def normalize_identifier(prefix: str, value: str) -> tuple[str, str]:
    normalized_prefix = _PREFIX_ALIASES.get(prefix.strip().lower(), prefix.strip())
    raw = value.strip()
    normalized_value = (
        raw if normalized_prefix == "ChEMBL" else _strip_known_prefix(raw, normalized_prefix)
    )
    match normalized_prefix:
        case "OpenTargetsDisease" | "EFO":
            normalized_value = _normalize_efo_like(normalized_value)
        case "MONDO":
            normalized_value = _normalize_colon_identifier(normalized_value, "MONDO")
        case "UMLS":
            normalized_value = _normalize_colon_identifier(normalized_value, "UMLS")
        case "MeSH":
            normalized_value = _strip_known_prefix(normalized_value, "MESH").upper()
        case "ChEMBL" | "ChEMBLTarget" | "ChEMBLMolecule":
            normalized_value = re.sub(r"\s+", "", normalized_value).upper()
        case "Ensembl":
            normalized_value = normalized_value.split(".", 1)[0].upper()
        case "UniProt" | "InChIKey":
            normalized_value = normalized_value.upper()
        case "PubChemCID" | "PMID":
            normalized_value = re.sub(r"\D+", "", normalized_value)
        case "PMCID":
            normalized_value = _strip_known_prefix(normalized_value, "PMCID").upper()
            if not normalized_value.startswith("PMC"):
                digits = re.sub(r"\D+", "", normalized_value)
                normalized_value = f"PMC{digits}"
        case "DOI":
            normalized_value = re.sub(
                r"^(?:https?://(?:dx\.)?doi\.org/|doi:)",
                "",
                normalized_value,
                flags=re.I,
            ).lower()
        case "OpenAlex":
            normalized_value = normalized_value.removeprefix("https://openalex.org/").upper()
        case _:
            normalized_value = normalized_value
    return normalized_prefix, normalized_value


def entity_key_from_identifiers(entity_type: str, identifiers: dict[str, str]) -> str:
    normalized = dict(normalize_identifier(prefix, value) for prefix, value in identifiers.items())
    priority = _ENTITY_IDENTIFIER_PRIORITY.get(entity_type, [])
    for prefix in priority:
        if prefix in normalized:
            return f"{entity_type}:{prefix}:{normalized[prefix]}"
    if normalized:
        prefix, value = sorted(normalized.items())[0]
        return f"{entity_type}:{prefix}:{value}"
    return make_entity_id(entity_type, "name", entity_type)


def merge_identifier_sets(*identifier_sets: dict[str, str]) -> IdentifierMergeResult:
    merged: dict[str, str] = {}
    conflicts: list[IdentifierConflict] = []
    warnings: list[str] = []
    for identifiers in identifier_sets:
        for prefix, value in identifiers.items():
            normalized_prefix, normalized_value = normalize_identifier(prefix, value)
            existing = merged.get(normalized_prefix)
            if existing is not None and existing != normalized_value:
                warning = (
                    f"Identifier conflict for {normalized_prefix}: {existing} != "
                    f"{normalized_value}; mapping requires review."
                )
                conflicts.append(
                    IdentifierConflict(
                        prefix=normalized_prefix,
                        existing_value=existing,
                        incoming_value=normalized_value,
                        warning=warning,
                    )
                )
                warnings.append(warning)
                continue
            merged[normalized_prefix] = normalized_value
    return IdentifierMergeResult(
        identifiers=merged,
        conflicts=conflicts,
        warnings=warnings,
        review_required=bool(conflicts),
    )


def detect_identifier_conflicts(
    existing: dict[str, str],
    incoming: dict[str, str],
) -> list[IdentifierConflict]:
    return merge_identifier_sets(existing, incoming).conflicts


def build_same_as_relations(
    entity_identifier_pairs: list[tuple[str, dict[str, str]]],
    *,
    mapping_method: str = "deterministic",
    source_artifact_id: str | None = None,
    user_confirmed: bool = False,
) -> list[GraphRelation]:
    active_method = "user_confirmed" if user_confirmed else mapping_method
    if mapping_method == "codex_suggested" and not user_confirmed:
        return []
    if active_method not in {"deterministic", "user_confirmed"}:
        return []
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entity_id, identifiers in entity_identifier_pairs:
        normalized = dict(
            normalize_identifier(prefix, value) for prefix, value in identifiers.items()
        )
        for prefix, value in normalized.items():
            grouped[(prefix, value)].append(entity_id)
    relations: list[GraphRelation] = []
    for (prefix, value), entity_ids in sorted(grouped.items()):
        unique_ids = sorted(set(entity_ids))
        if len(unique_ids) < 2:
            continue
        for subject, object_id in combinations(unique_ids, 2):
            relation_id = (
                "same-as:"
                + uuid5(
                    NAMESPACE_URL,
                    f"{subject}|{object_id}|{prefix}|{value}|{active_method}",
                ).hex[:16]
            )
            relations.append(
                GraphRelation(
                    relation_id=relation_id,
                    subject_entity_id=subject,
                    predicate="same_as",
                    object_entity_id=object_id,
                    relation_type="ontology_mapping",
                    confidence=1.0 if active_method == "deterministic" else 0.95,
                    direction="neutral",
                    source_artifact_ids=[source_artifact_id] if source_artifact_id else [],
                    source_record_ids=[f"{prefix}:{value}"],
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    metadata={
                        "mapping_method": active_method,
                        "identifier_prefix": prefix,
                        "identifier_value": value,
                        "codex_suggestion_activated": False,
                    },
                )
            )
    return relations


def _normalize_efo_like(value: str) -> str:
    cleaned = _strip_known_prefix(value, "EFO").upper().replace(":", "_")
    if cleaned.startswith("EFO_"):
        return cleaned
    digits = re.sub(r"\D+", "", cleaned)
    return f"EFO_{digits}" if digits else cleaned


def _normalize_colon_identifier(value: str, prefix: str) -> str:
    cleaned = _strip_known_prefix(value, prefix).upper().replace("_", ":")
    if cleaned.startswith(f"{prefix}:"):
        return cleaned
    digits = re.sub(r"\D+", "", cleaned)
    return f"{prefix}:{digits}" if digits else cleaned


def _strip_known_prefix(value: str, prefix: str) -> str:
    return re.sub(
        rf"^(?:{re.escape(prefix)}[:_\s-]*|{re.escape(prefix.lower())}[:_\s-]*)",
        "",
        value.strip(),
        flags=re.I,
    )


__all__ = [
    "IdentifierConflict",
    "IdentifierMergeResult",
    "build_same_as_relations",
    "detect_identifier_conflicts",
    "entity_key_from_identifiers",
    "make_entity_id",
    "merge_identifier_sets",
    "normalize_identifier",
]
