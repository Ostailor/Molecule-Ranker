from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from molecule_ranker.developability.structure import TargetStructureRecord
from molecule_ranker.schemas import Target


class RCSBPDBAdapter:
    """Retrieve target structure metadata from RCSB PDB APIs."""

    source_name = "RCSB PDB"
    default_search_url = "https://search.rcsb.org/rcsbsearch/v2/query"
    default_data_url = "https://data.rcsb.org/rest/v1/core"
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "molecule-ranker/0.4",
    }

    def __init__(
        self,
        *,
        search_url: str = default_search_url,
        data_url: str = default_data_url,
        timeout_seconds: float = 20.0,
        session: Any | None = None,
    ) -> None:
        self.search_url = search_url
        self.data_url = data_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.warnings: list[str] = []

    def retrieve_target_structures(
        self,
        target: Target,
        *,
        limit: int = 10,
    ) -> list[TargetStructureRecord]:
        self.warnings = []
        identifiers = self._search_identifiers(target, limit=limit)
        records: list[TargetStructureRecord] = []
        for pdb_id in identifiers[:limit]:
            payload = self._get_json(f"{self.data_url}/entry/{pdb_id}")
            if not payload:
                continue
            records.append(self._record_from_entry(target, pdb_id, payload))
        return records

    def _search_identifiers(self, target: Target, *, limit: int) -> list[str]:
        query = self._search_query(target, limit=limit)
        try:
            response = self.session.post(
                self.search_url,
                json=query,
                headers=self.request_headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
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
        uniprot = _uniprot_accession(target)
        if uniprot:
            terminal = {
                "type": "terminal",
                "service": "text",
                "parameters": {
                    "attribute": (
                        "rcsb_polymer_entity_container_identifiers."
                        "reference_sequence_identifiers.database_accession"
                    ),
                    "operator": "exact_match",
                    "value": uniprot,
                },
            }
        else:
            terminal = {
                "type": "terminal",
                "service": "full_text",
                "parameters": {"value": target.name or target.symbol},
            }
        return {
            "query": terminal,
            "return_type": "entry",
            "request_options": {"paginate": {"start": 0, "rows": limit}},
        }

    def _record_from_entry(
        self,
        target: Target,
        pdb_id: str,
        payload: dict[str, Any],
    ) -> TargetStructureRecord:
        polymer_entities = self._polymer_entities(pdb_id, payload)
        chains = sorted(
            {
                chain
                for entity in polymer_entities
                for chain in self._chains_from_entity(entity)
            }
        )
        uniprot_accessions = sorted(
            {
                accession
                for entity in polymer_entities
                for accession in self._uniprot_from_entity(entity)
            }
        )
        ligands = sorted(set(self._ligands_from_entry(payload)))
        method = _experimental_method(payload)
        resolution_values = _nested(
            payload,
            "rcsb_entry_info",
            "resolution_combined",
            default=[],
        )
        resolution = (
            _first_float(*resolution_values)
            if isinstance(resolution_values, list)
            else _first_float(resolution_values)
        )
        return TargetStructureRecord(
            target_symbol=target.symbol,
            structure_id=pdb_id.upper(),
            source="RCSB PDB",
            structure_kind="experimental",
            method=method,
            resolution=resolution,
            chains=chains,
            ligands=ligands,
            uniprot_accessions=uniprot_accessions,
            has_binding_site_annotation=bool(ligands),
            confidence=_experimental_confidence(resolution, bool(uniprot_accessions)),
            provenance={
                "source": self.source_name,
                "entry_url": f"{self.data_url}/entry/{pdb_id.upper()}",
                "retrieved_at": datetime.now(UTC).isoformat(),
            },
            metadata={"raw_entry_id": pdb_id.upper()},
        )

    def _polymer_entities(self, pdb_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        embedded = payload.get("polymer_entities")
        if isinstance(embedded, list):
            return [entity for entity in embedded if isinstance(entity, dict)]
        entity_ids = _nested(
            payload,
            "rcsb_entry_container_identifiers",
            "polymer_entity_ids",
            default=[],
        )
        entities: list[dict[str, Any]] = []
        for entity_id in entity_ids if isinstance(entity_ids, list) else []:
            entity = self._get_json(f"{self.data_url}/polymer_entity/{pdb_id}/{entity_id}")
            if entity:
                entities.append(entity)
        return entities

    def _chains_from_entity(self, entity: dict[str, Any]) -> list[str]:
        identifiers = entity.get("rcsb_polymer_entity_container_identifiers") or {}
        chains = identifiers.get("auth_asym_ids") or identifiers.get("asym_ids") or []
        return [str(chain) for chain in chains if chain not in (None, "")]

    def _uniprot_from_entity(self, entity: dict[str, Any]) -> list[str]:
        identifiers = entity.get("rcsb_polymer_entity_container_identifiers") or {}
        values: list[str] = []
        for item in identifiers.get("reference_sequence_identifiers") or []:
            if not isinstance(item, dict):
                continue
            database_name = str(item.get("database_name") or "").lower()
            accession = item.get("database_accession")
            if "uniprot" in database_name and accession:
                values.append(str(accession))
        for key in ("uniprot_ids", "uniprot_accessions"):
            raw = identifiers.get(key) or entity.get(key) or []
            values.extend(str(value) for value in raw if value not in (None, ""))
        return values

    def _ligands_from_entry(self, payload: dict[str, Any]) -> list[str]:
        values: list[str] = []
        for entity in payload.get("nonpolymer_entities") or []:
            if not isinstance(entity, dict):
                continue
            values.extend(
                filter(
                    None,
                    [
                        _nested(entity, "pdbx_entity_nonpoly", "comp_id"),
                        _nested(entity, "chem_comp", "id"),
                        _nested(
                            entity,
                            "rcsb_nonpolymer_entity_container_identifiers",
                            "nonpolymer_comp_id",
                        ),
                    ],
                )
            )
        ligand_ids = _nested(
            payload,
            "rcsb_entry_container_identifiers",
            "non_polymer_entity_ids",
            default=[],
        )
        if isinstance(ligand_ids, list):
            values.extend(str(value) for value in ligand_ids if value not in (None, ""))
        return [str(value) for value in values]

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            response = self.session.get(
                url,
                headers=self.request_headers,
                timeout=self.timeout_seconds,
            )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.warnings.append(f"RCSB PDB metadata retrieval failed for {url}: {exc}")
            return {}
        return payload if isinstance(payload, dict) else {}


class AlphaFoldDBAdapter:
    """Retrieve predicted structure metadata from AlphaFold DB."""

    source_name = "AlphaFold DB"
    default_base_url = "https://alphafold.ebi.ac.uk/api"
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "molecule-ranker/0.4",
    }

    def __init__(
        self,
        *,
        base_url: str = default_base_url,
        timeout_seconds: float = 20.0,
        session: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.warnings: list[str] = []

    def retrieve_target_structures(
        self,
        target: Target,
        *,
        limit: int = 10,
    ) -> list[TargetStructureRecord]:
        self.warnings = []
        uniprot = _uniprot_accession(target)
        if not uniprot:
            self.warnings.append(
                f"AlphaFold DB lookup skipped for {target.symbol}: missing UniProt accession."
            )
            return []
        try:
            response = self.session.get(
                f"{self.base_url}/prediction/{uniprot}",
                headers=self.request_headers,
                timeout=self.timeout_seconds,
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.warnings.append(f"AlphaFold DB lookup failed for {target.symbol}: {exc}")
            return []
        records = payload if isinstance(payload, list) else [payload]
        return [
            self._record_from_prediction(target, uniprot, record)
            for record in records[:limit]
            if isinstance(record, dict)
        ]

    def _record_from_prediction(
        self,
        target: Target,
        uniprot: str,
        payload: dict[str, Any],
    ) -> TargetStructureRecord:
        structure_id = str(
            payload.get("entryId")
            or payload.get("alphafoldId")
            or payload.get("id")
            or f"AF-{uniprot}"
        )
        confidence_metadata = _alphafold_confidence_metadata(payload)
        confidence_value = _first_float(
            confidence_metadata.get("global_metric_value"),
            confidence_metadata.get("average_plddt"),
            confidence_metadata.get("confidence_score"),
        )
        normalized_confidence = _normalize_plddt(confidence_value)
        return TargetStructureRecord(
            target_symbol=target.symbol,
            structure_id=structure_id,
            source="AlphaFold DB",
            structure_kind="predicted",
            method="AlphaFold predicted model",
            resolution=None,
            chains=[],
            ligands=[],
            uniprot_accessions=[str(payload.get("uniprotAccession") or uniprot)],
            has_binding_site_annotation=False,
            confidence=min(0.55, 0.35 + 0.20 * normalized_confidence),
            provenance={
                "source": self.source_name,
                "api_url": f"{self.base_url}/prediction/{uniprot}",
                "entry_url": payload.get("entryUrl") or f"https://alphafold.ebi.ac.uk/entry/{uniprot}",
                "retrieved_at": datetime.now(UTC).isoformat(),
            },
            metadata={
                "confidence_metadata": confidence_metadata,
                "cif_url": payload.get("cifUrl"),
                "pdb_url": payload.get("pdbUrl"),
                "pae_doc_url": payload.get("paeDocUrl"),
                "model_created_date": payload.get("modelCreatedDate"),
                "sequence_version": payload.get("sequenceVersion"),
            },
        )


def _uniprot_accession(target: Target) -> str | None:
    for key in ("uniprot", "uniprot_accession", "uniprotkb", "protein_accession"):
        value = target.identifiers.get(key)
        if value:
            return str(value)
    for protein_id in target.metadata.get("protein_ids") or []:
        if not isinstance(protein_id, dict):
            continue
        source = str(protein_id.get("source") or "").lower()
        value = protein_id.get("id") or protein_id.get("accession")
        if "uniprot" in source and value:
            return str(value)
    return None


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


def _first_string(*values: Any) -> str | None:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return None


def _experimental_method(payload: dict[str, Any]) -> str | None:
    values: list[Any] = [_nested(payload, "exptl", 0, "method")]
    methods = _nested(payload, "rcsb_entry_info", "experimental_method", default=[])
    if isinstance(methods, list):
        values.extend(methods)
    else:
        values.append(methods)
    return _first_string(*values)


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _experimental_confidence(resolution: float | None, has_uniprot_mapping: bool) -> float:
    confidence = 0.70
    if has_uniprot_mapping:
        confidence += 0.10
    if resolution is not None:
        confidence += max(0.0, min(0.15, (3.0 - resolution) * 0.05))
    return max(0.0, min(0.95, confidence))


def _alphafold_confidence_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "global_metric_value": payload.get("globalMetricValue"),
        "average_plddt": payload.get("averagePlddt") or payload.get("avgPlddt"),
        "confidence_score": payload.get("confidenceScore"),
        "model_confidence": payload.get("modelConfidence"),
    }


def _normalize_plddt(value: float | None) -> float:
    if value is None:
        return 0.5
    if value > 1.0:
        return max(0.0, min(1.0, value / 100.0))
    return max(0.0, min(1.0, value))


__all__ = ["AlphaFoldDBAdapter", "RCSBPDBAdapter"]
