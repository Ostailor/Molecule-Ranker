from __future__ import annotations

from typing import Any, Literal, cast

from rdkit import Chem
from rdkit.Chem import FilterCatalog

from molecule_ranker.developability.schemas import AlertSeverity, ChemistryAlert

AlertMode = Literal[
    "warn",
    "deprioritize",
    "reject_critical_only",
    "reject_high_and_critical",
]

_SEVERITY_WEIGHT = {
    "low": 0.05,
    "medium": 0.15,
    "high": 0.30,
    "critical": 0.50,
}

# Transparent local SMARTS alerts. These are coarse medicinal-chemistry risk flags,
# not proof of toxicity, assay interference, instability, aggregation, or in-vivo risk.
LOCAL_SMARTS_ALERTS: tuple[dict[str, str], ...] = (
    {
        "alert_id": "local_reactive_acid_chloride",
        "alert_type": "reactive_functionality",
        "alert_name": "Acid chloride",
        "severity": "high",
        "smarts": "C(=O)Cl",
        "description": "Acid chloride is a reactive functionality alert and risk flag.",
    },
    {
        "alert_id": "local_reactive_isocyanate",
        "alert_type": "reactive_functionality",
        "alert_name": "Isocyanate",
        "severity": "high",
        "smarts": "N=C=O",
        "description": "Isocyanate is a reactive functionality alert and risk flag.",
    },
    {
        "alert_id": "local_unstable_azide",
        "alert_type": "unstable_group",
        "alert_name": "Azide",
        "severity": "medium",
        "smarts": "[$([N-]=[N+]=N),$([N]=[N+]=[N-])]",
        "description": "Azide is an unstable-group alert for computational triage.",
    },
    {
        "alert_id": "local_electrophilic_michael_acceptor",
        "alert_type": "reactive_functionality",
        "alert_name": "Michael-acceptor-like motif",
        "severity": "medium",
        "smarts": "[C,c]=[C,c]-[C,S,N,P]=[O,S,N]",
        "description": "Michael-acceptor-like motif is an electrophilic warhead risk flag.",
    },
    {
        "alert_id": "local_redox_catechol",
        "alert_type": "structural_liability",
        "alert_name": "Catechol",
        "severity": "medium",
        "smarts": "c1([OH])c([OH])cccc1",
        "description": "Catechol is a redox-cycling structural-liability risk flag.",
    },
    {
        "alert_id": "local_redox_hydroquinone",
        "alert_type": "structural_liability",
        "alert_name": "Hydroquinone",
        "severity": "medium",
        "smarts": "Oc1ccc(O)cc1",
        "description": "Hydroquinone is a redox-cycling structural-liability risk flag.",
    },
    {
        "alert_id": "local_aggregator_long_hydrophobe",
        "alert_type": "assay_interference",
        "alert_name": "Long hydrophobic chain",
        "severity": "low",
        "smarts": "CCCCCCCCCCCC",
        "description": "Long hydrophobic chain is a frequent-hitter or aggregator-risk proxy.",
    },
    {
        "alert_id": "local_toxicophore_aromatic_nitro",
        "alert_type": "toxicophore",
        "alert_name": "Aromatic nitro group",
        "severity": "medium",
        "smarts": "[$([N+](=O)[O-]),$([N](=O)=O)]",
        "description": "Aromatic nitro group is a toxicophore-like risk flag.",
    },
    {
        "alert_id": "local_toxicophore_aniline",
        "alert_type": "toxicophore",
        "alert_name": "Aniline-like motif",
        "severity": "medium",
        "smarts": "[NX3;H2,H1;!$(NC=O)]c",
        "description": "Aniline-like motif is a toxicophore-like risk flag.",
    },
)


def detect_chemistry_alerts(smiles: str) -> list[ChemistryAlert]:
    mol = _mol_from_smiles_or_raise(smiles)
    alerts: list[ChemistryAlert] = []
    alerts.extend(_filter_catalog_alerts(mol, "pains"))
    alerts.extend(_filter_catalog_alerts(mol, "brenk"))
    alerts.extend(_local_smarts_alerts(mol))
    return _deduplicate_alerts(alerts)


def severity_from_alert(alert: Any) -> AlertSeverity:
    if isinstance(alert, ChemistryAlert):
        return alert.severity
    if isinstance(alert, dict):
        severity = str(alert.get("severity") or "medium").lower()
        return _normalize_severity(severity)
    description_getter = getattr(alert, "GetDescription", None)
    description = str(description_getter()) if callable(description_getter) else str(alert)
    lowered = description.lower()
    if "critical" in lowered:
        return "critical"
    if any(term in lowered for term in ("warhead", "acid chloride", "isocyanate")):
        return "high"
    if any(term in lowered for term in ("pains", "brenk", "catechol", "toxic")):
        return "medium"
    return "low"


def alert_penalty(alerts: list[ChemistryAlert]) -> float:
    raw = sum(_SEVERITY_WEIGHT[severity_from_alert(alert)] for alert in alerts)
    return round(max(0.0, min(raw, 1.0)), 3)


def evaluate_alert_mode(
    alerts: list[ChemistryAlert],
    *,
    alert_mode: AlertMode = "warn",
) -> dict[str, Any]:
    severities = {severity_from_alert(alert) for alert in alerts}
    rejected = False
    deprioritized = False
    if alert_mode == "deprioritize" and alerts:
        deprioritized = True
    elif alert_mode == "reject_critical_only":
        rejected = "critical" in severities
    elif alert_mode == "reject_high_and_critical":
        rejected = bool({"high", "critical"} & severities)
    elif alert_mode != "warn":
        raise ValueError(f"Unsupported alert_mode: {alert_mode!r}")

    return {
        "alert_mode": alert_mode,
        "rejected": rejected,
        "deprioritized": deprioritized,
        "penalty": alert_penalty(alerts),
        "alert_count": len(alerts),
        "max_severity": _max_severity(severities),
    }


def _filter_catalog_alerts(mol: Chem.Mol, catalog_name: str) -> list[ChemistryAlert]:
    catalog = _catalog(catalog_name)
    if catalog is None:
        return []
    alerts: list[ChemistryAlert] = []
    for index, entry in enumerate(catalog.GetMatches(mol), start=1):
        description = entry.GetDescription()
        filter_set = _entry_prop(entry, "FilterSet") or catalog_name.upper()
        source = _entry_prop(entry, "Reference") or f"RDKit FilterCatalog {filter_set}"
        alerts.append(
            ChemistryAlert(
                alert_id=f"rdkit_{catalog_name}_{description}_{index}",
                alert_type="pains" if catalog_name == "pains" else "brenk",
                alert_name=description,
                severity=severity_from_alert(
                    {"severity": "medium" if catalog_name == "pains" else "high"}
                ),
                matched_smarts=None,
                description=(
                    f"RDKit {filter_set} catalog match. This is an alert/risk flag, "
                    "not proof of toxicity or assay interference."
                ),
                source=source,
                metadata={
                    "catalog": catalog_name,
                    "filter_set": filter_set,
                    "scope": _entry_prop(entry, "Scope"),
                    "transparent_limitations": (
                        "FilterCatalog alerts are coarse triage signals and require review."
                    ),
                },
            )
        )
    return alerts


def _local_smarts_alerts(mol: Chem.Mol) -> list[ChemistryAlert]:
    alerts: list[ChemistryAlert] = []
    for definition in LOCAL_SMARTS_ALERTS:
        pattern = Chem.MolFromSmarts(definition["smarts"])
        if pattern is None or not mol.HasSubstructMatch(pattern):
            continue
        alerts.append(
            ChemistryAlert(
                alert_id=definition["alert_id"],
                alert_type=definition["alert_type"],  # type: ignore[arg-type]
                alert_name=definition["alert_name"],
                severity=definition["severity"],  # type: ignore[arg-type]
                matched_smarts=definition["smarts"],
                description=definition["description"],
                source="local_transparent_smarts",
                metadata={
                    "category_note": (
                        "Local SMARTS pattern is an explicitly documented alert, "
                        "not a toxicity proof."
                    ),
                    "pattern_set": "molecule_ranker_v0.4_local_alerts",
                },
            )
        )
    return alerts


def _catalog(catalog_name: str) -> FilterCatalog.FilterCatalog | None:
    params = FilterCatalog.FilterCatalogParams()
    catalogs = FilterCatalog.FilterCatalogParams.FilterCatalogs
    if catalog_name == "pains":
        params.AddCatalog(catalogs.PAINS)
    elif catalog_name == "brenk":
        params.AddCatalog(catalogs.BRENK)
    else:
        return None
    try:
        return FilterCatalog.FilterCatalog(params)
    except Exception:
        return None


def _mol_from_smiles_or_raise(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles!r}")
    try:
        Chem.SanitizeMol(mol)
    except Exception as exc:
        raise ValueError(f"Invalid SMILES: {smiles!r}") from exc
    return mol


def _entry_prop(entry: Any, key: str) -> str | None:
    try:
        value = entry.GetProp(key)
    except Exception:
        return None
    return str(value) if value not in (None, "") else None


def _deduplicate_alerts(alerts: list[ChemistryAlert]) -> list[ChemistryAlert]:
    seen: set[tuple[str, str | None]] = set()
    deduped: list[ChemistryAlert] = []
    for alert in alerts:
        key = (alert.alert_name.lower(), alert.matched_smarts)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(alert)
    return deduped


def _max_severity(severities: set[str]) -> str | None:
    if not severities:
        return None
    order = ["low", "medium", "high", "critical"]
    return max(severities, key=lambda severity: order.index(severity))


def _normalize_severity(value: str) -> AlertSeverity:
    if value in _SEVERITY_WEIGHT:
        return cast(AlertSeverity, value)
    return "medium"


__all__ = [
    "AlertMode",
    "ChemistryAlert",
    "LOCAL_SMARTS_ALERTS",
    "alert_penalty",
    "detect_chemistry_alerts",
    "evaluate_alert_mode",
    "severity_from_alert",
]
