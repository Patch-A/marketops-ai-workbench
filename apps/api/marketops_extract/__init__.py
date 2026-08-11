"""M1-02 deterministic deliverable extraction contract."""

from .contract import (
    AmbiguousTableError,
    Candidate,
    ExtractionContractError,
    ParserBlock,
    SourceCitation,
    SourceLocation,
    TableCell,
)
from .deterministic import DeterministicExtractor, extract_candidates
from .parser import (
    ParserWarning,
    RuntimeParseFailure,
    RuntimeParserResult,
    RuntimeProposalParser,
)

__all__ = [
    "Candidate",
    "DeterministicExtractor",
    "AmbiguousTableError",
    "ExtractionContractError",
    "ParserBlock",
    "ParserWarning",
    "RuntimeParseFailure",
    "RuntimeParserResult",
    "RuntimeProposalParser",
    "SourceCitation",
    "SourceLocation",
    "TableCell",
    "extract_candidates",
]
