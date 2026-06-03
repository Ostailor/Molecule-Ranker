# Generated Molecule Interpretation

Audience: scientists and reviewers interpreting generated molecule artifacts.

## Interpretation Boundaries

Generated molecules are computational hypotheses. They are not known actives,
not safety findings, not efficacy findings, not binding evidence, and not
synthesizability claims. The platform does not provide synthesis instructions,
lab protocols, dosing, or patient treatment guidance.

## Checklist

- Confirm generated label and generation trace.
- Confirm no direct evidence is attached unless exact imported result exists.
- Review novelty, uncertainty, and provenance metadata.
- Require review before export.
- Keep generated molecules separate from source-backed existing molecules.

## Exercise: Synthetic Generated Candidate

Synthetic data:

- Generated candidate: `Generated Hypothesis G-001`
- Trace artifact: `generation_trace_synthetic.json`
- Direct evidence: `none`
- Review status: `pending`

Steps:

1. Open the generated molecule report.
2. Identify generation method metadata.
3. Confirm no direct imported result is linked.
4. Mark as requiring review before export.
5. Add interpretation note using bounded language.

Expected outcomes:

- Candidate remains labeled generated.
- No activity, safety, efficacy, binding, or synthesizability claim is made.
- Review requirement is visible.

## Common Mistakes

- Calling generated molecules “validated”.
- Treating novelty as usefulness.
- Treating heuristic developability as safety.
- Exporting without review.
