"""Deterministic knowledge candidate extractor.

Turns EXTRACTED :class:`~knowledge_compiler.types.Document` structure into
CANDIDATE-state :class:`~knowledge_compiler.types.Candidate` objects. Only
high-confidence, explicitly-evidenced items become candidates:

* API declarations (``.. function::``, ``.. class::``, ``.. method::``, ...)
  and C API declarations (``.. c:function::``, ``.. c:type::``,
  ``.. c:macro::``, ``.. c:member::``, ``.. c:var::``, ...)
* inheritance (base classes in an explicit ``.. class:: Name(Base)``)
* definitions (entries in an explicit ``.. glossary::``)
* dependencies (explicit ``import`` statements inside code blocks, with REPL
  prompts stripped and module name / origin captured; stdlib (including dotted
  submodules such as ``urllib.request`` / ``collections.abc``) is "high";
  local/example modules stay "unknown"/low and are NOT claimed to be external;
  ``from __future__ import ...`` is never a dependency)
* procedure sections (headings with explicit how-to markers AND procedural
  structure in their body -- a heading alone is never a procedure, and
  whole-document title chunks are not procedures)
* code examples (explicit literal / ``.. code-block::`` / doctest blocks;
  test/generated configuration (``testsetup``, ``testcode``, ``testoutput``,
  ``productionlist``) and shell/session/text transcripts are excluded)
* references (explicit inline knowledge roles such as ``:func:``, ``:mod:``,
  ``:term:``; ``:ref:`` navigation anchors and UI/environment roles
  (``:file:``, ``:kbd:``, ``:option:``) stay in EXTRACTED but never become
  candidates, and identical ``(role, target)`` references are deduplicated
  within a document)

Everything else stays as extracted evidence. This module NEVER summarizes or
paraphrases prose -- a candidate's summary and evidence are always taken
verbatim from the document. Ambiguity is preserved as source evidence, never
guessed at.
"""

import re

from knowledge_compiler.types import (
    Chunk, Candidate, SourceLocation, stable_id,
    CHUNK_SIGNATURE, CHUNK_CODE, CHUNK_SECTION, CHUNK_DIRECTIVE,
    CHUNK_REFERENCE, CHUNK_TITLE, CHUNK_LIST, CANDIDATE,
    CAND_API, CAND_INHERITANCE, CAND_DEFINITION, CAND_DEPENDENCY,
    CAND_PROCEDURE, CAND_CODE_EXAMPLE, CAND_REFERENCE,
)

# API-ish directives that become a single api_declaration candidate.
_API_KINDS = {
    "function", "class", "method", "data", "attribute", "exception",
    "decorator", "staticmethod", "classmethod", "abstractmethod",
    "c:function", "c:macro", "c:type", "c:data", "c:member", "c:var",
    "c:struct", "c:enum", "c:enumerator", "module", "opcode", "2to3fixer",
}

# Explicit how-to markers in section headings (matched case-insensitively on
# the heading's first words). A heading alone is NEVER sufficient evidence for
# a procedure: it must also have procedural structure in its body (code or a
# list). Markers that describe navigation/informational sections ("more on",
# "using", "an informal", "first steps", "invoking", ...) are deliberately
# excluded because they are not evidence of a procedure.
_PROCEDURE_MARKERS = (
    "how to", "defining ", "creating ", "making ", "writing ", "reading ",
    "building ", "running ", "installing ", "organizing ", "extending ",
    "embedding ", "working with ",
)

# Inline role references that are treated as explicit knowledge references.
# Documentation-navigation (`ref`) and UI/environment roles (`file`, `kbd`,
# `option`) are intentionally NOT knowledge-bearing; they stay in the EXTRACTED
# layer for provenance but never become candidates on their own.
_KNOWLEDGE_ROLES = {
    "func", "class", "mod", "meth", "data", "exc", "attr",
    "term", "keyword", "const", "envvar",
    "c:func", "c:type", "c:member", "c:data", "c:macro", "c:var",
    "py:func", "py:class", "py:meth", "py:mod", "py:data", "py:attr",
}

# Code-block sources that are test/fixture/generated configuration. They stay
# in the EXTRACTED layer (with provenance) but never become user-facing
# ``code_example`` candidates. ``testsetup`` was already excluded; the same
# applies to ``testcode`` / ``testoutput`` (doctest machinery) and
# ``productionlist`` (grammar productions).
_NON_EXAMPLE_SOURCES = {"testsetup", "testcode", "testoutput",
                        "productionlist"}

# ``.. language::`` values (or content patterns) that mark a code chunk as a
# shell/session/text transcript rather than Python code.
_SHELL_LANGUAGES = {"shell", "shell-session", "console", "text", "none"}


def _is_python_example(code, meta):
    """Deterministic Python-example check for a code chunk."""
    if meta.get("language") in _SHELL_LANGUAGES:
        return False
    seen_nonblank = False
    for ln in code.splitlines():
        s = ln.strip()
        if not s:
            continue
        if not seen_nonblank:
            seen_nonblank = True
            if s.startswith("$ "):    # shell command
                return False
            if s.startswith("#!"):    # shebang script
                return False
        elif s.startswith("$ "):      # shell prompt anywhere in the block
            return False
    return True

# Deterministic top-level standard-library module names. An import whose module
# is not in this set is NOT automatically claimed to be an external dependency;
# it is marked origin='unknown' with low confidence (local/example modules such
# as the tutorial's ``fibo`` fall in this category).
_STDLIB_TOP_LEVEL = frozenset("""
abc aifc argparse array ast asyncio atexit audioop base64 bdb binascii bisect
builtins bz2 calendar cgi cgitb chunk cmath cmd code codecs codeop collections
colorsys compileall concurrent configparser contextlib contextvars copy copyreg
csv ctypes curses dataclasses datetime dbm decimal difflib dis doctest email
encodings ensurepip enum errno faulthandler fcntl filecmp fileinput fnmatch
fractions ftplib functools gc getopt getpass gettext glob graphlib grp gzip
hashlib heapq hmac html http idlelib imaplib imghdr importlib inspect io
ipaddress itertools json keyword lib2to3 linecache locale logging lzma mailbox
mailcap marshal math mimetypes mmap modulefinder msilib msvcrt multiprocessing
netrc nntplib numbers operator optparse os pathlib pdb pickle pickletools
pipes pkgutil platform plistlib poplib posix pprint profile pstats pty pwd
py_compile pyclbr pydoc queue quopri random re readline reprlib resource
rlcompleter runpy sched secrets select selectors shelve shlex shutil signal
site smtplib socket socketserver sqlite3 ssl stat statistics string stringprep
struct subprocess sunau symtable sys sysconfig syslog tabnanny tarfile tempfile
termios textwrap threading time timeit tkinter token tokenize tomllib trace
traceback tracemalloc tty turtle turtledemo types typing unicodedata unittest
urllib uu uuid venv warnings wave weakref webbrowser winreg winsound wsgiref
xdrlib xml xmlrpc zipapp zipfile zipimport zlib zoneinfo
""".split())

_IMPORT_RE = re.compile(
    r"^(?:>>>\s*)?(from\s+[\w.]+(?:\s+import\s+.*)?|import\s+[\w.\s,]+)$")


def _is_stdlib_module(module):
    """Deterministic stdlib check that also covers dotted submodules.

    ``urllib.request``, ``collections.abc`` and ``concurrent.futures`` are
    recognized via their top-level component; ``fibo`` / ``spam`` / ``emb``
    (local example modules) and third-party-only roots are not.
    """
    return module.split(".", 1)[0] in _STDLIB_TOP_LEVEL


def _define_dependency(module):
    """Dependency policy for an imported module name.

    Returns (origin, confidence) or ``None`` when the line is not a real
    module dependency (e.g. ``from __future__ import ...``).
    """
    if module is None or module == "__future__":
        return None
    if _is_stdlib_module(module):
        return "stdlib", "high"
    return "unknown", "low"


def _normalize_import(line):
    """Strip REPL prompts and return (normalized_import, module_name).

    ``>>> import fibo`` becomes ``("import fibo", "fibo")``; ``>>> from
    collections import deque`` becomes ``("from collections import deque",
    "collections")``. Returns ``(line, None)`` when no module can be resolved.
    """
    s = line.strip()
    if s.startswith(">>>") or s.startswith("..."):
        s = s[3:].lstrip()
    s = s.strip()
    module = None
    if s.startswith("from "):
        head = re.split(r"\s+import\s+", s[len("from "):], maxsplit=1)[0]
        module = head.strip().rstrip(".") or None
    elif s.startswith("import "):
        first = re.split(r"[,\s]", s[len("import "):], maxsplit=1)[0]
        first = re.split(r"\s+as\s+", first, maxsplit=1)[0]
        module = first.strip() or None
    return s, module


def _base_names(signature):
    """Extract explicit base-class names from a class signature ``Name(...)``.

    Returns only tokens that are plain identifiers / dotted names -- keyword
    arguments (``key=value``), positional-only markers (``/``), ``*args`` /
    ``**kwargs``, and the ``metaclass`` slot are never treated as bases.
    """
    inner = ""
    for m in re.finditer(r"\((.*)\)", signature):
        inner = m.group(1)
        break
    if not inner.strip():
        return []
    bases = []
    depth = 0
    current = []
    for ch in inner:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            bases.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        bases.append("".join(current).strip())
    out = []
    for token in bases:
        if not token:
            continue
        if token in ("/", "*",):
            continue
        if token.startswith("*"):
            continue
        if "=" in token:               # keyword argument or metaclass=
            continue
        if not re.fullmatch(r"[\w.]+", token):
            continue
        out.append(token)
    return out


def _glossary_definitions(body):
    """Parse explicit ``.. glossary::`` body into (term, definition) pairs.

    Term lines sit at the base indentation of the glossary content; the
    definition is the following more-indented lines. ``**term**`` markers are
    stripped for the summary only -- evidence keeps the raw text.
    """
    if not body:
        return []
    lines = body.split("\n")
    base = None
    for ln in lines:
        if ln.strip():
            base = len(ln) - len(ln.lstrip(" \t"))
            break
    if base is None:
        return []
    entries = []
    term = None
    def_lines = []
    for ln in lines:
        indent = len(ln) - len(ln.lstrip(" \t")) if ln.strip() else None
        if ln.strip() and indent == base and not ln.lstrip().startswith((": ", ":option", ":class")):
            if term is not None:
                entries.append((term, "\n".join(def_lines).strip()))
            term = ln.strip()
            def_lines = []
        elif ln.strip():
            def_lines.append(ln.strip())
        else:
            def_lines.append("")
    if term is not None:
        entries.append((term, "\n".join(def_lines).strip()))
    return entries


def _clean_term(term):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", term).strip()


def extract_candidates(doc):
    """Return a list of Candidate objects for one Document (CANDIDATE state)."""
    cands = []
    seen = set()
    seen_refs = set()

    # Body extent of every section/title chunk: chunks until the next
    # section/title at the same-or-shallower level (or the document end).
    struct = [(i, c) for i, c in enumerate(doc.chunks)
              if c.kind in (CHUNK_SECTION, CHUNK_TITLE)]
    body_end = {}
    for pos, (i, c) in enumerate(struct):
        level = c.meta.get("level", 0)
        end = len(doc.chunks)
        for j, nc in struct[pos + 1:]:
            if nc.kind == CHUNK_TITLE or nc.meta.get("level", 0) <= level:
                end = j
                break
        body_end[i] = end

    def has_procedural_body(start, end):
        # A heading alone is never a procedure: require an actionable
        # sequence/example structure (a code block or a list) in its body.
        for k in range(start, end):
            if doc.chunks[k].kind in (CHUNK_CODE, CHUNK_LIST):
                return True
        return False

    def add(kind, summary, evidence, location, section_path, meta=None,
            confidence="high"):
        cid = stable_id(doc.rel_path, kind, location.line_start,
                        location.line_end, evidence, summary)
        key = (kind, summary, evidence, location.line_start)
        if key in seen:
            return
        seen.add(key)
        # Top-level items (before any section heading) belong to the document
        # itself; root them under the document title for provenance.
        if not section_path and doc.title:
            section_path = [doc.title]
        cands.append(Candidate(
            kind=kind, summary=summary, evidence=evidence, document=doc.rel_path,
            section_path=list(section_path), location=location,
            confidence=confidence, meta=meta or {}, state=CANDIDATE,
            candidate_id=cid))

    for idx, chunk in enumerate(doc.chunks):
        if chunk.kind == CHUNK_SIGNATURE:
            directive = chunk.meta.get("directive")
            arg = chunk.meta.get("arg", "")
            name = chunk.meta.get("name") or arg
            if directive in _API_KINDS:
                add(CAND_API, arg, chunk.evidence, chunk.location,
                    chunk.section_path,
                    meta={"directive": directive, "name": name,
                          "signature": arg})
                if directive == "class":
                    for base in _base_names(arg):
                        add(CAND_INHERITANCE, base, chunk.evidence,
                            chunk.location, chunk.section_path,
                            meta={"class": name, "base": base})
        elif chunk.kind == CHUNK_SECTION:
            heading = (chunk.content or "").strip()
            lowered = heading.lower()
            if any(lowered.startswith(m) for m in _PROCEDURE_MARKERS):
                if has_procedural_body(idx + 1, body_end.get(idx, len(doc.chunks))):
                    add(CAND_PROCEDURE, heading, chunk.evidence, chunk.location,
                        chunk.section_path,
                        meta={"heading": heading,
                              "level": chunk.meta.get("level")})
        elif chunk.kind == CHUNK_CODE:
            code = chunk.content
            source = chunk.meta.get("source")
            # Test/fixture/generated configuration blocks (``testsetup``,
            # ``testcode``, ``testoutput``, ``productionlist``) and obvious
            # shell/session/text transcripts stay in the EXTRACTED layer with
            # their provenance but never become user-facing code examples.
            if source not in _NON_EXAMPLE_SOURCES and _is_python_example(code, chunk.meta):
                ev_head = code.strip().split("\n", 1)[0][:120]
                add(CAND_CODE_EXAMPLE, ev_head, chunk.evidence,
                    chunk.location, chunk.section_path,
                    meta={"source": source,
                          "language": chunk.meta.get("language"),
                          "n_lines": len(code.splitlines())})
            for ln in code.splitlines():
                stripped = ln.strip()
                if not stripped:
                    continue
                m = _IMPORT_RE.match(stripped)
                if m:
                    norm, module = _normalize_import(stripped)
                    policy = _define_dependency(module)
                    if policy is None:
                        continue
                    origin, confidence = policy
                    add(CAND_DEPENDENCY, norm, chunk.evidence,
                        chunk.location, chunk.section_path,
                        meta={"import": norm, "module": module,
                              "origin": origin,
                              "local": origin != "stdlib"},
                        confidence=confidence)
        elif chunk.kind == CHUNK_DIRECTIVE and chunk.meta.get("directive") == "glossary":
            for term, definition in _glossary_definitions(chunk.content):
                add(CAND_DEFINITION, _clean_term(term), chunk.evidence,
                    chunk.location, chunk.section_path,
                    meta={"term": _clean_term(term), "definition": definition})
        elif chunk.kind == CHUNK_REFERENCE:
            kind = chunk.meta.get("kind")
            if kind == "role":
                role = chunk.meta.get("role")
                if role in _KNOWLEDGE_ROLES:
                    target = chunk.meta.get("target")
                    key = ("role", role, target)
                    if key in seen_refs:
                        continue
                    seen_refs.add(key)
                    add(CAND_REFERENCE, chunk.content, chunk.evidence,
                        chunk.location, chunk.section_path,
                        meta={"role": role, "target": target})
            elif kind == "link":
                url = chunk.meta.get("url")
                text = (chunk.content or "").strip()
                key = ("link", url)
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                add(CAND_REFERENCE, text, chunk.evidence,
                    chunk.location, chunk.section_path,
                    meta={"kind": "link", "url": url, "text": text})

    # Deterministic output order: by source position, then kind, then id.
    cands.sort(key=lambda c: (c.location.line_start, c.kind, c.candidate_id))
    return cands