from __future__ import annotations

from molecule_ranker.knowledge_graph.reasoning import analyze_cross_program_knowledge
from molecule_ranker.knowledge_graph.schemas import KnowledgeGraph


def render_knowledge_graph_report_markdown(graph: KnowledgeGraph) -> str:
    analysis = analyze_cross_program_knowledge(graph)
    lines = [
        f"# Knowledge Graph Report: {graph.graph_id}",
        "",
        "Research use only. Graph paths are reasoning aids, not biomedical proof.",
        "",
        "## Summary",
        "",
        f"- Entities: {len(graph.entities)}",
        f"- Relations: {len(graph.relations)}",
        f"- Mechanism hypotheses: {len(graph.mechanisms)}",
        "",
        "## Recurring Mechanisms",
        "",
    ]
    lines.extend(f"- {pattern.name}: {pattern.count}" for pattern in analysis.recurring_mechanisms)
    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- {item.rationale}" for item in analysis.recommendations)
    return "\n".join(lines) + "\n"


__all__ = ["render_knowledge_graph_report_markdown"]
