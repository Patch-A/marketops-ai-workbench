"""M2-01 scoped source indexing and deterministic retrieval domain."""

from .service import (
    Citation,
    ParsedBlock,
    RetrievalFailure,
    RetrievalScopeContext,
    SearchHit,
    SearchResult,
    SourceChunk,
    SourceIdentity,
    SourceIndex,
    build_source_index,
    search_source_indexes,
    validate_citation,
    withdraw_source_index,
)
from .postgres import (
    AsyncpgRetrievalRepository,
    PersistIndexResult,
    WithdrawIndexResult,
)

__all__ = [
    "Citation",
    "ParsedBlock",
    "RetrievalFailure",
    "RetrievalScopeContext",
    "SearchHit",
    "SearchResult",
    "SourceChunk",
    "SourceIdentity",
    "SourceIndex",
    "build_source_index",
    "search_source_indexes",
    "validate_citation",
    "withdraw_source_index",
    "AsyncpgRetrievalRepository",
    "PersistIndexResult",
    "WithdrawIndexResult",
]
