from __future__ import annotations

from typing import Any, Protocol

from molecule_ranker.schemas import Disease, Target


class DiseaseResolverDataSource(Protocol):
    """Adapter interface for resolving disease names with public biomedical sources."""

    def resolve_disease(self, disease_name: str) -> Disease:
        """Resolve a disease query into a normalized disease model."""
        ...


class TargetDiscoveryDataSource(Protocol):
    """Adapter interface for retrieving disease-associated targets."""

    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
        """Find disease-relevant target hypotheses."""
        ...


class MoleculeRetrievalDataSource(Protocol):
    """Adapter interface for retrieving molecules associated with targets."""

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        """Retrieve existing molecule records for a disease and target set."""
        ...


class MoleculeAnnotationDataSource(Protocol):
    """Adapter interface for enriching molecule records with public chemical metadata."""

    def annotate_molecule(self, molecule: dict[str, Any]) -> dict[str, Any]:
        """Return an enriched molecule record."""
        ...

    def annotate_molecules(self, molecules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return enriched molecule records."""
        ...
