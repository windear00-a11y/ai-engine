"""Project/Code Index — deterministic discovery and filtering (B2).

Walks a workspace relative to the READ-ONLY path-trust boundary and produces
sorted, deterministic file records. Reuses `Workspace.resolve` (the same
symlink/traversal guarantee used by read tools) so the indexer never escapes
the workspace root.

Supported `.gitignore` patterns are applied deterministically. Hard-coded VCS /
dependency / build directories are always excluded regardless of .gitignore.
The indexer only READS bytes; it never executes project code.
"""

import io
import os
import re

from . import languages
from .types import (
    FileRecord, STATUS_OK, STATUS_UNSUPPORTED, STATUS_UNREADABLE,
    STATUS_OVERSIZED, LANG_PYTHON, LANG_CONFIG,
    LANG_JAVASCRIPT, LANG_TYPESCRIPT,
)

# Hard exclusions that apply regardless of .gitignore (deterministic).
HARD_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
    "dist", "build", ".eggs", "egg-info", ".cache", ".ai-engine",
}
# Files always skipped.
HARD_SKIP_FILES = {
    ".gitignore", ".DS_Store", "Thumbs.db",
}

# Cap for reading/parsing a single file (in bytes). Config/data files may
# legitimately be read raw as data; structural parse has its own cap.
MAX_FILE_BYTES = 2 * 1024 * 1024          # 2 MiB for hashing/read
MAX_STRUCTURAL_BYTES = 512 * 1024         # skip structural parse above this
BINARY_SNIFF = 8192                       # bytes to sniff for NUL


class IgnoreMatcher:
    """Deterministic .gitignore style matching (subset of the spec).

    Supports: blank lines, '#' comments, '!' negation (re-inclusion), trailing
    '/' (directory-only), leading '/' (root-anchored), trailing '/**', '*'
    wildcard within a path segment, and '**' for multi-segment matches.
    A file is excluded if the LAST matching pattern says so (negation wins).
    """

    def __init__(self, patterns):
        self.rules = []
        for raw in patterns:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            body = line[1:].strip() if negate else line
            if not body:
                continue
            dir_only = body.endswith("/")
            anchored = body.startswith("/")
            body = body.strip("/")
            self.rules.append((negate, dir_only, anchored, body))

    def _translate(self, pat):
        i = 0
        out = ""
        n = len(pat)
        while i < n:
            c = pat[i]
            if c == "*":
                if i + 1 < n and pat[i + 1] == "*":
                    out += ".*"          # '**' -> match any incl. '/'
                    i += 2
                    continue
                out += "[^/]*"           # single '*' -> within one segment
            elif c == "?":
                out += "[^/]"
            elif c == "[":
                j = i + 1
                if j < n and pat[j] in ("!", "^"):
                    j += 1
                if j < n and pat[j] == "]":
                    j += 1
                while j < n and pat[j] != "]":
                    j += 1
                if j < n:
                    cls = pat[i + 1:j]
                    if cls.startswith("!"):
                        cls = "^" + cls[1:]
                    out += "[" + cls + "]"
                    i = j + 1
                    continue
                out += re.escape(c)
            else:
                out += re.escape(c)
            i += 1
        return out

    def is_ignored(self, rel_path, is_dir):
        """True if the path is excluded (last-match-wins, negation respected).

        ``rel_path`` uses '/' separators, no leading slash.
        """
        ignored = False
        for negate, dir_only, anchored, body in self.rules:
            if dir_only and not is_dir:
                continue
            if body in (".", ""):
                continue
            pat, freq = body, False
            if body.startswith("**/"):
                pat = body[3:]
            elif body.startswith("/"):
                pat = body[1:]
            # directory pattern: match the dir itself or anything beneath
            base_body = body
            if self._segment_match(base_body, rel_path, anchored) or \
               self._match_tree(base_body, rel_path):
                ignored = not negate
        return ignored

    def _segment_match(self, body, rel_path, anchored):
        parts = rel_path.split("/")
        name = parts[-1]
        if "/" in body:
            if anchored:
                if self._glob_match(body, rel_path):
                    return True
            else:
                # match against any suffix that starts at a segment boundary
                # simple: match whole path or the trailing segment chain
                for i in range(len(parts)):
                    cand = "/".join(parts[i:])
                    if self._glob_match(body, cand):
                        return True
            return False
        # single-segment pattern: match any path segment name
        for p in parts:
            if self._glob_match(body, p):
                return True
        return False

    def _match_tree(self, body, rel_path):
        # e.g. pattern "foo" matches "foo" and "foo/**" (everything beneath)
        return rel_path == body or rel_path.startswith(body + "/")

    def _glob_match(self, pat, text):
        r = re.compile("^" + self._translate(pat) + "$")
        return bool(r.match(text))


def discover(root, project, ignore=None, max_bytes=MAX_FILE_BYTES,
             max_structural=MAX_STRUCTURAL_BYTES, skip_dirs=None,
             skip_files=None):
    """Walk ``root`` (an absolute resolved workspace path) deterministically
    and yield FileRecord objects (sorted by rel_path).

    ``ignore`` is an optional IgnoreMatcher built from a workspace .gitignore.
    Only files inside ``root`` are returned (path-trust enforced by caller via
    Workspace.resolve); this function never follows symlinks that escape root.
    """
    skip_dirs = set(skip_dirs or HARD_SKIP_DIRS)
    skip_files = set(skip_files or HARD_SKIP_FILES)
    records = []
    root_real = os.path.realpath(root)

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""

        keep_dirs = []
        for d in dirnames:
            if d in skip_dirs:
                continue
            child = d if not rel_dir else rel_dir + "/" + d
            if ignore and ignore.is_ignored(child, is_dir=True):
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs

        for fn in sorted(filenames):
            if fn in skip_files:
                continue
            rel_path = fn if not rel_dir else rel_dir + "/" + fn
            if ignore and ignore.is_ignored(rel_path, is_dir=False):
                continue
            abs_path = os.path.join(dirpath, fn)
            # symlink escape guard: never index a file whose real path leaves
            # the workspace root (read-only indexer must not reach outside).
            real = os.path.realpath(abs_path)
            if not _path_within(real, root_real):
                continue
            try:
                st = os.stat(abs_path, follow_symlinks=True)
            except OSError:
                records.append(_record_unreadable(project, rel_path, "stat failed"))
                continue
            if st.st_size > max_bytes:
                records.append(_record(
                    project, rel_path, st, STATUS_OVERSIZED,
                    "size %d > cap %d" % (st.st_size, max_bytes)))
                continue
            rec = _build_file_record(project, rel_path, abs_path, st,
                                     max_structural)
            records.append(rec)

    records.sort(key=lambda r: r.rel_path)
    return records


def _path_within(path, root_real):
    """True if the resolved ``path`` is under ``root_real`` (no escape)."""
    if not path:
        return False
    if path == root_real:
        return True
    return path.startswith(root_real + os.sep)


def build_ignore(root):
    """Read the workspace-root .gitignore (if present) into an IgnoreMatcher."""
    path = os.path.join(root, ".gitignore")
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            patterns = f.read().splitlines()
    except OSError:
        return None
    return IgnoreMatcher(patterns)


def _build_file_record(project, rel_path, abs_path, st, max_structural):
    lang = languages.language_for(rel_path)
    sha = _sha256(abs_path)

    if lang == LANG_CONFIG:
        return _record(project, rel_path, st, _status_for_data(rel_path),
                       config_type=languages.config_type_for(rel_path),
                       detail=_read_config(abs_path), sha=sha)

    if lang == LANG_PYTHON:
        if st.st_size > max_structural:
            return _record(project, rel_path, st, STATUS_OVERSIZED,
                           "structural cap %d bytes" % max_structural, sha=sha)
        if _is_binary(abs_path):
            return _record(project, rel_path, st, STATUS_UNREADABLE,
                           "binary file", sha=sha)
        return _record(project, rel_path, st, STATUS_OK, sha=sha)

    if lang in (LANG_JAVASCRIPT, LANG_TYPESCRIPT):
        # Indexed as a file; structural extraction deferred.
        return _record(project, rel_path, st, STATUS_UNSUPPORTED,
                       "structural extraction deferred", sha=sha)

    return _record(project, rel_path, st, STATUS_UNSUPPORTED,
                   "no extractor", sha=sha)


def _status_for_data(rel_path):
    # config files are indexed as data; treat as ok (present)
    return STATUS_OK


def _read_config(abs_path):
    try:
        with io.open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    return text[:10000]


def _record(project, rel_path, st, status, detail="", config_type="", sha=None):
    return FileRecord(
        project=project, rel_path=rel_path,
        language=languages.language_for(rel_path),
        size_bytes=st.st_size, sha256=sha or "",
        mtime_ns=getattr(st, "st_mtime_ns", 0), parse_status=status,
        parse_error=detail, is_test=_is_test_path(rel_path),
        config_type=config_type, detail="")


def _record_unreadable(project, rel_path, err=""):
    return FileRecord(
        project=project, rel_path=rel_path,
        language=languages.language_for(rel_path), size_bytes=0, sha256="",
        mtime_ns=0, parse_status=STATUS_UNREADABLE, parse_error=err or "stat failed")


def _sha256(abs_path):
    h = __import__("hashlib").sha256()
    try:
        with io.open(abs_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        pass
    return h.hexdigest()


def _is_binary(abs_path):
    try:
        with io.open(abs_path, "rb") as f:
            head = f.read(BINARY_SNIFF)
    except OSError:
        return False
    return b"\x00" in head


def _is_test_path(rel_path):
    base = os.path.basename(rel_path)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    parts = rel_path.split("/")
    test_dirs = {"test", "tests", "spec", "__tests__"}
    return bool(parts and parts[-2] in test_dirs) if len(parts) >= 2 else False
