from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from molecule_ranker.knowledge_graph.reasoning import analyze_cross_program_knowledge
from molecule_ranker.knowledge_graph.schemas import CrossProgramAnalysis, KnowledgeGraph


def render_knowledge_graph_dashboard_html(
    graph: KnowledgeGraph,
    analysis: CrossProgramAnalysis | None = None,
) -> str:
    analysis = analysis or analyze_cross_program_knowledge(graph)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>molecule-ranker V1.5 knowledge graph</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;color:#1f2937}"
        "section{margin:18px 0}.notice{border:1px solid #d6a800;background:#fff8db;"
        "padding:12px;border-radius:6px}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #d8dee8;padding:8px;text-align:left}</style></head><body>"
        "<h1>Cross-program knowledge graph</h1>"
        '<p class="notice">This graph is a memory and reasoning layer. It does not create '
        "biomedical truth, EvidenceItem records, assay results, causality, efficacy, safety, "
        "binding, or activity claims. No medical advice, synthesis instructions, lab protocols, "
        "dosing, or patient guidance.</p>"
        f"<section><h2>Graph</h2><p>{_h(graph.graph_id)} · {len(graph.entities)} entities · "
        f"{len(graph.relations)} relations</p></section>"
        f"{_patterns('Recurring mechanisms', analysis.recurring_mechanisms)}"
        f"{_target_patterns(analysis)}"
        f"{_patterns('Scaffolds and series', analysis.scaffold_patterns)}"
        f"{_findings('Contradictions and stale hypotheses', _review_findings(analysis))}"
        f"{_patterns('Repeated developability blockers', analysis.repeated_developability_risks)}"
        f"{_findings('Novelty checks', analysis.novelty_assessments)}"
        "</body></html>\n"
    )


def write_knowledge_graph_dashboard(
    graph: KnowledgeGraph,
    output_dir: Path,
    analysis: CrossProgramAnalysis | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.joinpath("index.html").write_text(
        render_knowledge_graph_dashboard_html(graph, analysis)
    )
    output_dir.joinpath("knowledge_graph.json").write_text(graph.model_dump_json(indent=2) + "\n")
    return output_dir


def _patterns(title: str, patterns: list[Any]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{_h(pattern.name)}</td>"
        f"<td>{pattern.count}</td>"
        f"<td>{_h(', '.join(pattern.related_entity_ids))}</td>"
        f"<td>{_h(pattern.rationale)}</td>"
        "</tr>"
        for pattern in patterns
    )
    return (
        f"<section><h2>{_h(title)}</h2><table><thead><tr><th>Name</th><th>Count</th>"
        f"<th>Related entities</th><th>Rationale</th></tr></thead><tbody>{rows}</tbody>"
        "</table></section>"
    )


def _target_patterns(analysis: CrossProgramAnalysis) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{_h(pattern.name)}</td>"
        f"<td>{pattern.strong_candidate_count}</td>"
        f"<td>{pattern.weak_candidate_count}</td>"
        f"<td>{pattern.contradiction_count}</td>"
        f"<td>{_h(pattern.rationale)}</td>"
        "</tr>"
        for pattern in analysis.target_patterns
    )
    return (
        "<section><h2>Target outcome patterns</h2><table><thead><tr><th>Target</th>"
        "<th>Strong</th><th>Weak</th><th>Contradictions</th><th>Rationale</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></section>"
    )


def _findings(title: str, findings: list[Any]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{_h(finding.name)}</td>"
        f"<td>{_h(finding.status)}</td>"
        f"<td>{_h(finding.reason)}</td>"
        "</tr>"
        for finding in findings
    )
    return (
        f"<section><h2>{_h(title)}</h2><table><thead><tr><th>Name</th><th>Status</th>"
        f"<th>Reason</th></tr></thead><tbody>{rows}</tbody></table></section>"
    )


def _review_findings(analysis: CrossProgramAnalysis) -> list[Any]:
    return [*analysis.contradictions, *analysis.hypothesis_status]


def _h(value: Any) -> str:
    return escape(str(value), quote=True)
