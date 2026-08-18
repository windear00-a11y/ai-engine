"""RST source adapter for the CPython documentation corpus.

A self-contained, deterministic, line-oriented parser for reStructuredText
(.rst) documents. It understands only *document structure* -- headings,
paragraphs, directives, literal/code blocks, lists, references and API-like
signature directives -- and always records the exact source lines as evidence.
It never attempts semantic interpretation.

Deliberately independent: this module only depends on the framework types and
implements :class:`SourceAdapter`. No docutils requirement.
"""

import re

from knowledge_compiler.adapters.base import SourceAdapter
from knowledge_compiler.types import (
    Document, Chunk, SourceLocation,
    CHUNK_TITLE, CHUNK_SECTION, CHUNK_PARAGRAPH, CHUNK_CODE, CHUNK_DIRECTIVE,
    CHUNK_REFERENCE, CHUNK_SIGNATURE, CHUNK_LIST, CHUNK_ADMONITION,
    CHUNK_COMMENT, EXTRACTED,
)

# Adornment characters give heading levels. The order below mirrors CPython's
# conventions: the document title uses '*' (overline+underline), then '=', '-',
# '~', '^', etc. for progressively deeper sections.
ADORNMENT_LEVELS = {
    "*": 0, "=": 1, "-": 2, "~": 3, "^": 4, '"': 5, "'": 6, "`": 7,
    "#": 8, "+": 9, "_": 10,
}
_DEFAULT_LEVEL = 99

# Directives that declare explicit API signatures (function/class/...).
API_DIRECTIVES = {
    "function", "class", "method", "data", "attribute", "exception",
    "decorator", "staticmethod", "classmethod", "abstractmethod",
    "c:function", "c:macro", "c:type", "c:data", "c:member", "c:var",
    "c:struct", "c:enum", "c:enumerator", "module", "opcode", "2to3fixer",
}

# Directives whose body is code.
CODE_DIRECTIVES = {"code-block", "sourcecode", "doctest", "testcode",
                   "testoutput", "testsetup", "productionlist"}

# Directives that are admonition/graphics containers (kept as extracted chunks;
# never turned into candidates in v1).
ADMONITION_DIRECTIVES = {"note", "warning", "tip", "important", "caution",
                         "seealso", "deprecated"}

# Inline cross-reference role pattern (allows colon inside role, e.g. c:func).
_ROLE_RE = re.compile(r":([a-zA-Z0-9_:-]+):`([^`]*)`")
# Markdown-style link: `text <url>`_
_LINK_RE = re.compile(r"`([^`]+)\s*<([^>]+)>`_")
# Reference-style link: `name`_
_NAMED_REF_RE = re.compile(r"`([^`]+)`_")
# Footnote reference, e.g. [#]_ or [1]_
_FOOTNOTE_RE = re.compile(r"\[(?:#|\d+)]_")

_LINK_TARGET_RE = re.compile(r"^\.\.\s+_([^:]+):\s*(.*)$")
# Directive names may be colon-qualified (``c:function``, ``c:type``, ...).
_DIRECTIVE_RE = re.compile(
    r"^\.\.\s+([a-zA-Z0-9_+-]+(?::[a-zA-Z0-9_+-]+)*)(::)?\s*(.*)$")
_LIST_ITEM_RE = re.compile(r"^([-*+]|\d+[.)]|#\.)\s+")


class RSTAdapter(SourceAdapter):
    name = "rst"
    extensions = (".rst",)

    # -- SourceAdapter ------------------------------------------------------

    def supports(self, path):
        return path.endswith(".rst")

    def parse(self, path, rel_path, source_name):
        doc = Document(path=path, rel_path=rel_path, source_name=source_name,
                       adapter=self.name)
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError, UnicodeError) as e:
            doc.errors.append((1, f"could not decode {path!r}: {e}"))
            doc.text = ""
            return doc
        doc.text = text
        self._parse_text(text, doc)
        return doc

    # -- parsing ------------------------------------------------------------

    @staticmethod
    def _is_adornment(line):
        stripped = line.strip()
        if not stripped:
            return False
        ch = stripped[0]
        if ch.isalnum():
            return False
        return all(c == ch for c in stripped)

    @classmethod
    def _adornment_level(cls, line):
        stripped = line.strip()
        ch = stripped[0]
        return ADORNMENT_LEVELS.get(ch, _DEFAULT_LEVEL)

    @classmethod
    def _is_list_item(cls, line):
        s = line.lstrip()
        return bool(s) and (line[0] in " \t" or True) and bool(_LIST_ITEM_RE.match(s))

    @classmethod
    def _dedent(cls, lines):
        nonblank = [ln for ln in lines if ln.strip()]
        if not nonblank:
            return [], []
        indent = min(len(ln) - len(ln.lstrip(" \t")) for ln in nonblank)
        out = []
        for ln in lines:
            if ln.strip():
                out.append(ln[indent:])
            else:
                out.append("")
        return out, list(lines)

    @classmethod
    def _trim_blank(cls, lines):
        """Drop leading/trailing blank lines (used for block *content*)."""
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return list(lines)

    @classmethod
    def _collect_indented(cls, lines, start):
        """Return (raw_indented_lines, next_index)."""
        buf = []
        i = start
        while i < len(lines):
            ln = lines[i]
            if ln.strip() and not (ln[0] in " \t"):
                break
            buf.append(ln)
            i += 1
        return buf, i

    def _parse_text(self, text, doc):
        lines = [ln.rstrip("\r") for ln in text.split("\n")]
        n = len(lines)
        # section stack holds (level, title) pairs.
        stack = []
        title_set = False
        i = 0
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if self._is_adornment(line):
                i += 1
                continue
            if line.startswith(".. "):
                i = self._handle_directive(lines, i, doc, stack)
                continue
            if not (line[0] in " \t"):
                # heading?
                if (i + 1 < n and self._is_adornment(lines[i + 1])
                        and not line.startswith(" ")):
                    i = self._handle_heading(lines, i, doc, stack,
                                             is_title=not title_set)
                    title_set = True
                    continue
                if self._is_list_item(line):
                    i = self._handle_list(lines, i, doc, stack)
                    continue
            i = self._handle_paragraph(lines, i, doc, stack)

    def _handle_heading(self, lines, i, doc, stack, is_title):
        title = lines[i].strip()
        level = self._adornment_level(lines[i + 1])
        # Update section stack: pop deeper-or-equal levels.
        while stack and stack[-1][0] >= level:
            stack.pop()
        section_path = [t for _, t in stack]
        stack.append((level, title))
        meta = {"heading": title, "level": level}
        if is_title:
            doc.title = title
            kind = CHUNK_TITLE
        else:
            kind = CHUNK_SECTION
        loc = SourceLocation(self._rel(doc), i + 1, i + 1)
        chunk = Chunk(kind=kind, content=title, evidence=lines[i],
                      location=loc, section_path=section_path, meta=meta)
        doc.chunks.append(chunk)
        self._extract_references(title, doc, loc, section_path)
        return i + 2

    def _handle_paragraph(self, lines, i, doc, stack):
        start = i
        buf = []
        while i < len(lines):
            ln = lines[i]
            if not ln.strip():
                break
            if ln.startswith(".. "):
                break
            if self._is_adornment(ln):
                break
            if not (ln[0] in " \t") and not ln.startswith(" "):
                if self._is_list_item(ln):
                    break
                if i + 1 < len(lines) and self._is_adornment(lines[i + 1]):
                    break
            buf.append(ln)
            i += 1
        text = "\n".join(buf)
        literal = text.rstrip().endswith("::")
        loc = SourceLocation(self._rel(doc), start + 1, i)
        section_path = [t for _, t in stack]
        chunk = Chunk(kind=CHUNK_PARAGRAPH, content=text, evidence=text,
                      location=loc, section_path=section_path,
                      meta={"literal": literal})
        doc.chunks.append(chunk)
        self._extract_references(text, doc, loc, section_path)

        if literal:
            # Literal (code) block follows after any blank lines.
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            content, next_i = self._collect_indented(lines, j)
            if content and any(x.strip() for x in content):
                dedented, raw = self._dedent(content)
                code_ev = "\n".join(raw)
                cloc = SourceLocation(self._rel(doc), j + 1, next_i)
                cchunk = Chunk(kind=CHUNK_CODE,
                               content="\n".join(self._trim_blank(dedented)),
                               evidence=code_ev, location=cloc,
                               section_path=section_path,
                               meta={"source": "literal"})
                doc.chunks.append(cchunk)
                i = next_i
            else:
                i = next_i
        return i

    def _handle_list(self, lines, i, doc, stack):
        start = i
        buf = []
        while i < len(lines):
            ln = lines[i]
            if not ln.strip():
                break
            if ln.startswith(".. "):
                break
            if self._is_adornment(ln):
                break
            buf.append(ln)
            i += 1
        text = "\n".join(buf)
        loc = SourceLocation(self._rel(doc), start + 1, i)
        chunk = Chunk(kind=CHUNK_LIST, content=text, evidence=text,
                      location=loc, section_path=[t for _, t in stack])
        doc.chunks.append(chunk)
        self._extract_references(text, doc, loc, [t for _, t in stack])
        return i

    def _handle_directive(self, lines, i, doc, stack):
        line = lines[i]
        section_path = [t for _, t in stack]

        m = _LINK_TARGET_RE.match(line)
        if m:
            target = (m.group(1) or m.group(2)).strip()
            content, next_i = self._collect_indented(lines, i + 1)
            evidence = "\n".join([line] + list(content))
            loc = SourceLocation(self._rel(doc), i + 1, next_i)
            chunk = Chunk(kind=CHUNK_REFERENCE, content=target,
                          evidence=evidence, location=loc,
                          section_path=section_path,
                          meta={"role": None, "target": target,
                                "kind": "link_target"})
            doc.chunks.append(chunk)
            return next_i

        m = _DIRECTIVE_RE.match(line)
        if not m:
            # Not a recognized directive -- treat as a comment.
            content, next_i = self._collect_indented(lines, i + 1)
            loc = SourceLocation(self._rel(doc), i + 1, next_i)
            doc.chunks.append(Chunk(
                kind=CHUNK_COMMENT,
                content="\n".join(ded for ded, _ in []) + line,
                evidence="\n".join([line] + list(content)),
                location=loc, section_path=section_path,
                meta={"directive": "comment"}))
            return next_i

        name = m.group(1)
        has_body = bool(m.group(2))
        arg = (m.group(3) or "").strip()
        content, next_i = self._collect_indented(lines, i + 1)
        evidence = "\n".join([line] + list(content))

        if not has_body:
            # Comment form: ".. a comment"
            loc = SourceLocation(self._rel(doc), i + 1, next_i)
            doc.chunks.append(Chunk(
                kind=CHUNK_COMMENT, content=line, evidence=evidence,
                location=loc, section_path=section_path,
                meta={"directive": name}))
            return next_i

        dedented, raw = self._dedent(content)
        dedented = self._trim_blank(dedented)
        body = "\n".join(dedented)
        loc = SourceLocation(self._rel(doc), i + 1, next_i)

        if name in API_DIRECTIVES:
            parsed_name = self._signature_name(arg, directive=name)
            doc.chunks.append(Chunk(
                kind=CHUNK_SIGNATURE, content=arg, evidence=evidence,
                location=loc, section_path=section_path,
                meta={"directive": name, "arg": arg, "name": parsed_name,
                      "body": body}))
        elif name in CODE_DIRECTIVES:
            doc.chunks.append(Chunk(
                kind=CHUNK_CODE, content="\n".join(dedented), evidence=evidence,
                location=loc, section_path=section_path,
                meta={"source": name, "language": arg or None}))
        elif name in ADMONITION_DIRECTIVES:
            doc.chunks.append(Chunk(
                kind=CHUNK_ADMONITION, content=body, evidence=evidence,
                location=loc, section_path=section_path,
                meta={"directive": name, "arg": arg}))
        else:
            doc.chunks.append(Chunk(
                kind=CHUNK_DIRECTIVE, content=body, evidence=evidence,
                location=loc, section_path=section_path,
                meta={"directive": name, "arg": arg}))
        return next_i

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _rel(doc):
        return doc.rel_path

    @staticmethod
    def _signature_name(arg, directive=None):
        """Return the identifier that a signature directive declares."""
        arg = arg.strip()
        paren = arg.find("(")
        head = arg[:paren].strip() if paren != -1 else arg
        if directive and directive.startswith("c:"):
            # C signatures carry a return-type prefix: ``PyObject* Foo(...)``
            # declares ``Foo``, ``struct _typeobject *Bar`` declares ``Bar``.
            tokens = head.split()
            if not tokens:
                return head
            return tokens[-1].lstrip("*")
        if paren != -1:
            return head
        return arg.split()[0].strip() if arg.split() else arg

    def _extract_references(self, text, doc, loc, section_path):
        base = self._rel(doc)
        for m in _ROLE_RE.finditer(text):
            role, target = m.group(1), m.group(2)
            sloc = SourceLocation(base, loc.line_start, loc.line_end)
            doc.chunks.append(Chunk(
                kind=CHUNK_REFERENCE, content=f":{role}:`{target}`",
                evidence=m.group(0), location=sloc,
                section_path=list(section_path),
                meta={"role": role, "target": target, "kind": "role"}))
        for m in _LINK_RE.finditer(text):
            text_part, url = m.group(1), m.group(2)
            sloc = SourceLocation(base, loc.line_start, loc.line_end)
            doc.chunks.append(Chunk(
                kind=CHUNK_REFERENCE,
                content=re.sub(r"\s+", " ", text_part).strip(),
                evidence=m.group(0), location=sloc,
                section_path=list(section_path), meta={"kind": "link",
                                                       "url": url}))
        for m in _NAMED_REF_RE.finditer(text):
            name = m.group(1)
            sloc = SourceLocation(base, loc.line_start, loc.line_end)
            doc.chunks.append(Chunk(
                kind=CHUNK_REFERENCE, content=name, evidence=m.group(0),
                location=sloc, section_path=list(section_path),
                meta={"kind": "ref", "name": name}))
        for m in _FOOTNOTE_RE.finditer(text):
            sloc = SourceLocation(base, loc.line_start, loc.line_end)
            doc.chunks.append(Chunk(
                kind=CHUNK_REFERENCE, content=m.group(0), evidence=m.group(0),
                location=sloc, section_path=list(section_path),
                meta={"kind": "footnote"}))