from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.contracts.api_contracts import export_api_contracts
from molecule_ranker.contracts.artifact_contracts import ARTIFACT_CONTRACTS


def export_artifact_contracts() -> dict[str, Any]:
    return {
        "artifact_contract_version": "1.0",
        "contracts": {
            filename: contract.as_dict()
            for filename, contract in sorted(ARTIFACT_CONTRACTS.items())
        },
    }


def export_all_contracts() -> dict[str, Any]:
    return {
        "api": export_api_contracts(),
        "artifacts": export_artifact_contracts(),
    }


def write_contract_exports(output_dir: str | Path) -> dict[str, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    artifact_path = target / "artifact_contracts.v1.json"
    api_path = target / "api_contracts.v1.json"
    artifact_path.write_text(json.dumps(export_artifact_contracts(), indent=2, sort_keys=True))
    api_path.write_text(json.dumps(export_api_contracts(), indent=2, sort_keys=True))
    return {"artifact_contracts": artifact_path, "api_contracts": api_path}
