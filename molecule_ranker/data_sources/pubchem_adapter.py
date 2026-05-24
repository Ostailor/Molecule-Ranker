from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import requests

from molecule_ranker.data_sources.errors import EvidenceRetrievalError, ExternalDataUnavailableError


class PubChemAdapter:
    """PubChem PUG REST adapter for molecule identifier and chemistry enrichment."""

    source_name = "PubChem"
    default_base_url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

    def __init__(
        self,
        *,
        base_url: str = default_base_url,
        timeout_seconds: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def annotate_molecules(self, molecules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.annotate_molecule(molecule) for molecule in molecules]

    def annotate_molecule(self, molecule: dict[str, Any]) -> dict[str, Any]:
        name = str(molecule.get("name") or "")
        if not name:
            raise EvidenceRetrievalError("PubChem annotation requires a molecule name.")
        cid = self._lookup_cid(name)
        synonyms = self._synonyms(cid)
        properties = self._properties(cid)
        enriched = dict(molecule)
        identifiers = dict(enriched.get("identifiers", {}))
        identifiers["pubchem_cid"] = str(cid)
        enriched["identifiers"] = identifiers
        enriched["synonyms"] = synonyms
        enriched["chemical_metadata"] = properties
        retrieved_at = datetime.now(UTC).isoformat()
        enriched.setdefault("evidence", []).append(
            {
                "source": self.source_name,
                "source_record_id": str(cid),
                "title": f"PubChem compound annotation for {name}",
                "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
                "evidence_type": "chemical_annotation",
                "summary": f"PubChem returned CID {cid} and chemical metadata for {name}.",
                "confidence": 0.75,
                "retrieval_timestamp": retrieved_at,
                "metadata": {
                    "query_name": name,
                    "cid": cid,
                    "properties": properties,
                },
            }
        )
        return enriched

    def _lookup_cid(self, name: str) -> int:
        payload = self._get(f"compound/name/{quote(name)}/cids/JSON")
        cids = payload.get("IdentifierList", {}).get("CID", [])
        if not cids:
            raise EvidenceRetrievalError(f"PubChem returned no CID for {name!r}.")
        return int(cids[0])

    def _synonyms(self, cid: int) -> list[str]:
        payload = self._get(f"compound/cid/{cid}/synonyms/JSON")
        infos = payload.get("InformationList", {}).get("Information", [])
        if not infos:
            return []
        return [str(value) for value in infos[0].get("Synonym", [])]

    def _properties(self, cid: int) -> dict[str, Any]:
        path = (
            f"compound/cid/{cid}/property/"
            "MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey/JSON"
        )
        payload = self._get(path)
        properties = payload.get("PropertyTable", {}).get("Properties", [])
        return properties[0] if properties else {}

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self.session.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ExternalDataUnavailableError(f"PubChem request failed: {exc}") from exc
        except ValueError as exc:
            raise EvidenceRetrievalError("PubChem returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise EvidenceRetrievalError("PubChem returned an unexpected payload.")
        return payload
