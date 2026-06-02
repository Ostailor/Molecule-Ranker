from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

PerformanceStep = Callable[[], dict[str, Any]]

PERFORMANCE_STEPS = [
    "ranking_pipeline",
    "literature_retrieval",
    "generation",
    "developability",
    "model_training",
    "structure_workflow",
    "graph_build",
    "hypothesis_generation",
    "campaign_planning",
    "evaluation_benchmark",
    "dashboard_response",
    "api_response",
    "job_queue_wait",
    "job_run",
    "artifact_write",
    "artifact_read",
    "codex_task",
]


def synthetic_benchmark_steps(workflow: str = "golden") -> dict[str, PerformanceStep]:
    if workflow != "golden":
        raise ValueError("Only the synthetic golden workflow is available by default.")
    return {
        "ranking_pipeline": _ranking_pipeline,
        "literature_retrieval": _literature_retrieval,
        "generation": _generation,
        "developability": _developability,
        "model_training": _model_training,
        "structure_workflow": _structure_workflow,
        "graph_build": _graph_build,
        "hypothesis_generation": _hypothesis_generation,
        "campaign_planning": _campaign_planning,
        "evaluation_benchmark": _evaluation_benchmark,
        "dashboard_response": _dashboard_response,
        "api_response": _api_response,
        "job_queue_wait": _job_queue_wait,
        "job_run": _job_run,
        "artifact_write": _artifact_write,
        "artifact_read": _artifact_read,
        "codex_task": _codex_task,
    }


def _ranking_pipeline() -> dict[str, Any]:
    scores = sorted(((_stable_float(f"candidate-{idx}"), idx) for idx in range(96)), reverse=True)
    return {"candidate_count": len(scores), "top_rank": scores[0][1]}


def _literature_retrieval() -> dict[str, Any]:
    records = [_hash_record("paper", idx) for idx in range(64)]
    return {"record_count": len(records), "source": "mocked-literature"}


def _generation() -> dict[str, Any]:
    hypotheses = [_hash_record("hypothesis", idx) for idx in range(48)]
    return {"hypothesis_count": len(hypotheses), "mode": "synthetic-hypotheses"}


def _developability() -> dict[str, Any]:
    flags = [idx for idx in range(72) if idx % 7 == 0]
    return {"candidate_count": 72, "triage_flag_count": len(flags)}


def _model_training() -> dict[str, Any]:
    rows = [(idx, idx % 2, _stable_float(f"feature-{idx}")) for idx in range(128)]
    loss_proxy = sum(abs(label - score) for _, label, score in rows) / len(rows)
    return {"training_rows": len(rows), "loss_proxy": round(loss_proxy, 6)}


def _structure_workflow() -> dict[str, Any]:
    poses = [_stable_float(f"pose-{idx}") for idx in range(40)]
    return {"pose_count": len(poses), "score_checksum": round(sum(poses), 6)}


def _graph_build() -> dict[str, Any]:
    nodes = [f"node-{idx}" for idx in range(80)]
    edges = [(nodes[idx], nodes[(idx * 3) % len(nodes)]) for idx in range(len(nodes))]
    return {"node_count": len(nodes), "edge_count": len(edges)}


def _hypothesis_generation() -> dict[str, Any]:
    questions = [f"question-{idx}-{_stable_int(str(idx)) % 5}" for idx in range(36)]
    return {"question_count": len(questions)}


def _campaign_planning() -> dict[str, Any]:
    work_packages = [idx for idx in range(12)]
    milestones = [idx for idx in work_packages if idx % 3 == 0]
    return {"work_package_count": len(work_packages), "milestone_count": len(milestones)}


def _evaluation_benchmark() -> dict[str, Any]:
    labels = [idx % 2 for idx in range(100)]
    predictions = [_stable_float(f"prediction-{idx}") for idx in range(100)]
    calibration_proxy = (
        sum(
            abs(label - prediction)
            for label, prediction in zip(labels, predictions, strict=True)
        )
        / 100
    )
    return {"case_count": len(labels), "calibration_proxy": round(calibration_proxy, 6)}


def _dashboard_response() -> dict[str, Any]:
    payload = {"projects": [{"id": idx, "status": "ready"} for idx in range(20)]}
    return {"response_bytes": len(json.dumps(payload, sort_keys=True))}


def _api_response() -> dict[str, Any]:
    payload = {"runs": [{"id": idx, "state": "completed"} for idx in range(30)]}
    return {"response_bytes": len(json.dumps(payload, sort_keys=True))}


def _job_queue_wait() -> dict[str, Any]:
    queued_at = 1_000
    started_at = 1_025
    return {"wait_ms": started_at - queued_at, "queue": "synthetic"}


def _job_run() -> dict[str, Any]:
    tasks = [idx * idx for idx in range(80)]
    return {"task_count": len(tasks), "checksum": sum(tasks)}


def _artifact_write() -> dict[str, Any]:
    payload = {"artifacts": [_hash_record("artifact", idx) for idx in range(24)]}
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "artifact.json"
        raw = json.dumps(payload, sort_keys=True)
        path.write_text(raw)
        return {"bytes_written": path.stat().st_size}


def _artifact_read() -> dict[str, Any]:
    payload = {"artifacts": [_hash_record("artifact-read", idx) for idx in range(24)]}
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "artifact.json"
        path.write_text(json.dumps(payload, sort_keys=True))
        loaded = json.loads(path.read_text())
        return {"artifact_count": len(loaded["artifacts"])}


def _codex_task() -> dict[str, Any]:
    tasks = [{"duration_ms": 12 + idx, "timed_out": False} for idx in range(5)]
    return {
        "task_count": len(tasks),
        "timeout_count": sum(1 for task in tasks if task["timed_out"]),
        "mean_duration_ms": sum(task["duration_ms"] for task in tasks) / len(tasks),
    }


def _hash_record(prefix: str, idx: int) -> dict[str, str | int]:
    digest = hashlib.sha256(f"{prefix}:{idx}".encode()).hexdigest()[:12]
    return {"id": f"{prefix}-{idx}", "digest": digest}


def _stable_float(value: str) -> float:
    return _stable_int(value) / 1_000_000


def _stable_int(value: str) -> int:
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return int(digest, 16) % 1_000_000


__all__ = ["PERFORMANCE_STEPS", "PerformanceStep", "synthetic_benchmark_steps"]
