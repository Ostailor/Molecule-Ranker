# molecule-ranker

`molecule-ranker` is an agent-first V0.0 research prototype for ranking existing
molecules that may be therapeutically relevant to a disease hypothesis.

The current version does not generate novel molecules. It includes a stubbed
`NovelMoleculeAgent` interface so future versions can add generation without
changing the existing-molecule ranking pipeline.

## What V0.0 Does

- Resolves a disease name into a typed `Disease` schema.
- Discovers target hypotheses through Open Targets.
- Retrieves existing molecules connected to those targets through ChEMBL.
- Can enrich molecule records through PubChem.
- Scores candidates with a transparent score breakdown.
- Writes machine-readable candidates, an evidence report, and an agent trace.

The system uses cautious research language: candidates are hypotheses, scores are
prioritization aids, and all therapeutic relevance claims require experimental
validation.

## What V0.0 Does Not Do

- It does not build a web UI.
- It does not generate novel molecules.
- It does not use a database; JSON artifacts are enough for V0.0.
- It does not provide dosage, prescribing, or patient treatment instructions.
- It does not claim that any molecule cures a disease.

## Setup

Python 3.11+ is required. The repository is configured for `uv`:

```bash
uv sync
```

You can also install the package with any PEP 517-compatible Python tool because
the project metadata lives in `pyproject.toml`.

## Usage

Run the ranking pipeline:

```bash
uv run molecule-ranker rank "Parkinson disease" --top 20
```

The command writes:

```text
results/parkinson-disease/candidates.json
results/parkinson-disease/report.md
results/parkinson-disease/trace.json
```

If public APIs are unavailable or no records are found, the adapters raise clear
domain-specific errors instead of returning fake candidates.

## Architecture

The package is intentionally modular:

```text
molecule_ranker/
  __init__.py
  cli.py
  config.py
  orchestrator.py
  schemas.py
  agents/
    base.py
    disease_resolver.py
    target_discovery.py
    molecule_retrieval.py
    evidence_scoring.py
    report_writer.py
    novel_molecule.py
  data_sources/
    base.py
    errors.py
    opentargets_adapter.py
    chembl_adapter.py
    pubchem_adapter.py
  scoring/
    scorer.py
  utils/
    slugify.py
    cache.py
    logging.py
```

Agent pipeline:

1. `DiseaseResolverAgent`
2. `TargetDiscoveryAgent`
3. `MoleculeRetrievalAgent`
4. `EvidenceScoringAgent`
5. `ReportWriterAgent`
6. `NovelMoleculeAgent` stub only

Biomedical APIs are isolated behind data-source protocols. HTTP requests are
made only inside adapter classes. Open Targets resolves diseases and target
associations, ChEMBL retrieves molecule mechanisms, and PubChem enriches chemical
metadata.

## Output Schemas

Core schemas are defined with Pydantic:

- `Disease`
- `Target`
- `MoleculeCandidate`
- `EvidenceItem`
- `ScoreBreakdown`
- `AgentTrace`

Every ranked molecule includes a rationale, evidence items, and a score
breakdown. The report is human-readable, while `candidates.json` and
`trace.json` are machine-readable.

## Limitations

- V0.0 depends on public external API availability.
- Scores are transparent heuristics, not validated predictive models.
- The app does not claim that any molecule cures a disease.
- The app does not provide dosage, prescribing, or patient treatment guidance.
- Candidate rankings are research hypotheses and require experimental validation.
- Novel molecule generation is intentionally not implemented in V0.0.

## Research Hypotheses, Not Medical Advice

Outputs are intended for research triage only. A ranked molecule is a candidate
hypothesis supported by public-source evidence and a score breakdown. It is not a
diagnosis, prescription, clinical recommendation, or claim of efficacy. Any
therapeutic relevance requires independent expert review and experimental
validation.

## Development

Run tests:

```bash
uv run pytest
```

Run lint:

```bash
uv run ruff check .
```

Run type checking:

```bash
uv run pyright
```

Run a smoke command:

```bash
uv run molecule-ranker rank "Parkinson disease" --top 2
```
