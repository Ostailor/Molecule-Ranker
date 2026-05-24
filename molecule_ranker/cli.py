from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from molecule_ranker import __version__
from molecule_ranker.config import RankerConfig
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator
from molecule_ranker.utils import slugify

app = typer.Typer(
    help="Rank existing molecules for disease research hypotheses using transparent evidence.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Agent-first molecule ranking research prototype."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def rank(
    disease_name: Annotated[str, typer.Argument(help="Disease name to resolve and rank.")],
    top: Annotated[int, typer.Option("--top", min=1, help="Number of candidates to retain.")] = 20,
    results_root: Annotated[
        Path,
        typer.Option(
            "--results-root",
            help="Directory where disease-specific outputs are written.",
        ),
    ] = Path("results"),
) -> None:
    """Run the V0.0 existing-molecule ranking pipeline."""
    config = RankerConfig(results_dir=results_root, default_top=top)
    result = MoleculeRankerOrchestrator(config=config).rank(disease_name, top=top)
    output_dir = results_root / slugify(result.disease.canonical_name)
    typer.echo(f"Wrote {len(result.candidates)} candidates to {output_dir}")


if __name__ == "__main__":
    app()
