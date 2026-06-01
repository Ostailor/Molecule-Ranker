# Campaign Planning

V1.7 campaign planning helps research teams turn reviewed hypotheses and
candidate batches into high-level campaign-management artifacts. It supports
budget fit, review-gated work packages, assay/review/computation slot
allocation, progress summaries, replan triggers, expected learning value,
opportunity cost, campaign memos, and campaign dashboards.

Campaign plans are for research use only. They provide no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation and remain computational hypotheses
unless exact imported experimental evidence exists.

Campaign planning consumes deterministic artifacts such as ranked hypotheses,
portfolio candidates, active learning suggestions, expert review decisions,
model predictions, graph contradictions, integration updates, and imported assay
results. It does not create evidence, assay results, citations, molecules,
mechanisms, outcomes, campaign metrics, or costs. Supplied costs must include
cost provenance; otherwise the planner records unknown cost and uses only the
available slot constraints.

Codex may draft campaign summaries and planning memos only from deterministic
campaign artifacts. Codex cannot compute priority, budget fit, dependencies,
slot allocation, replan triggers, expected learning value, opportunity cost, or
campaign advancement decisions.

Create a campaign plan:

```bash
uv run molecule-ranker campaign plan \
  --hypotheses ranked_hypotheses.json \
  --candidates portfolio_candidates.json \
  --max-work-packages 8 \
  --max-assay-slots 4 \
  --max-review-slots 6 \
  --output campaign_plan.json \
  --memo-output campaign_memo.md \
  --dashboard-output campaign_dashboard.html
```

Campaign work packages remain high-level. They may allocate broad assay,
review, or computation capacity, but they do not include reagents,
concentrations, incubation times, temperatures, procedural steps, animal dosing,
human dosing, patient treatment guidance, or synthesis routes.
