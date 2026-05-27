from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from molecule_ranker.codex_backbone.guardrails import (
    check_output,
    detect_protocol_or_synthesis_text,
    redact_secrets,
)
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.review.dossier import (
    GENERATED_DIRECT_EVIDENCE_NOTICE,
    DossierWriterAgent,
)
from molecule_ranker.review.schemas import CodexReviewArtifact, ReviewItem, ReviewWorkspace
from molecule_ranker.utils import slugify


class CodexReviewProvider(Protocol):
    def run_task(self, task: CodexTask) -> CodexTaskResult: ...


class CodexReviewAssistant:
    """Runs Codex-backed review assistance without mutating scientific records."""

    def __init__(
        self,
        provider: CodexReviewProvider,
        *,
        working_directory: str | Path = ".",
    ) -> None:
        self.provider = provider
        self.working_directory = Path(working_directory).resolve()

    def draft_questions(
        self,
        workspace: ReviewWorkspace,
        review_item_id: str,
    ) -> CodexReviewArtifact:
        item = _get_item(workspace, review_item_id)
        context_path = self._write_context_artifact(
            workspace,
            [item],
            assistant_task="codex_review_questions",
        )
        task = self._task(
            workspace,
            [item],
            task_type="generate_review_questions",
            task_slug="review-questions",
            prompt=(
                "Draft high-level candidate review questions for the supplied review item. "
                "Questions must help a human reviewer inspect evidence limitations, conflicts, "
                "developability flags, and imported experimental summaries. Do not draft a final "
                "decision, create evidence, alter scores, create assay results, or propose "
                "protocols, synthesis, dosing, or treatment advice."
            ),
            context_path=context_path,
        )
        return self._run_and_package(
            workspace,
            [item],
            task,
            review_task_type="codex_review_questions",
        )

    def summarize_dossier(
        self,
        workspace: ReviewWorkspace,
        review_item_id: str,
    ) -> CodexReviewArtifact:
        item = _get_item(workspace, review_item_id)
        context_path = self._write_context_artifact(
            workspace,
            [item],
            assistant_task="codex_dossier_summary",
        )
        task = self._task(
            workspace,
            [item],
            task_type="draft_dossier",
            task_slug="dossier-summary",
            prompt=(
                "Summarize the supplied candidate dossier for expert review using only the "
                "review item and dossier sections. Include key evidence, key risks, conflicting "
                "or missing evidence, experimental result context if present, and validation "
                "questions. Do not make final reviewer decisions or create biomedical evidence."
            ),
            context_path=context_path,
        )
        return self._run_and_package(
            workspace,
            [item],
            task,
            review_task_type="codex_dossier_summary",
        )

    def compare_candidates(
        self,
        workspace: ReviewWorkspace,
        item_a: str,
        item_b: str,
    ) -> CodexReviewArtifact:
        items = [_get_item(workspace, item_a), _get_item(workspace, item_b)]
        context_path = self._write_context_artifact(
            workspace,
            items,
            assistant_task="codex_candidate_compare",
        )
        task = self._task(
            workspace,
            items,
            task_type="compare_candidates",
            task_slug="candidate-compare",
            prompt=(
                "Compare the supplied review candidates for expert triage. Explain shared "
                "strengths, differences, risks, conflicting evidence, imported experimental "
                "result context, and review questions. Do not select a biomedical winner, mutate "
                "scores, create evidence, create assay results, or provide protocols, synthesis, "
                "dosing, or treatment advice."
            ),
            context_path=context_path,
        )
        return self._run_and_package(
            workspace,
            items,
            task,
            review_task_type="codex_candidate_compare",
        )

    def explain_conflicting_evidence(
        self,
        workspace: ReviewWorkspace,
        review_item_id: str,
    ) -> CodexReviewArtifact:
        item = _get_item(workspace, review_item_id)
        context_path = self._write_context_artifact(
            workspace,
            [item],
            assistant_task="codex_conflicting_evidence",
        )
        task = self._task(
            workspace,
            [item],
            task_type="explain_conflicting_evidence",
            task_slug="conflicting-evidence",
            prompt=(
                "Explain conflicting or limited evidence for the supplied review item. "
                "Use only conflicts, limitations, imported result summaries, review comments, "
                "and evidence gaps present in the review-context artifact. Do not resolve the "
                "conflict as biomedical truth or make a final reviewer decision."
            ),
            context_path=context_path,
        )
        return self._run_and_package(
            workspace,
            [item],
            task,
            review_task_type="codex_conflicting_evidence",
        )

    def summarize_experimental_results(
        self,
        workspace: ReviewWorkspace,
        review_item_id: str,
    ) -> CodexReviewArtifact:
        item = _get_item(workspace, review_item_id)
        context_path = self._write_context_artifact(
            workspace,
            [item],
            assistant_task="codex_experimental_summary",
        )
        task = self._task(
            workspace,
            [item],
            task_type="summarize_experimental_results",
            task_slug="experimental-summary",
            prompt=(
                "Summarize imported experimental result context for the supplied review item. "
                "Use only already-linked experimental summaries in the review-context artifact. "
                "Do not create assay results, infer clinical efficacy, or provide experimental "
                "protocols or operating conditions."
            ),
            context_path=context_path,
        )
        return self._run_and_package(
            workspace,
            [item],
            task,
            review_task_type="codex_experimental_summary",
        )

    def draft_project_update(self, workspace: ReviewWorkspace) -> CodexReviewArtifact:
        context_path = self._write_context_artifact(
            workspace,
            workspace.review_items,
            assistant_task="codex_project_update",
        )
        task = self._task(
            workspace,
            workspace.review_items,
            task_type="draft_project_update",
            task_slug="project-update",
            prompt=(
                "Draft a project update note from review workspace status only. Summarize review "
                "queue status, Codex assistance status, open uncertainties, and suggested safe "
                "next project actions. Do not create or modify scientific evidence."
            ),
            context_path=context_path,
        )
        return self._run_and_package(
            workspace,
            workspace.review_items,
            task,
            review_task_type="codex_project_update",
        )

    def _task(
        self,
        workspace: ReviewWorkspace,
        items: list[ReviewItem],
        *,
        task_type: str,
        task_slug: str,
        prompt: str,
        context_path: Path,
    ) -> CodexTask:
        item_ids = [item.review_item_id for item in items]
        return CodexTask(
            task_id=slugify(f"{workspace.workspace_id}-{task_slug}-{'-'.join(item_ids[:3])}"),
            task_type=task_type,  # type: ignore[arg-type]
            prompt=_review_prompt(prompt, items),
            working_directory=str(self.working_directory),
            input_artifact_paths=[str(context_path)],
            allowed_commands=[],
            forbidden_commands=[
                "git push",
                "rm -rf",
                "sudo",
                "curl |",
                "printenv",
                "cat .env",
            ],
            expected_output_format="json",
            timeout_seconds=300,
            require_json=True,
            metadata={
                "workspace_id": workspace.workspace_id,
                "review_item_ids": item_ids,
                "review_assistance_only": True,
                "must_not_mutate_scores": True,
                "must_not_create_evidence": True,
                "must_not_create_assay_results": True,
            },
        )

    def _run_and_package(
        self,
        workspace: ReviewWorkspace,
        items: list[ReviewItem],
        task: CodexTask,
        *,
        review_task_type: str,
    ) -> CodexReviewArtifact:
        result = self.provider.run_task(task)
        is_dry_run = bool(result.metadata.get("dry_run")) or bool(
            isinstance(result.output_json, dict) and result.output_json.get("dry_run")
        )
        output_json = _postprocess_output_json(result.output_json, items)
        output_text = redact_secrets(result.output_text or result.stdout)
        guardrail_text = "" if is_dry_run else output_text
        allowed_refs = _allowed_refs(task.input_artifact_paths, items)
        guarded = check_output(
            result.model_copy(
                update={
                    "output_json": output_json,
                    "output_text": guardrail_text,
                    "stdout": "" if is_dry_run else result.stdout,
                }
            ),
            allowed_refs,
            _allowed_citation_refs(items),
        )
        warnings = list(guarded.guardrail_warnings)
        warnings.extend(
            warning
            for warning in _required_warning_gaps(output_json, items)
            if warning not in warnings
        )
        if (
            not is_dry_run
            and any(detect_protocol_or_synthesis_text(json.dumps(output_json or {}) + output_text))
        ):
            output_json = _strip_unsafe_review_payload(output_json)
            output_text = ""
        artifact = CodexReviewArtifact(
            workspace_id=workspace.workspace_id,
            review_item_ids=[item.review_item_id for item in items],
            task_type=review_task_type,  # type: ignore[arg-type]
            status=guarded.status,
            output_json=output_json,
            output_text=output_text,
            artifact_refs=_extract_artifact_refs(output_json, task.input_artifact_paths),
            guardrail_warnings=warnings,
            generated_at=datetime.now(UTC),
            metadata={
                "codex_task_id": task.task_id,
                "codex_task_type": task.task_type,
                "codex_result_status": result.status,
                "artifacts_read": result.artifacts_read,
                "usage_summary": result.usage_summary,
                "review_assistance_only": True,
            },
        )
        return artifact

    def _write_context_artifact(
        self,
        workspace: ReviewWorkspace,
        items: list[ReviewItem],
        *,
        assistant_task: str,
    ) -> Path:
        context_dir = self.working_directory / ".review" / "codex_context"
        context_dir.mkdir(parents=True, exist_ok=True)
        item_slug = "-".join(item.review_item_id for item in items[:3])
        safe_name = slugify(f"{workspace.workspace_id}-{assistant_task}-{item_slug}")
        path = context_dir / f"{safe_name}.json"
        payload = _review_context_payload(workspace, items, assistant_task)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path


def _review_context_payload(
    workspace: ReviewWorkspace,
    items: list[ReviewItem],
    assistant_task: str,
) -> dict[str, Any]:
    dossier_agent = DossierWriterAgent()
    item_ids = {item.review_item_id for item in items}
    return {
        "artifact_id": f"review-context:{workspace.workspace_id}:{assistant_task}",
        "workspace": {
            "workspace_id": workspace.workspace_id,
            "run_id": workspace.run_id,
            "disease_name": workspace.disease_name,
            "created_at": workspace.created_at.isoformat(),
        },
        "assistant_task": assistant_task,
        "review_items": [item.model_dump(mode="json") for item in items],
        "dossier_sections": {
            item.review_item_id: dossier_agent.build_dossier(
                workspace,
                item.review_item_id,
            ).metadata.get("sections", [])
            for item in items
        },
        "review_decisions": [
            decision.model_dump(mode="json")
            for decision in workspace.decisions
            if decision.review_item_id in item_ids
        ],
        "review_comments": [
            comment.model_dump(mode="json")
            for comment in workspace.comments
            if comment.review_item_id in item_ids
        ],
        "followup_requests": [
            request.model_dump(mode="json")
            for request in workspace.followup_requests
            if request.review_item_id in item_ids
        ],
        "codex_boundaries": [
            "Codex review assistance is not evidence, not a decision, and not a score update.",
            (
                "Generated molecules have no direct experimental evidence unless an exact "
                "imported result exists."
            ),
            "No protocols, synthesis routes, dosing, treatment advice, or clinical conclusions.",
        ],
    }


def _review_prompt(base_prompt: str, items: list[ReviewItem]) -> str:
    generated_notice = (
        f" Preserve this warning: {GENERATED_DIRECT_EVIDENCE_NOTICE}"
        if any(item.candidate_origin == "generated" for item in items)
        else ""
    )
    return (
        f"{base_prompt}{generated_notice} Use only the supplied review-context artifact. "
        "Cite artifact_refs using file paths or artifact IDs. Return JSON only. "
        "Codex output must stay separate from reviewer decisions, biomedical evidence, "
        "score fields, assay results, and generated molecule records."
    )


def _postprocess_output_json(
    output_json: dict[str, Any] | None,
    items: list[ReviewItem],
) -> dict[str, Any] | None:
    if output_json is None:
        return None
    updated = dict(output_json)
    if any(item.candidate_origin == "generated" for item in items):
        not_claimed = list(updated.get("not_claimed") or [])
        if not any(
            "no direct experimental evidence" in str(value).lower()
            for value in not_claimed
        ):
            not_claimed.append(GENERATED_DIRECT_EVIDENCE_NOTICE)
        updated["not_claimed"] = not_claimed
    refs = list(updated.get("artifact_refs") or [])
    for item in items:
        ref = f"review_item:{item.review_item_id}"
        if ref not in refs:
            refs.append(ref)
    updated["artifact_refs"] = refs
    return updated


def _required_warning_gaps(
    output_json: dict[str, Any] | None,
    items: list[ReviewItem],
) -> list[str]:
    if output_json is None or not any(item.candidate_origin == "generated" for item in items):
        return []
    text = json.dumps(output_json).lower()
    if "no direct experimental evidence" in text:
        return []
    return ["Generated molecule output omitted no-direct-evidence warning; warning restored."]


def _strip_unsafe_review_payload(output_json: dict[str, Any] | None) -> dict[str, Any] | None:
    if output_json is None:
        return None
    return {
        "guardrail_failed": True,
        "message": (
            "Codex output was withheld because it included protocol, synthesis, dosing, "
            "or treatment content."
        ),
        "artifact_refs": output_json.get("artifact_refs", []),
    }


def _extract_artifact_refs(
    output_json: dict[str, Any] | None,
    input_artifact_paths: list[str],
) -> list[str]:
    refs = list(input_artifact_paths)
    if isinstance(output_json, dict):
        for ref in output_json.get("artifact_refs") or []:
            if isinstance(ref, str) and ref not in refs:
                refs.append(ref)
    return refs


def _allowed_refs(input_artifact_paths: list[str], items: list[ReviewItem]) -> set[str]:
    refs = set(input_artifact_paths)
    for path in input_artifact_paths:
        refs.add(Path(path).name)
    for item in items:
        refs.update(
            {
                item.review_item_id,
                f"review_item:{item.review_item_id}",
                item.candidate_id,
                item.candidate_name,
            }
        )
    return refs


def _allowed_citation_refs(items: list[ReviewItem]) -> set[str]:
    refs: set[str] = set()
    for item in items:
        _collect_citation_refs(item.literature_summary, refs)
        _collect_citation_refs(item.evidence_summary, refs)
    return refs


def _collect_citation_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        for key, raw in value.items():
            lowered = str(key).lower()
            if lowered == "pmid" and raw:
                refs.add(f"PMID:{raw}")
                refs.add(str(raw))
            elif lowered in {"doi", "citation_id", "source_record_id"} and raw:
                refs.add(str(raw))
            _collect_citation_refs(raw, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_citation_refs(item, refs)


def _get_item(workspace: ReviewWorkspace, review_item_id: str) -> ReviewItem:
    for item in workspace.review_items:
        if item.review_item_id == review_item_id:
            return item
    raise ValueError(f"Unknown review item: {review_item_id}")
