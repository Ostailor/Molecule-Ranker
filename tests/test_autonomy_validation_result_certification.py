from __future__ import annotations

from molecule_ranker.autonomy_validation.result_certification import certify_e2e_result
from molecule_ranker.autonomy_validation.scenario_builder import get_builtin_autonomy_scenario


def test_certified_mocked_workflow() -> None:
    certification = certify_e2e_result(
        "workflow-cert-mocked",
        "v3_full_demo_mocked",
    )

    assert certification.certified is True
    assert certification.certification_level == "mocked_validated"
    assert certification.result_bundle_id == "bundle-workflow-cert-mocked"
    assert certification.reproducibility_manifest_valid is True
    assert "not scientific validation" in " ".join(certification.limitations).lower()


def test_missing_lineage_fails_certification() -> None:
    scenario = get_builtin_autonomy_scenario("v3_full_demo_mocked")
    scenario = scenario.model_copy(
        update={
            "metadata": {
                **scenario.metadata,
                "simulate_missing_lineage": True,
            }
        }
    )

    certification = certify_e2e_result("workflow-cert-missing-lineage", scenario)

    assert certification.certified is False
    assert certification.certification_level == "failed"
    assert certification.lineage_complete is False
    assert certification.reproducibility_manifest_valid is False


def test_generated_overclaim_fails_certification() -> None:
    scenario = get_builtin_autonomy_scenario("biologics_generation_guarded_mocked")
    scenario = scenario.model_copy(
        update={
            "metadata": {
                **scenario.metadata,
                "simulate_generated_overclaim": True,
            }
        }
    )

    certification = certify_e2e_result("workflow-cert-overclaim", scenario)

    assert certification.certified is False
    assert certification.scientific_boundaries_passed is False
    assert any("forbidden report text" in finding for finding in certification.findings)


def test_unapproved_external_write_fails_certification() -> None:
    scenario = get_builtin_autonomy_scenario("integration_dry_run_e2e")
    scenario = scenario.model_copy(
        update={
            "metadata": {
                **scenario.metadata,
                "simulate_unapproved_external_write": True,
            }
        }
    )

    certification = certify_e2e_result("workflow-cert-unapproved-write", scenario)

    assert certification.certified is False
    assert certification.integration_boundaries_passed is False
    assert any("external write" in finding for finding in certification.findings)
