from __future__ import annotations

from typing import Any, Protocol

from molecule_ranker.developability.schemas import DockingAssessment


class DockingUnavailableError(RuntimeError):
    """Raised when optional docking cannot be run under strict structure mode."""


class DockingEngine(Protocol):
    engine_name: str

    def dock(
        self,
        ligand_smiles: str,
        structure: Any,
        binding_site: Any,
        config: Any,
    ) -> DockingAssessment:
        """Run optional docking and return cautious computational triage metadata."""
        ...


__all__ = ["DockingEngine", "DockingUnavailableError"]
