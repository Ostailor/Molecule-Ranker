from __future__ import annotations

from html import escape

from .schemas import PortfolioOptimizationRun


def render_portfolio_dashboard_html(run: PortfolioOptimizationRun) -> str:
    rows = []
    for selection in run.selections:
        rows.append(
            "<tr>"
            f"<td>{_h(selection.selection_id)}</td>"
            f"<td>{_h(', '.join(selection.selected_candidate_ids))}</td>"
            f"<td>{selection.portfolio_score:.3f}</td>"
            f"<td>{len(selection.constraint_violations)}</td>"
            "</tr>"
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>molecule-ranker V1.5 portfolio</title></head><body>"
        "<h1>Portfolio Optimization</h1>"
        "<p>Research prioritization aid only. No clinical, lab, synthesis, dosing, "
        "or patient-treatment instruction.</p>"
        "<table><thead><tr><th>Selection</th><th>Candidates</th><th>Score</th>"
        "<th>Violations</th></tr></thead><tbody>"
        f"{''.join(rows)}"
        "</tbody></table></body></html>"
    )


def _h(value: object) -> str:
    return escape(str(value), quote=True)
