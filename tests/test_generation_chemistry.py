from __future__ import annotations

import pytest

from molecule_ranker.generation.chemistry import (
    BASIC_PROPERTY_BOUNDS,
    allowed_elements_check,
    basic_property_bounds_check,
    canonicalize_inchi,
    canonicalize_smiles,
    descriptors_from_mol,
    detect_basic_alerts,
    inchi_key_from_mol,
    mol_from_inchi,
    mol_from_smiles,
    morgan_fingerprint,
    tanimoto_similarity,
)


def test_valid_smiles_canonicalizes_and_inchi_key_is_available():
    mol = mol_from_smiles("OC(=O)c1ccccc1")

    assert mol is not None
    assert canonicalize_smiles("OC(=O)c1ccccc1") == "O=C(O)c1ccccc1"
    assert inchi_key_from_mol(mol) == "WPYMKLBDIGXBTP-UHFFFAOYSA-N"


def test_invalid_smiles_returns_none():
    invalid = "C1(CC"

    assert mol_from_smiles(invalid) is None
    assert canonicalize_smiles(invalid) is None


def test_inchi_structure_canonicalizes_to_smiles():
    inchi = "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"

    mol = mol_from_inchi(inchi)

    assert mol is not None
    assert canonicalize_inchi(inchi) == "CCO"


def test_descriptors_are_computed_for_v03_descriptor_set():
    mol = mol_from_smiles("CC(=O)Oc1ccccc1C(=O)O")
    assert mol is not None

    descriptors = descriptors_from_mol(mol)

    assert set(descriptors) == {
        "molecular_weight",
        "logp",
        "tpsa",
        "hbd",
        "hba",
        "rotatable_bonds",
        "aromatic_rings",
        "heavy_atom_count",
        "formal_charge",
    }
    assert descriptors["molecular_weight"] == pytest.approx(180.159, abs=0.001)
    assert descriptors["heavy_atom_count"] == 13
    assert descriptors["formal_charge"] == 0


def test_fingerprints_and_tanimoto_similarity_work():
    mol_a = mol_from_smiles("CCOc1ccccc1")
    mol_b = mol_from_smiles("CCOc1ccccc1N")
    mol_c = mol_from_smiles("CCCCCCCC")
    assert mol_a is not None
    assert mol_b is not None
    assert mol_c is not None

    fingerprint = morgan_fingerprint(mol_a)

    assert fingerprint is not None
    assert tanimoto_similarity(mol_a, mol_a) == pytest.approx(1.0)
    assert 0.0 < tanimoto_similarity(mol_a, mol_b) < 1.0
    assert tanimoto_similarity(mol_a, mol_b) > tanimoto_similarity(mol_a, mol_c)


def test_allowed_element_check_rejects_metals_unless_allowed():
    sodium_benzoate = mol_from_smiles("[Na+].O=C([O-])c1ccccc1")
    assert sodium_benzoate is not None

    assert allowed_elements_check(sodium_benzoate, {"C", "H", "N", "O"}) is False
    assert allowed_elements_check(sodium_benzoate, {"C", "H", "N", "O", "Na"}) is True


def test_property_bounds_produce_rejection_reasons():
    descriptors = {
        "molecular_weight": 90.0,
        "logp": 7.1,
        "tpsa": 20.0,
        "hbd": 9.0,
        "hba": 2.0,
        "rotatable_bonds": 16.0,
        "aromatic_rings": 0.0,
        "heavy_atom_count": 5.0,
        "formal_charge": 0.0,
    }

    reasons = basic_property_bounds_check(descriptors, BASIC_PROPERTY_BOUNDS)

    assert "molecular_weight below minimum 150.0" in reasons
    assert "logp above maximum 6.0" in reasons
    assert "hbd above maximum 8.0" in reasons
    assert "rotatable_bonds above maximum 15.0" in reasons
    assert "heavy_atom_count below minimum 8.0" in reasons


def test_detect_basic_alerts_reports_reactive_groups_without_external_services():
    nitrobenzene = mol_from_smiles("O=[N+]([O-])c1ccccc1")
    catechol = mol_from_smiles("Oc1ccccc1O")
    assert nitrobenzene is not None
    assert catechol is not None

    assert "nitro_group" in detect_basic_alerts(nitrobenzene)
    assert "catechol" in detect_basic_alerts(catechol)
