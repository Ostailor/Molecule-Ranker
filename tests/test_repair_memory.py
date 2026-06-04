from __future__ import annotations

from molecule_ranker.agent_repair.memory import (
    RepairMemory,
    compute_failure_signature,
    export_repair_memory,
    import_repair_memory,
    record_repair_outcome,
)


def test_same_failure_signature_retrieves_successful_repair_strategy() -> None:
    memory = RepairMemory()
    signature = _signature()

    record_repair_outcome(
        memory=memory,
        signature=signature,
        failure_category="invalid_schema",
        repair_plan_id="repair-plan-1",
        succeeded=True,
        recommended_repair_strategy="Retry with strict JSON schema and revalidate.",
    )

    recommendations = memory.get_repair_recommendations(signature)

    assert recommendations
    assert recommendations[0].successful_repair_plan_id == "repair-plan-1"
    assert "strict JSON schema" in recommendations[0].recommended_repair_strategy


def test_failed_repair_lowers_success_rate() -> None:
    memory = RepairMemory()
    signature = _signature(error_code="SCHEMA_MISSING_FIELD")

    memory.record_repair_outcome(
        signature=signature,
        failure_category="invalid_schema",
        repair_plan_id="repair-plan-1",
        succeeded=True,
        recommended_repair_strategy="Regenerate derived artifact and revalidate.",
    )
    updated = memory.record_repair_outcome(
        signature=signature,
        failure_category="invalid_schema",
        repair_plan_id="repair-plan-2",
        succeeded=False,
        recommended_repair_strategy="Regenerate derived artifact and revalidate.",
    )

    assert updated.occurrence_count == 2
    assert updated.repair_success_rate == 0.5
    assert updated.successful_repair_plan_id == "repair-plan-1"


def test_repair_memory_redacts_secrets_and_drops_scientific_payloads() -> None:
    memory = RepairMemory()
    signature = _signature()
    memory.record_repair_outcome(
        signature=signature,
        failure_category="invalid_schema",
        repair_plan_id="repair-plan-1",
        succeeded=True,
        recommended_repair_strategy="Revalidate existing artifact.",
        metadata={
            "api_key": "sk-live-secret",
            "notes": "authorization: Bearer abc123",
            "raw_assay_data": {"row": "should not persist"},
            "codex_transcript": "full transcript should not persist",
            "safe_context": {"tool_retry": "bounded"},
        },
    )

    exported = export_repair_memory(memory)
    exported_text = repr(exported)

    assert "sk-live-secret" not in exported_text
    assert "Bearer abc123" not in exported_text
    assert "should not persist" not in exported_text
    assert "full transcript should not persist" not in exported_text
    assert "[REDACTED]" in exported_text
    assert exported["records"][0]["metadata"]["safe_context"]["tool_retry"] == "bounded"


def test_exported_repair_memory_imports_into_new_store() -> None:
    memory = RepairMemory()
    signature = _signature(error_code="TIMEOUT")
    memory.record_repair_outcome(
        signature=signature,
        failure_category="timeout",
        repair_plan_id="repair-plan-timeout",
        succeeded=True,
        recommended_repair_strategy="Reduce limits and rerun bounded job.",
    )

    restored = RepairMemory()
    imported = import_repair_memory(export_repair_memory(memory), memory=restored)

    assert len(imported) == 1
    assert restored.get_repair_recommendations(signature)[0].repair_success_rate == 1.0


def _signature(error_code: str = "SCHEMA_INVALID") -> str:
    return compute_failure_signature(
        tool_name="plan_followup",
        failure_category="invalid_schema",
        error_code=error_code,
        artifact_type="runtime_report",
        schema_version="repair.v1",
        relevant_config_keys=["schema_mode", "retry_limit"],
        policy_context={"autonomy": "execute_safe_repairs"},
        guardrail_category="none",
    )
