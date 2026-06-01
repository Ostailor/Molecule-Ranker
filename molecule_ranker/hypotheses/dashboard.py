from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .schemas import HypothesisSet


def render_hypothesis_dashboard_html(hypotheses: HypothesisSet) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{_h(hypothesis.hypothesis_type)}</td>"
        f"<td>{_h(hypothesis.title)}</td>"
        f"<td>{_h(hypothesis.summary)}</td>"
        f"<td>{hypothesis.rank_score:.2f}</td>"
        f"<td>{_h(hypothesis.review_status)}</td>"
        f"<td>{_h(', '.join(hypothesis.relation_ids))}</td>"
        "</tr>"
        for hypothesis in hypotheses.hypotheses
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>molecule-ranker V1.6 hypotheses</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;color:#1f2937}"
        "section{margin:18px 0}.notice{border:1px solid #d6a800;background:#fff8db;"
        "padding:12px;border-radius:6px}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #d8dee8;padding:8px;text-align:left}</style></head><body>"
        "<h1>Hypothesis dashboard</h1>"
        '<p class="notice">A hypothesis is not evidence. A research question is not a lab '
        "protocol. A validation plan is not an experimental procedure. No medical advice, "
        "synthesis routes, lab instructions, dosing, or patient guidance.</p>"
        f"<section><h2>Graph</h2><p>{_h(hypotheses.graph_id)} · "
        f"{len(hypotheses.hypotheses)} hypotheses</p></section>"
        "<section><h2>Ranked hypotheses</h2><table><thead><tr><th>Type</th>"
        "<th>Title</th><th>Summary</th><th>Rank</th><th>Review</th><th>Relations</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></section>"
        "</body></html>\n"
    )


def write_hypothesis_dashboard(hypotheses: HypothesisSet, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.joinpath("index.html").write_text(render_hypothesis_dashboard_html(hypotheses))
    output_dir.joinpath("hypotheses.json").write_text(hypotheses.model_dump_json(indent=2) + "\n")
    return output_dir


def _h(value: Any) -> str:
    return escape(str(value), quote=True)
