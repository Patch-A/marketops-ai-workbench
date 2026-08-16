"""Authenticated-project retrieval orchestration over the PostgreSQL repository."""

from __future__ import annotations

import asyncio
from typing import Protocol

from .postgres import WithdrawIndexResult
from .service import (
    RetrievalFailure,
    RetrievalScopeContext,
    SearchResult,
    SourceIndex,
    search_source_indexes,
)


class RetrievalRepository(Protocol):
    async def load_search_snapshot(
        self, scope: RetrievalScopeContext, project_id: str
    ) -> tuple[tuple[SourceIndex, ...], dict[str, str]]: ...

    async def list_ready_indexes(
        self, scope: RetrievalScopeContext, project_id: str
    ) -> tuple[SourceIndex, ...]: ...

    async def get_index(
        self, scope: RetrievalScopeContext, project_id: str, index_id: str
    ) -> SourceIndex | None: ...

    async def current_source_hashes(
        self, scope: RetrievalScopeContext, project_id: str
    ) -> dict[str, str]: ...

    async def withdraw_index(
        self, index_id: str, project_id: str, scope: RetrievalScopeContext
    ) -> WithdrawIndexResult: ...


class RetrievalApplicationService:
    def __init__(self, repository: RetrievalRepository) -> None:
        self.repository = repository

    async def search(
        self,
        *,
        project_id: str,
        query: str,
        limit: int,
        scope: RetrievalScopeContext,
    ) -> SearchResult:
        try:
            indexes, source_hashes = await self.repository.load_search_snapshot(
                scope, project_id
            )
            return search_source_indexes(
                indexes,
                project_id=project_id,
                query=query,
                scope=scope,
                current_source_hashes=source_hashes,
                limit=limit,
            )
        except asyncio.CancelledError:
            raise
        except RetrievalFailure:
            raise
        except Exception:
            raise RetrievalFailure(
                "RETRIEVAL_READ_FAILED", "project retrieval failed"
            ) from None

    async def read_index(
        self,
        *,
        project_id: str,
        index_id: str,
        scope: RetrievalScopeContext,
    ) -> SourceIndex:
        try:
            index = await self.repository.get_index(scope, project_id, index_id)
        except asyncio.CancelledError:
            raise
        except RetrievalFailure:
            raise
        except Exception:
            raise RetrievalFailure(
                "RETRIEVAL_READ_FAILED", "source index could not be read"
            ) from None
        if index is None:
            raise RetrievalFailure("INDEX_NOT_FOUND", "source index was not found")
        return index

    async def withdraw(
        self,
        *,
        project_id: str,
        index_id: str,
        scope: RetrievalScopeContext,
    ) -> WithdrawIndexResult:
        try:
            return await self.repository.withdraw_index(index_id, project_id, scope)
        except asyncio.CancelledError:
            raise
        except RetrievalFailure:
            raise
        except Exception:
            raise RetrievalFailure(
                "RETRIEVAL_WRITE_FAILED", "source index could not be withdrawn"
            ) from None
