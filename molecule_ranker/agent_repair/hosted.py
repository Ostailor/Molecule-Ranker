from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.agent_repair.schemas import (
    FailureDiagnosis,
    RegressionCheck,
    RepairExecution,
    RepairMemoryRecord,
    RepairPlan,
)


class RepairHostedStore:
    """JSON-backed persistence for hosted agent repair artifacts."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.base_dir = root_dir / ".molecule-ranker" / "repair"
        self.diagnoses_dir = self.base_dir / "diagnoses"
        self.plans_dir = self.base_dir / "plans"
        self.executions_dir = self.base_dir / "executions"
        self.approvals_dir = self.base_dir / "approvals"
        self.memory_dir = self.base_dir / "memory"
        self.regression_dir = self.base_dir / "regression"

    def save_diagnosis(self, diagnosis: FailureDiagnosis) -> None:
        _write_json(
            self.diagnoses_dir / f"{diagnosis.diagnosis_id}.json",
            diagnosis.model_dump(mode="json"),
        )

    def get_diagnosis(self, diagnosis_id: str) -> FailureDiagnosis:
        return FailureDiagnosis.model_validate(
            _read_json(self.diagnoses_dir / f"{diagnosis_id}.json")
        )

    def list_diagnoses(self) -> list[FailureDiagnosis]:
        return [
            FailureDiagnosis.model_validate(_read_json(path))
            for path in sorted(self.diagnoses_dir.glob("*.json"))
        ]

    def save_plan(self, plan: RepairPlan) -> None:
        _write_json(self.plans_dir / f"{plan.repair_plan_id}.json", plan.model_dump(mode="json"))

    def get_plan(self, repair_plan_id: str) -> RepairPlan:
        return RepairPlan.model_validate(_read_json(self.plans_dir / f"{repair_plan_id}.json"))

    def list_plans(self) -> list[RepairPlan]:
        return [
            RepairPlan.model_validate(_read_json(path))
            for path in sorted(self.plans_dir.glob("*.json"))
        ]

    def save_execution(self, execution: RepairExecution) -> None:
        _write_json(
            self.executions_dir / f"{execution.repair_execution_id}.json",
            execution.model_dump(mode="json"),
        )

    def get_execution(self, execution_id: str) -> RepairExecution:
        return RepairExecution.model_validate(
            _read_json(self.executions_dir / f"{execution_id}.json")
        )

    def list_executions(self) -> list[RepairExecution]:
        return [
            RepairExecution.model_validate(_read_json(path))
            for path in sorted(self.executions_dir.glob("*.json"))
        ]

    def save_regression_check(self, check: RegressionCheck) -> None:
        _write_json(
            self.regression_dir / f"{check.regression_check_id}.json",
            check.model_dump(mode="json"),
        )

    def list_regression_checks(self) -> list[RegressionCheck]:
        return [
            RegressionCheck.model_validate(_read_json(path))
            for path in sorted(self.regression_dir.glob("*.json"))
        ]

    def save_memory(self, record: RepairMemoryRecord) -> None:
        _write_json(self.memory_dir / f"{record.memory_id}.json", record.model_dump(mode="json"))

    def list_memory(self) -> list[RepairMemoryRecord]:
        return [
            RepairMemoryRecord.model_validate(_read_json(path))
            for path in sorted(self.memory_dir.glob("*.json"))
        ]

    def save_approval(self, approval: dict[str, Any]) -> None:
        approval_id = str(approval["approval_id"])
        _write_json(self.approvals_dir / f"{approval_id}.json", approval)

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        return dict(_read_json(self.approvals_dir / f"{approval_id}.json"))

    def list_approvals(self) -> list[dict[str, Any]]:
        return [dict(_read_json(path)) for path in sorted(self.approvals_dir.glob("*.json"))]


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise KeyError(path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = ["RepairHostedStore"]
