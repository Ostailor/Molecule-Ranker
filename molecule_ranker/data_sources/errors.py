class ExternalDataUnavailableError(RuntimeError):
    """Raised when an external biomedical data source cannot be reached or parsed."""


class DiseaseResolutionError(RuntimeError):
    """Raised when a disease cannot be resolved to a public biomedical entity."""


class TargetDiscoveryError(RuntimeError):
    """Raised when disease-associated targets cannot be discovered."""


class MoleculeRetrievalError(RuntimeError):
    """Raised when molecule records cannot be retrieved for discovered targets."""


class EvidenceRetrievalError(RuntimeError):
    """Raised when supporting evidence or annotations cannot be retrieved."""


class NoCandidatesFoundError(RuntimeError):
    """Raised when no molecule candidates are available for ranking."""
