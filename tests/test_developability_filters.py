from __future__ import annotations

import pytest

from molecule_ranker.developability.filters import (
    alert_penalty,
    detect_chemistry_alerts,
    evaluate_alert_mode,
    severity_from_alert,
)
from molecule_ranker.developability.schemas import ChemistryAlert


def _alert(severity: str) -> ChemistryAlert:
    return ChemistryAlert(
        alert_id=f"test-{severity}",
        alert_type="structural_liability",
        alert_name=f"{severity} alert",
        severity=severity,  # type: ignore[arg-type]
        matched_smarts=None,
        description="Test alert used to verify alert-mode behavior.",
        source="test",
        metadata={},
    )


def test_pains_like_molecule_triggers_alert():
    alerts = detect_chemistry_alerts("O=C1NC(=S)SC1")

    assert alerts
    assert any(alert.alert_type == "pains" for alert in alerts)
    assert all("proof" in alert.description.lower() for alert in alerts)


def test_benign_molecule_has_no_critical_alerts():
    alerts = detect_chemistry_alerts("CCO")

    assert not any(alert.severity == "critical" for alert in alerts)


def test_local_smarts_severity_mapping_works():
    alerts = detect_chemistry_alerts("CC(=O)Cl")

    acid_chloride = next(alert for alert in alerts if alert.alert_name == "Acid chloride")
    assert severity_from_alert(acid_chloride) == "high"
    assert acid_chloride.alert_type == "reactive_functionality"
    assert acid_chloride.source == "local_transparent_smarts"


def test_alert_penalty_is_bounded():
    alerts = [_alert("critical") for _ in range(10)]

    assert alert_penalty(alerts) == pytest.approx(1.0)


def test_alert_mode_controls_reject_and_deprioritize_behavior():
    high = [_alert("high")]
    critical = [_alert("critical")]

    assert evaluate_alert_mode(high, alert_mode="warn")["rejected"] is False
    assert evaluate_alert_mode(high, alert_mode="warn")["deprioritized"] is False
    assert evaluate_alert_mode(high, alert_mode="deprioritize")["deprioritized"] is True
    assert evaluate_alert_mode(high, alert_mode="reject_critical_only")["rejected"] is False
    assert evaluate_alert_mode(critical, alert_mode="reject_critical_only")["rejected"] is True
    assert evaluate_alert_mode(high, alert_mode="reject_high_and_critical")["rejected"] is True


def test_unsupported_alert_mode_fails_clearly():
    with pytest.raises(ValueError, match="Unsupported alert_mode"):
        evaluate_alert_mode([_alert("low")], alert_mode="reject_all")  # type: ignore[arg-type]
