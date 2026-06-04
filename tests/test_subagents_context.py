from __future__ import annotations

from molecule_ranker.subagents.context import (
    SubagentContextBuilder,
    SubagentContextPolicy,
    build_subagent_context,
)


def test_subagent_context_excludes_unauthorized_artifacts() -> None:
    context = build_subagent_context(
        subagent_id="evidence-reviewer",
        artifacts=[
            {
                "artifact_id": "evidence-1",
                "artifact_type": "literature",
                "summary": "PubMed provenance summary.",
            },
            {
                "artifact_id": "design-1",
                "artifact_type": "generation",
                "summary": "Generated molecule batch.",
            },
            {
                "artifact_id": "hidden-evidence",
                "artifact_type": "evidence",
                "summary": "Hidden evidence.",
            },
        ],
        policy=SubagentContextPolicy(
            visible_artifact_ids=["evidence-1", "design-1"],
        ),
    )

    assert [artifact["artifact_id"] for artifact in context.allowed_artifacts] == [
        "evidence-1"
    ]
    assert context.relevant_summaries == [
        {
            "artifact_id": "evidence-1",
            "artifact_type": "literature",
            "summary": "PubMed provenance summary.",
        }
    ]
    assert "hidden-evidence" not in str(context.model_dump())
    assert "design-1" not in str(context.model_dump())


def test_integration_credentials_are_redacted_from_context() -> None:
    context = build_subagent_context(
        subagent_id="integration-operator",
        artifacts=[
            {
                "artifact_id": "connector-1",
                "artifact_type": "connector_health",
                "summary": "Sync target healthy.",
                "metadata": {
                    "api_key": "sk-secret-value",
                    "headers": {"Authorization": "Bearer token-value"},
                    "connector": "benchling",
                },
            }
        ],
        policy=SubagentContextPolicy(visible_artifact_ids=["connector-1"]),
    )

    dumped = str(context.model_dump())

    assert "sk-secret-value" not in dumped
    assert "token-value" not in dumped
    assert "[REDACTED]" in dumped
    assert context.allowed_artifacts[0]["metadata"]["connector"] == "benchling"


def test_raw_assay_file_excluded_unless_permitted() -> None:
    artifacts = [
        {
            "artifact_id": "assay-summary-1",
            "artifact_type": "result_summary",
            "summary": "Assay summary with QC pass.",
        },
        {
            "artifact_id": "raw-assay-1",
            "artifact_type": "raw_assay",
            "summary": "Raw assay rows.",
            "content": "well,value\nA1,3.2",
            "raw": True,
        },
    ]

    default_context = build_subagent_context(
        subagent_id="experiment-analyst",
        artifacts=artifacts,
        policy=SubagentContextPolicy(
            visible_artifact_ids=["assay-summary-1", "raw-assay-1"],
        ),
    )
    permitted_context = build_subagent_context(
        subagent_id="experiment-analyst",
        artifacts=artifacts,
        policy=SubagentContextPolicy(
            visible_artifact_ids=["assay-summary-1", "raw-assay-1"],
            permit_raw_assay_files=True,
        ),
    )

    assert [artifact["artifact_id"] for artifact in default_context.allowed_artifacts] == [
        "assay-summary-1"
    ]
    assert [artifact["artifact_id"] for artifact in permitted_context.allowed_artifacts] == [
        "assay-summary-1",
        "raw-assay-1",
    ]
    assert permitted_context.allowed_artifacts[1]["content"] == "well,value\nA1,3.2"


def test_role_specific_tools_included() -> None:
    builder = SubagentContextBuilder()
    evidence_context = builder.build(
        subagent_id="evidence-reviewer",
        artifacts=[
            {
                "artifact_id": "evidence-1",
                "artifact_type": "evidence",
                "summary": "Evidence summary.",
            }
        ],
    )
    designer_context = builder.build(
        subagent_id="molecule-designer",
        artifacts=[
            {
                "artifact_id": "generation-1",
                "artifact_type": "generation",
                "summary": "Generation summary.",
            }
        ],
    )

    evidence_tools = {tool["tool_name"] for tool in evidence_context.allowed_tools}
    designer_tools = {tool["tool_name"] for tool in designer_context.allowed_tools}

    assert "summarize_literature" in evidence_tools
    assert "query_graph" in evidence_tools
    assert "run_generation" not in evidence_tools
    assert "run_generation" in designer_tools
    assert "run_design_loop" in designer_tools
    assert "summarize_literature" not in designer_tools
