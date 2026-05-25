from __future__ import annotations

import pytest

from molecule_ranker.data_sources.chembl_adapter import ChEMBLAdapter
from molecule_ranker.data_sources.errors import (
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
)
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter

pytestmark = [pytest.mark.live, pytest.mark.network]


def test_live_small_public_data_run_structural_properties() -> None:
    open_targets = OpenTargetsAdapter(
        timeout_seconds=15,
        max_retries=1,
        retry_delay_seconds=0.25,
    )
    chembl = ChEMBLAdapter(
        timeout_seconds=15,
        max_retries=1,
        retry_delay_seconds=0.25,
        max_molecules_per_target=1,
        max_activity_records_per_target=1,
        max_indications_per_molecule=1,
        max_warnings_per_molecule=1,
    )

    disease = open_targets.resolve_disease("Parkinson disease")
    assert disease.canonical_name
    assert disease.identifiers
    assert not _has_fixture_source(disease.identifiers)

    targets = open_targets.discover_targets(disease, limit=5)
    assert targets
    assert all(target.evidence for target in targets)
    assert all(
        item.source and item.source_record_id
        for target in targets
        for item in target.evidence
    )
    assert not any(
        item.source.lower() == "fixture"
        for target in targets
        for item in target.evidence
    )

    try:
        records = chembl.retrieve_molecules(disease, targets[:3], limit_per_target=1)
    except (ExternalDataUnavailableError, MoleculeRetrievalError, NoCandidatesFoundError) as exc:
        pytest.skip(f"No live ChEMBL molecule records available for small smoke run: {exc}")

    if not records:
        pytest.skip("No live ChEMBL molecule records available for small smoke run.")

    for record in records:
        evidence = record.get("evidence", [])
        assert evidence
        assert any(item.get("source_record_id") for item in evidence)
        assert not any(str(item.get("source", "")).lower() == "fixture" for item in evidence)


def _has_fixture_source(identifiers: dict[str, str]) -> bool:
    return any("fixture" in str(value).lower() for value in identifiers.values())
