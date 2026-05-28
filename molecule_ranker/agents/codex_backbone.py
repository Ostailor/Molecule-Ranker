from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from molecule_ranker.agents.base import AgentExecutionError, BaseAgent, PipelineContext
from molecule_ranker.codex import create_llm_provider
from molecule_ranker.codex_backbone import CodexBackboneConfig, CodexTask
from molecule_ranker.codex_backbone.schemas import CodexTaskResult
from molecule_ranker.contracts import with_artifact_contract_metadata
from molecule_ranker.utils import slugify

DEFAULT_CODEX_TASKS = [
    "summarize_run",
    "explain_top_candidates",
    "draft_review_questions",
    "plan_followup_run",
]

TASK_TYPE_ALIASES = {
    "explain_top_candidates": "explain_ranking",
    "draft_review_questions": "generate_review_questions",
}


class CodexProviderProtocol(Protocol):
    def run_task(self, task: CodexTask) -> CodexTaskResult: ...


class CodexBackboneAgent(BaseAgent):
    """Run optional Codex-backed orchestration after deterministic scoring."""

    name = "CodexBackboneAgent"

    def __init__(self, provider: CodexProviderProtocol | None = None) -> None:
        super().__init__()
        self.provider = provider

    def process(self, context: PipelineContext) -> PipelineContext:
        enabled = bool(context.config.get("enable_codex_backbone", False))
        if not enabled:
            context.config["codex_backbone_enabled"] = False
            context.config["codex_backbone_results"] = []
            return context

        provider = self.provider or create_llm_provider(_provider_config(context.config))
        tasks = _build_tasks(context)
        results: list[CodexTaskResult] = []
        failures: list[str] = []
        for task in tasks:
            try:
                result = provider.run_task(task)
            except Exception as exc:
                result = CodexTaskResult(
                    task_id=task.task_id,
                    task_type=task.task_type,
                    status="failed",
                    output_text="",
                    stdout="",
                    stderr=str(exc),
                    return_code=None,
                    artifacts_read=[],
                    artifacts_written=[],
                    commands_observed=[],
                    guardrail_warnings=[f"Codex provider raised: {exc}"],
                    usage_summary={},
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                    metadata={"requested_task": task.metadata.get("requested_task")},
                )
            results.append(result)
            if result.status != "succeeded":
                failures.append(f"{task.task_id}: {result.status}")

        payload = _results_payload(context, results)
        output_dir = _codex_output_dir(context)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "codex_backbone.json"
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

        context.output_dir = output_dir
        context.config["codex_backbone_enabled"] = True
        context.config["codex_backbone_json"] = str(output_path)
        context.config["codex_backbone_results"] = payload["results"]
        context.config["codex_backbone_summary"] = payload["summary"]

        if failures and bool(context.config.get("strict_codex_backbone", False)):
            raise AgentExecutionError(
                "Codex backbone failed in strict mode: " + "; ".join(failures)
            )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if not bool(context.config.get("codex_backbone_enabled", False)):
            return "Codex backbone disabled."
        summary = context.config.get("codex_backbone_summary", {})
        if not isinstance(summary, dict):
            summary = {}
        return (
            "Codex backbone completed "
            f"{summary.get('succeeded_count', 0)}/{summary.get('task_count', 0)} tasks."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        results = context.config.get("codex_backbone_results", [])
        results = results if isinstance(results, list) else []
        return {
            "enabled": bool(context.config.get("codex_backbone_enabled", False)),
            "artifact_path": context.config.get("codex_backbone_json"),
            "task_count": len(results),
            "statuses": {
                str(item.get("task_id")): item.get("status")
                for item in results
                if isinstance(item, dict)
            },
            "guardrail_warnings": [
                warning
                for item in results
                if isinstance(item, dict)
                for warning in item.get("guardrail_warnings", [])
            ],
        }


def _provider_config(config: dict[str, Any]) -> CodexBackboneConfig:
    return CodexBackboneConfig(
        enable_codex_backbone=bool(config.get("enable_codex_backbone", False)),
        codex_cli_command=str(config.get("codex_cli_command", "codex")),
        codex_model=str(config["codex_model"]) if config.get("codex_model") else None,
        codex_reasoning_effort=(
            str(config["codex_reasoning_effort"])
            if config.get("codex_reasoning_effort") is not None
            else "high"
        ),
        codex_working_dir=(
            Path(config["codex_working_dir"]) if config.get("codex_working_dir") else None
        ),
        codex_timeout_seconds=int(config.get("codex_timeout_seconds", 300) or 300),
        codex_require_json=bool(config.get("codex_require_json", True)),
        codex_dry_run=bool(config.get("codex_dry_run", False)),
        codex_allow_shell_commands=bool(config.get("codex_allow_shell_commands", False)),
        codex_allowed_commands=list(config.get("codex_allowed_commands", []) or []),
        codex_forbidden_commands=list(config.get("codex_forbidden_commands", []) or []),
        codex_max_artifact_bytes=int(config.get("codex_max_artifact_bytes", 1_000_000)),
        codex_redact_secrets=bool(config.get("codex_redact_secrets", True)),
        codex_store_transcripts=bool(config.get("codex_store_transcripts", True)),
        codex_guardrails_enabled=bool(config.get("codex_guardrails_enabled", True)),
    )


def _build_tasks(context: PipelineContext) -> list[CodexTask]:
    requested_tasks = list(context.config.get("codex_tasks") or DEFAULT_CODEX_TASKS)
    max_tasks = int(context.config.get("codex_max_tasks_per_run", 5) or 5)
    tasks: list[CodexTask] = []
    for requested in requested_tasks[:max_tasks]:
        if not isinstance(requested, str):
            continue
        task_type = TASK_TYPE_ALIASES.get(requested, requested)
        if task_type not in {
            "summarize_run",
            "explain_ranking",
            "generate_review_questions",
            "plan_followup_run",
        }:
            continue
        tasks.append(
            CodexTask(
                task_id=f"codex-{slugify(requested)}",
                task_type=task_type,  # type: ignore[arg-type]
                prompt=_task_prompt(context, requested),
                working_directory=str(_codex_output_dir(context)),
                input_artifact_paths=_existing_artifact_paths(context),
                allowed_commands=[],
                forbidden_commands=[],
                expected_output_format="json",
                timeout_seconds=int(context.config.get("codex_timeout_seconds", 300) or 300),
                require_json=bool(context.config.get("codex_require_json", True)),
                metadata={
                    "requested_task": requested,
                    "codex_prompt_mode": context.config.get("codex_prompt_mode", "stdin"),
                },
            )
        )
    return tasks


def _task_prompt(context: PipelineContext, requested_task: str) -> str:
    context_payload = _context_payload(context)
    task_instructions = {
        "summarize_run": (
            "Summarize the current deterministic molecule-ranker run. Use only the "
            "provided context and artifacts."
        ),
        "explain_top_candidates": (
            "Explain why the top candidates are ranked where they are. Do not alter "
            "scores or create evidence."
        ),
        "draft_review_questions": (
            "Draft high-level expert review questions from the current run. Do not "
            "propose lab protocols or synthesis steps."
        ),
        "plan_followup_run": (
            "Plan safe follow-up computational molecule-ranker tasks from the current "
            "run. Include only safe CLI commands when needed."
        ),
    }.get(requested_task, "Inspect the current run context.")
    return json.dumps(
        {
            "task": requested_task,
            "instructions": task_instructions,
            "context": context_payload,
            "hard_constraints": [
                "Do not change candidate scores.",
                "Do not create EvidenceItem records.",
                "Do not create assay results.",
                "Do not create generated molecules.",
                (
                    "Codex output may only contain explanations, review questions, "
                    "and follow-up plans."
                ),
            ],
        },
        indent=2,
        sort_keys=True,
    )


def _context_payload(context: PipelineContext) -> dict[str, Any]:
    return {
        "disease": context.disease.model_dump(mode="json") if context.disease else None,
        "target_count": len(context.targets),
        "targets": [
            {
                "symbol": target.symbol,
                "name": target.name,
                "disease_relevance_score": target.disease_relevance_score,
            }
            for target in context.targets
        ],
        "candidate_count": len(context.candidates),
        "top_candidates": [
            {
                "name": candidate.name,
                "origin": candidate.origin,
                "score": candidate.score,
                "confidence": (
                    candidate.score_breakdown.confidence if candidate.score_breakdown else None
                ),
                "known_targets": candidate.known_targets,
                "warnings": candidate.warnings,
                "score_breakdown": (
                    candidate.score_breakdown.model_dump(mode="json")
                    if candidate.score_breakdown
                    else None
                ),
                "evidence_count": len(candidate.evidence),
                "literature_evidence_present": candidate.literature_evidence is not None,
                "developability_summary": (
                    candidate.developability_assessment.model_dump(mode="json")
                    if candidate.developability_assessment
                    else None
                ),
            }
            for candidate in context.candidates[:5]
        ],
        "generated_candidate_count": len(context.generated_candidates),
        "generated_candidates": [
            {
                "name": candidate.name,
                "target_symbol": candidate.target_symbol,
                "generation_score": candidate.generation_score,
                "warnings": candidate.warnings,
            }
            for candidate in context.generated_candidates[:5]
        ],
        "trace_summaries": [
            {
                "agent_name": trace.agent_name,
                "output_summary": trace.output_summary,
                "warnings": trace.warnings,
            }
            for trace in context.traces
        ],
    }


def _results_payload(
    context: PipelineContext,
    results: list[CodexTaskResult],
) -> dict[str, Any]:
    return with_artifact_contract_metadata(
        {
            "success": all(result.status == "succeeded" for result in results),
            "generated_at": datetime.now(UTC).isoformat(),
            "disease": context.disease.model_dump(mode="json") if context.disease else None,
            "summary": {
                "enabled": True,
                "task_count": len(results),
                "succeeded_count": sum(
                    1 for result in results if result.status == "succeeded"
                ),
                "failed_count": sum(1 for result in results if result.status != "succeeded"),
                "guardrail_warning_count": sum(
                    len(result.guardrail_warnings) for result in results
                ),
            },
            "results": [result.model_dump(mode="json") for result in results],
            "limitations": [
                "Codex output is orchestration and summarization only.",
                "Codex output is not biomedical evidence.",
                "Codex output did not alter candidate scores or create evidence records.",
                "Codex output did not create assay results or generated molecules.",
            ],
        },
        "codex_backbone",
    )


def _existing_artifact_paths(context: PipelineContext) -> list[str]:
    output_dir = _codex_output_dir(context)
    paths = [
        output_dir / "report.md",
        output_dir / "candidates.json",
        output_dir / "trace.json",
        output_dir / "generated_candidates.json",
        output_dir / "developability.json",
        output_dir / "experimental_evidence.json",
    ]
    return [str(path) for path in paths if path.exists()]


def _codex_output_dir(context: PipelineContext) -> Path:
    if context.output_dir is not None:
        return context.output_dir
    results_dir = Path(context.config.get("results_dir") or "results")
    if context.disease is None:
        return results_dir
    return results_dir / slugify(context.disease.canonical_name)
