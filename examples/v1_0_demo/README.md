# V1.0 Synthetic Demo Project

This directory is a synthetic V1.0 internal MVP demo. It uses clearly fake
entities only:

- Disease: `ExampleDiseaseA`
- Target: `ExampleTargetA`
- Existing candidate: `ExampleCandidateA`
- Generated hypothesis: `ExampleGeneratedHypothesisA`
- External system: `ExampleExternalSystemA`

The data in this directory is not biomedical evidence, not patient guidance,
not assay validation, and not a source of scientific truth. It contains no
PMIDs, DOIs, clinical claims, treatment claims, dosing, synthesis instructions,
or lab protocols.

## What The Demo Covers

The demo command script walks through the V1.0 release-quality workflow surface:

- project create
- mocked/offline ranking validation
- generated hypothesis validation
- developability triage from saved artifacts
- review workspace creation
- synthetic assay-result import
- active-learning batch generation
- integration dry-run sync
- mocked Codex project summary
- export package creation
- dashboard build

All commands are intended for internal platform demonstration and operator
training. Default validation commands use deterministic mocked data and do not
call live public APIs, Benchling, warehouse systems, or OpenAI.

## Files

- `synthetic_assay_results.csv`: fake assay-import rows with inconclusive or
  failed-QC labels only.
- `synthetic_external_sync_payload.json`: dry-run external sync payload using
  fake external IDs.
- `demo_commands.sh`: shell commands for an offline demo run.
- `expected_artifacts_manifest.json`: expected demo outputs and their purpose.

## Safety Notes

Generated hypotheses remain labeled as generated hypotheses. They are not
evidence and must not be described as confirmed, active, safe, or useful for
patients. Synthetic assay rows in this demo are file-import examples only and
must not be used as scientific support.
