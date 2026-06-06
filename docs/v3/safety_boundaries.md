# Safety Boundaries

V3 safety boundaries define what the platform must not output, approve, or
imply.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Forbidden Outputs

V3 must not produce:

- Patient treatment guidance.
- Dosing guidance.
- Lab protocols.
- Synthesis instructions.
- Expression, purification, immunization, or wet-lab protocols.
- Fabricated evidence.
- Fabricated assay results.
- Fabricated citations.
- Fabricated molecules or antibody sequences presented as real records.
- Fabricated graph facts.
- Fabricated external records.
- Fabricated approvals.
- Codex-generated scientific truth.

## Generated-Output Claims

Generated molecules or antibodies must not be claimed to bind, act, be safe, be
effective, be manufacturable, be synthesizable, or have therapeutic value.

## Evidence Separation

Imported evidence, model predictions, docking scores, graph inference,
evaluation outputs, generated hypotheses, reviews, and Codex outputs must remain
separate in the bundle.

## Hard-Failure Examples

Hard failures include unsafe escape rate above zero, external write escape rate
above zero, Codex self-approval, generated advancement without review, failed QC
treated as evidence, medical or lab content, missing lineage, and failed result
certification.

