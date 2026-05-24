from __future__ import annotations

from pathlib import Path

from molecule_ranker.data_sources.chembl_adapter import ChEMBLAdapter
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter
from molecule_ranker.data_sources.pubchem_adapter import PubChemAdapter
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator

PRODUCTION_ROOT = Path(__file__).resolve().parents[1] / "molecule_ranker"


def _production_text() -> str:
    return "\n".join(path.read_text() for path in PRODUCTION_ROOT.rglob("*.py"))


def test_live_orchestrator_defaults_to_real_adapters_only():
    orchestrator = MoleculeRankerOrchestrator()

    assert isinstance(orchestrator.disease_resolver._data_source, OpenTargetsAdapter)
    assert isinstance(orchestrator.target_discovery._data_source, OpenTargetsAdapter)
    assert isinstance(orchestrator.molecule_retrieval._data_source, ChEMBLAdapter)
    assert isinstance(orchestrator.molecule_retrieval._annotation_source, PubChemAdapter)


def test_production_code_does_not_import_test_fixtures():
    text = _production_text()

    assert "tests/fixtures" not in text
    assert "tests.fixtures" not in text
    assert "from tests" not in text
    assert "import tests" not in text


def test_production_code_has_no_fixture_or_fallback_biomedical_paths():
    text = _production_text().lower()
    prohibited = [
        "fixture mode",
        "fixture_mode",
        "source=\"fixture\"",
        "source='fixture'",
        '"source": "fixture"',
        "'source': 'fixture'",
        "fallback disease",
        "fallback_disease",
        "resolve_disease_fixture",
        "get_targets_for_disease_fixture",
        "get_molecules_for_targets_fixture",
        "hardcoded disease-target",
        "hardcoded target-molecule",
        "disease_target_mapping",
        "target_molecule_mapping",
        "fake evidence",
        "placeholder candidate",
        "silent empty report",
    ]

    for pattern in prohibited:
        assert pattern not in text
