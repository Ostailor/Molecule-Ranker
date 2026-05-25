from __future__ import annotations

import pytest

from molecule_ranker.developability.descriptors import (
    compute_physchem_profile,
    compute_qed,
    descriptor_risk_flags,
    egan_violations,
    ghose_violations,
    lipinski_violations,
    muegge_violations,
    veber_violations,
)


def test_compute_physchem_profile_for_simple_valid_molecule():
    profile = compute_physchem_profile("CCO")

    assert profile.canonical_smiles == "CCO"
    assert profile.inchi_key
    assert profile.molecular_weight == pytest.approx(46.069, abs=0.01)
    assert profile.logp is not None
    assert profile.tpsa == pytest.approx(20.23, abs=0.01)
    assert profile.hbd == 1
    assert profile.hba == 1
    assert profile.rotatable_bonds == 0
    assert profile.aromatic_rings == 0
    assert profile.heavy_atom_count == 3
    assert profile.formal_charge == 0
    assert profile.fraction_csp3 == pytest.approx(1.0)
    assert profile.qed is not None
    assert profile.metadata["descriptor_source"] == "rdkit"
    assert "assumptions" in profile.metadata
    assert "QED is a drug-likeness heuristic" in profile.metadata["assumptions"]["qed"]


def test_invalid_smiles_fails_clearly():
    with pytest.raises(ValueError, match="Invalid SMILES"):
        compute_physchem_profile("not-a-smiles")


def test_compute_qed_is_bounded_for_valid_smiles_and_none_for_invalid_smiles():
    qed = compute_qed("CCO")

    assert qed is not None
    assert 0.0 <= qed <= 1.0
    assert compute_qed("not-a-smiles") is None


def test_rule_violations_are_computed_from_profile():
    profile = compute_physchem_profile("CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC")

    assert profile.lipinski_violations == lipinski_violations(profile)
    assert profile.veber_violations == veber_violations(profile)
    assert profile.ghose_violations == ghose_violations(profile)
    assert profile.egan_violations == egan_violations(profile)
    assert profile.muegge_violations == muegge_violations(profile)
    assert profile.lipinski_violations > 0
    assert profile.veber_violations > 0
    assert profile.egan_violations > 0


def test_descriptor_flags_are_generated_for_out_of_range_molecule():
    profile = compute_physchem_profile("CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC")

    flags = descriptor_risk_flags(profile)

    assert "lipinski_violations:2" in flags
    assert any(flag.startswith("veber_violations:") for flag in flags)
    assert "logp_high" in flags
    assert "rotatable_bonds_high" in flags
