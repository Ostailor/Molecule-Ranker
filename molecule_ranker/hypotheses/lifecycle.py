from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.hypotheses.schemas import (
    HypothesisGenerationRun,
    HypothesisLifecycleEvent,
    ResearchHypothesis,
    ResearchHypothesisStatus,
)
from molecule_ranker.hypotheses.store import HypothesisStore

__all__ = [
    "HypothesisGenerationRun",
    "HypothesisLifecycleEvent",
    "HypothesisLifecycleManager",
]


STATUS_EVENT_TYPES: dict[str, str] = {
    "accepted_for_planning": "accepted",
    "rejected": "rejected",
    "contradicted": "contradicted",
    "stale": "made_stale",
    "retired": "retired",
}


class HypothesisLifecycleManager:
    """Apply deterministic status changes and write auditable lifecycle events."""

    def __init__(
        self,
        store: HypothesisStore,
        *,
        require_generated_molecule_human_approval: bool = True,
    ) -> None:
        self.store = store
        self.require_generated_molecule_human_approval = (
            require_generated_molecule_human_approval
        )

    def transition_status(
        self,
        hypothesis_id: str,
        status: ResearchHypothesisStatus,
        *,
        actor: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        patch: dict[str, Any] | None = None,
    ) -> ResearchHypothesis:
        current = self.store.get_hypothesis(hypothesis_id)
        event_metadata = dict(metadata or {})
        self._validate_transition_actor(
            current,
            status,
            actor=actor,
            metadata=event_metadata,
        )
        before = current.model_dump(mode="json")
        update_patch = dict(patch or {})
        update_patch["status"] = status
        updated = self.store.update_hypothesis(hypothesis_id, update_patch, actor=actor)
        event_type = STATUS_EVENT_TYPES.get(status, "updated")
        if status == "proposed" and current.status == "stale":
            event_type = "revived"
        self.store.add_lifecycle_event(
            HypothesisLifecycleEvent(
                hypothesis_id=hypothesis_id,
                event_type=event_type,  # type: ignore[arg-type]
                actor=actor,
                summary=summary
                or f"Hypothesis status changed from {current.status} to {status}.",
                before=before,
                after=updated.model_dump(mode="json"),
                metadata={
                    "previous_status": current.status,
                    "new_status": status,
                    **event_metadata,
                },
            )
        )
        return updated

    def mark_contradicted(
        self,
        hypothesis_id: str,
        *,
        actor: str,
        contradicting_relation_ids: Iterable[str] = (),
        source_artifact_ids: Iterable[str] = (),
        summary: str = "New graph-backed contradiction marked the hypothesis contradicted.",
    ) -> ResearchHypothesis:
        current = self.store.get_hypothesis(hypothesis_id)
        relation_ids = _append_unique(
            current.contradicting_relation_ids,
            contradicting_relation_ids,
        )
        artifact_ids = _append_unique(current.source_artifact_ids, source_artifact_ids)
        return self.transition_status(
            hypothesis_id,
            "contradicted",
            actor=actor,
            summary=summary,
            patch={
                "contradicting_relation_ids": relation_ids,
                "source_artifact_ids": artifact_ids,
                "contradiction_score": max(current.contradiction_score, 0.85),
            },
        )

    def mark_stale(
        self,
        hypothesis_id: str,
        *,
        actor: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> ResearchHypothesis:
        return self.transition_status(
            hypothesis_id,
            "stale",
            actor=actor,
            summary=reason,
            metadata=metadata,
        )

    def revive_with_evidence(
        self,
        hypothesis_id: str,
        *,
        actor: str,
        evidence_item_ids: Iterable[str] = (),
        supporting_relation_ids: Iterable[str] = (),
        source_artifact_ids: Iterable[str] = (),
        summary: str = "New graph-backed evidence revived the stale hypothesis.",
        status: ResearchHypothesisStatus = "proposed",
    ) -> ResearchHypothesis:
        current = self.store.get_hypothesis(hypothesis_id)
        return self.transition_status(
            hypothesis_id,
            status,
            actor=actor,
            summary=summary,
            patch={
                "evidence_item_ids": _append_unique(
                    current.evidence_item_ids,
                    evidence_item_ids,
                ),
                "supporting_relation_ids": _append_unique(
                    current.supporting_relation_ids,
                    supporting_relation_ids,
                ),
                "source_artifact_ids": _append_unique(
                    current.source_artifact_ids,
                    source_artifact_ids,
                ),
                "support_score": max(current.support_score, 0.55),
            },
        )

    def update_from_experimental_result(
        self,
        hypothesis_id: str,
        result: Any,
        *,
        actor: str,
    ) -> ResearchHypothesis:
        current = self.store.get_hypothesis(hypothesis_id)
        result_id = str(getattr(result, "result_id", ""))
        qc_status = str(getattr(result, "qc_status", "unknown"))
        outcome_label = str(getattr(result, "outcome_label", "inconclusive"))
        assay_result_ids = _append_unique(current.assay_result_ids, [result_id])
        patch: dict[str, Any] = {"assay_result_ids": assay_result_ids}
        metadata = {
            "assay_result_id": result_id,
            "outcome_label": outcome_label,
            "qc_status": qc_status,
            "assay_result_is_imported_evidence": True,
        }
        if qc_status != "passed":
            status: ResearchHypothesisStatus = "needs_more_evidence"
            summary = "Imported assay result did not pass QC; hypothesis needs more evidence."
        elif outcome_label == "negative":
            status = "contradicted"
            patch["contradiction_score"] = max(current.contradiction_score, 0.8)
            summary = (
                "Imported QC-passed negative assay result contradicted the "
                "hypothesis context."
            )
        elif outcome_label == "positive":
            status = "under_review"
            patch["support_score"] = max(current.support_score, 0.65)
            summary = "Imported QC-passed positive assay result moved the hypothesis to review."
        else:
            status = "needs_more_evidence"
            summary = "Imported assay result was inconclusive for hypothesis planning."
        return self.transition_status(
            hypothesis_id,
            status,
            actor=actor,
            summary=summary,
            metadata=metadata,
            patch=patch,
        )

    def link_stage_gate(
        self,
        hypothesis_id: str,
        stage_gate: Any,
        *,
        actor: str,
    ) -> Any:
        current = self.store.get_hypothesis(hypothesis_id)
        stage_gate_id = str(stage_gate.stage_gate_id)
        metadata = _metadata_with_link(current.metadata, "stage_gate_ids", stage_gate_id)
        self.store.update_hypothesis(hypothesis_id, {"metadata": metadata}, actor=actor)
        self.store.add_lifecycle_event(
            HypothesisLifecycleEvent(
                hypothesis_id=hypothesis_id,
                event_type="updated",
                actor=actor,
                summary=f"Linked portfolio stage gate {stage_gate_id}.",
                metadata={"stage_gate_id": stage_gate_id},
            )
        )
        return _model_copy_with_metadata(stage_gate, "hypothesis_ids", hypothesis_id)

    def link_active_learning_batch(
        self,
        hypothesis_id: str,
        batch: Any,
        *,
        actor: str,
    ) -> Any:
        current = self.store.get_hypothesis(hypothesis_id)
        batch_id = str(batch.batch_id)
        metadata = _metadata_with_link(
            current.metadata,
            "active_learning_batch_ids",
            batch_id,
        )
        self.store.update_hypothesis(hypothesis_id, {"metadata": metadata}, actor=actor)
        self.store.add_lifecycle_event(
            HypothesisLifecycleEvent(
                hypothesis_id=hypothesis_id,
                event_type="updated",
                actor=actor,
                summary=f"Linked active-learning batch {batch_id}.",
                metadata={"active_learning_batch_id": batch_id},
            )
        )
        return _model_copy_with_metadata(batch, "hypothesis_ids", hypothesis_id)

    def _validate_transition_actor(
        self,
        hypothesis: ResearchHypothesis,
        status: str,
        *,
        actor: str,
        metadata: dict[str, Any],
    ) -> None:
        if status != "accepted_for_planning":
            return
        if _is_codex_actor(actor):
            raise ValueError("Codex cannot approve hypotheses")
        if (
            self.require_generated_molecule_human_approval
            and hypothesis.hypothesis_type == "generated_molecule"
            and metadata.get("human_approval") is not True
        ):
            raise ValueError(
                "Generated-molecule hypotheses require explicit human approval"
            )


def _append_unique(existing: Iterable[str], new_values: Iterable[str]) -> list[str]:
    values = list(existing)
    seen = set(values)
    for value in new_values:
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _metadata_with_link(
    metadata: dict[str, Any],
    key: str,
    value: str,
) -> dict[str, Any]:
    updated = dict(metadata)
    current = updated.get(key, [])
    values = current if isinstance(current, list) else [current]
    updated[key] = _append_unique([str(item) for item in values if item], [value])
    updated["updated_at"] = datetime.now(UTC).isoformat()
    return updated


def _model_copy_with_metadata(model: Any, key: str, value: str) -> Any:
    metadata = _metadata_with_link(dict(getattr(model, "metadata", {})), key, value)
    return model.model_copy(update={"metadata": metadata})


def _is_codex_actor(actor: str) -> bool:
    normalized = actor.lower().replace("_", "-").replace(" ", "-")
    return "codex" in normalized
