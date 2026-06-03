# Reviewer Training

Audience: reviewers responsible for stage gates, generated molecule review,
Codex output review, campaign approvals, and evaluation interpretation.

## Interpretation Boundaries

Review decisions are governance artifacts. They do not create biomedical truth,
assay results, clinical conclusions, lab procedures, dosing guidance, or
synthesis instructions.

## Checklist

- Confirm artifact provenance and hash.
- Confirm generated molecules remain labeled.
- Confirm Codex outputs are separate from evidence and decisions.
- Confirm model predictions and docking outputs are not overclaimed.
- Confirm campaign plans are not procedural instructions.
- Record decision, rationale, and reviewer identity.

## Exercise: Synthetic Stage Gate

Synthetic data:

- Review item: `review-G-001`
- Candidate: `Generated Hypothesis G-001`
- Evidence status: `no direct imported result`
- Codex summary: `codex_summary_G-001.md`
- Proposed action: `export for downstream planning`

Steps:

1. Inspect provenance and generated label.
2. Compare Codex summary against source artifacts.
3. Check for overclaims or invented citations.
4. Reject export until review requirements are satisfied.
5. Record a decision rationale.

Expected outcomes:

- Codex summary is not treated as evidence.
- Generated candidate does not export without required review status.
- Decision artifact is separate from Codex transcript.
- Rationale mentions boundaries and missing direct evidence.

## Common Mistakes

- Accepting a Codex summary without source comparison.
- Allowing “active”, “safe”, or “effective” labels for generated molecules.
- Treating a campaign plan as a lab protocol.
- Omitting rationale from stage-gate decisions.
