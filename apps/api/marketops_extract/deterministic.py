"""Deterministic extraction from explicitly labelled parser blocks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .contract import (
    AmbiguousTableError,
    Candidate,
    ExtractionContractError,
    ParserBlock,
    SourceCitation,
    _sha256,
    _uuid,
    stable_candidate_id,
)


KIND_TERMS = {
    "deliverable": ("deliverable", "交付物", "交付成果"),
    "milestone": ("milestone", "里程碑"),
    "constraint": ("constraint", "约束", "限制条件"),
    "assumption": ("assumption", "hypothesis", "假设"),
}


def _label_kinds(label: str) -> frozenset[str]:
    normalized = label.casefold()
    if any(marker in normalized for marker in ("编号", "序号", " id", "id_", "code")):
        return frozenset()
    return frozenset(
        kind
        for kind, terms in KIND_TERMS.items()
        if any(term.casefold() in normalized for term in terms)
    )


def _candidate_kind(block: ParserBlock) -> str | None:
    section_matches = frozenset().union(*(_label_kinds(item) for item in block.section_path))
    column_matches = _label_kinds(block.column_name) if block.column_name else frozenset()
    if block.kind == "table" and not block.cells:
        combined = section_matches | column_matches
        if len(combined) != 1 or (section_matches and column_matches and section_matches != column_matches):
            raise AmbiguousTableError()
        return next(iter(combined))
    if len(section_matches) > 1:
        raise ExtractionContractError("AMBIGUOUS_SECTION", "section maps to multiple candidate kinds")
    return next(iter(section_matches)) if section_matches else None


def _global_location_order(location: Any, parent: Any | None = None) -> tuple[str, int, int, int]:
    raw = location.as_dict()
    if location.kind == "line_range":
        return ("lines", raw["startLine"], 0, raw["endLine"])
    if location.kind == "markdown_table_cell":
        return ("lines", raw["line"], 1, raw["columnIndex"])
    if location.kind == "csv_range":
        return ("rows", raw["startRow"], 0, raw["endRow"])
    if location.kind == "csv_cell":
        return ("rows", raw["row"], 1, raw["columnIndex"])
    if location.kind == "docx_paragraph":
        return ("body", raw["bodyIndex"], raw["paragraph"], 0)
    if location.kind == "docx_table":
        return ("body", raw["bodyIndex"], raw["table"], 0)
    if location.kind == "docx_table_cell":
        if parent is not None and parent.kind == "docx_table":
            outer = parent.as_dict()
            return ("body", outer["bodyIndex"], outer["table"], raw["row"] * 100000 + raw["column"])
        return ("body", raw["table"], raw["row"], raw["column"])
    raise ExtractionContractError("INVALID_LOCATION", "unsupported location ordering")


def _global_location_span(location: Any) -> tuple[str, int, int]:
    raw = location.as_dict()
    if location.kind == "line_range":
        return ("lines", raw["startLine"], raw["endLine"])
    if location.kind == "markdown_table_cell":
        return ("lines", raw["line"], raw["line"])
    if location.kind == "csv_range":
        return ("rows", raw["startRow"], raw["endRow"])
    if location.kind == "csv_cell":
        return ("rows", raw["row"], raw["row"])
    if location.kind in {"docx_paragraph", "docx_table"}:
        return ("body", raw["bodyIndex"], raw["bodyIndex"])
    raise ExtractionContractError("INVALID_LOCATION", "unsupported location ordering")


def _validate_block_order(blocks: tuple[ParserBlock, ...]) -> None:
    seen: set[tuple[Any, ...]] = set()
    previous_family: str | None = None
    previous_end: int | None = None
    for block in blocks:
        identity = block.location.identity()
        if identity in seen:
            raise ExtractionContractError("DUPLICATE_LOCATION", "parser blocks reuse a source location")
        seen.add(identity)
        family, start, end = _global_location_span(block.location)
        if previous_family is not None and family != previous_family:
            raise ExtractionContractError("MIXED_LOCATION_FAMILY", "parser blocks use mixed source coordinate families")
        if previous_end is not None and start <= previous_end:
            raise ExtractionContractError("NON_MONOTONIC_LOCATION", "parser blocks are not in source order")
        previous_family = family
        previous_end = end
        previous_cell: tuple[str, int, int, int] | None = None
        for cell in block.cells:
            cell_identity = cell.location.identity()
            if cell_identity in seen:
                raise ExtractionContractError("DUPLICATE_LOCATION", "parser blocks reuse a source location")
            seen.add(cell_identity)
            cell_key = _global_location_order(cell.location, block.location)
            if cell_key[0] != family:
                raise ExtractionContractError("MIXED_LOCATION_FAMILY", "table cells use mixed source coordinate families")
            if previous_cell is not None and cell_key <= previous_cell:
                raise ExtractionContractError("NON_MONOTONIC_LOCATION", "table cells are not in source order")
            previous_cell = cell_key


class DeterministicExtractor:
    """Validate a parser batch, then return an immutable tuple of candidates."""

    def extract(
        self,
        *,
        proposal_version_id: str,
        proposal_sha256: str,
        blocks: Iterable[Mapping[str, Any] | ParserBlock],
    ) -> tuple[Candidate, ...]:
        version_id = _uuid(proposal_version_id, "proposal_version_id")
        source_hash = _sha256(proposal_sha256, "proposal_sha256")
        if isinstance(blocks, (str, bytes, Mapping)):
            raise ExtractionContractError("INVALID_BLOCKS", "blocks must be an iterable of parser blocks")
        try:
            raw_blocks = tuple(blocks)
        except TypeError:
            raise ExtractionContractError("INVALID_BLOCKS", "blocks must be iterable") from None
        parsed: list[ParserBlock] = []
        for raw in raw_blocks:
            if isinstance(raw, ParserBlock):
                parsed.append(raw)
            else:
                parsed.append(ParserBlock.from_mapping(raw))
        parsed_blocks = tuple(parsed)
        _validate_block_order(parsed_blocks)

        classified: list[tuple[ParserBlock, str]] = []
        for block in parsed_blocks:
            if block.cells:
                column_kinds = {
                    cell.column: _label_kinds(cell.column) for cell in block.cells
                }
                supported = frozenset().union(*column_kinds.values())
                section_kinds = frozenset().union(*(_label_kinds(item) for item in block.section_path))
                if len(supported) != 1:
                    if not supported and not section_kinds:
                        # Tables outside the four extraction sections are retained by
                        # the parser but are outside this candidate slice.
                        continue
                    raise AmbiguousTableError()
                supported_kind = next(iter(supported))
                if section_kinds and section_kinds != frozenset({supported_kind}):
                    raise AmbiguousTableError()
                semantic_columns = {column for column, matches in column_kinds.items() if matches}
                if len(semantic_columns) != 1:
                    raise AmbiguousTableError()
                for cell in block.cells:
                    matches = column_kinds[cell.column]
                    if not matches:
                        continue
                    if matches != frozenset({supported_kind}):
                        raise AmbiguousTableError()
                    classified.append(
                        (
                            ParserBlock(
                                kind="table",
                                text=cell.value,
                                section_path=block.section_path,
                                location=cell.location,
                                column_name=cell.column,
                            ),
                            supported_kind,
                        )
                    )
                continue
            kind = _candidate_kind(block)
            if block.kind != "heading" and kind is not None:
                classified.append((block, kind))

        candidates: list[Candidate] = []
        for block, kind in classified:
            classification = "hypothesis" if kind == "assumption" else "fact"
            citation = SourceCitation(
                version_id,
                source_hash,
                block.location,
                tuple(block.section_path),
                block.text,
            )
            candidates.append(
                Candidate(
                    candidate_id=stable_candidate_id(
                        version_id, kind, classification, block.text, citation
                    ),
                    kind=kind,
                    text=block.text,
                    classification=classification,
                    confidence=1.0,
                    source_citation=citation,
                )
            )
        return tuple(candidates)


def extract_candidates(
    *,
    proposal_version_id: str,
    proposal_sha256: str,
    blocks: Iterable[Mapping[str, Any] | ParserBlock],
) -> tuple[Candidate, ...]:
    return DeterministicExtractor().extract(
        proposal_version_id=proposal_version_id,
        proposal_sha256=proposal_sha256,
        blocks=blocks,
    )
