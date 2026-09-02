"""Deterministic fix-rule matcher — Layer 5 5B (E302, F401, SyntaxError colon).

Proposal-only, no file writes, no DB mutation, no child process, no AI.

Architecture: Diagnostic -> FixProposal (data only) -> dispatch -> Planner.

Determinism contract: every rule re-reads the source file, derives the exact
old_text/new_text from the current on-disk state, and computes a full-content
expected_hash. Same diagnostic + same source state => identical proposal and id.
Rules never guess; they return [] unless the exact transformation is proven.
"""

import os
import re
import ast
import difflib
import hashlib

from tools.indexer.query import IndexQueries
from tools.coding.fs import Workspace
from .fix_proposal import make_fix_proposal

RULE_ID_E302 = "e302_blank_lines"
RULE_ID_F401 = "f401_unused_import"
RULE_ID_SYNTAX_COLON = "syntax_error_missing_colon"

# Real parser emits message as "<CODE>: <text>" (see parser.py:186).
_RE_CODE_PREFIX = re.compile(r"^([A-Z]\d+):", re.MULTILINE)
# F401 message text: "F401: '<name>' imported but unused"
_RE_F401_NAME = re.compile(r"F401:\s*['\"]([^'\"]+)['\"]")


def _diagnostic_code(diag):
    """The leading pycodestyle/ruff CODE (e.g. 'E302') if message starts 'CODE:'.

    Returns None when the message has no structured code prefix, so prose that
    merely mentions a code string is never treated as that rule (fixes the
    audit LOW finding: loose substring matching).
    """
    m = _RE_CODE_PREFIX.match((diag.message or "").strip())
    return m.group(1) if m else None


def _is_lint_code(diag, code):
    """Exact structural lint gate: kind==lint AND certainty==fact AND CODE prefix."""
    if diag.kind != "lint":
        return False
    if diag.certainty != "fact":
        return False
    return _diagnostic_code(diag) == code


def _is_syntax_colon(diag):
    """Exact syntax gate: kind==syntax_error, certainty==fact, message mentions
    'expected' and a missing colon explicitly."""
    if diag.kind != "syntax_error":
        return False
    if diag.certainty != "fact":
        return False
    msg = diag.message or ""
    return "expected" in msg and ":" in msg


# -- shared source/workspace helpers ----------------------------------------

def _resolve(db_path, workspace_root, rel_path):
    """grounded + within-workspace check. Returns (q, abs_path) or (None, None)."""
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
    if q is None or q.find_file(rel_path) is None:
        return None, None
    if not _is_within_workspace(workspace_root, rel_path):
        return None, None
    try:
        ws = Workspace(workspace_root) if workspace_root else None
        abs_path = ws.resolve(rel_path) if ws is not None else rel_path
    except Exception:
        return None, None
    return q, abs_path


def _read_source(abs_path):
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def _hash_content(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _is_within_workspace(workspace_root, rel_path):
    if not workspace_root or not rel_path:
        return False
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        return False
    if ".." in rel_path.split("/"):
        return False
    try:
        ws = Workspace(workspace_root)
        abs_path = ws.resolve(rel_path)
        real_root = os.path.realpath(workspace_root)
        real_abs = os.path.realpath(abs_path)
        return real_abs == real_root or real_abs.startswith(real_root + os.sep)
    except Exception:
        return False


def _build_diff_preview(rel_path, old_lines, new_lines):
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="a/" + rel_path,
        tofile="b/" + rel_path,
        lineterm="",
    )
    return "\n".join(list(diff))[:2000]


# ============================================================================
# E302 — missing blank lines before a top-level def
# ============================================================================

def match_e302(diagnostic, workspace_root, db_path=None):
    if not _is_lint_code(diagnostic, "E302"):
        return []
    if diagnostic.file is None or diagnostic.line is None:
        return []
    q, abs_path = _resolve(db_path, workspace_root, diagnostic.file)
    if q is None or abs_path is None:
        return []
    content = _read_source(abs_path)
    if content is None:
        return []
    lines = content.splitlines(keepends=True)
    line_idx = diagnostic.line - 1
    if line_idx < 0 or line_idx >= len(lines):
        return []
    if diagnostic.line == 1:
        return []
    # Already-correct: immediate predecessor already a blank line -> no fix
    prev_line = lines[line_idx - 1] if line_idx - 1 >= 0 else ""
    if prev_line.strip() == "":
        return []
    # Ambiguity: target line must be unique for a deterministic file.edit
    target_line_content = lines[line_idx]
    if content.count(target_line_content) != 1:
        return []
    expected_hash = _hash_content(content)
    old_text = target_line_content
    new_text = "\n" + old_text
    new_content_lines = lines[:line_idx] + ["\n"] + lines[line_idx:]
    diff_preview = _build_diff_preview(diagnostic.file, lines, new_content_lines)
    proposal = make_fix_proposal(
        rule_id=RULE_ID_E302,
        diagnostic_id=diagnostic.id,
        file=diagnostic.file,
        line=diagnostic.line,
        column=diagnostic.column,
        old_text=old_text,
        new_text=new_text,
        diff_preview=diff_preview,
        expected_hash=expected_hash,
        certainty="fact",
        risk="low",
        description=f"Insert one blank line before {diagnostic.file}:{diagnostic.line} (E302)",
        expected_verification=f"flake8 {diagnostic.file} should not emit E302 at line {diagnostic.line} after apply",
    )
    return [proposal]


# ============================================================================
# F401 — unused import removal (narrow, deterministic, conservative)
# ============================================================================

def _module_defined_names(ast_tree):
    """Top-level defined symbol names (defs/classes/assigns) to detect shadowing."""
    names = set()
    for node in ast_tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _has_all_assignment(ast_tree):
    for node in ast_tree.body:
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    return True
    return False


def _binding_unused(ast_tree, binding_name, q, rel_path):
    """Prove the binding is unused via AST Name walk + index symbol check.

    Returns True (unused, safe) or False (used / cannot be proven unused).

    The import statement itself never produces ``ast.Name`` nodes (aliases are
    stored as strings on ``ast.alias``), so any ``ast.Name`` referencing the
    binding -- including on the import's own physical line -- is a genuine
    usage and is not skipped.
    """
    for node in ast.walk(ast_tree):
        if isinstance(node, ast.Name) and node.id == binding_name:
            return False
    # Top-level shadowing: a def/class/assign with the same name is ambiguous
    if binding_name in _module_defined_names(ast_tree):
        return False
    # Existing indexed symbols: an index symbol with the same short name in this
    # file is a conflict/usage signal -> do not remove
    try:
        for s in q.symbols_in_file(rel_path):
            if s.get("name") == binding_name:
                return False
    except Exception:
        pass
    return True


def _line_is_single_statement(raw_line, node):
    """True only when ``node`` is the ONLY statement on its physical line.

    ``raw_line`` is a ``splitlines(keepends=True)`` element. The check requires
    whitespace-only text before the statement's start column and only
    whitespace/comment after its end column. Anything else (a second statement
    such as ``import os; x = 1``) makes removal ambiguous, so this returns
    False and the rule fails closed with ``[]``. Missing column metadata or
    columns outside the line also return False (no guessing).
    """
    col = getattr(node, "col_offset", None)
    end_col = getattr(node, "end_col_offset", None)
    if not isinstance(col, int) or not isinstance(end_col, int):
        return False
    body = raw_line.rstrip("\r\n")
    if col < 0 or end_col <= col or end_col > len(body):
        return False
    if body[:col].strip() != "":
        return False
    suffix = body[end_col:]
    if suffix.strip() == "":
        return True
    return suffix.lstrip().startswith("#")


def match_f401(diagnostic, workspace_root, db_path=None):
    if not _is_lint_code(diagnostic, "F401"):
        return []
    if diagnostic.file is None or diagnostic.line is None:
        return []
    m = _RE_F401_NAME.search(diagnostic.message or "")
    if not m:
        return []
    binding_name = m.group(1)
    q, abs_path = _resolve(db_path, workspace_root, diagnostic.file)
    if q is None or abs_path is None:
        return []
    content = _read_source(abs_path)
    if content is None:
        return []
    # ast.parse the CURRENT source (deterministic, read-only)
    try:
        tree = ast.parse(content, filename=diagnostic.file)
    except SyntaxError:
        return []
    # Locate a top-level single-line import spanning the diagnostic line
    import_node = None
    import_line = diagnostic.line
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if getattr(node, "lineno", None) == import_line and \
           getattr(node, "end_lineno", None) == import_line:
            import_node = node
            break
    if import_node is None:
        return []
    # Multi-name import -> conservative []
    if len(import_node.names) != 1:
        return []
    alias = import_node.names[0]
    # Wildcard / star import -> never propose removal
    if alias.name == "*":
        return []
    # Resolve the actual local binding name for the flagged import
    if alias.asname:
        local_binding = alias.asname
    elif isinstance(import_node, ast.Import):
        local_binding = alias.name.split(".")[0]
    else:
        local_binding = alias.name
    # The flagged name in the diagnostic must correspond to this binding
    # (for `from x import os`, binding == 'os'; for aliased, message names the
    # local or original — accept either for a single-name import)
    if local_binding != binding_name and alias.name != binding_name:
        return []
    # __all__ present -> cannot safely prove unused
    if _has_all_assignment(tree):
        return []
    if not _binding_unused(tree, local_binding, q, diagnostic.file):
        return []
    # Exact minimal transform: the physical line must BE the import statement
    # (plus optional trailing comment). A shared line (e.g. 'import os; x = 1')
    # cannot be reduced to an import-only patch without text splicing, so it
    # fails closed with [] rather than guessing.
    lines = content.splitlines(keepends=True)
    line_idx = import_line - 1
    if line_idx < 0 or line_idx >= len(lines):
        return []
    if not _line_is_single_statement(lines[line_idx], import_node):
        return []
    old_text = lines[line_idx]
    if content.count(old_text) != 1:
        # ambiguous duplicate line -> []
        return []
    new_text = ""
    expected_hash = _hash_content(content)
    new_lines = lines[:line_idx] + lines[line_idx + 1:]
    diff_preview = _build_diff_preview(diagnostic.file, lines, new_lines)
    proposal = make_fix_proposal(
        rule_id=RULE_ID_F401,
        diagnostic_id=diagnostic.id,
        file=diagnostic.file,
        line=import_line,
        column=diagnostic.column,
        old_text=old_text,
        new_text=new_text,
        diff_preview=diff_preview,
        expected_hash=expected_hash,
        certainty="fact",
        risk="low",
        description=f"Remove unused import {local_binding!r} at {diagnostic.file}:{import_line} (F401)",
        expected_verification=f"flake8 {diagnostic.file} should not emit F401 at line {import_line} after apply",
    )
    return [proposal]


# ============================================================================
# SyntaxError "expected ':'" — proven missing-colon insertion
# ============================================================================

def match_syntax_colon(diagnostic, workspace_root, db_path=None):
    if not _is_syntax_colon(diagnostic):
        return []
    if diagnostic.file is None or diagnostic.line is None:
        return []
    q, abs_path = _resolve(db_path, workspace_root, diagnostic.file)
    if q is None or abs_path is None:
        return []
    content = _read_source(abs_path)
    if content is None:
        return []
    lines = content.splitlines(keepends=True)
    line_idx = diagnostic.line - 1
    if line_idx < 0 or line_idx >= len(lines):
        return []
    old_line = lines[line_idx]
    stripped = old_line.rstrip("\r\n")
    # Already ends with ':' (or is blank) -> no missing-colon fix
    if not stripped or stripped.endswith(":"):
        return []
    if content.count(old_line) != 1:
        return []
    new_line = stripped + ":\n"
    # Deterministic proof: re-parse the full source with the colon inserted;
    # only propose if the file becomes syntactically valid (no guessing).
    new_content = _replace_line(content, line_idx, new_line)
    try:
        ast.parse(new_content, filename=diagnostic.file)
    except (SyntaxError, ValueError):
        return []
    expected_hash = _hash_content(content)
    new_lines = lines[:]
    new_lines[line_idx] = new_line
    diff_preview = _build_diff_preview(diagnostic.file, lines, new_lines)
    proposal = make_fix_proposal(
        rule_id=RULE_ID_SYNTAX_COLON,
        diagnostic_id=diagnostic.id,
        file=diagnostic.file,
        line=diagnostic.line,
        column=diagnostic.column,
        old_text=old_line,
        new_text=new_line,
        diff_preview=diff_preview,
        expected_hash=expected_hash,
        certainty="fact",
        risk="low",
        description=f"Insert missing ':' at {diagnostic.file}:{diagnostic.line} (SyntaxError)",
        expected_verification=f"python {diagnostic.file} should compile without SyntaxError after apply",
    )
    return [proposal]


def _replace_line(content, line_idx, new_line):
    lines = content.splitlines(keepends=True)
    lines[line_idx] = new_line
    return "".join(lines)


# ============================================================================
# Dispatch
# ============================================================================

_RULES = (match_e302, match_f401, match_syntax_colon)


def _match_single(diagnostic, workspace_root, db_path):
    """Run every rule; each inspects the diagnostic and returns [] if not its
    concern. No rule executes another rule's transformation."""
    out = []
    for rule in _RULES:
        try:
            out.extend(rule(diagnostic, workspace_root, db_path))
        except Exception:
            # fail closed
            continue
    return out


def dispatch(diagnostics, workspace_root=None, db_path=None):
    """Deterministic entry point: accepts a single Diagnostic or an iterable.

    Returns zero or more FixProposal objects:
    - unsupported / heuristic / malformed diagnostic -> []
    - multiple diagnostics -> deterministic ordering
    - duplicate proposals -> deterministic deduplication by id
    """
    if diagnostics is None:
        return []
    if isinstance(diagnostics, (list, tuple)):
        items = diagnostics
    else:
        items = [diagnostics]
    seen = {}
    for diag in items:
        if diag is None:
            continue
        for p in _match_single(diag, workspace_root, db_path):
            seen[p.id] = p
    result = list(seen.values())
    result.sort(key=lambda p: (p.file, p.patch["line"], p.rule_id, p.id))
    return result


def match(diagnostic, workspace_root=None, db_path=None):
    """Backward-compatible single-diagnostic matcher (5B-1a contract)."""
    try:
        return dispatch(diagnostic, workspace_root=workspace_root, db_path=db_path)
    except Exception:
        return []
