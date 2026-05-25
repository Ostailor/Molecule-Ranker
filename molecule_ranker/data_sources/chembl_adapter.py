from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import requests

from molecule_ranker.data_sources.chembl_target_mapper import (
    ChEMBLTargetMapper,
    ChEMBLTargetMapping,
)
from molecule_ranker.data_sources.errors import (
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
)
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.schemas import Disease, Target
from molecule_ranker.utils.http_cache import CachedHttpResponse, HttpResponseCache
from molecule_ranker.utils.pagination import (
    PaginatedResult,
    PaginationMetadata,
    paginate_chembl_list,
)
from molecule_ranker.utils.retry import RetryMetadata, RetryPolicy, request_with_retries


class ChEMBLAdapter:
    """ChEMBL REST adapter for target-linked molecule and mechanism retrieval.

    Target lookup and mechanism records are required evidence sources. Molecule
    detail lookup is optional enrichment: when a mechanism record already
    identifies an existing ChEMBL molecule, a detail lookup failure preserves the
    evidence-backed record with a warning instead of fabricating replacement
    metadata.
    """

    source_name = "ChEMBL"
    default_base_url = "https://www.ebi.ac.uk/chembl/api/data"
    relevant_activity_types = {
        "IC50",
        "EC50",
        "KI",
        "KD",
        "POTENCY",
        "INHIBITION",
        "ACTIVITY",
    }
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "molecule-ranker/0.3",
    }

    def __init__(
        self,
        *,
        base_url: str = default_base_url,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
        session: requests.Session | None = None,
        cache: HttpResponseCache | None = None,
        use_cache: bool = False,
        cache_ttl_seconds: int = 24 * 60 * 60,
        target_mapper: ChEMBLTargetMapper | None = None,
        max_molecules_per_target: int | None = None,
        max_activity_records_per_target: int | None = None,
        max_indications_per_molecule: int = 20,
        max_warnings_per_molecule: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.session = session or requests.Session()
        self.cache = cache
        self.use_cache = use_cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_molecules_per_target = max_molecules_per_target
        self.max_activity_records_per_target = max_activity_records_per_target
        self.max_indications_per_molecule = max_indications_per_molecule
        self.max_warnings_per_molecule = max_warnings_per_molecule
        self._last_response_provenance: dict[str, Any] = {"mode": "live"}
        self.last_trace_metadata: dict[str, Any] = self._empty_trace_metadata()
        self.target_mapper = target_mapper or ChEMBLTargetMapper(
            base_url=self.base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            session=self.session,
        )

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        self.last_trace_metadata = self._empty_trace_metadata()
        records_by_id: dict[str, dict[str, Any]] = {}
        mapped_targets: list[tuple[Target, ChEMBLTargetMapping]] = []
        mapping_warnings: list[str] = []
        for target in targets:
            mapping = self.target_mapper.map_target(target)
            mapper_retry_metadata = getattr(self.target_mapper, "last_retry_metadata", None)
            if isinstance(mapper_retry_metadata, RetryMetadata):
                self._record_retry_metadata(mapper_retry_metadata)
            if mapping is None:
                mapping_warnings.extend(getattr(self.target_mapper, "warnings", []))
                continue
            mapped_targets.append((target, mapping))
        if not mapped_targets:
            raise MoleculeRetrievalError(
                f"No ChEMBL target mappings found for {len(targets)} target(s)."
            )
        mapping_warnings = sorted(set(mapping_warnings))
        mechanism_limit = self.max_molecules_per_target or limit_per_target
        activity_limit = self.max_activity_records_per_target or limit_per_target
        for target, mapping in mapped_targets:
            target_id = mapping.chembl_target_id
            try:
                mechanism_result = self._list_endpoint(
                    "mechanism.json",
                    collection_key="mechanisms",
                    base_params={"target_chembl_id": target_id},
                    max_records=mechanism_limit,
                )
                mechanisms = mechanism_result.records
            except (ExternalDataUnavailableError, MoleculeRetrievalError) as exc:
                mechanism_result = None
                mechanisms = []
                mechanism_warning = (
                    "ChEMBL mechanism records unavailable for "
                    f"{target.symbol} ({target_id}); activity evidence was still queried: {exc}"
                )
                self.last_trace_metadata["warnings"].append(mechanism_warning)
            for mechanism in mechanisms:
                molecule_id = mechanism.get("molecule_chembl_id")
                if not molecule_id:
                    continue
                warnings: list[str] = []
                if mechanism_result is not None and mechanism_result.metadata.truncated:
                    warnings.append(
                        "ChEMBL mechanism records were truncated by configured "
                        f"max_molecules_per_target={mechanism_limit}."
                    )
                try:
                    molecule = self._molecule_details(str(molecule_id))
                except ExternalDataUnavailableError as exc:
                    molecule = {}
                    warnings.append(
                        "Optional ChEMBL molecule-detail enrichment unavailable "
                        f"for {molecule_id}: {exc}"
                    )
                record = self._record_from_mechanism(
                    disease=disease,
                    target=target,
                    mapping=mapping,
                    mechanism=mechanism,
                    molecule=molecule,
                )
                record["warnings"].extend(warnings)
                record["warnings"].extend(mapping_warnings)
                self._merge_record(records_by_id, str(molecule_id), record)
            try:
                activities = self._activity_records(
                    disease=disease,
                    target=target,
                    mapping=mapping,
                    limit=activity_limit,
                )
            except (ExternalDataUnavailableError, MoleculeRetrievalError, IndexError):
                activities = []
            for record in activities:
                molecule_id = str(record["identifiers"]["chembl"])
                record["warnings"].extend(mapping_warnings)
                self._merge_record(records_by_id, molecule_id, record)
        records = list(records_by_id.values())
        if not records:
            raise NoCandidatesFoundError(
                f"ChEMBL found no molecule evidence for {len(targets)} target(s)."
            )
        return records

    def _molecule_details(self, molecule_chembl_id: str) -> dict[str, Any]:
        payload = self._get(
            "molecule.json",
            {"molecule_chembl_id": molecule_chembl_id, "limit": 1},
        )
        molecules = payload.get("molecules", [])
        return molecules[0] if molecules else {}

    def _activity_records(
        self,
        *,
        disease: Disease,
        target: Target,
        mapping: ChEMBLTargetMapping,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        target_chembl_id = mapping.chembl_target_id
        records: list[dict[str, Any]] = []
        for _activity, normalized in self._activity_page_rows(
            target_chembl_id=target_chembl_id,
            limit=limit,
        ):
            molecule_id = str(normalized["molecule_chembl_id"])
            warnings: list[str] = []
            try:
                molecule = self._molecule_details(molecule_id)
            except ExternalDataUnavailableError as exc:
                molecule = {}
                warnings.append(
                    "Optional ChEMBL molecule-detail enrichment unavailable "
                    f"for {molecule_id}: {exc}"
                )
            try:
                assay = self._assay_details(str(normalized.get("assay_chembl_id") or ""))
            except ExternalDataUnavailableError as exc:
                assay = {}
                warnings.append(
                    "Optional ChEMBL assay-detail enrichment unavailable "
                    f"for {normalized.get('assay_chembl_id')}: {exc}"
                )
            record = self._record_from_activity(
                disease=disease,
                target=target,
                target_chembl_id=target_chembl_id,
                normalized_activity=normalized,
                assay=assay,
                molecule=molecule,
                mapping_confidence=mapping.confidence,
                mapping_method=mapping.mapping_method,
            )
            record["warnings"].extend(warnings)
            self._add_drug_context(record, molecule_id, disease)
            records.append(record)
        return records

    def _activity_page_rows(
        self, *, target_chembl_id: str, limit: int
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        seen_activity_ids: set[str] = set()
        result = self._list_endpoint(
            "activity.json",
            collection_key="activities",
            base_params={
                "target_chembl_id": target_chembl_id,
                "order_by": "-pchembl_value",
            },
            max_records=limit,
        )
        for activity in result.records:
            response_provenance = dict(
                activity.pop("_response_provenance", self._last_response_provenance)
            )
            normalized = self._normalize_activity(activity, target_chembl_id)
            if normalized is None:
                continue
            normalized["response_provenance"] = response_provenance
            if result.metadata.truncated:
                normalized["truncated"] = True
            activity_id = str(normalized["activity_id"])
            if activity_id in seen_activity_ids:
                continue
            seen_activity_ids.add(activity_id)
            rows.append((activity, normalized))
        return rows

    def _normalize_activity(
        self, activity: dict[str, Any], target_chembl_id: str
    ) -> dict[str, Any] | None:
        activity_id = activity.get("activity_id")
        molecule_id = activity.get("molecule_chembl_id")
        standard_type = self._normalize_standard_type(activity.get("standard_type"))
        standard_value = self._optional_float(activity.get("standard_value"))
        pchembl = self._optional_float(activity.get("pchembl_value"))
        if (
            activity_id in (None, "")
            or molecule_id in (None, "")
            or standard_type is None
            or standard_type not in self.relevant_activity_types
            or (standard_value is None and pchembl is None)
        ):
            return None
        units = activity.get("standard_units")
        relation = activity.get("standard_relation") or activity.get("relation")
        return {
            "activity_id": str(activity_id),
            "assay_chembl_id": activity.get("assay_chembl_id"),
            "target_chembl_id": str(activity.get("target_chembl_id") or target_chembl_id),
            "molecule_chembl_id": str(molecule_id),
            "standard_type": standard_type,
            "standard_value": standard_value,
            "standard_units": str(units) if units not in (None, "") else None,
            "relation": str(relation) if relation not in (None, "") else None,
            "pchembl_value": pchembl,
        }

    def _assay_details(self, assay_chembl_id: str) -> dict[str, Any]:
        if not assay_chembl_id:
            return {}
        payload = self._get("assay.json", {"assay_chembl_id": assay_chembl_id, "limit": 1})
        assays = payload.get("assays", [])
        return assays[0] if assays else {}

    def _record_from_mechanism(
        self,
        *,
        disease: Disease,
        target: Target,
        mapping: ChEMBLTargetMapping,
        mechanism: dict[str, Any],
        molecule: dict[str, Any],
    ) -> dict[str, Any]:
        molecule_id = str(mechanism["molecule_chembl_id"])
        target_chembl_id = mapping.chembl_target_id
        mechanism_response_provenance = dict(
            mechanism.pop("_response_provenance", self._last_response_provenance)
        )
        max_phase = mechanism.get("max_phase")
        clinical_precedence = (
            max(0.0, min(float(max_phase) / 4.0, 1.0)) if max_phase is not None else 0.0
        )
        direct_interaction = bool(mechanism.get("direct_interaction"))
        target_fit = 0.8 if direct_interaction else 0.5
        retrieved_at = datetime.now(UTC).isoformat()
        pref_name = molecule.get("pref_name") or molecule_id
        record = {
            "name": str(pref_name),
            "molecule_type": molecule.get("molecule_type") or "unknown",
            "identifiers": {"chembl": molecule_id},
            "chemical_metadata": self._chemical_metadata_from_chembl(molecule),
            "known_targets": [target.symbol],
            "development_status": (
                f"max_phase_{max_phase}" if max_phase is not None else None
            ),
            "mechanism_of_action": mechanism.get("mechanism_of_action")
            or mechanism.get("mechanism_comment"),
            "target_fit": target_fit,
            "clinical_precedence": clinical_precedence,
            "safety_prior": 0.5,
            "repurposing_value": 0.5,
            "warnings": [],
            "evidence": [
                {
                    "source": self.source_name,
                    "source_record_id": str(mechanism.get("mec_id") or mechanism.get("record_id")),
                    "title": f"ChEMBL mechanism for {target.symbol}",
                    "url": f"https://www.ebi.ac.uk/chembl/mechanism_report_card/{molecule_id}/",
                    "evidence_type": "mechanism",
                    "summary": (
                        f"ChEMBL reports {mechanism.get('action_type') or 'an action'} "
                        f"for {molecule_id} on {target.symbol} in the context of "
                        f"{disease.canonical_name} target retrieval."
                    ),
                    "confidence": target_fit,
                    "retrieval_timestamp": retrieved_at,
                    "metadata": {
                        "target_chembl_id": target_chembl_id,
                        "molecule_chembl_id": molecule_id,
                        "action_type": mechanism.get("action_type"),
                        "max_phase": max_phase,
                        "mapping_method": mapping.mapping_method,
                        "mapping_confidence": mapping.confidence,
                        "target_mapping_confidence": mapping.confidence,
                        "response_provenance": mechanism_response_provenance,
                    },
                }
            ],
        }
        self._add_drug_context(record, molecule_id, disease)
        return record

    def _record_from_activity(
        self,
        *,
        disease: Disease,
        target: Target,
        target_chembl_id: str,
        normalized_activity: dict[str, Any],
        assay: dict[str, Any],
        molecule: dict[str, Any],
        mapping_confidence: float,
        mapping_method: str | None = None,
    ) -> dict[str, Any]:
        molecule_id = str(normalized_activity["molecule_chembl_id"])
        pchembl = self._optional_float(normalized_activity.get("pchembl_value"))
        assay_type = assay.get("assay_type")
        assay_confidence = self._assay_confidence_score(assay)
        target_fit = self._activity_confidence(
            pchembl=pchembl,
            assay_confidence=assay_confidence,
            mapping_confidence=mapping_confidence,
            has_standard_type=bool(normalized_activity.get("standard_type")),
        )
        retrieved_at = datetime.now(UTC).isoformat()
        pref_name = molecule.get("pref_name") or molecule_id
        molecule_max_phase = self._optional_float(molecule.get("max_phase"))
        clinical_precedence = (
            max(0.0, min(molecule_max_phase / 4.0, 1.0))
            if molecule_max_phase is not None
            else 0.0
        )
        activity_metadata = {
            **normalized_activity,
            "assay_type": assay_type,
            "molecule_max_phase": molecule_max_phase,
            "mapping_method": mapping_method,
            "mapping_confidence": mapping_confidence,
            "target_mapping_confidence": mapping_confidence,
            "response_provenance": self._metadata_provenance(normalized_activity),
        }
        evidence = [
            {
                "source": self.source_name,
                "source_record_id": str(normalized_activity["activity_id"]),
                "title": f"ChEMBL activity for {target.symbol}",
                "url": (
                    "https://www.ebi.ac.uk/chembl/compound_report_card/"
                    f"{molecule_id}/"
                ),
                "evidence_type": "activity",
                "summary": (
                    f"ChEMBL reports {normalized_activity['standard_type']} "
                    f"for {molecule_id} against {target.symbol} in the context of "
                    f"{disease.canonical_name} target retrieval."
                ),
                "confidence": target_fit,
                "retrieval_timestamp": retrieved_at,
                "metadata": activity_metadata,
            }
        ]
        if assay:
            assay_id = str(
                assay.get("assay_chembl_id") or normalized_activity.get("assay_chembl_id")
            )
            evidence.append(
                {
                    "source": self.source_name,
                    "source_record_id": assay_id,
                    "title": f"ChEMBL assay for {target.symbol}",
                    "url": f"https://www.ebi.ac.uk/chembl/assay_report_card/{assay_id}/",
                    "evidence_type": "assay",
                    "summary": str(assay.get("description") or "ChEMBL assay metadata."),
                    "confidence": self._assay_evidence_confidence(
                        assay_confidence=assay_confidence,
                        mapping_confidence=mapping_confidence,
                    ),
                    "retrieval_timestamp": retrieved_at,
                    "metadata": {
                        **activity_metadata,
                        "target_chembl_id": target_chembl_id,
                        "molecule_chembl_id": molecule_id,
                        "assay_chembl_id": assay_id,
                        "assay_type": assay_type,
                    },
                }
            )
        return {
            "name": str(pref_name),
            "molecule_type": molecule.get("molecule_type") or "unknown",
            "identifiers": {"chembl": molecule_id},
            "chemical_metadata": self._chemical_metadata_from_chembl(molecule),
            "known_targets": [target.symbol],
            "development_status": (
                f"molecule_max_phase_{int(molecule_max_phase)}"
                if molecule_max_phase is not None
                else None
            ),
            "mechanism_of_action": None,
            "target_fit": target_fit,
            "clinical_precedence": clinical_precedence,
            "safety_prior": 0.5,
            "repurposing_value": 0.5,
            "warnings": [],
            "evidence": evidence,
        }

    def _add_drug_context(
        self, record: dict[str, Any], molecule_chembl_id: str, disease: Disease
    ) -> None:
        retrieved_at = datetime.now(UTC).isoformat()
        try:
            indication_result = self._list_endpoint(
                "drug_indication.json",
                collection_key="drug_indications",
                base_params={"molecule_chembl_id": molecule_chembl_id},
                max_records=self.max_indications_per_molecule,
            )
            indications = indication_result.records
        except (ExternalDataUnavailableError, MoleculeRetrievalError, IndexError):
            indication_result = PaginatedResult(
                records=[],
                metadata=self._empty_pagination_metadata(),
                page_payloads=[],
            )
            indications = []
        indication_response_provenance = dict(self._last_response_provenance)
        if indication_result.metadata.truncated:
            record["warnings"].append(
                "ChEMBL indication records were truncated by configured "
                f"max_indications_per_molecule={self.max_indications_per_molecule}."
            )
        max_phase = None
        for indication in indications:
            indication_response_provenance = dict(
                indication.pop("_response_provenance", indication_response_provenance)
            )
            phase = indication.get("max_phase_for_ind")
            phase_value = self._optional_float(phase)
            if phase_value is not None:
                max_phase = max(phase_value, max_phase or 0.0)
            indication_name = (
                indication.get("mesh_heading")
                or indication.get("efo_term")
                or indication.get("indication")
                or "drug indication"
            )
            indication_record_id = self._source_record_id(
                indication,
                fallback=f"{molecule_chembl_id}:{indication_name}",
                keys=("drugind_id", "indication_id", "record_id"),
            )
            record["evidence"].append(
                {
                    "source": self.source_name,
                    "source_record_id": indication_record_id,
                    "title": f"ChEMBL indication for {molecule_chembl_id}",
                    "url": (
                        "https://www.ebi.ac.uk/chembl/compound_report_card/"
                        f"{molecule_chembl_id}/"
                    ),
                    "evidence_type": "indication",
                    "summary": (
                        f"ChEMBL lists {indication_name} as an indication record for "
                        f"{molecule_chembl_id}. This is known indication context for "
                        f"{disease.canonical_name}, not an efficacy claim for the queried disease."
                    ),
                    "confidence": 0.7,
                    "retrieval_timestamp": retrieved_at,
                    "metadata": {
                        "molecule_chembl_id": molecule_chembl_id,
                        "indication": indication_name,
                        "max_phase_for_ind": phase_value,
                        "mesh_id": indication.get("mesh_id"),
                        "efo_id": indication.get("efo_id"),
                        "query_disease_match": self._normalized_text_match(
                            indication_name,
                            disease.canonical_name,
                        ),
                        "reference_info": self._reference_info(indication),
                        "response_provenance": indication_response_provenance,
                    },
                }
            )
        if max_phase is not None:
            record["clinical_precedence"] = max(
                float(record.get("clinical_precedence") or 0.0),
                max(0.0, min(max_phase / 4.0, 1.0)),
            )
            record["development_status"] = record.get("development_status") or (
                f"indication_max_phase_{int(max_phase)}"
            )

        try:
            warning_result = self._list_endpoint(
                "drug_warning.json",
                collection_key="drug_warnings",
                base_params={"molecule_chembl_id": molecule_chembl_id},
                max_records=self.max_warnings_per_molecule,
            )
            warnings = warning_result.records
        except (ExternalDataUnavailableError, MoleculeRetrievalError, IndexError):
            warning_result = PaginatedResult(
                records=[],
                metadata=self._empty_pagination_metadata(),
                page_payloads=[],
            )
            warnings = []
        warning_response_provenance = dict(self._last_response_provenance)
        if warning_result.metadata.truncated:
            record["warnings"].append(
                "ChEMBL warning records were truncated by configured "
                f"max_warnings_per_molecule={self.max_warnings_per_molecule}."
            )
        for warning in warnings:
            warning_response_provenance = dict(
                warning.pop("_response_provenance", warning_response_provenance)
            )
            warning_type = str(warning.get("warning_type") or "drug warning")
            description = str(warning.get("warning_description") or warning_type)
            record["warnings"].append(f"ChEMBL warning: {warning_type}")
            warning_record_id = self._source_record_id(
                warning,
                fallback=f"{molecule_chembl_id}:{warning_type}",
                keys=("warning_id", "drug_warning_id", "record_id"),
            )
            record["evidence"].append(
                {
                    "source": self.source_name,
                    "source_record_id": warning_record_id,
                    "title": f"ChEMBL warning for {molecule_chembl_id}",
                    "url": (
                        "https://www.ebi.ac.uk/chembl/compound_report_card/"
                        f"{molecule_chembl_id}/"
                    ),
                    "evidence_type": "safety_warning",
                    "summary": description,
                    "confidence": 0.8,
                    "retrieval_timestamp": retrieved_at,
                    "metadata": {
                        "molecule_chembl_id": molecule_chembl_id,
                        "warning_type": warning_type,
                        "country": warning.get("warning_country") or warning.get("country"),
                        "year": warning.get("warning_year") or warning.get("year"),
                        "warning_class": warning.get("warning_class"),
                        "response_provenance": warning_response_provenance,
                    },
                }
            )
        if any(self._is_serious_warning(warning) for warning in warnings):
            record["safety_prior"] = min(float(record.get("safety_prior") or 0.5), 0.35)
        elif warnings:
            record["safety_prior"] = min(float(record.get("safety_prior") or 0.5), 0.45)

    def _merge_record(
        self,
        records_by_id: dict[str, dict[str, Any]],
        molecule_id: str,
        record: dict[str, Any],
    ) -> None:
        existing = records_by_id.get(molecule_id)
        if existing is None:
            records_by_id[molecule_id] = record
            return
        existing["known_targets"] = sorted(
            set(existing["known_targets"]) | set(record["known_targets"])
        )
        existing["evidence"] = self._deduplicate_evidence(
            [*existing["evidence"], *record["evidence"]]
        )
        existing["warnings"] = sorted(set(existing.get("warnings", [])) | set(record["warnings"]))
        existing["target_fit"] = max(
            float(existing.get("target_fit") or 0.0),
            float(record.get("target_fit") or 0.0),
        )
        existing["clinical_precedence"] = max(
            float(existing.get("clinical_precedence") or 0.0),
            float(record.get("clinical_precedence") or 0.0),
        )
        existing["safety_prior"] = min(
            float(existing.get("safety_prior") or 0.5),
            float(record.get("safety_prior") or 0.5),
        )

    def health_check(self, *, timeout_seconds: float = 5.0) -> AdapterHealthStatus:
        checked_at = datetime.now(UTC)
        endpoint = f"{self.base_url}/status.json"
        fallback_endpoint = f"{self.base_url}/molecule.json"
        timeout = min(timeout_seconds, self.timeout_seconds)
        started = perf_counter()
        status_error: str | None = None
        retry_policy = RetryPolicy(
            max_retries=self.max_retries,
            backoff_seconds=self.retry_delay_seconds,
            jitter_seconds=min(max(self.retry_delay_seconds, 0.0), 0.25),
        )
        try:
            response, _retry_metadata = request_with_retries(
                lambda: self.session.get(
                    endpoint,
                    params={},
                    timeout=timeout,
                    headers=self.request_headers,
                ),
                retry_policy,
            )
            payload = response.json()
            if not isinstance(payload, dict):
                raise MoleculeRetrievalError("ChEMBL returned an unexpected health payload.")
        except Exception as exc:  # pragma: no cover - exact failures are source-dependent
            status_error = str(exc)
            try:
                response, _retry_metadata = request_with_retries(
                    lambda: self.session.get(
                        fallback_endpoint,
                        params={"molecule_chembl_id": "CHEMBL25", "limit": 1},
                        timeout=timeout,
                        headers=self.request_headers,
                    ),
                    retry_policy,
                )
                payload = response.json()
                if not isinstance(payload, dict):
                    raise MoleculeRetrievalError(
                        "ChEMBL returned an unexpected fallback health payload."
                    )
            except Exception as fallback_exc:  # pragma: no cover - source-dependent
                return AdapterHealthStatus(
                    source_name=self.source_name,
                    ok=False,
                    endpoint=endpoint,
                    checked_at=checked_at,
                    latency_ms=self._elapsed_ms(started),
                    error=(
                        f"status probe failed: {status_error}; "
                        f"fallback probe failed: {fallback_exc}"
                    ),
                    metadata={
                        "probe": "status",
                        "fallback_probe": "molecule",
                        "fallback_endpoint": fallback_endpoint,
                    },
                )
            return AdapterHealthStatus(
                source_name=self.source_name,
                ok=True,
                endpoint=fallback_endpoint,
                checked_at=checked_at,
                latency_ms=self._elapsed_ms(started),
                error=None,
                metadata={
                    "probe": "molecule",
                    "status_endpoint": endpoint,
                    "status_error": status_error,
                },
            )
        return AdapterHealthStatus(
            source_name=self.source_name,
            ok=True,
            endpoint=endpoint,
            checked_at=checked_at,
            latency_ms=self._elapsed_ms(started),
            error=None,
            metadata={"probe": "status"},
        )

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        cache_key = self._cache_key(url, params)
        retry_metadata = RetryMetadata()
        try:
            response, retry_metadata = request_with_retries(
                lambda: self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout_seconds,
                    headers=self.request_headers,
                ),
                RetryPolicy(
                    max_retries=self.max_retries,
                    backoff_seconds=self.retry_delay_seconds,
                    jitter_seconds=min(max(self.retry_delay_seconds, 0.0), 0.25),
                ),
            )
            payload = response.json()
            if not isinstance(payload, dict):
                raise MoleculeRetrievalError("ChEMBL returned an unexpected payload.")
            response_metadata = self._response_metadata(url, params, response, retry_metadata)
            self._record_retry_metadata(retry_metadata)
            self._write_cache(cache_key, url, params, payload, response_metadata)
            self._last_response_provenance = response_metadata
            return payload
        except requests.RequestException as exc:
            retry_metadata = getattr(exc, "retry_metadata", retry_metadata)
            self._record_retry_metadata(retry_metadata)
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise ExternalDataUnavailableError(f"ChEMBL request failed: {exc}") from exc
        except ValueError as exc:
            self._record_retry_metadata(retry_metadata)
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise MoleculeRetrievalError("ChEMBL returned invalid JSON.") from exc

    def _list_endpoint(
        self,
        path: str,
        *,
        collection_key: str,
        base_params: dict[str, Any],
        max_records: int,
    ) -> PaginatedResult:
        def fetch_page(offset: int, page_size: int) -> dict[str, Any]:
            payload = self._get(
                path,
                {**base_params, "limit": page_size, "offset": offset},
            )
            provenance = dict(self._last_response_provenance)
            records = payload.get(collection_key, []) or []
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict):
                        record["_response_provenance"] = provenance
            return payload

        result = paginate_chembl_list(
            fetch_page,
            collection_key=collection_key,
            max_records=max_records,
            page_size=max(1, min(max_records, 100)),
        )
        self._record_pagination_metadata(result.metadata.asdict())
        return result

    def _cache_key(self, url: str, params: dict[str, Any]) -> str | None:
        if self.cache is None:
            return None
        return self.cache.build_key(
            source_name=self.source_name,
            endpoint=url,
            method="GET",
            query_params=params,
        )

    def _cached_response(self, cache_key: str | None) -> CachedHttpResponse | None:
        if not self.use_cache or self.cache is None or cache_key is None:
            return None
        return self.cache.get(cache_key, ttl_seconds=self.cache_ttl_seconds)

    def _write_cache(
        self,
        cache_key: str | None,
        url: str,
        params: dict[str, Any],
        payload: dict[str, Any],
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.cache is None or cache_key is None:
            return
        self.cache.write_success(
            cache_key=cache_key,
            response_json=payload,
            source=self.source_name,
            endpoint=url,
            method="GET",
            request_metadata={"query_params": params},
            ttl_seconds=self.cache_ttl_seconds,
            response_metadata=response_metadata or {},
        )

    def _response_metadata(
        self,
        url: str,
        params: dict[str, Any],
        response: requests.Response,
        retry_metadata: RetryMetadata,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(url, params)
        return {
            "mode": "live",
            "source": self.source_name,
            "endpoint": url,
            "query_params": dict(params),
            "status_code": int(getattr(response, "status_code", 200)),
            "cache_key": cache_key,
            "retry_count": retry_metadata.retry_count,
            "rate_limit_retry_count": retry_metadata.rate_limit_retry_count,
            "attempts": retry_metadata.attempts,
            "status_codes": list(retry_metadata.status_codes),
        }

    def _empty_trace_metadata(self) -> dict[str, Any]:
        return {
            "pages_fetched": 0,
            "records_fetched": 0,
            "records_retained": 0,
            "truncated": False,
            "retry_count": 0,
            "rate_limit_retry_count": 0,
            "warnings": [],
        }

    def _empty_pagination_metadata(self) -> PaginationMetadata:
        return PaginationMetadata()

    def _record_pagination_metadata(self, metadata: dict[str, Any]) -> None:
        self.last_trace_metadata["pages_fetched"] += int(metadata.get("pages_fetched") or 0)
        self.last_trace_metadata["records_fetched"] += int(metadata.get("records_fetched") or 0)
        self.last_trace_metadata["records_retained"] += int(
            metadata.get("records_retained") or 0
        )
        self.last_trace_metadata["truncated"] = bool(
            self.last_trace_metadata["truncated"] or metadata.get("truncated")
        )

    def _record_retry_metadata(self, metadata: RetryMetadata) -> None:
        self.last_trace_metadata["retry_count"] += metadata.retry_count
        self.last_trace_metadata["rate_limit_retry_count"] += metadata.rate_limit_retry_count

    def _deduplicate_evidence(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            key = (str(item.get("evidence_type") or ""), str(item.get("source_record_id") or ""))
            if key[1] and key in seen:
                continue
            if key[1]:
                seen.add(key)
            deduped.append(item)
        return deduped

    def _activity_confidence(
        self,
        *,
        pchembl: float | None,
        assay_confidence: float | None,
        mapping_confidence: float,
        has_standard_type: bool,
    ) -> float:
        potency_component = max(0.0, min((pchembl or 5.5) / 8.0, 1.0))
        assay_component = assay_confidence if assay_confidence is not None else 0.5
        type_component = 1.0 if has_standard_type else 0.6
        confidence = (
            0.4 * max(0.0, min(mapping_confidence, 1.0))
            + 0.3 * assay_component
            + 0.2 * potency_component
            + 0.1 * type_component
        )
        return max(0.0, min(confidence, 1.0))

    def _assay_evidence_confidence(
        self, *, assay_confidence: float | None, mapping_confidence: float
    ) -> float:
        assay_component = assay_confidence if assay_confidence is not None else 0.5
        confidence = 0.55 * max(0.0, min(mapping_confidence, 1.0)) + 0.45 * assay_component
        return max(0.0, min(confidence, 1.0))

    def _assay_confidence_score(self, assay: dict[str, Any]) -> float | None:
        raw_score = assay.get("confidence_score") or assay.get("assay_confidence_score")
        score = self._optional_float(raw_score)
        if score is None:
            return None
        return max(0.0, min(score / 9.0, 1.0))

    def _normalize_standard_type(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value).strip().upper()

    def _metadata_provenance(self, metadata: dict[str, Any]) -> dict[str, Any]:
        provenance = metadata.get("response_provenance")
        if isinstance(provenance, dict):
            return dict(provenance)
        return dict(self._last_response_provenance)

    def _chemical_metadata_from_chembl(self, molecule: dict[str, Any]) -> dict[str, Any]:
        structures = molecule.get("molecule_structures")
        if not isinstance(structures, dict):
            structures = {}
        metadata = {
            "canonical_smiles": structures.get("canonical_smiles"),
            "inchi": structures.get("standard_inchi"),
            "inchikey": structures.get("standard_inchi_key"),
            "molecular_formula": molecule.get("molecule_properties", {}).get("full_molformula")
            if isinstance(molecule.get("molecule_properties"), dict)
            else None,
            "molecular_weight": molecule.get("molecule_properties", {}).get("full_mwt")
            if isinstance(molecule.get("molecule_properties"), dict)
            else None,
        }
        return {key: value for key, value in metadata.items() if value not in (None, "")}

    def _source_record_id(
        self,
        payload: dict[str, Any],
        *,
        fallback: str,
        keys: tuple[str, ...],
    ) -> str:
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return fallback

    def _reference_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: payload.get(key)
            for key in ("ref_type", "ref_id", "ref_url", "reference", "reference_url")
            if payload.get(key) not in (None, "")
        }

    def _is_serious_warning(self, warning: dict[str, Any]) -> bool:
        text = " ".join(
            str(warning.get(key) or "")
            for key in ("warning_type", "warning_class", "warning_description")
        ).lower()
        serious_terms = ("black box", "boxed", "contraindication", "withdrawn", "fatal")
        return any(term in text for term in serious_terms)

    def _normalized_text_match(self, left: str, right: str) -> bool:
        return self._normalize_text(left) == self._normalize_text(right)

    def _normalize_text(self, value: str) -> str:
        return " ".join(value.casefold().replace("-", " ").split())

    def _optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _elapsed_ms(self, started: float) -> float:
        return round((perf_counter() - started) * 1000.0, 3)
