from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.structure.schemas import StructureRecord
from molecule_ranker.structure.sources import StructureSourceHealthStatus


class UserStructureAdapter:
    """Load user-supplied PDB/mmCIF files from approved artifact roots."""

    source_name = "user_supplied"
    allowed_suffixes = {".pdb", ".cif", ".mmcif"}

    def __init__(self, *, allowed_roots: list[Path]) -> None:
        if not allowed_roots:
            raise ValueError("UserStructureAdapter requires at least one allowed root.")
        self.allowed_roots = [root.resolve() for root in allowed_roots]
        self.warnings: list[str] = []

    def load(
        self,
        path: str | Path,
        *,
        target_symbol: str,
        target_identifiers: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StructureRecord:
        self.warnings = []
        resolved = Path(path).resolve()
        self._validate_path(resolved)
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        warning = "User-supplied structure is not trusted without metadata review."
        self.warnings.append(warning)
        return StructureRecord(
            structure_id=f"{self.source_name}:{digest[:16]}",
            source=self.source_name,
            external_id=str(resolved),
            target_symbol=target_symbol,
            target_identifiers=dict(target_identifiers or {}),
            structure_type="user_supplied",
            experimental_method=None,
            resolution_angstrom=None,
            coverage={},
            chains=[],
            ligands=[],
            mutations=[],
            organism=None,
            release_date=None,
            quality_metrics={"file_size_bytes": resolved.stat().st_size},
            url=None,
            retrieved_at=datetime.now(UTC),
            metadata={
                "sha256": digest,
                "path": str(resolved),
                "user_provenance": dict(metadata or {}),
                "warnings": list(self.warnings),
                "requires_metadata_review": True,
            },
        )

    def health_check(self) -> StructureSourceHealthStatus:
        missing = [str(root) for root in self.allowed_roots if not root.exists()]
        return StructureSourceHealthStatus(
            source=self.source_name,
            ok=not missing,
            status="available" if not missing else "degraded",
            warnings=[f"Allowed root does not exist: {root}" for root in missing],
            metadata={"allowed_roots": [str(root) for root in self.allowed_roots]},
        )

    def _validate_path(self, resolved: Path) -> None:
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Structure file does not exist: {resolved}")
        if resolved.suffix.lower() not in self.allowed_suffixes:
            raise ValueError("User structure must be a PDB or mmCIF file.")
        if not any(_is_relative_to(resolved, root) for root in self.allowed_roots):
            raise PermissionError(
                "User structure path must remain within allowed artifact roots."
            )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = ["UserStructureAdapter"]
