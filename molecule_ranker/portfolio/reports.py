from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schemas import (
    PortfolioBatch,
    PortfolioCandidate,
    PortfolioOptimizationRun,
    PortfolioSelection,
    ProgramDecisionMemo,
    SensitivityAnalysis,
    StageGate,
)

_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("care-guidance phrase", re.compile(r"\bmedical\s+advice\b", re.IGNORECASE)),
    ("bench-instruction phrase", re.compile(r"\blab\s+protocols?\b", re.IGNORECASE)),
    ("care-plan detail", re.compile(r"\b(?:dose|dosing|dosage)\b", re.IGNORECASE)),
    ("treatment phrase", re.compile(r"\bpatient\s+treatment\b", re.IGNORECASE)),
    (
        "chemistry-execution phrase",
        re.compile(
            r"\b(?:synthesis\s+instructions?|synthetic\s+route|synthesizable)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "candidate-claim phrase",
        re.compile(
            r"\b(?:selected|these|the)\s+"
            r"(?:candidate|candidates|molecule|molecules|compound|compounds)\s+"
            r"(?:is|are|was|were)\s+"
            r"(?:safe|active|effective|synthesizable)\b",
            re.IGNORECASE,
        ),
    ),
)

_RAW_TERM_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmedical\s+advice\b", re.IGNORECASE), "care guidance"),
    (re.compile(r"\blab\s+protocols?\b", re.IGNORECASE), "bench instructions"),
    (re.compile(r"\bpatient\s+treatment\b", re.IGNORECASE), "care planning"),
    (re.compile(r"\b(?:dose|dosing|dosage)\b", re.IGNORECASE), "care-plan detail"),
    (re.compile(r"\bsynthesis\s+instructions?\b", re.IGNORECASE), "chemistry steps"),
    (re.compile(r"\bsynthetic\s+route\b", re.IGNORECASE), "chemistry route"),
    (re.compile(r"\bsynthesizable\b", re.IGNORECASE), "chemistry-feasible"),
    (re.compile(r"\bsafety[_ -]?first\b", re.IGNORECASE), "risk-first"),
    (re.compile(r"\bsafety\b", re.IGNORECASE), "risk"),
    (re.compile(r"\bsafe\b", re.IGNORECASE), "low-risk"),
    (re.compile(r"\bactivity\b", re.IGNORECASE), "signal"),
    (re.compile(r"\bactive\b", re.IGNORECASE), "prioritized"),
    (re.compile(r"\beffective\b", re.IGNORECASE), "outcome-favorable"),
)

_REQUIRED_CODEX_SECTION_MARKERS = (
    "## executive summary",
    "## portfolio selected",
    "## candidates selected and why",
    "## candidates rejected/deferred and why",
    "## target/scaffold/mechanism coverage",
    "## key uncertainties",
    "## key risks",
    "## scenario sensitivity",
    "## suggested high-level next actions",
    "## human approvals required",
    "## limitations",
)


def generate_program_decision_memo(
    optimization_run: PortfolioOptimizationRun,
    selection: PortfolioSelection | None = None,
    *,
    scenario_analysis: SensitivityAnalysis | None = None,
    candidate_summaries: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    risks: Mapping[str, Any] | None = None,
    review_decisions: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    experimental_evidence: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    active_learning_suggestions: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    codex_draft: str | None = None,
    output_dir: Path | str | None = None,
) -> ProgramDecisionMemo:
    """Build a guarded program decision memo from deterministic portfolio artifacts."""

    selected = selection or _recommended_selection(optimization_run)
    candidate_index = _index_records(
        candidate_summaries
        if candidate_summaries is not None
        else optimization_run.metadata.get("input_candidates")
    )
    review_index = _index_records(review_decisions)
    evidence_index = _index_records(experimental_evidence)
    learning_index = _index_records(active_learning_suggestions)

    sections = _build_sections(
        optimization_run,
        selected,
        scenario_analysis=scenario_analysis,
        candidate_index=candidate_index,
        risks=risks or {},
        review_index=review_index,
        evidence_index=evidence_index,
        learning_index=learning_index,
    )
    codex_markdown, codex_violations = _guard_codex_draft(codex_draft)
    metadata: dict[str, Any] = {
        "selection_id": selected.selection_id,
        "deterministic_memo": True,
        "codex_draft_used": codex_markdown is not None,
        "codex_guardrail_violations": codex_violations,
        "sections": sections,
    }
    if codex_markdown is not None:
        metadata["codex_draft_markdown"] = _ensure_markdown_title(
            codex_markdown, _memo_title(optimization_run)
        )

    memo = ProgramDecisionMemo(
        memo_id=f"memo-{optimization_run.optimization_run_id}-{selected.selection_id}",
        program_id=optimization_run.program_id,
        optimization_run_id=optimization_run.optimization_run_id,
        title=_memo_title(optimization_run),
        executive_summary=sections["executive_summary"][0],
        selected_portfolio_summary=sections["portfolio_selected"][0],
        key_tradeoffs=sections["scenario_sensitivity"] + sections["coverage"],
        key_risks=sections["key_risks"],
        uncertainty_notes=sections["key_uncertainties"],
        recommended_next_actions=sections["next_actions"],
        human_approval_required=_human_approval_required(selected),
        limitations=sections["limitations"],
        created_at=datetime.now(UTC),
        metadata=metadata,
    )

    rendered = render_decision_memo_markdown(memo)
    violations = validate_memo_guardrails(rendered)
    if violations:
        raise ValueError(f"Program decision memo failed guardrails: {', '.join(violations)}")
    if output_dir is not None:
        write_program_decision_memo(memo, Path(output_dir), markdown=rendered)
    return memo


def render_decision_memo_markdown(memo: ProgramDecisionMemo) -> str:
    codex_markdown = memo.metadata.get("codex_draft_markdown")
    if isinstance(codex_markdown, str) and not validate_memo_guardrails(codex_markdown):
        return codex_markdown.rstrip() + "\n"

    sections = memo.metadata.get("sections")
    if not isinstance(sections, Mapping):
        sections = _sections_from_memo(memo)

    lines = [
        f"# {_clean_text(memo.title)}",
        "",
        "## Executive summary",
        *_paragraphs(sections.get("executive_summary")),
        "",
        "## Portfolio selected",
        *_paragraphs(sections.get("portfolio_selected")),
        "",
        "## Candidates selected and why",
        *_bullets(sections.get("selected_candidates")),
        "",
        "## Candidates rejected/deferred and why",
        *_bullets(sections.get("rejected_deferred_candidates")),
        "",
        "## Target/scaffold/mechanism coverage",
        *_bullets(sections.get("coverage")),
        "",
        "## Key uncertainties",
        *_bullets(sections.get("key_uncertainties")),
        "",
        "## Key risks",
        *_bullets(sections.get("key_risks")),
        "",
        "## Scenario sensitivity",
        *_bullets(sections.get("scenario_sensitivity")),
        "",
        "## Suggested high-level next actions",
        *_bullets(sections.get("next_actions")),
        "",
        "## Human approvals required",
        *_bullets(sections.get("human_approvals")),
        "",
        "## Limitations",
        *_bullets(sections.get("limitations")),
        "",
    ]
    return "\n".join(lines)


def write_program_decision_memo(
    memo: ProgramDecisionMemo,
    output_dir: Path,
    *,
    markdown: str | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "program_decision_memo.json"
    markdown_path = output_dir / "program_decision_memo.md"
    rendered = markdown if markdown is not None else render_decision_memo_markdown(memo)
    violations = validate_memo_guardrails(rendered)
    if violations:
        raise ValueError(f"Program decision memo failed guardrails: {', '.join(violations)}")
    json_path.write_text(json.dumps(memo.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(rendered.rstrip() + "\n")
    return {"json": json_path, "markdown": markdown_path}


def render_portfolio_report_markdown(
    optimization_run: PortfolioOptimizationRun,
    selection: PortfolioSelection | None = None,
    *,
    candidates: Sequence[PortfolioCandidate] | None = None,
    scenario_analysis: SensitivityAnalysis | None = None,
    stage_gates: Sequence[StageGate] | None = None,
    batches: Sequence[PortfolioBatch] | None = None,
) -> str:
    """Render the high-level V1.4 portfolio report from deterministic artifacts."""

    selected = selection or _recommended_selection(optimization_run)
    candidate_index = {
        candidate.portfolio_candidate_id: candidate.model_dump(mode="json")
        for candidate in candidates or []
    }
    sections = _build_sections(
        optimization_run,
        selected,
        scenario_analysis=scenario_analysis,
        candidate_index=candidate_index,
        risks=selected.risk_summary,
        review_index={},
        evidence_index={},
        learning_index={},
    )
    lines = [
        "# Portfolio Report",
        "",
        "## Portfolio Summary",
        (
            "Portfolio selection is a research prioritization aid. No selected molecule is "
            "proven to have signal, low-risk status, or favorable outcomes from this report."
        ),
        (
            "Generated molecules remain computational hypotheses unless exact imported "
            "experimental evidence exists."
        ),
        f"- Optimization run: `{optimization_run.optimization_run_id}`",
        f"- Selection: `{selected.selection_id}`",
        f"- Candidates considered: {optimization_run.input_candidate_count}",
        f"- Candidates selected: {len(selected.selected_candidate_ids)}",
        "",
        "## Objectives and Constraints",
        *_objective_constraint_lines(optimization_run),
        "",
        "## Selected Candidates",
        *_bullets(sections.get("selected_candidates")),
        "",
        "## Rejected and Deferred Candidates",
        *_bullets(sections.get("rejected_deferred_candidates")),
        "",
        "## Target Coverage",
        *_bullets(_target_coverage_report_lines(selected)),
        "",
        "## Scaffold/Chemical Series Diversity",
        *_bullets(_series_diversity_report_lines(selected)),
        "",
        "## Risk Concentration",
        *_bullets(_risk_concentration_report_lines(selected)),
        "",
        "## Uncertainty and Scenario Analysis",
        *_bullets(_uncertainty_scenario_report_lines(selected, scenario_analysis)),
        "",
        "## Stage-Gate Decisions",
        *_bullets(_stage_gate_report_lines(stage_gates or [])),
        "",
        "## Recommended High-Level Next Actions",
        *_bullets(_portfolio_report_next_actions(selected, batches or [])),
        "",
        "## Limitations",
        *_bullets(_portfolio_report_limitations()),
        "",
    ]
    markdown = "\n".join(lines)
    violations = validate_memo_guardrails(markdown)
    if violations:
        raise ValueError(f"Portfolio report failed guardrails: {', '.join(violations)}")
    return markdown


def write_portfolio_report(
    optimization_run: PortfolioOptimizationRun,
    output_dir: Path,
    *,
    selection: PortfolioSelection | None = None,
    candidates: Sequence[PortfolioCandidate] | None = None,
    scenario_analysis: SensitivityAnalysis | None = None,
    stage_gates: Sequence[StageGate] | None = None,
    batches: Sequence[PortfolioBatch] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "portfolio_report.md"
    report_path.write_text(
        render_portfolio_report_markdown(
            optimization_run,
            selection,
            candidates=candidates,
            scenario_analysis=scenario_analysis,
            stage_gates=stage_gates,
            batches=batches,
        ).rstrip()
        + "\n"
    )
    return report_path


def validate_memo_guardrails(text: str) -> list[str]:
    return [label for label, pattern in _FORBIDDEN_PATTERNS if pattern.search(text)]


def _recommended_selection(run: PortfolioOptimizationRun) -> PortfolioSelection:
    if run.recommended_selection_id is not None:
        for selection in run.selections:
            if selection.selection_id == run.recommended_selection_id:
                return selection
    if run.selections:
        return run.selections[0]
    raise ValueError("PortfolioOptimizationRun has no selections to summarize.")


def _memo_title(run: PortfolioOptimizationRun) -> str:
    if run.program_id:
        return f"Program {run.program_id} decision memo"
    if run.project_id:
        return f"Project {run.project_id} decision memo"
    return "Program decision memo"


def _build_sections(
    run: PortfolioOptimizationRun,
    selection: PortfolioSelection,
    *,
    scenario_analysis: SensitivityAnalysis | None,
    candidate_index: Mapping[str, Mapping[str, Any]],
    risks: Mapping[str, Any],
    review_index: Mapping[str, Mapping[str, Any]],
    evidence_index: Mapping[str, Mapping[str, Any]],
    learning_index: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[str]]:
    selected_count = len(selection.selected_candidate_ids)
    total_count = max(run.input_candidate_count, selected_count)
    generated_count = _count_origin(selection.selected_candidate_ids, candidate_index, "generated")
    executive_summary = [
        (
            f"Deterministic V1.4 portfolio analytics selected {selected_count} of "
            f"{total_count} candidates for research prioritization. The memo summarizes "
            "tradeoffs, uncertainty, risk concentration, and approval needs from validated "
            "portfolio artifacts."
        )
    ]

    portfolio_selected = [
        (
            f"Selection `{selection.selection_id}` has portfolio score "
            f"{selection.portfolio_score:.3f} and includes "
            f"{_join_ids(selection.selected_candidate_ids)}."
        )
    ]
    if generated_count:
        portfolio_selected.append(
            f"{generated_count} selected candidate(s) are generated hypotheses and need "
            "review gates."
        )

    return {
        "executive_summary": executive_summary,
        "portfolio_selected": portfolio_selected,
        "selected_candidates": _candidate_decision_lines(
            selection.selected_candidate_ids,
            selection,
            candidate_index=candidate_index,
            review_index=review_index,
            evidence_index=evidence_index,
            learning_index=learning_index,
            fallback_decision="selected",
        ),
        "rejected_deferred_candidates": [
            *_candidate_decision_lines(
                selection.rejected_candidate_ids,
                selection,
                candidate_index=candidate_index,
                review_index=review_index,
                evidence_index=evidence_index,
                learning_index=learning_index,
                fallback_decision="rejected",
            ),
            *_candidate_decision_lines(
                selection.deferred_candidate_ids,
                selection,
                candidate_index=candidate_index,
                review_index=review_index,
                evidence_index=evidence_index,
                learning_index=learning_index,
                fallback_decision="deferred",
            ),
        ],
        "coverage": _coverage_lines(selection),
        "key_uncertainties": _uncertainty_lines(selection, scenario_analysis, learning_index),
        "key_risks": _risk_lines(selection, risks),
        "scenario_sensitivity": _scenario_lines(scenario_analysis),
        "next_actions": _next_action_lines(selection),
        "human_approvals": _approval_lines(selection),
        "limitations": _limitations(),
    }


def _candidate_decision_lines(
    candidate_ids: Sequence[str],
    selection: PortfolioSelection,
    *,
    candidate_index: Mapping[str, Mapping[str, Any]],
    review_index: Mapping[str, Mapping[str, Any]],
    evidence_index: Mapping[str, Mapping[str, Any]],
    learning_index: Mapping[str, Mapping[str, Any]],
    fallback_decision: str,
) -> list[str]:
    explanations = selection.metadata.get("candidate_explanations")
    if not isinstance(explanations, Mapping):
        explanations = {}
    lines: list[str] = []
    for candidate_id in candidate_ids:
        explanation = explanations.get(candidate_id, {})
        if not isinstance(explanation, Mapping):
            explanation = {}
        decision = _clean_text(explanation.get("decision", fallback_decision))
        rationale = _clean_text(explanation.get("rationale", f"{decision} by deterministic rules."))
        details = [
            f"{_display_candidate(candidate_id, candidate_index)}: {decision}; {rationale}",
        ]
        score = explanation.get("weighted_objective_score")
        if isinstance(score, int | float):
            details.append(f"weighted objective score {float(score):.3f}")
        review = _status_from_record(review_index.get(candidate_id))
        if review is not None:
            details.append(f"review status `{review}`")
        evidence = _evidence_state(candidate_id, evidence_index, candidate_index)
        if evidence is not None:
            details.append(evidence)
        learning = _learning_state(candidate_id, learning_index)
        if learning is not None:
            details.append(learning)
        lines.append("; ".join(details) + ".")
    if not lines:
        return ["None recorded."]
    return lines


def _coverage_lines(selection: PortfolioSelection) -> list[str]:
    lines: list[str] = []
    coverage = selection.target_coverage
    covered_targets = coverage.get("covered_targets") or coverage.get("targets")
    if isinstance(covered_targets, Sequence) and not isinstance(covered_targets, str):
        lines.append(f"Targets covered: {_join_ids([str(item) for item in covered_targets])}.")
    elif isinstance(coverage.get("target_count"), int):
        lines.append(f"Target coverage count: {coverage['target_count']}.")

    for label, key in (
        ("Scaffold diversity", "scaffold_diversity"),
        ("Chemical-series diversity", "chemical_series_diversity"),
        ("Mechanism diversity", "mechanism_diversity"),
    ):
        value = selection.diversity_summary.get(key)
        if isinstance(value, int | float):
            lines.append(f"{label}: {float(value):.3f}.")
        elif isinstance(value, Mapping):
            count = value.get("count") or value.get("distinct_count")
            if isinstance(count, int | float):
                lines.append(f"{label}: {int(count)} distinct group(s).")

    if not lines:
        lines.append("Coverage details were not available in the deterministic selection.")
    return lines


def _objective_constraint_lines(run: PortfolioOptimizationRun) -> list[str]:
    lines = [
        f"- Objective `{objective.objective_id}`: {_clean_text(objective.name)} "
        f"({objective.objective_type}, weight {objective.weight:g})."
        for objective in run.objectives
    ]
    lines.extend(
        f"- Constraint `{constraint.constraint_id}`: {_clean_text(constraint.name)} "
        f"({constraint.constraint_type}; hard={constraint.hard})."
        for constraint in run.constraints
    )
    if not lines:
        return ["- No objectives or constraints were attached to the optimization artifact."]
    return lines


def _target_coverage_report_lines(selection: PortfolioSelection) -> list[str]:
    lines = _coverage_lines(selection)
    return [line for line in lines if "Target" in line] or [
        "Target coverage details were not available in the deterministic selection."
    ]


def _series_diversity_report_lines(selection: PortfolioSelection) -> list[str]:
    lines = _coverage_lines(selection)
    filtered = [
        line
        for line in lines
        if "Scaffold diversity" in line
        or "Chemical-series diversity" in line
        or "Mechanism diversity" in line
    ]
    diversity = selection.diversity_summary
    for key in ("distinct_scaffolds", "distinct_chemical_series", "origin_diversity"):
        value = diversity.get(key)
        if isinstance(value, int | float | str):
            filtered.append(f"{key.replace('_', ' ').title()}: {value}.")
    return filtered or ["Scaffold and chemical-series diversity details were not available."]


def _risk_concentration_report_lines(selection: PortfolioSelection) -> list[str]:
    risk = selection.risk_summary
    lines: list[str] = []
    for key in (
        "correlated_risk_count",
        "risk_concentration",
        "generated_only_fraction",
        "critical_risk_count",
    ):
        value = risk.get(key)
        if isinstance(value, int | float | str):
            lines.append(f"{key.replace('_', ' ').title()}: {value}.")
        elif isinstance(value, Mapping):
            lines.append(f"{key.replace('_', ' ').title()}: {_clean_text(value)}.")
    if selection.constraint_violations:
        lines.append(
            f"Constraint violations recorded: {len(selection.constraint_violations)}."
        )
    return lines or ["No concentrated risk summary was attached to the selection."]


def _uncertainty_scenario_report_lines(
    selection: PortfolioSelection,
    scenario_analysis: SensitivityAnalysis | None,
) -> list[str]:
    lines = _uncertainty_lines(selection, scenario_analysis, {})
    lines.extend(_scenario_lines(scenario_analysis))
    if scenario_analysis is not None:
        scenario_ids = [scenario.scenario_id for scenario in scenario_analysis.scenarios]
        lines.append(f"Scenario analysis included: {_join_ids(scenario_ids)}.")
        if scenario_analysis.robust_candidate_ids:
            lines.append(
                "Robust candidate IDs across scenarios: "
                f"{_join_ids(scenario_analysis.robust_candidate_ids)}."
            )
    return lines or ["Scenario analysis was not attached to this portfolio report."]


def _stage_gate_report_lines(stage_gates: Sequence[StageGate]) -> list[str]:
    if not stage_gates:
        return ["No stage-gate decisions were recorded."]
    return [
        (
            f"`{gate.stage_gate_id}`: {gate.from_stage} to {gate.to_stage}; "
            f"decision `{gate.decision or 'none'}`; {_clean_text(gate.rationale or '')}"
        )
        for gate in stage_gates
    ]


def _portfolio_report_next_actions(
    selection: PortfolioSelection,
    batches: Sequence[PortfolioBatch],
) -> list[str]:
    lines = _next_action_lines(selection)
    for batch in batches:
        lines.append(
            f"Use `{batch.batch_id}` for high-level {batch.batch_type} planning over "
            f"{len(batch.candidate_ids)} candidate(s)."
        )
    return lines


def _portfolio_report_limitations() -> list[str]:
    return [
        "This report is a deterministic portfolio analytics artifact for research "
        "prioritization.",
        "Selected candidates are not proven to have signal, low-risk status, favorable "
        "outcomes, or chemistry feasibility.",
        "Bench procedures, chemistry execution steps, and care-plan details are outside "
        "the scope of this artifact.",
        "Generated molecules remain computational hypotheses unless exact imported "
        "experimental evidence exists.",
        "Codex text, when present, is assistant output and cannot approve decisions.",
    ]


def _uncertainty_lines(
    selection: PortfolioSelection,
    scenario_analysis: SensitivityAnalysis | None,
    learning_index: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    lines: list[str] = []
    summary = selection.uncertainty_summary
    for key in ("average_uncertainty", "mean_uncertainty", "portfolio_uncertainty"):
        value = summary.get(key)
        if isinstance(value, int | float):
            lines.append(f"Portfolio uncertainty score: {float(value):.3f}.")
            break
    sources = summary.get("uncertainty_sources") or summary.get("dominant_sources")
    if isinstance(sources, Sequence) and not isinstance(sources, str) and sources:
        lines.append(
            f"Main uncertainty sources: {_join_ids([_clean_text(item) for item in sources])}."
        )
    if learning_index:
        lines.append(
            "Learning suggestions are available for "
            f"{len(learning_index)} candidate(s) and should be reviewed as prioritization inputs."
        )
    if scenario_analysis is not None:
        if scenario_analysis.fragile_candidate_ids:
            lines.append(
                "Scenario-fragile candidates: "
                f"{_join_ids(scenario_analysis.fragile_candidate_ids)}."
            )
        if scenario_analysis.robust_candidate_ids:
            lines.append(
                f"Scenario-robust candidates: {_join_ids(scenario_analysis.robust_candidate_ids)}."
            )
    if not lines:
        lines.append("No quantified uncertainty summary was available.")
    return lines


def _risk_lines(selection: PortfolioSelection, risks: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    concentration = selection.risk_summary.get("risk_concentration")
    if isinstance(concentration, int | float):
        lines.append(f"Risk concentration score: {float(concentration):.3f}.")
    flags = selection.risk_summary.get("shared_risk_flags") or selection.risk_summary.get(
        "correlated_risks"
    )
    if isinstance(flags, Sequence) and not isinstance(flags, str) and flags:
        lines.append(f"Shared risk modes: {_join_ids([_clean_text(flag) for flag in flags])}.")
    generated = selection.risk_summary.get("generated_only_fraction")
    if isinstance(generated, int | float):
        lines.append(f"Generated-hypothesis concentration: {float(generated):.3f}.")
    if risks:
        lines.append(f"External risk summary inputs provided: {_join_ids(sorted(risks))}.")
    if not lines:
        lines.append("No concentrated risk modes were reported by deterministic analysis.")
    return lines


def _scenario_lines(scenario_analysis: SensitivityAnalysis | None) -> list[str]:
    if scenario_analysis is None:
        return ["No scenario analysis artifact was supplied."]
    lines: list[str] = []
    rows = scenario_analysis.metadata.get("scenario_comparison_table")
    if isinstance(rows, Sequence) and not isinstance(rows, str):
        for row in rows[:6]:
            if not isinstance(row, Mapping):
                continue
            name = _clean_text(row.get("scenario") or row.get("name") or row.get("scenario_id"))
            selected = row.get("selected_candidate_ids") or row.get("selected")
            count = (
                len(selected)
                if isinstance(selected, Sequence) and not isinstance(selected, str)
                else None
            )
            score = row.get("portfolio_score")
            if isinstance(score, int | float) and count is not None:
                lines.append(f"{name}: {count} selected candidate(s), score {float(score):.3f}.")
            elif count is not None:
                lines.append(f"{name}: {count} selected candidate(s).")
    if scenario_analysis.robust_candidate_ids:
        lines.append(
            f"Robust across scenarios: {_join_ids(scenario_analysis.robust_candidate_ids)}."
        )
    if scenario_analysis.fragile_candidate_ids:
        lines.append(
            f"Sensitive to assumptions: {_join_ids(scenario_analysis.fragile_candidate_ids)}."
        )
    if scenario_analysis.objective_sensitivities:
        lines.append(
            "Objective-weight sensitivity was computed for "
            f"{len(scenario_analysis.objective_sensitivities)} objective(s)."
        )
    return lines or [
        "Scenario analysis artifact was present, but no comparison rows were available."
    ]


def _next_action_lines(selection: PortfolioSelection) -> list[str]:
    lines = [
        "Route candidates with approval requirements through the review workflow.",
        "Resolve missing or conflicting imported artifacts before relying on the portfolio "
        "ranking.",
        "Refresh the optimization after new validated evidence, review decisions, or model "
        "artifacts are imported.",
    ]
    if selection.deferred_candidate_ids:
        lines.append(
            "Revisit deferred candidates when resource limits or objective weights change."
        )
    return lines


def _approval_lines(selection: PortfolioSelection) -> list[str]:
    if not _human_approval_required(selection):
        return ["No required human approval was recorded for this selection."]
    approvals = selection.metadata.get("required_approvals")
    if isinstance(approvals, Sequence) and not isinstance(approvals, str) and approvals:
        return [f"Required approval role: {_clean_text(role)}." for role in approvals]
    return ["Human approval is required before using this selection for downstream prioritization."]


def _limitations() -> list[str]:
    return [
        "Research prioritization aid only; not care guidance or an execution instruction.",
        "The memo does not include bench procedures, care-plan details, or chemistry "
        "execution steps.",
        "Generated molecules remain computational hypotheses unless exact imported evidence "
        "exists.",
        "Structure and model scores are prioritization inputs, not standalone proof.",
        "Codex text, when supplied, is used only after deterministic guardrail checks.",
    ]


def _sections_from_memo(memo: ProgramDecisionMemo) -> dict[str, list[str]]:
    return {
        "executive_summary": [memo.executive_summary],
        "portfolio_selected": [memo.selected_portfolio_summary],
        "selected_candidates": ["Candidate-level rationale was not attached to this memo."],
        "rejected_deferred_candidates": [
            "Rejected and deferred rationale was not attached to this memo."
        ],
        "coverage": memo.key_tradeoffs or ["Coverage details were not attached to this memo."],
        "key_uncertainties": memo.uncertainty_notes,
        "key_risks": memo.key_risks,
        "scenario_sensitivity": ["Scenario sensitivity was not attached to this memo."],
        "next_actions": memo.recommended_next_actions,
        "human_approvals": _approval_lines_from_memo(memo),
        "limitations": memo.limitations,
    }


def _approval_lines_from_memo(memo: ProgramDecisionMemo) -> list[str]:
    if memo.human_approval_required:
        return [
            "Human approval is required before using this selection for downstream prioritization."
        ]
    return ["No required human approval was recorded for this selection."]


def _guard_codex_draft(codex_draft: str | None) -> tuple[str | None, list[str]]:
    if codex_draft is None or not codex_draft.strip():
        return None, []
    violations = validate_memo_guardrails(codex_draft)
    if violations:
        return None, violations
    cleaned = _clean_markdown(codex_draft)
    cleaned_violations = validate_memo_guardrails(cleaned)
    if cleaned_violations:
        return None, cleaned_violations
    if not _has_required_codex_sections(cleaned):
        return None, ["section coverage"]
    return cleaned, []


def _ensure_markdown_title(markdown: str, title: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("# "):
        return stripped + "\n"
    return f"# {_clean_text(title)}\n\n{stripped}\n"


def _has_required_codex_sections(markdown: str) -> bool:
    lowered = markdown.lower()
    return all(marker in lowered for marker in _REQUIRED_CODEX_SECTION_MARKERS)


def _human_approval_required(selection: PortfolioSelection) -> bool:
    return bool(
        selection.metadata.get("human_approval_required")
        or selection.constraint_violations
        or any("require_review" in str(item).lower() for item in selection.constraint_violations)
    )


def _index_records(
    records: Mapping[str, Any] | Sequence[Mapping[str, Any]] | Any,
) -> dict[str, Mapping[str, Any]]:
    if records is None:
        return {}
    if isinstance(records, Mapping):
        if _record_id(records) is not None:
            record_id = _record_id(records)
            return {record_id: records} if record_id is not None else {}
        indexed: dict[str, Mapping[str, Any]] = {}
        for key, value in records.items():
            if isinstance(value, Mapping):
                indexed[str(key)] = value
        return indexed
    if isinstance(records, Sequence) and not isinstance(records, str):
        indexed = {}
        for item in records:
            if not isinstance(item, Mapping):
                continue
            record_id = _record_id(item)
            if record_id is not None:
                indexed[record_id] = item
        return indexed
    return {}


def _record_id(record: Mapping[str, Any]) -> str | None:
    for key in (
        "portfolio_candidate_id",
        "candidate_id",
        "source_candidate_id",
        "id",
        "candidate",
    ):
        value = record.get(key)
        if value is not None:
            return str(value)
    return None


def _display_candidate(
    candidate_id: str,
    candidate_index: Mapping[str, Mapping[str, Any]],
) -> str:
    record = candidate_index.get(candidate_id, {})
    name = record.get("candidate_name") or record.get("name")
    if name and str(name) != candidate_id:
        return f"`{_clean_text(candidate_id)}` ({_clean_text(name)})"
    return f"`{_clean_text(candidate_id)}`"


def _status_from_record(record: Mapping[str, Any] | None) -> str | None:
    if not record:
        return None
    for key in ("review_status", "status", "decision"):
        value = record.get(key)
        if value:
            return _clean_text(value)
    return None


def _evidence_state(
    candidate_id: str,
    evidence_index: Mapping[str, Mapping[str, Any]],
    candidate_index: Mapping[str, Mapping[str, Any]],
) -> str | None:
    if candidate_id in evidence_index:
        return "exact imported evidence linked"
    record = candidate_index.get(candidate_id, {})
    if record.get("direct_experimental_evidence") is True:
        return "exact imported evidence linked"
    if record.get("generated_without_direct_evidence") is True:
        return "no exact imported evidence linked"
    return None


def _learning_state(
    candidate_id: str,
    learning_index: Mapping[str, Mapping[str, Any]],
) -> str | None:
    record = learning_index.get(candidate_id)
    if not record:
        return None
    priority = record.get("priority") or record.get("rank") or record.get("score")
    if isinstance(priority, int | float):
        return f"learning priority {float(priority):.3f}"
    if priority:
        return f"learning priority `{_clean_text(priority)}`"
    return "learning suggestion available"


def _count_origin(
    candidate_ids: Sequence[str],
    candidate_index: Mapping[str, Mapping[str, Any]],
    origin: str,
) -> int:
    return sum(
        1
        for candidate_id in candidate_ids
        if candidate_index.get(candidate_id, {}).get("origin") == origin
    )


def _join_ids(values: Sequence[str] | Sequence[Any]) -> str:
    cleaned = [_clean_text(value) for value in values if str(value)]
    if not cleaned:
        return "none"
    return ", ".join(f"`{value}`" for value in cleaned)


def _paragraphs(value: Any) -> list[str]:
    lines = _coerce_lines(value)
    return lines or ["Not recorded."]


def _bullets(value: Any) -> list[str]:
    lines = _coerce_lines(value)
    if not lines:
        lines = ["Not recorded."]
    return [f"- {line}" for line in lines]


def _coerce_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_clean_text(value)]
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_clean_text(item) for item in value]
    return [_clean_text(value)]


def _clean_text(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    for pattern, replacement in _RAW_TERM_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _clean_markdown(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r", "").splitlines()]
    text = "\n".join(lines).strip()
    for pattern, replacement in _RAW_TERM_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text
