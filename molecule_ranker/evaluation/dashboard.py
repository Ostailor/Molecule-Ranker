from __future__ import annotations

from molecule_ranker.evaluation.schemas import EvaluationReport


def evaluation_dashboard_summary(report: EvaluationReport) -> dict[str, object]:
    return {
        "evaluation_id": report.evaluation_id,
        "task_id": report.task_id,
        "dataset_id": report.dataset_id,
        "metric_count": len(report.metrics),
        "warning_count": len(report.warnings),
        "limitation_count": len(report.limitations),
    }


__all__ = ["evaluation_dashboard_summary"]
