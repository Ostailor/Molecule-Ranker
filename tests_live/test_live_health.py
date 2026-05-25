from __future__ import annotations

import pytest

from molecule_ranker.data_sources.chembl_adapter import ChEMBLAdapter
from molecule_ranker.data_sources.openalex_adapter import OpenAlexAdapter
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter
from molecule_ranker.data_sources.pubchem_adapter import PubChemAdapter
from molecule_ranker.data_sources.pubmed_adapter import PubMedAdapter

pytestmark = [pytest.mark.live, pytest.mark.network]


def test_live_adapter_health_checks() -> None:
    adapters = [
        OpenTargetsAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
        ChEMBLAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
        PubChemAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
        PubMedAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
        OpenAlexAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
    ]

    statuses = [adapter.health_check(timeout_seconds=10) for adapter in adapters]

    assert {status.source_name for status in statuses} == {
        "Open Targets",
        "ChEMBL",
        "PubChem",
        "PubMed",
        "OpenAlex",
    }
    for status in statuses:
        assert status.ok, f"{status.source_name} health failed: {status.error}"
        assert status.endpoint.startswith("https://")
        assert status.checked_at is not None
        assert status.latency_ms is None or status.latency_ms >= 0
