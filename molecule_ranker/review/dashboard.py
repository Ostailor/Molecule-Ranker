from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from molecule_ranker.review.comparison import build_candidate_comparison
from molecule_ranker.review.dossier import DossierWriterAgent
from molecule_ranker.review.schemas import ReviewItem, ReviewWorkspace


def generate_static_review_dashboard(
    workspace: ReviewWorkspace,
    output_dir: str | Path,
) -> Path:
    """Write a no-server static review dashboard and return the output directory."""
    root = Path(output_dir)
    candidates_dir = root / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    root.joinpath("index.html").write_text(_page("Workspace summary", _index_body(workspace)))
    root.joinpath("queue.html").write_text(_page("Review queue", _queue_body(workspace)))
    root.joinpath("audit.html").write_text(_page("Audit log", _audit_body(workspace)))
    root.joinpath("compare.html").write_text(
        _page("Candidate comparison", _compare_body(workspace))
    )
    for item in workspace.review_items:
        dossier = DossierWriterAgent().build_dossier(workspace, item.review_item_id)
        candidates_dir.joinpath(f"{_safe_filename(item.review_item_id)}.html").write_text(
            _page(
                f"Candidate dossier: {item.candidate_name}",
                _candidate_body(workspace, item, dossier),
            )
        )
    return root


def render_static_review_dashboard(workspace: ReviewWorkspace) -> str:
    """Compatibility single-page dashboard used by legacy JSON workspace commands."""
    return _page("Molecule Review Workspace", _index_body(workspace) + _queue_body(workspace))


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        "<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)}</title>"
        "<style>"
        ":root{color-scheme:light}body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
        "margin:0;color:#17202a;background:#fbfcfd;line-height:1.45}"
        "header,main{max-width:1180px;margin:0 auto;padding:20px}"
        "header{border-bottom:1px solid #d7dde5;background:#fff}"
        "nav a{margin-right:14px;color:#075985}h1,h2,h3{line-height:1.2}"
        ".notice{background:#fff8db;border:1px solid #d6a800;padding:12px;border-radius:6px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}"
        ".metric,.panel{background:#fff;border:1px solid #d7dde5;border-radius:6px;padding:12px}"
        "table{width:100%;border-collapse:collapse;background:#fff;margin:12px 0}"
        "th,td{border:1px solid #d7dde5;padding:8px;text-align:left;vertical-align:top}"
        "th{background:#edf2f7}code,pre{white-space:pre-wrap;word-break:break-word}"
        ".tag{display:inline-block;border:1px solid #b8c2cc;border-radius:999px;"
        "padding:1px 8px;margin:1px}"
        ".risk{border-color:#b91c1c;color:#7f1d1d}.muted{color:#52616f}"
        "label{display:inline-block;margin:8px 8px 8px 0}select{padding:4px}"
        "</style>"
        "</head><body><header><h1>Review Dashboard</h1>"
        "<nav><a href=\"index.html\">Summary</a><a href=\"queue.html\">Queue</a>"
        "<a href=\"compare.html\">Compare</a><a href=\"audit.html\">Audit</a></nav>"
        "</header><main>"
        f"{body}"
        "</main></body></html>\n"
    )


def _index_body(workspace: ReviewWorkspace) -> str:
    priorities = _counts(item.priority_bucket for item in workspace.review_items)
    statuses = _counts(item.review_status for item in workspace.review_items)
    origins = _counts(item.candidate_origin for item in workspace.review_items)
    return (
        f"<h2>{_h(workspace.disease_name)} workspace summary</h2>"
        "<p class=\"notice\">Expert triage only. Human decisions are kept separate from "
        "model-generated scores. Computational scores do not establish safety, efficacy, "
        "binding, or synthesizability.</p>"
        "<section class=\"grid\">"
        f"{_metric('Workspace ID', workspace.workspace_id)}"
        f"{_metric('Run ID', workspace.run_id)}"
        f"{_metric('Created', workspace.created_at.isoformat())}"
        f"{_metric('Review items', str(len(workspace.review_items)))}"
        f"{_metric('Decisions', str(len(workspace.decisions)))}"
        f"{_metric('Comments', str(len(workspace.comments)))}"
        f"{_metric('Codex assistance', str(len(workspace.codex_review_artifacts)))}"
        "</section>"
        f"{_distribution_panel('Priority buckets', priorities)}"
        f"{_distribution_panel('Review statuses', statuses)}"
        f"{_distribution_panel('Existing vs generated', origins)}"
        "<h2>Disclaimers</h2><ul>"
        "<li>No medical advice, dosage, treatment, synthesis, or protocol instructions.</li>"
        "<li>Review decisions are expert triage labels, not clinical conclusions.</li>"
        "</ul>"
    )


def _queue_body(workspace: ReviewWorkspace) -> str:
    rows = []
    for item in workspace.review_items:
        href = f"candidates/{_safe_filename(item.review_item_id)}.html"
        rows.append(
            "<tr "
            f"data-priority=\"{_attr(item.priority_bucket)}\" "
            f"data-status=\"{_attr(item.review_status)}\" "
            f"data-origin=\"{_attr(item.candidate_origin)}\">"
            f"<td><a href=\"{_attr(href)}\">{_h(item.candidate_name)}</a></td>"
            f"<td>{_h(item.candidate_origin)}</td>"
            f"<td>{_h(item.priority_bucket)}</td>"
            f"<td>{_h(item.review_status)}</td>"
            f"<td>{_h(str(item.score))}</td>"
            f"<td>{_h(str(item.confidence))}</td>"
            f"<td>{_tags(item.target_symbols)}</td>"
            f"<td>{_tags(item.risk_flags, risk=True)}</td>"
            "</tr>"
        )
    return (
        "<h2>Review queue</h2>"
        "<p class=\"muted\">Static filtering runs locally in this page; "
        "no remote scripts are loaded.</p>"
        "<label>Priority <select id=\"priority-filter\"><option value=\"\">All</option>"
        f"{_options(sorted({item.priority_bucket for item in workspace.review_items}))}"
        "</select></label>"
        "<label>Status <select id=\"status-filter\"><option value=\"\">All</option>"
        f"{_options(sorted({item.review_status for item in workspace.review_items}))}"
        "</select></label>"
        "<label>Origin <select id=\"origin-filter\"><option value=\"\">All</option>"
        f"{_options(sorted({item.candidate_origin for item in workspace.review_items}))}"
        "</select></label>"
        "<table id=\"queue\"><thead><tr><th>Candidate</th><th>Origin</th><th>Priority</th>"
        "<th>Status</th><th>Score</th><th>Confidence</th><th>Targets</th><th>Risk flags</th>"
        "</tr></thead><tbody>"
        f"{''.join(rows)}"
        "</tbody></table>"
        "<script>"
        "const ids=['priority','status','origin'];"
        "function applyFilters(){const vals=Object.fromEntries(ids.map(id=>[id,"
        "document.getElementById(id+'-filter').value]));"
        "document.querySelectorAll('#queue tbody tr').forEach(row=>{"
        "row.hidden=ids.some(id=>vals[id]&&row.dataset[id]!==vals[id]);});}"
        "ids.forEach(id=>document.getElementById(id+'-filter').addEventListener('change',applyFilters));"
        "</script>"
    )


def _candidate_body(workspace: ReviewWorkspace, item: ReviewItem, dossier: Any) -> str:
    decisions = [
        decision
        for decision in workspace.decisions
        if decision.review_item_id == item.review_item_id
    ]
    comments = [
        comment for comment in workspace.comments if comment.review_item_id == item.review_item_id
    ]
    codex_artifacts = [
        artifact
        for artifact in workspace.codex_review_artifacts
        if item.review_item_id in artifact.review_item_ids
    ]
    citations = _citation_links(item.literature_summary)
    return (
        f"<h2>{_h(item.candidate_name)}</h2>"
        "<p class=\"notice\">Candidate dossier for expert review. Generated molecules, if shown, "
        "are hypotheses and are not claimed to have direct experimental evidence.</p>"
        "<section class=\"grid\">"
        f"{_metric('Origin', item.candidate_origin)}"
        f"{_metric('Priority', item.priority_bucket)}"
        f"{_metric('Status', item.review_status)}"
        f"{_metric('Score', str(item.score))}"
        f"{_metric('Confidence', str(item.confidence))}"
        "</section>"
        "<h3>Evidence summary</h3>"
        f"{_json_block(item.evidence_summary)}"
        "<h3>Linked experimental results</h3>"
        f"{_json_block(item.evidence_summary.get('experimental_results', {}))}"
        "<h3>Literature citations</h3>"
        f"{'<ul>' + ''.join(citations) + '</ul>' if citations else '<p>None recorded.</p>'}"
        "<h3>Developability summary</h3>"
        f"{_json_block(item.developability_summary)}"
        "<h3>Warnings and risk flags</h3>"
        f"<p>{_tags(item.risk_flags, risk=True)} {_tags(item.warnings)}</p>"
        "<h3>Reviewer decisions</h3>"
        f"{_decision_list(decisions)}"
        "<h3>Reviewer comments</h3>"
        f"{_comment_list(comments)}"
        "<h3>Codex review assistance</h3>"
        f"{_codex_artifact_list(codex_artifacts)}"
        "<h3>Dossier sections</h3>"
        f"{_json_block(dossier.metadata.get('sections', []))}"
    )


def _compare_body(workspace: ReviewWorkspace) -> str:
    if len(workspace.review_items) < 2:
        return "<h2>Comparison</h2><p>At least two candidates are required for comparison.</p>"
    comparison = build_candidate_comparison(
        workspace,
        [item.review_item_id for item in workspace.review_items],
    )
    rows = []
    for row in comparison.comparison_table:
        rows.append(
            "<tr>"
            f"<td>{_h(row['candidate_name'])}</td>"
            f"<td>{_h(row['candidate_origin'])}</td>"
            f"<td>{_h(str(row['score']))}</td>"
            f"<td>{_h(str(row['confidence']))}</td>"
            f"<td>{_h(str(row['direct_evidence_count']))}</td>"
            f"<td>{_h(str(row['literature_support']))}</td>"
            f"<td>{_h(str(row['safety_warning_count']))}</td>"
            f"<td>{_h(str(row['developability_risk']))}</td>"
            f"<td>{_tags(row['reviewer_decisions'])}</td>"
            "</tr>"
        )
    return (
        "<h2>Candidate comparison</h2>"
        f"<p class=\"notice\">{_h(comparison.recommendation_summary)}</p>"
        f"<p>Shared targets: {_tags(comparison.shared_targets)}</p>"
        "<table><thead><tr><th>Candidate</th><th>Origin</th><th>Score</th>"
        "<th>Confidence</th><th>Direct evidence</th><th>Literature support</th>"
        "<th>Safety warnings</th><th>Developability risk</th><th>Reviewer decisions</th>"
        "</tr></thead><tbody>"
        f"{''.join(rows)}"
        "</tbody></table><h3>Differentiators</h3><ul>"
        f"{''.join(f'<li>{_h(item)}</li>' for item in comparison.differentiators)}"
        "</ul>"
    )


def _audit_body(workspace: ReviewWorkspace) -> str:
    rows = []
    for event in workspace.audit_events:
        rows.append(
            "<tr>"
            f"<td>{_h(event.timestamp.isoformat())}</td>"
            f"<td>{_h(event.actor)}</td>"
            f"<td>{_h(event.event_type)}</td>"
            f"<td>{_h(event.object_type)}:{_h(event.object_id)}</td>"
            f"<td>{_h(event.summary)}</td>"
            "</tr>"
        )
    return (
        "<h2>Audit log</h2>"
        "<table><thead><tr><th>Timestamp</th><th>Actor</th><th>Event</th>"
        "<th>Object</th><th>Summary</th></tr></thead><tbody>"
        f"{''.join(rows)}"
        "</tbody></table>"
    )


def _metric(label: str, value: str) -> str:
    return f"<div class=\"metric\"><strong>{_h(label)}</strong><br>{_h(value)}</div>"


def _distribution_panel(title: str, distribution: dict[str, int]) -> str:
    rows = "".join(
        f"<tr><td>{_h(key)}</td><td>{count}</td></tr>" for key, count in distribution.items()
    )
    return f"<section class=\"panel\"><h2>{_h(title)}</h2><table>{rows}</table></section>"


def _decision_list(decisions: list[Any]) -> str:
    if not decisions:
        return "<p>None recorded.</p>"
    return "<ul>" + "".join(
        f"<li>{_h(decision.decision)} by {_h(decision.reviewer.reviewer_id)}: "
        f"{_h(decision.rationale)}</li>"
        for decision in decisions
    ) + "</ul>"


def _comment_list(comments: list[Any]) -> str:
    if not comments:
        return "<p>None recorded.</p>"
    return "<ul>" + "".join(
        f"<li>{_h(comment.comment_type)} by {_h(comment.reviewer.reviewer_id)}: "
        f"{_h(comment.comment_text)}</li>"
        for comment in comments
    ) + "</ul>"


def _codex_artifact_list(artifacts: list[Any]) -> str:
    if not artifacts:
        return "<p>None recorded.</p>"
    return (
        "<p class=\"notice\">Codex assistance is separate from reviewer decisions, "
        "evidence, assay results, generated molecules, and scores.</p><ul>"
        + "".join(
            f"<li>{_h(artifact.task_type)} ({_h(artifact.status)}): "
            f"{_h(', '.join(artifact.artifact_refs) or artifact.artifact_id)}</li>"
            for artifact in artifacts
        )
        + "</ul>"
    )


def _citation_links(summary: dict[str, Any]) -> list[str]:
    raw_items = summary.get("items") or summary.get("citations") or []
    if not isinstance(raw_items, list):
        return []
    links: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or raw.get("source_record_id") or "Citation")
        url = _citation_url(raw)
        if url:
            links.append(f"<li><a href=\"{_attr(url)}\">{_h(title)}</a></li>")
        else:
            links.append(f"<li>{_h(title)}</li>")
    return links


def _citation_url(raw: dict[str, Any]) -> str | None:
    if raw.get("url"):
        return str(raw["url"])
    if raw.get("doi"):
        return f"https://doi.org/{raw['doi']}"
    if raw.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{raw['pmid']}/"
    return None


def _json_block(value: Any) -> str:
    return f"<pre>{_h(json.dumps(_strip_long_text(value), indent=2, sort_keys=True))}</pre>"


def _strip_long_text(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_long_text(item)
            for key, item in value.items()
            if str(key).lower() not in {"abstract", "full_text", "article_text", "body", "text"}
        }
    if isinstance(value, list):
        return [_strip_long_text(item) for item in value]
    return value


def _tags(values: Any, *, risk: bool = False) -> str:
    if not values:
        return ""
    if isinstance(values, str):
        values = [values]
    return " ".join(
        f"<span class=\"tag{' risk' if risk else ''}\">{_h(str(value))}</span>"
        for value in values
    )


def _options(values: list[str]) -> str:
    return "".join(f"<option value=\"{_attr(value)}\">{_h(value)}</option>" for value in values)


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


def _h(value: Any) -> str:
    return escape(str(value), quote=False)


def _attr(value: Any) -> str:
    return escape(str(value), quote=True)
