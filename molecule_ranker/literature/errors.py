class LiteratureRetrievalError(RuntimeError):
    """Raised when a literature source cannot return usable paper records."""


class LiteratureParsingError(RuntimeError):
    """Raised when a literature source response cannot be parsed safely."""


class CitationExtractionError(RuntimeError):
    """Raised when citation metadata cannot be extracted from a retrieved paper."""
