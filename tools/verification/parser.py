"""Deterministic parser — Layer 5 5A-1.

Parses execution results into normalized Diagnostics.
Read-only, no subprocess, no eval, no file writes, no mutation.

Precedence:
1. syntax_error
2. unittest (failure/error)
3. traceback
4. fallback build_error

Grounding via IndexQueries.find_file / find_symbol — FACT only if verified.
"""

import os
import re

from tools.indexer.query import IndexQueries
from .diagnostic import make_diagnostic


# Conservative regexes — must include explicit file + line to avoid invention
_RE_SYNTAX_COLON = re.compile(
    r'^(?P<file>[^\s:]+\.py):(?P<line>\d+):\s*(?P<msg>SyntaxError[^\n]*)',
    re.MULTILINE,
)
_RE_SYNTAX_FILE_LINE = re.compile(
    r'File\s+"(?P<file>[^"]+\.py)"\s*,\s*line\s+(?P<line>\d+)',
    re.MULTILINE,
)
_RE_SYNTAX_MSG = re.compile(r'SyntaxError:\s*(?P<msg>[^\n]+)', re.MULTILINE)

_RE_UNITTEST_FAIL = re.compile(
    r'^(FAIL|ERROR):\s+(?P<test>\S+)\s+\((?P<module>[^)]+)\)',
    re.MULTILINE,
)
_RE_TRACEBACK_FILE = re.compile(
    r'File\s+"(?P<file>[^"]+)"\s*,\s*line\s+(?P<line>\d+)(?:\s*,\s*in\s+(?P<symbol>[^\n]+))?',
    re.MULTILINE,
)
_RE_EXCEPTION_TAIL = re.compile(
    r'^(?P<exc>[A-Za-z_][A-Za-z0-9_\.]*Error|Exception|AssertionError):\s*(?P<msg>[^\n]*)',
    re.MULTILINE,
)
_RE_FLAKE_SKIP = False  # flake8/ruff not in 5A-1


def _ground_file(q, rel_path):
    if q is None or rel_path is None:
        return None, False
    # rel_path may be absolute or like "pkg/a.py" or "./pkg/a.py"
    # Normalize to workspace-relative for query
    norm = rel_path
    # If absolute, try to make relative to workspace root is out of scope here; just check as is
    # IndexQueries expects rel_path exactly as stored (e.g., "pkg/a.py")
    # Try direct, then basename, then stripped leading ./ or /
    candidates = [norm, norm.lstrip("./"), norm.lstrip("/")]
    for c in candidates:
        rec = q.find_file(c)
        if rec is not None:
            return c, True
        # Also try to find by suffix match against file_manifest? Conservative: only direct
    return None, False


def _ground_symbol(q, symbol):
    if q is None or symbol is None:
        return None, False
    # symbol may be qualified like pkg.a.foo or just foo; try exact qname
    recs = q.find_symbols_by_name(symbol.split(".")[-1])
    exact = [s for s in recs if s.get("qname") == symbol]
    if exact:
        return symbol, True
    # Try find_symbol directly
    direct = q.find_symbol(symbol)
    if isinstance(direct, list) and direct:
        return symbol, True
    if isinstance(direct, dict) and direct:
        return symbol, True
    return None, False


def parse(tool, raw, workspace_root=None, db_path=None):
    """Parse raw execution result into Diagnostics.

    :param tool: str like "python", "unittest", "flake8", etc.
    :param raw: dict with keys exit_code, stdout, stderr, error, command
    :param workspace_root: for IndexQueries grounding
    :param db_path: optional explicit index DB path
    :return: list[Diagnostic] (as objects, caller may .as_dict())
    """
    if raw is None or not isinstance(raw, dict):
        return []
    try:
        exit_code = raw.get("exit_code")
        stdout = raw.get("stdout") or ""
        stderr = raw.get("stderr") or ""
        error = raw.get("error") or ""
        # Combine stdout+stderr+error for parsing, but preserve raw for diagnostic
        combined = "\n".join([s for s in [stdout, stderr, error] if s])
        raw_capped = combined[:2000]
        # Resolve IndexQueries if possible
        q = None
        try:
            if db_path and os.path.exists(db_path):
                qq = IndexQueries(db_path)
                if qq.project is not None:
                    q = qq
            elif workspace_root:
                cand = os.path.join(os.path.realpath(workspace_root), ".ai-engine", "project_index.db")
                if os.path.exists(cand):
                    qq = IndexQueries(cand)
                    if qq.project is not None:
                        q = qq
        except Exception:
            q = None

        diagnostics = []
        seen_ids = set()

        def add(diag):
            if diag.id not in seen_ids:
                seen_ids.add(diag.id)
                diagnostics.append(diag)

        # Precedence 1: syntax_error — explicit file:line SyntaxError
        # Pattern 1: pkg/a.py:12: SyntaxError: ...
        for m in _RE_SYNTAX_COLON.finditer(combined):
            f = m.group("file")
            line = int(m.group("line"))
            msg = m.group("msg").strip()
            gf, ok = _ground_file(q, f)
            if ok:
                d = make_diagnostic(
                    kind="syntax_error",
                    certainty="fact",
                    file=gf,
                    line=line,
                    column=None,
                    symbol=None,
                    message=msg,
                    raw=raw_capped,
                    tool=tool,
                    exit_code=exit_code,
                )
                add(d)
            else:
                # Heuristic if file not grounded, still emit but as heuristic with file=null per rule?
                # Spec: if file cannot be grounded: file=null, certainty=heuristic
                # But for syntax_error, we have explicit file; we mark heuristic with null to avoid invention
                d = make_diagnostic(
                    kind="syntax_error",
                    certainty="heuristic",
                    file=None,
                    line=None,
                    column=None,
                    symbol=None,
                    message=msg,
                    raw=raw_capped,
                    tool=tool,
                    exit_code=exit_code,
                )
                # Only emit heuristic if no fact already; but we prefer fact, so skip heuristic if fact exists?
                # For now emit heuristic only if no fact for same message
                if not any(x.kind == "syntax_error" and x.certainty == "fact" for x in diagnostics):
                    add(d)
            # Only first match deterministic; but allow multiple distinct files/lines
        if diagnostics:
            # If syntax_error found, return sorted deterministic
            diagnostics.sort(key=lambda d: (d.file or "", d.line or 0, d.id))
            return diagnostics

        # Precedence 2: unittest — FAIL/ERROR with traceback
        # Example: FAIL: test_foo (tests.test_a.TestA)
        # Then traceback File "...", line N
        unittest_matches = list(_RE_UNITTEST_FAIL.finditer(combined))
        if unittest_matches:
            for um in unittest_matches:
                test_name = um.group("test")
                # Find traceback file/line following this unittest header
                # Search from um.end() onwards
                tail = combined[um.end(): um.end() + 2000]
                fm = _RE_TRACEBACK_FILE.search(tail)
                exc_m = _RE_EXCEPTION_TAIL.search(tail)
                file_val = None
                line_val = None
                symbol_val = None
                certainty = "heuristic"
                msg = um.group(0)
                if fm:
                    raw_file = fm.group("file")
                    raw_line = int(fm.group("line"))
                    raw_sym = fm.group("symbol")
                    gf, ok = _ground_file(q, raw_file)
                    if ok:
                        file_val = gf
                        line_val = raw_line
                        certainty = "fact"
                        if raw_sym:
                            gs, ok2 = _ground_symbol(q, raw_sym.strip())
                            if ok2:
                                symbol_val = gs
                            else:
                                symbol_val = raw_sym.strip()
                        else:
                            symbol_val = test_name
                    else:
                        # heuristic: file not grounded
                        file_val = None
                        line_val = None
                        symbol_val = test_name
                        certainty = "heuristic"
                    if exc_m:
                        msg = f"{exc_m.group('exc')}: {exc_m.group('msg').strip()}"
                    else:
                        msg = f"unittest {um.group(1)} {test_name}"
                else:
                    # No traceback file, heuristic
                    file_val = None
                    line_val = None
                    symbol_val = test_name
                    msg = f"unittest {um.group(1)} {test_name}"
                    if exc_m:
                        msg = f"{exc_m.group('exc')}: {exc_m.group('msg').strip()}"
                kind = "test_failure" if um.group(1) == "FAIL" else "test_error"
                d = make_diagnostic(
                    kind=kind,
                    certainty=certainty,
                    file=file_val,
                    line=line_val,
                    column=None,
                    symbol=symbol_val,
                    message=msg,
                    raw=raw_capped,
                    tool=tool,
                    exit_code=exit_code,
                )
                add(d)
            diagnostics.sort(key=lambda d: (d.file or "", d.line or 0, d.id))
            return diagnostics

        # Precedence 3: generic traceback — File "...", line N + Exception
        # Find all File line occurrences
        for fm in _RE_TRACEBACK_FILE.finditer(combined):
            raw_file = fm.group("file")
            raw_line = int(fm.group("line"))
            raw_sym = fm.group("symbol")
            # Look for exception tail after this file line
            tail_start = fm.end()
            tail = combined[tail_start: tail_start + 500]
            exc_m = _RE_EXCEPTION_TAIL.search(tail)
            msg = exc_m.group(0).strip() if exc_m else f"Traceback at {raw_file}:{raw_line}"
            gf, ok = _ground_file(q, raw_file)
            if ok:
                file_val = gf
                line_val = raw_line
                certainty = "fact"
                symbol_val = None
                if raw_sym:
                    gs, ok2 = _ground_symbol(q, raw_sym.strip())
                    if ok2:
                        symbol_val = gs
                    else:
                        # Even if not grounded, keep raw symbol as heuristic? But spec says symbol FACT only if verified
                        symbol_val = None
            else:
                file_val = None
                line_val = None
                certainty = "heuristic"
                symbol_val = None
                # Per rule: heuristic with file=null
                msg = f"Traceback {raw_file}:{raw_line} (ungrounded) {msg}"
            d = make_diagnostic(
                kind="traceback",
                certainty=certainty,
                file=file_val,
                line=line_val,
                column=None,
                symbol=symbol_val,
                message=msg,
                raw=raw_capped,
                tool=tool,
                exit_code=exit_code,
            )
            add(d)
        if diagnostics:
            diagnostics.sort(key=lambda d: (d.file or "", d.line or 0, d.id))
            return diagnostics

        # Precedence 4: fallback
        if exit_code is not None and exit_code != 0:
            # Heuristic build_error with file=null
            msg = (stderr or stdout or error or f"command failed with exit {exit_code}").strip().split("\n")[0][:500]
            if not msg:
                msg = f"command failed with exit {exit_code}"
            d = make_diagnostic(
                kind="build_error",
                certainty="heuristic",
                file=None,
                line=None,
                column=None,
                symbol=None,
                message=msg,
                raw=raw_capped,
                tool=tool,
                exit_code=exit_code,
            )
            add(d)
            return diagnostics

        # exit_code ==0 and no pattern -> no diagnostic
        return []
    except Exception:
        # Parser must not throw
        return []
