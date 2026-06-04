from __future__ import annotations

import json

from molecule_ranker.engineering_repair.schemas import (
    EngineeringFailureReport,
    EngineeringRepairExecutionReport,
    EngineeringRepairPlan,
)


def report_to_json(report: EngineeringFailureReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def plan_to_json(plan: EngineeringRepairPlan) -> str:
    return json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def execution_to_json(report: EngineeringRepairExecutionReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
