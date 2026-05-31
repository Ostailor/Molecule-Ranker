from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from molecule_ranker.schemas import Target
from molecule_ranker.structure.schemas import StructureRecord
from molecule_ranker.structure.sources import (
    StructureSourceHealthStatus,
    timed_health_check,
    write_raw_metadata_artifact,
)
from molecule_ranker.utils.cache import JsonCache


class RCSBStructureAdapter:
    """Retrieve conservative target structure metadata from RCSB PDB."""

    source_name = "RCSB_PDB"
    default_search_url = "https://search.rcsb.org/rcsbsearch/v2/query"
    default_data_url = "https://data.rcsb.org/rest/v1/core"
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "molecule-ranker/1.4",
    }

    def __init__(
        self,
        *,
        search_url: str = default_search_url,
        data_url: str = default_data_url,
        timeout_seconds: float = 20.0,
        session: Any | None = None,
        cache_dir: Path | None = None,
        raw_artifact_dir: Path | None = None,
        ttl_seconds: int | None = 604800,
    ) -> None:
        self.search_url = search_url
        self.data_url = data_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.cache = JsonCache(cache_dir) if cache_dir is not None else None
        self.raw_artifact_dir = raw_artifact_dir
        self.ttl_seconds = ttl_seconds
        self.warnings: list[str] = []

    def retrieve(self, target: Target, *, limit: int = 10) -> list[StructureRecord]:
        self.warnings = []
        identifiers = self._search_identifiers(target, limit=limit)
        records: list[StructureRecord] = []
        for pdb_id in identifiers[:limit]:
            entry = self._get_json(f"{self.data_url}/entry/{pdb_id}")
            if not isinstance(entry, dict) or not entry:
                continue
            record = self._record_from_entry(target, pdb_id, entry)
            if record is not None:
                records.append(record)
        return records

    def health_check(self) -> StructureSourceHealthStatus:
        return timed_health_check(source=self.source_name, check=self._health_payload)

    def _health_payload(self) -> dict[str, Any]:
        query = {
            "query": {
                "type": "terminal",
                "service": "full_text",
                "parameters": {"value": "kinase"},
            },
            "return_type": "entry",
            "request_options": {"paginate": {"start": 0, "rows": 0}},
        }
        payload = self._post_json(self.search_url, query)
        result_count = len(payload.get("result_set", [])) if isinstance(payload, dict) else 0
        return {"result_count": result_count}

    def _search_identifiers(self, target: Target, *, limit: int) -> list[str]:
        query = self._search_query(target, limit=limit)
        try:
            payload = self._post_json(self.search_url, query)
        except Exception as exc:
            self.warnings.append(f"RCSB PDB structure search failed for {target.symbol}: {exc}")
            return []
        result_set = payload.get("result_set", []) if isinstance(payload, dict) else []
        identifiers = [
            str(result.get("identifier")).upper()
            for result in result_set
            if isinstance(result, dict) and result.get("identifier")
        ]
        return list(dict.fromkeys(identifiers))

    def _search_query(self, target: Target, *, limit: int) -> dict[str, Any]:
        nodes = []
        for accession in _target_uniprot_accessions(target):
            nodes.append(
                {
                    "type": "terminal",
                    "service": "text",
                    "parameters": {
                        "attribute": (
                            "rcsb_polymer_entity_container_identifiers."
                            "reference_sequence_identifiers.database_accession"
                        ),
                        "operator": "exact_match",
                        "value": accession,
                    },
                }
            )
        for value in _target_text_terms(target):
            nodes.append(
                {
                    "type": "terminal",
                    "service": "full_text",
                    "parameters": {"value": value},
                }
            )
        query = (
            nodes[0]
            if len(nodes) == 1
            else {"type": "group", "logical_operator": "or", "nodes": nodes}
        )
        return {
            "query": query,
            "return_type": "entry",
            "request_options": {"paginate": {"start": 0, "rows": limit}},
        }

    def _record_from_entry(
        self,
        target: Target,
        pdb_id: str,
        entry: dict[str, Any],
    ) -> StructureRecord | None:
        polymer_entities = self._polymer_entities(pdb_id, entry)
        uniprot_accessions = sorted(
            {
                accession
                for entity in polymer_entities
                for accession in _uniprot_from_entity(entity)
            }
        )
        expected_uniprots = _target_uniprot_accessions(target)
        if expected_uniprots and not (set(expected_uniprots) & set(uniprot_accessions)):
            self.warnings.append(
                f"Rejected {pdb_id}: ambiguous target mapping for {target.symbol}."
            )
            return None
        if not expected_uniprots and len(set(uniprot_accessions)) > 1:
            self.warnings.append(
                f"Rejected {pdb_id}: ambiguous multi-protein mapping for {target.symbol}."
            )
            return None

        chains = sorted(
            {
                chain
                for entity in polymer_entities
                for chain in _chains_from_entity(entity)
            }
        )
        organisms = sorted(
            {
                organism
                for entity in polymer_entities
                for organism in _organisms_from_entity(entity)
            }
        )
        external_id = pdb_id.upper()
        raw_artifact = write_raw_metadata_artifact(
            raw_artifact_dir=self.raw_artifact_dir,
            source=self.source_name,
            external_id=external_id,
            payload={"entry": entry, "polymer_entities": polymer_entities},
        )
        metadata = {
            "raw_entry_id": external_id,
            "biological_relevance_not_assumed": True,
            "target_mapping_policy": "reject_if_uniprot_mapping_ambiguous",
        }
        if raw_artifact:
            metadata["raw_metadata_artifact"] = raw_artifact
        return StructureRecord(
            structure_id=f"{self.source_name}:{external_id}",
            source=self.source_name,
            external_id=external_id,
            target_symbol=target.symbol,
            target_identifiers=dict(target.identifiers),
            structure_type="experimental",
            experimental_method=_normalize_method(_experimental_method(entry)),
            resolution_angstrom=_resolution(entry),
            coverage=_coverage(polymer_entities, uniprot_accessions),
            chains=chains,
            ligands=_ligands_from_entry(entry),
            mutations=_mutations_from_entry(entry),
            organism=organisms[0] if organisms else target.organism,
            release_date=_release_date(entry),
            quality_metrics=_quality_metrics(entry),
            url=f"https://www.rcsb.org/structure/{external_id}",
            retrieved_at=datetime.now(UTC),
            metadata=metadata,
        )

    def _polymer_entities(self, pdb_id: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
        embedded = entry.get("polymer_entities")
        if isinstance(embedded, list):
            return [entity for entity in embedded if isinstance(entity, dict)]
        entity_ids = _nested(
            entry,
            "rcsb_entry_container_identifiers",
            "polymer_entity_ids",
            default=[],
        )
        entities: list[dict[str, Any]] = []
        for entity_id in entity_ids if isinstance(entity_ids, list) else []:
            payload = self._get_json(f"{self.data_url}/polymer_entity/{pdb_id}/{entity_id}")
            if isinstance(payload, dict) and payload:
                entities.append(payload)
        return entities

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        cached = self._cache_get("POST", url, payload)
        if cached is not None:
            return cached
        response = self.session.post(
            url,
            json=payload,
            headers=self.request_headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()
        result = result if isinstance(result, dict) else {}
        self._cache_set("POST", url, payload, result)
        return result

    def _get_json(self, url: str) -> dict[str, Any]:
        cached = self._cache_get("GET", url, None)
        if cached is not None:
            return cached
        response = self.session.get(
            url,
            headers=self.request_headers,
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        result = response.json()
        result = result if isinstance(result, dict) else {}
        self._cache_set("GET", url, None, result)
        return result

    def _cache_get(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if self.cache is None:
            return None
        key = self.cache.make_key(
            self.source_name,
            {"method": method, "url": url, "payload": payload},
        )
        cached = self.cache.get(key, ttl_seconds=self.ttl_seconds)
        if isinstance(cached, dict):
            response = cached.get("response")
            return response if isinstance(response, dict) else None
        return None

    def _cache_set(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        response: dict[str, Any],
    ) -> None:
        if self.cache is None:
            return
        request = {"method": method, "url": url, "payload": payload}
        key = self.cache.make_key(self.source_name, request)
        self.cache.set(
            key,
            {"response": response},
            source=self.source_name,
            endpoint=url,
            request=request,
            ttl_seconds=self.ttl_seconds,
        )


def _target_uniprot_accessions(target: Target) -> list[str]:
    values = []
    for key in ("uniprot", "uniprot_accession", "uniprotkb", "protein_accession"):
        value = target.identifiers.get(key)
        if value:
            values.append(str(value))
    for protein_id in target.metadata.get("protein_ids") or []:
        if not isinstance(protein_id, dict):
            continue
        source = str(protein_id.get("source") or "").lower()
        value = protein_id.get("id") or protein_id.get("accession")
        if "uniprot" in source and value:
            values.append(str(value))
    return list(dict.fromkeys(values))


def _target_text_terms(target: Target) -> list[str]:
    terms = [target.symbol]
    if target.name:
        terms.append(target.name)
    for key in ("chembl_target_id", "chembl", "opentargets_id", "open_targets_id"):
        value = target.identifiers.get(key)
        if value:
            terms.append(str(value))
    return [term for term in dict.fromkeys(terms) if term]


def _nested(payload: dict[str, Any], *path: Any, default: Any = None) -> Any:
    current: Any = payload
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and len(current) > key:
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def _uniprot_from_entity(entity: dict[str, Any]) -> list[str]:
    identifiers = entity.get("rcsb_polymer_entity_container_identifiers") or {}
    values: list[str] = []
    for item in identifiers.get("reference_sequence_identifiers") or []:
        if not isinstance(item, dict):
            continue
        database_name = str(item.get("database_name") or "").lower()
        accession = item.get("database_accession")
        if "uniprot" in database_name and accession:
            values.append(str(accession))
    return values


def _chains_from_entity(entity: dict[str, Any]) -> list[str]:
    identifiers = entity.get("rcsb_polymer_entity_container_identifiers") or {}
    chains = identifiers.get("auth_asym_ids") or identifiers.get("asym_ids") or []
    return [str(chain) for chain in chains if chain not in (None, "")]


def _organisms_from_entity(entity: dict[str, Any]) -> list[str]:
    values = []
    for item in entity.get("rcsb_entity_source_organism") or []:
        if isinstance(item, dict) and item.get("scientific_name"):
            values.append(str(item["scientific_name"]))
    return values


def _experimental_method(entry: dict[str, Any]) -> str | None:
    values: list[Any] = [_nested(entry, "exptl", 0, "method")]
    methods = _nested(entry, "rcsb_entry_info", "experimental_method", default=[])
    values.extend(methods if isinstance(methods, list) else [methods])
    for value in values:
        if value not in (None, ""):
            return str(value)
    return None


def _normalize_method(method: str | None) -> str | None:
    if method is None:
        return None
    mapping = {
        "X-RAY DIFFRACTION": "X-ray diffraction",
        "ELECTRON MICROSCOPY": "cryo-EM",
        "SOLUTION NMR": "NMR",
    }
    return mapping.get(method.upper(), method)


def _resolution(entry: dict[str, Any]) -> float | None:
    values = _nested(entry, "rcsb_entry_info", "resolution_combined", default=[])
    values = values if isinstance(values, list) else [values]
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _ligands_from_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    ligands = []
    for entity in entry.get("nonpolymer_entities") or []:
        if not isinstance(entity, dict):
            continue
        ligand_id = (
            _nested(entity, "pdbx_entity_nonpoly", "comp_id")
            or _nested(entity, "chem_comp", "id")
            or _nested(entity, "rcsb_nonpolymer_entity_container_identifiers", "nonpolymer_comp_id")
        )
        if ligand_id:
            ligands.append(
                {
                    "ligand_id": str(ligand_id),
                    "name": _nested(entity, "chem_comp", "name"),
                    "source": "RCSB_PDB",
                }
            )
    return ligands


def _mutations_from_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    mutations = entry.get("mutations") or []
    return [item for item in mutations if isinstance(item, dict)]


def _release_date(entry: dict[str, Any]) -> str | None:
    value = _nested(entry, "rcsb_accession_info", "initial_release_date")
    return str(value)[:10] if value else None


def _coverage(polymer_entities: list[dict[str, Any]], uniprots: list[str]) -> dict[str, Any]:
    lengths = [
        _nested(entity, "entity_poly", "rcsb_sample_sequence_length")
        for entity in polymer_entities
    ]
    return {
        "mapped_uniprot_accessions": uniprots,
        "entity_sequence_lengths": [int(value) for value in lengths if isinstance(value, int)],
    }


def _quality_metrics(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_angstrom": _resolution(entry),
        "experimental_method": _normalize_method(_experimental_method(entry)),
    }


__all__ = ["RCSBStructureAdapter"]
