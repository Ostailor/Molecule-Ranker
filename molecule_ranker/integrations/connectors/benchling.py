from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import requests

from molecule_ranker.experimental.schemas import AssayResult
from molecule_ranker.integrations.connectors.base import (
    AssayConnector,
    ConnectorCallRecorder,
    ConnectorError,
    ELNConnector,
    RegistryConnector,
)
from molecule_ranker.integrations.schemas import (
    ConnectorConfig,
    EntityMapping,
    ExternalRecordRef,
    IntegrationHealthStatus,
)

CredentialResolver = Callable[[str], str]

DEFAULT_REGISTRY_FIELD_MAPPING = {
    "external_registry_id": ["registry_id", "registryId", "fields.registry_id"],
    "inchi_key": ["inchi_key", "InChIKey", "fields.inchi_key", "fields.InChIKey"],
    "canonical_smiles": [
        "canonical_smiles",
        "canonicalSmiles",
        "fields.canonical_smiles",
        "fields.canonicalSmiles",
    ],
    "name": ["name", "displayName", "fields.name"],
}

DEFAULT_ASSAY_RESULT_MAPPING = {
    "experiment_id": ["experiment_id", "assay_run_id", "run_id", "fields.experiment_id"],
    "assay_name": ["assay_name", "assayName", "fields.assay_name"],
    "candidate_id": ["candidate_id", "fields.candidate_id"],
    "molecule_name": ["molecule_name", "entity_name", "fields.molecule_name"],
    "outcome": ["outcome", "outcome_label", "fields.outcome"],
    "value": ["value", "measured_value", "fields.value"],
    "unit": ["unit", "fields.unit"],
}


class BenchlingConnector(RegistryConnector, AssayConnector, ELNConnector):
    connector_name = "benchling"
    provider = "benchling"
    system_type = "eln"
    capabilities = (
        "health_check",
        "eln_lims_read",
        "compound_registry_read",
        "assay_run_read",
        "assay_result_read",
        "external_id_mapping",
        "review_dossier_export",
        "assay_summary_export",
        "webhook_ingestion",
    )
    limitations = RegistryConnector.limitations + (
        "Benchling write-back is disabled unless explicitly configured.",
        "Tenant schema names and field IDs must be supplied through connector configuration.",
        (
            "No instrument control, lab protocol, synthesis, dosing, or treatment guidance "
            "is supported."
        ),
    )

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        recorder: ConnectorCallRecorder | None = None,
        http_client: Any | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        effective_config = config
        if config.mode == "dry_run" and not config.allow_writes:
            effective_config = config.model_copy(update={"mode": "read_only"})
        super().__init__(effective_config, recorder=recorder)
        self.http_client = http_client or requests.Session()
        self.credential_resolver = credential_resolver

    def health_check(self) -> IntegrationHealthStatus:
        def call() -> IntegrationHealthStatus:
            base_url = self._base_url(required=False)
            try:
                token = self._api_key()
            except ConnectorError as exc:
                return self._health("unconfigured", str(exc))
            if not base_url:
                return self._health("unconfigured", "BENCHLING_BASE_URL is not configured.")
            try:
                self._request(
                    "GET",
                    self._config_value("benchling_health_path", "/api/v2/users/me"),
                )
            except Exception as exc:
                return self._health("degraded", f"Benchling health check failed: {exc}")
            if not token:
                return self._health("unconfigured", "Benchling credential is not configured.")
            return self._health("ok", "Benchling connection is configured.")

        return self._call("health_check", call)

    def _search_molecule(
        self,
        query: str | None = None,
        *,
        registry_id: str | None = None,
        inchi_key: str | None = None,
        canonical_smiles: str | None = None,
        name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"pageSize": limit}
        schema_id = self.config.config.get("benchling_registry_schema_id")
        if schema_id:
            params["schemaId"] = schema_id
        if query:
            params["name"] = query
        search_values = {
            "external_registry_id": registry_id,
            "inchi_key": inchi_key,
            "canonical_smiles": canonical_smiles,
            "name": name,
        }
        for logical_name, raw_value in search_values.items():
            if raw_value:
                params[logical_name] = raw_value
        response = self._request(
            "GET",
            self._config_value("benchling_registry_search_path", "/api/v2/custom-entities"),
            params=params,
        )
        entities = _extract_items(response, "customEntities", "entities", "items")
        return [self._entity_result(entity) for entity in entities]

    def _get_molecule(self, molecule_id: str) -> dict[str, Any]:
        path_template = self._config_value(
            "benchling_registry_entity_path_template",
            "/api/v2/custom-entities/{entity_id}",
        )
        response = self._request("GET", path_template.format(entity_id=molecule_id))
        return self._entity_result(response)

    def _map_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "candidate")
        project_id = candidate.get("project_id")
        confirmed_id = candidate.get("user_confirmed_external_record_id")
        if confirmed_id:
            match = self._get_molecule(str(confirmed_id))
            mapping = self._mapping(
                candidate_id=candidate_id,
                project_id=project_id,
                match=match,
                method="user_confirmed",
                confidence=1.0,
                status="active",
                metadata={"matched_on": "user_confirmed_external_record_id"},
            )
            return {"mapping": mapping, "external_ref": mapping.external_ref, "matches": [match]}

        attempts = [
            ("registry_id", candidate.get("external_registry_id") or candidate.get("registry_id")),
            ("inchi_key", candidate.get("inchi_key") or candidate.get("InChIKey")),
            (
                "canonical_smiles",
                candidate.get("canonical_smiles") or candidate.get("canonicalSmiles"),
            ),
            ("name_exact", candidate.get("name") or candidate.get("candidate_name")),
        ]
        for method, value in attempts:
            if not value:
                continue
            matches = self._candidate_matches(method, str(value))
            exact_matches = [
                match for match in matches if self._match_is_exact(match, method, str(value))
            ]
            if len(exact_matches) == 1:
                mapping = self._mapping(
                    candidate_id=candidate_id,
                    project_id=project_id,
                    match=exact_matches[0],
                    method=method,
                    confidence=1.0,
                    status="active",
                    metadata={"matched_on": method},
                )
                return {
                    "mapping": mapping,
                    "external_ref": mapping.external_ref,
                    "matches": matches,
                }
            if len(exact_matches) > 1:
                mapping = self._mapping(
                    candidate_id=candidate_id,
                    project_id=project_id,
                    match=exact_matches[0],
                    method=method,
                    confidence=0.5,
                    status="pending_review",
                    metadata={
                        "matched_on": method,
                        "ambiguity": "multiple_exact_matches",
                        "candidate_match_count": len(exact_matches),
                    },
                )
                return {
                    "mapping": mapping,
                    "external_ref": mapping.external_ref,
                    "matches": matches,
                }
        return {
            "candidate_id": candidate_id,
            "status": "pending_review",
            "reason": "no deterministic Benchling entity match",
            "external_ref": ExternalRecordRef(
                external_system_id=self.config.connector_id,
                external_record_type="mapping_attempt",
                external_record_id=f"unmapped-{candidate_id}",
                retrieved_at=datetime.now(UTC),
                metadata={"provider": "benchling"},
            ),
        }

    def _export_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        return self._create_notebook_entry({"candidate": candidate, "type": "candidate_review"})

    def _list_assay_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        path = self.config.config.get("benchling_assay_runs_path")
        if not path:
            raise ConnectorError("benchling_assay_runs_path is not configured.")
        response = self._request("GET", str(path), params={"pageSize": limit})
        runs = _extract_items(response, "assayRuns", "runs", "items")
        return [self._record_result(run, "assay_run") for run in runs]

    def _get_assay_results(
        self,
        *,
        assay_run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        path = self.config.config.get("benchling_assay_results_path")
        if not path:
            raise ConnectorError("benchling_assay_results_path is not configured.")
        params: dict[str, Any] = {"pageSize": limit}
        if assay_run_id:
            params["assayRunId"] = assay_run_id
        response = self._request("GET", str(path), params=params)
        results = _extract_items(response, "assayResults", "results", "items")
        return [self._record_result(result, "assay_result") for result in results]

    def _import_assay_results(
        self,
        *,
        assay_run_id: str | None = None,
        limit: int = 100,
    ) -> list[AssayResult]:
        rows = self._get_assay_results(assay_run_id=assay_run_id, limit=limit)
        return [self._assay_result(row["raw"]) for row in rows]

    def _export_assay_results(self, results: list[dict[str, Any] | AssayResult]) -> dict[str, Any]:
        path = self._config_value(
            "benchling_assay_summary_export_path",
            "/api/v2/assay-results",
        )
        payload = {
            "projectId": self.config.config.get("benchling_project_id"),
            "results": [_assay_summary(result) for result in results],
        }
        response = self._request("POST", path, json=payload)
        return self._record_result(response, "assay_result_summary")

    def _create_notebook_entry(self, review_dossier: dict[str, Any]) -> dict[str, Any]:
        folder_id = self.config.config.get("benchling_notebook_folder_id")
        if not folder_id:
            raise ConnectorError("benchling_notebook_folder_id is required for notebook export.")
        payload = {
            "folderId": folder_id,
            "projectId": self.config.config.get("benchling_project_id"),
            "name": review_dossier.get("title") or review_dossier.get("name") or "Review dossier",
            "fields": _without_none(
                {
                    "summary": review_dossier.get("summary"),
                    "artifact_ids": review_dossier.get("artifact_ids"),
                }
            ),
        }
        response = self._request(
            "POST",
            self._config_value("benchling_notebook_entries_path", "/api/v2/entries"),
            json=payload,
        )
        return self._record_result(response, "notebook_entry")

    def _attach_report(self, entry_id: str, report: dict[str, Any]) -> dict[str, Any]:
        path_template = self._config_value(
            "benchling_attachment_path_template",
            "/api/v2/entries/{entry_id}/attachments",
        )
        payload = {
            "entryId": entry_id,
            "name": report.get("name") or report.get("title") or "molecule-ranker report",
            "artifactIds": report.get("artifact_ids", []),
            "summary": report.get("summary"),
        }
        response = self._request("POST", path_template.format(entry_id=entry_id), json=payload)
        return self._record_result(response, "notebook_entry")

    def _list_entries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            self._config_value("benchling_notebook_entries_path", "/api/v2/entries"),
            params={"pageSize": limit},
        )
        entries = _extract_items(response, "entries", "items")
        return [self._record_result(entry, "notebook_entry") for entry in entries]

    def _get_entry(self, entry_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/api/v2/entries/{entry_id}")
        return self._record_result(response, "notebook_entry")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        base_url = self._base_url(required=True)
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._api_key()}"
        headers.setdefault("Accept", "application/json")
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        response = self.http_client.request(method, url, headers=headers, timeout=30, **kwargs)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        if hasattr(response, "json"):
            return response.json()
        return response

    def _api_key(self) -> str:
        credential_ref = self.config.credential_ref
        if credential_ref and credential_ref.backend == "env":
            if not credential_ref.key_ref:
                raise ConnectorError("Benchling environment credential reference is empty.")
            value = os.environ.get(credential_ref.key_ref)
            if not value:
                raise ConnectorError(
                    f"Benchling credential env var {credential_ref.key_ref} is not set."
                )
            return value
        if credential_ref and self.credential_resolver:
            return self.credential_resolver(credential_ref.credential_id)
        if credential_ref:
            raise ConnectorError(
                "Benchling credential reference requires a resolver for non-env backends."
            )
        value = os.environ.get("BENCHLING_API_KEY")
        if not value:
            raise ConnectorError(
                "Benchling credential is not configured; set BENCHLING_API_KEY or credential_ref."
            )
        return value

    def _base_url(self, *, required: bool) -> str:
        value = self.config.base_url or os.environ.get("BENCHLING_BASE_URL") or ""
        if required and not value:
            raise ConnectorError("BENCHLING_BASE_URL is not configured.")
        return value

    def _config_value(self, key: str, default: str) -> str:
        return str(self.config.config.get(key) or default)

    def _health(self, status: str, message: str) -> IntegrationHealthStatus:
        return IntegrationHealthStatus(
            connector_id=self.config.connector_id,
            provider=self.config.provider,
            status=status,  # type: ignore[arg-type]
            message=message,
            capabilities=list(self.capabilities),
            limitations=list(self.limitations),
        )

    def _field_mapping(self) -> dict[str, list[str]]:
        configured = self.config.config.get("benchling_registry_field_mapping") or {}
        return {
            key: list(configured.get(key, default_paths))
            for key, default_paths in DEFAULT_REGISTRY_FIELD_MAPPING.items()
        }

    def _assay_mapping(self) -> dict[str, list[str]]:
        configured = self.config.config.get("benchling_assay_result_mapping") or {}
        return {
            key: list(configured.get(key, default_paths))
            for key, default_paths in DEFAULT_ASSAY_RESULT_MAPPING.items()
        }

    def _entity_result(self, entity: dict[str, Any]) -> dict[str, Any]:
        ref = self._external_ref(entity, "registry_entry")
        mapping = self._field_mapping()
        return {
            "external_ref": ref,
            "benchling_id": ref.external_record_id,
            "url": ref.external_url,
            "registry_id": _first_value(entity, mapping["external_registry_id"]),
            "inchi_key": _first_value(entity, mapping["inchi_key"]),
            "canonical_smiles": _first_value(entity, mapping["canonical_smiles"]),
            "name": _first_value(entity, mapping["name"]),
            "raw": entity,
        }

    def _record_result(self, record: dict[str, Any], record_type: str) -> dict[str, Any]:
        ref = self._external_ref(record, record_type)
        return {
            "external_ref": ref,
            "benchling_id": ref.external_record_id,
            "url": ref.external_url,
            "raw": record,
        }

    def _external_ref(self, record: dict[str, Any], record_type: str) -> ExternalRecordRef:
        record_id = _record_id(record)
        if not record_id:
            raise ConnectorError(f"Benchling {record_type} record is missing an ID.")
        return ExternalRecordRef(
            external_system_id=self.config.connector_id,
            external_record_type=record_type,
            external_record_id=record_id,
            external_url=_record_url(record),
            external_version=str(record.get("modifiedAt") or record.get("archiveRecordId") or "")
            or None,
            retrieved_at=datetime.now(UTC),
            metadata=_without_none(
                {
                    "provider": "benchling",
                    "schema_id": record.get("schemaId") or _nested(record, "schema.id"),
                    "registry_id": record.get("registryId"),
                }
            ),
        )

    def _candidate_matches(self, method: str, value: str) -> list[dict[str, Any]]:
        if method == "registry_id":
            return self._search_molecule(registry_id=value)
        if method == "inchi_key":
            return self._search_molecule(inchi_key=value)
        if method == "canonical_smiles":
            return self._search_molecule(canonical_smiles=value)
        return self._search_molecule(name=value)

    def _match_is_exact(self, match: dict[str, Any], method: str, value: str) -> bool:
        key = "registry_id" if method == "registry_id" else method
        if method == "name_exact":
            key = "name"
        observed = match.get(key)
        if observed is None:
            return False
        return str(observed).strip().lower() == value.strip().lower()

    def _mapping(
        self,
        *,
        candidate_id: str,
        project_id: str | None,
        match: dict[str, Any],
        method: str,
        confidence: float,
        status: str,
        metadata: dict[str, Any],
    ) -> EntityMapping:
        return EntityMapping(
            mapping_id=f"map-{uuid4().hex[:16]}",
            project_id=project_id,
            internal_entity_type="candidate",
            internal_entity_id=candidate_id,
            external_ref=match["external_ref"],
            mapping_method=method,  # type: ignore[arg-type]
            mapping_confidence=confidence,
            status=status,  # type: ignore[arg-type]
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by=None,
            metadata=metadata,
        )

    def _assay_result(self, raw: dict[str, Any]) -> AssayResult:
        mapping = self._assay_mapping()
        source_id = _record_id(raw)
        if not source_id:
            raise ConnectorError("Benchling assay result is missing an ID.")
        return AssayResult(
            experiment_id=_string_or_none(_first_value(raw, mapping["experiment_id"])),
            assay_name=_string_or_none(_first_value(raw, mapping["assay_name"])),
            candidate_id=_string_or_none(_first_value(raw, mapping["candidate_id"])),
            molecule_name=_string_or_none(_first_value(raw, mapping["molecule_name"])),
            outcome=_string_or_none(_first_value(raw, mapping["outcome"])),  # type: ignore[arg-type]
            value=_float_or_none(_first_value(raw, mapping["value"])),
            unit=_string_or_none(_first_value(raw, mapping["unit"])),
            provenance={
                "source_type": "connected_system",
                "source_system": self.config.connector_id,
                "source_record_id": source_id,
            },
            imported_at=datetime.now(UTC),
        )


def _extract_items(response: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if not isinstance(response, dict):
        return []
    for key in keys:
        value = response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _record_id(record: dict[str, Any]) -> str:
    for key in ["id", "entityId", "assayResultId", "assayRunId", "entryId", "registryId"]:
        value = record.get(key)
        if value:
            return str(value)
    return ""


def _record_url(record: dict[str, Any]) -> str | None:
    for key in ["webURL", "webUrl", "url"]:
        value = record.get(key)
        if value:
            return str(value)
    return None


def _first_value(record: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        value = _nested(record, path)
        if value not in (None, "", []):
            return _unwrap_benchling_value(value)
    return None


def _nested(record: dict[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _unwrap_benchling_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ["value", "displayValue", "text"]:
            if key in value:
                return value[key]
    return value


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _string_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _assay_summary(result: dict[str, Any] | AssayResult) -> dict[str, Any]:
    if isinstance(result, AssayResult):
        return {
            "resultId": result.result_id,
            "experimentId": result.experiment_id,
            "assayName": result.assay_name,
            "candidateId": result.candidate_id,
            "outcome": result.outcome,
            "value": result.value,
            "unit": result.unit,
        }
    return dict(result)
