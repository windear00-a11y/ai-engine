"""Persistent Project/Code Index — Component B of the original Phase 1.

This is a separate, per-project, derived, READ-ONLY-by-construction index that
never touches the production knowledge database, Contract v1, or retrieval/.
It is Python-first (stdlib AST only) and entirely deterministic. It is consumed
by the future Deterministic Planner (Component C) and is not yet wired into
TaskEngine/CodingTools.
"""

from .types import (
    project_id, file_id, symbol_id, edge_id, stable_id,
    FileRecord, SymbolRecord, EdgeRecord, SourceLocation,
)
from .indexer import ProjectIndex, default_db_path
from .query import IndexQueries
from .store import ProjectIndexStore

__all__ = [
    "ProjectIndex", "IndexQueries", "ProjectIndexStore",
    "default_db_path",
    "project_id", "file_id", "symbol_id", "edge_id", "stable_id",
    "FileRecord", "SymbolRecord", "EdgeRecord", "SourceLocation",
]
