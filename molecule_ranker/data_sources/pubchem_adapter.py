from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from urllib.parse import quote

import requests

from molecule_ranker.data_sources.errors import EvidenceRetrievalError, ExternalDataUnavailableError
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.utils.http_cache import CachedHttpResponse, HttpResponseCache
from molecule_ranker.utils.retry import RetryMetadata, RetryPolicy, request_with_retries


class PubChemAdapter:
    """PubChem PUG REST adapter for molecule identifier and chemistry enrichment."""

    source_name = "PubChem"
    default_base_url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    synonym_limit = 20

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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.session = session or requests.Session()
        self.cache = cache
        self.use_cache = use_cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self._last_response_provenance: dict[str, Any] = {"mode": "live"}
        self.last_trace_metadata: dict[str, Any] = self._empty_trace_metadata()

    def annotate_molecules(self, molecules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        annotated: list[dict[str, Any]] = []
        for molecule in molecules:
            try:
                annotated.append(self.annotate_molecule(molecule))
            except (EvidenceRetrievalError, ExternalDataUnavailableError) as exc:
                preserved = dict(molecule)
                preserved.setdefault("warnings", []).append(str(exc))
                annotated.append(preserved)
        return annotated

    def annotate_molecule(self, molecule: dict[str, Any]) -> dict[str, Any]:
        lookup = self._lookup_request(molecule)
        cid = self._lookup_cid(lookup)
        properties = self._properties(cid)
        synonyms = self._synonyms(cid)
        chemical_metadata = self._chemical_metadata(
            cid=cid,
            properties=properties,
            synonyms=synonyms,
        )
        enriched = dict(molecule)
        identifiers = dict(enriched.get("identifiers", {}))
        identifiers["pubchem_cid"] = str(cid)
        if chemical_metadata.get("inchikey"):
            identifiers["inchikey"] = str(chemical_metadata["inchikey"])
        if chemical_metadata.get("inchi"):
            identifiers["inchi"] = str(chemical_metadata["inchi"])
        enriched["identifiers"] = identifiers
        enriched["synonyms"] = synonyms
        enriched["chemical_metadata"] = {
            **dict(enriched.get("chemical_metadata", {})),
            **chemical_metadata,
        }
        retrieved_at = datetime.now(UTC).isoformat()
        enriched.setdefault("evidence", []).append(
            {
                "source": self.source_name,
                "source_record_id": str(cid),
                "title": f"PubChem compound annotation for CID {cid}",
                "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
                "evidence_type": "chemical_annotation",
                "summary": (
                    f"PubChem returned CID {cid} and chemical metadata using "
                    f"{lookup['method']} lookup."
                ),
                "confidence": 0.75,
                "retrieval_timestamp": retrieved_at,
                "metadata": {
                    "lookup": lookup,
                    "cid": cid,
                    "properties": chemical_metadata,
                    "response_provenance": dict(self._last_response_provenance),
                },
            }
        )
        return enriched

    def _lookup_request(self, molecule: dict[str, Any]) -> dict[str, str]:
        identifiers = {str(key).lower(): str(value) for key, value in dict(
            molecule.get("identifiers", {})
        ).items() if value not in (None, "")}
        metadata = {
            str(key).lower(): str(value)
            for key, value in dict(molecule.get("chemical_metadata", {})).items()
            if value not in (None, "")
        }
        structures = {
            str(key).lower(): str(value)
            for key, value in dict(molecule.get("molecule_structures", {})).items()
            if value not in (None, "")
        }
        candidates = [
            (
                "inchikey",
                identifiers.get("inchikey")
                or identifiers.get("inchi_key")
                or metadata.get("inchikey")
                or metadata.get("inchi_key")
                or structures.get("standard_inchi_key"),
            ),
            (
                "inchi",
                identifiers.get("inchi")
                or metadata.get("inchi")
                or structures.get("standard_inchi"),
            ),
            (
                "smiles",
                identifiers.get("isomeric_smiles")
                or identifiers.get("canonical_smiles")
                or metadata.get("isomeric_smiles")
                or metadata.get("canonical_smiles")
                or structures.get("canonical_smiles"),
            ),
            ("name", str(molecule.get("name") or "").strip() or None),
        ]
        for method, query in candidates:
            if query:
                return {"method": method, "query": query}
        raise EvidenceRetrievalError(
            "PubChem annotation requires an InChIKey, InChI, SMILES, or molecule name."
        )

    def _lookup_cid(self, lookup: dict[str, str]) -> int:
        method = lookup["method"]
        query = lookup["query"]
        payload = self._get(f"compound/{method}/{quote(query, safe='')}/cids/JSON")
        cids = payload.get("IdentifierList", {}).get("CID", [])
        if not cids:
            raise EvidenceRetrievalError(
                f"PubChem returned no CID for {method} lookup {query!r}."
            )
        if len(cids) > 1:
            raise EvidenceRetrievalError(
                f"PubChem returned ambiguous CIDs for {method} lookup {query!r}."
            )
        return int(cids[0])

    def _synonyms(self, cid: int) -> list[str]:
        payload = self._get(f"compound/cid/{cid}/synonyms/JSON")
        infos = payload.get("InformationList", {}).get("Information", [])
        if not infos:
            return []
        return [str(value) for value in infos[0].get("Synonym", [])[: self.synonym_limit]]

    def _properties(self, cid: int) -> dict[str, Any]:
        path = (
            f"compound/cid/{cid}/property/"
            "MolecularFormula,MolecularWeight,CanonicalSMILES,IsomericSMILES,InChI,InChIKey/JSON"
        )
        payload = self._get(path)
        properties = payload.get("PropertyTable", {}).get("Properties", [])
        return properties[0] if properties else {}

    def _chemical_metadata(
        self, *, cid: int, properties: dict[str, Any], synonyms: list[str]
    ) -> dict[str, Any]:
        return {
            "cid": cid,
            "canonical_smiles": properties.get("CanonicalSMILES"),
            "isomeric_smiles": properties.get("IsomericSMILES"),
            "inchi": properties.get("InChI"),
            "inchikey": properties.get("InChIKey"),
            "molecular_formula": properties.get("MolecularFormula"),
            "molecular_weight": properties.get("MolecularWeight"),
            "synonyms": synonyms,
        }

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        cache_key = self._cache_key(url)
        retry_metadata = RetryMetadata()
        try:
            response, retry_metadata = request_with_retries(
                lambda: self.session.get(url, timeout=self.timeout_seconds),
                RetryPolicy(
                    max_retries=self.max_retries,
                    backoff_seconds=self.retry_delay_seconds,
                    jitter_seconds=min(max(self.retry_delay_seconds, 0.0), 0.25),
                ),
            )
            if getattr(response, "status_code", 200) == 404:
                raise EvidenceRetrievalError(f"PubChem returned no record for {url}.")
            payload = response.json()
        except requests.RequestException as exc:
            retry_metadata = getattr(exc, "retry_metadata", retry_metadata)
            self._record_retry_metadata(retry_metadata)
            if retry_metadata.status_codes and retry_metadata.status_codes[-1] == 404:
                raise EvidenceRetrievalError(f"PubChem returned no record for {url}.") from exc
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise ExternalDataUnavailableError(f"PubChem request failed: {exc}") from exc
        except ValueError as exc:
            self._record_retry_metadata(retry_metadata)
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise EvidenceRetrievalError("PubChem returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise EvidenceRetrievalError("PubChem returned an unexpected payload.")
        response_metadata = self._response_metadata(url, response, retry_metadata)
        self._record_retry_metadata(retry_metadata)
        self._record_page_metadata(records_fetched=self._record_count(payload))
        self._write_cache(cache_key, url, payload, response_metadata)
        self._last_response_provenance = response_metadata
        return payload

    def _cache_key(self, url: str) -> str | None:
        if self.cache is None:
            return None
        return self.cache.build_key(
            source_name=self.source_name,
            endpoint=url,
            method="GET",
        )

    def _cached_response(self, cache_key: str | None) -> CachedHttpResponse | None:
        if not self.use_cache or self.cache is None or cache_key is None:
            return None
        return self.cache.get(cache_key, ttl_seconds=self.cache_ttl_seconds)

    def _write_cache(
        self,
        cache_key: str | None,
        url: str,
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
            request_metadata={"query_params": {}},
            ttl_seconds=self.cache_ttl_seconds,
            response_metadata=response_metadata or {},
        )

    def health_check(self, *, timeout_seconds: float = 5.0) -> AdapterHealthStatus:
        checked_at = datetime.now(UTC)
        path = "compound/name/aspirin/cids/JSON"
        endpoint = f"{self.base_url}/{path}"
        started = perf_counter()
        try:
            response = self.session.get(
                endpoint,
                timeout=min(timeout_seconds, self.timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise EvidenceRetrievalError("PubChem returned an unexpected health payload.")
        except Exception as exc:  # pragma: no cover - exact failures are source-dependent
            return AdapterHealthStatus(
                source_name=self.source_name,
                ok=False,
                endpoint=endpoint,
                checked_at=checked_at,
                latency_ms=self._elapsed_ms(started),
                error=str(exc),
                metadata={"probe": "compound_name_cid", "compound": "aspirin"},
            )
        return AdapterHealthStatus(
            source_name=self.source_name,
            ok=True,
            endpoint=endpoint,
            checked_at=checked_at,
            latency_ms=self._elapsed_ms(started),
            error=None,
            metadata={"probe": "compound_name_cid", "compound": "aspirin"},
        )

    def _elapsed_ms(self, started: float) -> float:
        return round((perf_counter() - started) * 1000.0, 3)

    def _response_metadata(
        self,
        url: str,
        response: requests.Response,
        retry_metadata: RetryMetadata,
    ) -> dict[str, Any]:
        return {
            "mode": "live",
            "source": self.source_name,
            "endpoint": url,
            "status_code": int(getattr(response, "status_code", 200)),
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
        }

    def _record_retry_metadata(self, metadata: RetryMetadata) -> None:
        self.last_trace_metadata["retry_count"] += metadata.retry_count
        self.last_trace_metadata["rate_limit_retry_count"] += metadata.rate_limit_retry_count

    def _record_page_metadata(self, *, records_fetched: int) -> None:
        self.last_trace_metadata["pages_fetched"] += 1
        self.last_trace_metadata["records_fetched"] += records_fetched
        self.last_trace_metadata["records_retained"] += records_fetched

    def _record_count(self, payload: dict[str, Any]) -> int:
        if isinstance(payload.get("IdentifierList"), dict):
            values = payload["IdentifierList"].get("CID") or []
            return len(values) if isinstance(values, list) else 0
        if isinstance(payload.get("PropertyTable"), dict):
            values = payload["PropertyTable"].get("Properties") or []
            return len(values) if isinstance(values, list) else 0
        if isinstance(payload.get("InformationList"), dict):
            values = payload["InformationList"].get("Information") or []
            return len(values) if isinstance(values, list) else 0
        return 1
