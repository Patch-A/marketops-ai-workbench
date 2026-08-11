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
    MAX_PARSER_BLOCKS,
    MAX_PARSER_TABLE_CELLS,
    MAX_PARSER_WARNINGS,
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
    "MAX_PARSER_BLOCKS",
    "MAX_PARSER_TABLE_CELLS",
    "MAX_PARSER_WARNINGS",
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
