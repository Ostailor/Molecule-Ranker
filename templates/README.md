# Assay Result Import Templates

These templates show the expected file shape for V0.6 experimental result import. The example rows are neutral placeholders only. They are not real assay results and should not be used as scientific evidence.

## Files

- `assay_results_template.csv`: CSV import template with supported columns.
- `assay_results_template.json`: JSON import template using a top-level `results` array.

## Required Columns

- `candidate_name`: Candidate label. Do not leave this blank.
- `candidate_origin`: One of `existing`, `generated`, or `unknown`.
- `assay_name`: High-level assay name.
- `assay_type`: One of `biochemical`, `cellular`, `phenotypic`, `safety`, `developability`, `computational_validation`, or `other`.
- `endpoint_name`: High-level endpoint name.
- `endpoint_category`: One of `potency`, `target_engagement`, `phenotypic`, `safety`, `developability`, `selectivity`, `quality_control`, or `other`.
- `outcome_label`: One of the supported outcome labels below.
- `activity_direction`: One of `active`, `inactive`, `toxic`, `non_toxic`, `improved`, `worsened`, `no_effect`, `ambiguous`, or `not_applicable`.
- `qc_status`: One of the supported QC statuses below.

## Optional Columns

- `candidate_id`
- `canonical_smiles`
- `inchi_key`
- `disease_name`
- `target_symbol`
- `measured_value`
- `unit`
- `relation`
- `replicate_count`
- `replicate_values`
- `uncertainty`
- `result_date`
- `source_record_id`
- `notes`

## Outcome Labels

- `positive`
- `negative`
- `inconclusive`
- `failed_qc`
- `not_tested`
- `invalid`

Use `inconclusive` when the row is incomplete or ambiguous. Do not infer outcomes from model scores or ranking position.

## QC Status

- `passed`
- `failed`
- `partial`
- `unknown`

Failed QC results can be imported for provenance and quality tracking, but they should not be treated as supporting evidence.

## Example Import

```bash
molecule-ranker experiment import templates/assay_results_template.csv --db-path .experiments/results.sqlite --dry-run
```

Remove `--dry-run` only after replacing placeholders with user-supplied result records.

## Scientific Limitations

- Templates do not contain real experimental evidence.
- Imported in-vitro or biochemical results do not imply clinical efficacy.
- Generated molecule results apply only to the exact tested structure.
- Incomplete or ambiguous records should be marked incomplete, inconclusive, or invalid rather than guessed.
- Do not include lab protocols, reagent recipes, synthesis routes, dosing instructions, animal experiment steps, human experiment steps, or patient treatment instructions.
