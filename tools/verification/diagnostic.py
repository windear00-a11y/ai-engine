"""Normalized Diagnostic model — Layer 5 5A-1.

Deterministic, JSON-serializable, no timestamps.
"""

from dataclasses import dataclass
from typing import Optional

from tools.indexer.types import stable_id


@dataclass
class Diagnostic:
    id: str
    kind: str
    certainty: str
    file: Optional[str]
    line: Optional[int]
    column: Optional[int]
    symbol: Optional[str]
    message: str
    raw: str
    tool: str
    exit_code: Optional[int]

    def as_dict(self):
        d = {
            "id": self.id,
            "kind": self.kind,
            "certainty": self.certainty,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "symbol": self.symbol,
            "message": self.message,
            "raw": self.raw,
            "tool": self.tool,
            "exit_code": self.exit_code,
        }
        return d


def make_diagnostic(kind, certainty, file, line, column, symbol, message, raw, tool, exit_code):
    # Deterministic id from stable inputs
    # Use file/line/message/kind/tool as identity; raw not included to keep id stable across truncation
    fid = file if file is not None else ""
    line_s = str(line) if line is not None else ""
    col_s = str(column) if column is not None else ""
    sym_s = symbol if symbol is not None else ""
    did = stable_id("diag", kind, fid, line_s, col_s, sym_s, message, tool)
    # Cap raw to 2000 chars for determinism (execution already caps 1M, but diagnostic keeps truncated)
    raw_capped = raw[:2000] if raw is not None else ""
    return Diagnostic(
        id=did,
        kind=kind,
        certainty=certainty,
        file=file,
        line=line,
        column=column,
        symbol=symbol,
        message=message,
        raw=raw_capped,
        tool=tool,
        exit_code=exit_code,
    )
