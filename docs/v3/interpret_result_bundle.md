# Interpret Result Bundle

A V3 result bundle is a research-planning package. It separates source-backed
evidence, model predictions, graph inference, generated hypotheses, Codex
outputs, governance decisions, lineage, evaluation, and certification.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Bundle Files

- `v3_result_bundle.json`: machine-readable bundle.
- `v3_result_bundle.md`: human-readable summary.
- `v3_result_bundle.zip`: archive of bundle and referenced artifacts.

## Required Sections

The bundle includes summaries for candidates, generated molecules, biologics,
evidence, literature, developability, experimental evidence, model predictions,
structure, graph inference, hypotheses, portfolio, campaign planning, review,
evaluation, integrations, Codex activity, governance, approvals, lineage,
validation, limitations, and required next human decisions.

## Ranked Candidates

Ranked candidates are planning outputs. Read them with their source evidence,
score explanations, limitations, and review status. A high rank does not prove
binding, activity, safety, efficacy, or therapeutic value.

## Generated Hypotheses

Generated candidates are labeled as computational hypotheses. They are not
claims of activity, safety, synthesizability, manufacturability, or suitability.
They must remain behind review gates.

## Codex Outputs

Codex outputs may summarize, plan, or organize workflow steps. They are separate
from evidence and must not be interpreted as independent scientific facts.

## Certification

Result certification verifies platform and workflow controls: required
artifacts, lineage, guardrails, governance, external-write status, output
separation, generated-output labeling, reproducibility, and safety-case linkage.
It is not clinical validation.

## What The Bundle Does Not Prove

The bundle does not prove that a candidate is safe, effective, active, binding,
synthesizable, manufacturable, clinically useful, or approved for any use.

