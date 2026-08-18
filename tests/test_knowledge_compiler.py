import json
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from knowledge_compiler.adapters.rst import RSTAdapter
from knowledge_compiler.adapters.base import SourceAdapter
from knowledge_compiler.extractor import extract_candidates
from knowledge_compiler.pipeline import Pipeline
from knowledge_compiler.types import (
    Document, Chunk, SourceLocation, CHUNK_PARAGRAPH,
    CANDIDATE, VALIDATED,
)
from knowledge_compiler.validator import validate_candidates

FIXTURES = os.path.join(_ROOT, "tests", "fixtures", "rst")


def read(rel):
    with open(os.path.join(FIXTURES, rel), "r", encoding="utf-8") as f:
        return f.read()


def parse_fixture(rel):
    return RSTAdapter().parse(os.path.join(FIXTURES, rel), rel, "fixtures")


def chunks_of(doc, kind):
    return [c for c in doc.chunks if c.kind == kind]


class RSTHeadingsTests(unittest.TestCase):
    def test_title_and_sections(self):
        doc = parse_fixture("headings.rst")
        self.assertEqual(doc.title, "Widget Guide")
        kinds = [c.kind for c in doc.chunks]
        self.assertEqual(kinds.count("title"), 1)
        self.assertEqual(kinds.count("section"), 4)

    def test_nested_section_hierarchy(self):
        doc = parse_fixture("headings.rst")
        by_content = {c.content: c for c in chunks_of(doc, "section")}
        # deepest heading has full ancestry path (document title first)
        self.assertEqual(by_content["Level Three"].section_path,
                         ["Widget Guide", "Introduction", "Nested"])
        # mid heading has shorter path
        self.assertEqual(by_content["Nested"].section_path,
                         ["Widget Guide", "Introduction"])
        # top-level section's path is just the document title
        self.assertEqual(by_content["Introduction"].section_path,
                         ["Widget Guide"])

    def test_paragraphs_under_correct_section(self):
        doc = parse_fixture("headings.rst")
        para = [c for c in chunks_of(doc, "paragraph")
                if c.content.startswith("Text under level three.")]
        self.assertEqual(len(para), 1)
        self.assertEqual(para[0].section_path,
                         ["Widget Guide", "Introduction", "Nested", "Level Three"])


class RSTCodeAndDirectiveTests(unittest.TestCase):
    def test_code_block_directive(self):
        doc = parse_fixture("code.rst")
        code = [c for c in chunks_of(doc, "code_block")
                if c.meta.get("source") == "code-block"]
        self.assertEqual(len(code), 1)
        self.assertEqual(code[0].content, "def add(a, b):\n    return a + b")
        self.assertIn("def add(a, b):", code[0].evidence)

    def test_literal_block_and_doctest(self):
        doc = parse_fixture("code.rst")
        literal = [c for c in chunks_of(doc, "code_block")
                   if c.meta.get("source") == "literal"]
        self.assertEqual(len(literal), 2)
        self.assertIn("x = 1", literal[0].content)
        self.assertIn(">>> print(\"hi\")", literal[1].content)

    def test_admonition_directive(self):
        doc = parse_fixture("code.rst")
        notes = chunks_of(doc, "admonition")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].meta["directive"], "note")
        self.assertIn("Remember to close", notes[0].content)

    def test_index_directive_captured(self):
        doc = parse_fixture("code.rst")
        idx = [c for c in chunks_of(doc, "directive")
               if c.meta.get("directive") == "index"]
        self.assertEqual(len(idx), 1)


class SourceLocationTests(unittest.TestCase):
    def test_line_numbers_are_one_based_and_exact(self):
        doc = parse_fixture("headings.rst")
        title = chunks_of(doc, "title")[0]
        # line 1 is the overline adornment; the title text is on line 2
        self.assertEqual((title.location.line_start, title.location.line_end),
                         (2, 2))
        intro = next(c for c in chunks_of(doc, "section")
                     if c.content == "Introduction")
        self.assertEqual(intro.location.line_start, 5)
        # paragraph after "Some intro paragraph." line 8
        para = next(c for c in chunks_of(doc, "paragraph")
                    if c.content.startswith("Some intro"))
        self.assertEqual(para.location.line_start, 8)

    def test_code_block_location(self):
        doc = parse_fixture("code.rst")
        code = [c for c in chunks_of(doc, "code_block")
                if c.meta.get("source") == "code-block"][0]
        self.assertGreater(code.location.line_start, 0)
        self.assertGreaterEqual(code.location.line_end, code.location.line_start)


class MalformedFileTests(unittest.TestCase):
    def test_invalid_utf8_recorded_without_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "broken.rst")
            with open(bad, "wb") as f:
                f.write(b"Title\n=====\n\n\xff\xfe invalid bytes here\n")
            doc = RSTAdapter().parse(bad, "broken.rst", "fixtures")
            self.assertTrue(doc.errors)
            self.assertEqual(doc.chunks, [])
            # pipeline still handles it as a document
            result = Pipeline(output_root=os.path.join(tmp, "out")) \
                .candidates(tmp)
            self.assertEqual(len(result), 1)

    def test_garbage_but_decodable_rst(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "weird.rst")
            with open(path, "w", encoding="utf-8") as f:
                f.write(".....\n\nsome lines\n- - - \n")
            doc = RSTAdapter().parse(path, "weird.rst", "fixtures")
            # must not raise; paragraphs may or may not be found
            self.assertIsInstance(doc.chunks, list)
            self.assertFalse(doc.errors)


class DeterminismTests(unittest.TestCase):
    def test_repeated_extraction_is_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            o1 = os.path.join(tmp, "o1")
            o2 = os.path.join(tmp, "o2")
            Pipeline(output_root=o1).extract(FIXTURES, report=True)
            Pipeline(output_root=o2).extract(FIXTURES, report=True)
            files1 = {}
            for dp, _, fs in os.walk(os.path.join(o1, "extracted")):
                for n in fs:
                    p = os.path.join(dp, n)
                    with open(p, "rb") as f:
                        files1[n] = f.read()
            files2 = {}
            for dp, _, fs in os.walk(os.path.join(o2, "extracted")):
                for n in fs:
                    p = os.path.join(dp, n)
                    with open(p, "rb") as f:
                        files2[n] = f.read()
            self.assertEqual(set(files1), set(files2))
            for k in files1:
                self.assertEqual(files1[k], files2[k], k)

    def test_repeated_candidates_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            o1 = os.path.join(tmp, "o1")
            o2 = os.path.join(tmp, "o2")
            Pipeline(output_root=o1).candidates(FIXTURES, report=True)
            Pipeline(output_root=o2).candidates(FIXTURES, report=True)
            def load(dirpath):
                data = {}
                for dp, _, fs in os.walk(os.path.join(dirpath, "candidates")):
                    for n in fs:
                        with open(os.path.join(dp, n)) as f:
                            data[n] = json.load(f)
                return data
            self.assertEqual(load(o1), load(o2))


class NoInventedFactsTests(unittest.TestCase):
    def test_ambiguous_prose_produces_no_candidates(self):
        doc = parse_fixture("ambiguous.rst")
        cands = extract_candidates(doc)
        # Only references/admonitions are structural; the marketing prose
        # must NOT become any kind of candidate.
        for c in cands:
            self.assertNotIn("wonderful", c.summary)
            self.assertNotIn("buy them", c.summary)
        self.assertEqual(
            [c.kind for c in cands if "wonderful" in c.evidence], [])

    def test_candidate_summary_is_verbatim_source(self):
        doc = parse_fixture("api.rst")
        source_text = read("api.rst")
        for c in extract_candidates(doc):
            self.assertIn(c.summary, source_text)
            self.assertIn(c.evidence, source_text)


class CandidateGenerationTests(unittest.TestCase):
    def test_api_declarations(self):
        doc = parse_fixture("api.rst")
        api = [c for c in extract_candidates(doc) if c.kind == "api_declaration"]
        kinds = {c.meta["name"] for c in api}
        self.assertIn("add", kinds)
        self.assertIn("Widget", kinds)
        self.assertIn("Widget.render", kinds)
        self.assertIn("DEFAULT_SIZE", kinds)
        self.assertTrue(all(c.state == CANDIDATE for c in api))

    def test_inheritance_from_class_signature(self):
        doc = parse_fixture("api.rst")
        inh = [c for c in extract_candidates(doc) if c.kind == "inheritance"]
        self.assertEqual(len(inh), 1)
        self.assertEqual(inh[0].summary, "Base")
        self.assertEqual(inh[0].meta["class"], "Widget")
        # metaclass keyword argument must NOT be treated as a base
        self.assertNotIn("metaclass=Meta", [c.summary for c in inh])

    def test_glossary_definitions(self):
        doc = parse_fixture("glossary.rst")
        defs = [c for c in extract_candidates(doc) if c.kind == "definition"]
        self.assertEqual({c.summary for c in defs}, {"mutable", "immutable"})
        mutable = next(c for c in defs if c.summary == "mutable")
        self.assertIn("can change", mutable.meta["definition"])

    def test_code_example_and_dependency(self):
        doc = parse_fixture("code.rst")
        cands = extract_candidates(doc)
        examples = [c for c in cands if c.kind == "code_example"]
        self.assertTrue(examples)
        # no imports in this fixture, so no dependency candidates
        self.assertEqual([c for c in cands if c.kind == "dependency"], [])

    def test_dependency_from_explicit_import(self):
        text = ("Title\n=====\n\nCode:\n\n.. code-block:: python\n\n"
                "   import math\n   from fractions import Fraction\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "imp.rst")
            with open(path, "w") as f:
                f.write(text)
            doc = RSTAdapter().parse(path, "imp.rst", "fixtures")
            deps = [c for c in extract_candidates(doc)
                    if c.kind == "dependency"]
            self.assertEqual({c.summary for c in deps},
                             {"import math",
                              "from fractions import Fraction"})

    def test_procedure_candidate_from_howto_heading(self):
        text = ("Guide\n=====\n\n"
                "Creating the Widget Tool\n------------------------\n\n"
                "Steps:\n\n"
                "#. Open the configuration file.\n"
                "#. Set the option.\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "howto.rst")
            with open(path, "w") as f:
                f.write(text)
            doc = RSTAdapter().parse(path, "howto.rst", "fixtures")
            procs = [c for c in extract_candidates(doc)
                     if c.kind == "procedure"]
            self.assertEqual([c.summary for c in procs],
                             ["Creating the Widget Tool"])

    def test_reference_candidate(self):
        text = ("Docs\n====\n\nSee :func:`len` and :ref:`guide`.\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "refs.rst")
            with open(path, "w") as f:
                f.write(text)
            doc = RSTAdapter().parse(path, "refs.rst", "fixtures")
            refs = [c for c in extract_candidates(doc)
                    if c.kind == "reference"]
            self.assertTrue(any(c.summary == ":func:`len`" for c in refs))


class ProvenanceAndValidationTests(unittest.TestCase):
    def test_candidate_provenance_fields(self):
        doc = parse_fixture("api.rst")
        for c in extract_candidates(doc):
            self.assertTrue(c.document)
            self.assertIsInstance(c.section_path, list)
            self.assertTrue(c.location)
            self.assertTrue(c.evidence)
            self.assertTrue(c.summary)

    def test_validation_passes_for_grounded_candidates(self):
        doc = parse_fixture("api.rst")
        cands = extract_candidates(doc)
        records = validate_candidates(doc, cands)
        self.assertTrue(all(r.valid for r in records))
        self.assertTrue(all(c.state == VALIDATED for c in cands))

    def test_validation_rejects_ungrounded_evidence(self):
        doc = parse_fixture("api.rst")
        cand = extract_candidates(doc)[0]
        # Corrupt the evidence so it no longer matches the source.
        cand.evidence = "this is fabricated text that is not in the file"
        record = validate_candidates(doc, [cand])[0]
        self.assertFalse(record.valid)
        self.assertEqual(cand.state, CANDIDATE)


class AdapterIsolationTests(unittest.TestCase):
    def test_pipeline_works_with_unrelated_adapter(self):
        class TXTAdapter(SourceAdapter):
            name = "txt"
            extensions = (".txt",)

            def supports(self, path):
                return path.endswith(".txt")

            def parse(self, path, rel_path, source_name):
                doc = Document(path=path, rel_path=rel_path,
                               source_name=source_name, adapter=self.name)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                doc.text = text
                for idx, ln in enumerate(text.split("\n")):
                    if ln.strip():
                        doc.chunks.append(Chunk(
                            kind=CHUNK_PARAGRAPH, content=ln, evidence=ln,
                            location=SourceLocation(rel_path, idx + 1,
                                                    idx + 1)))
                return doc

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "notes.txt"), "w") as f:
                f.write("line one\nline two\n")
            pipe = Pipeline(output_root=os.path.join(tmp, "out"),
                            adapter=TXTAdapter())
            manifest = pipe.scan(tmp)
            self.assertEqual(manifest["adapter"], "txt")
            docs = pipe.extract(tmp)
            self.assertEqual(len(docs), 1)
            self.assertEqual(len(docs[0].chunks), 2)
            # paragraphs alone never become candidates (isolation: extractor
            # does not depend on RST chunk kinds)
            results = pipe.candidates(tmp)
            self.assertEqual(len(results[0]["candidates"]), 0)

    def test_rst_adapter_usable_without_pipeline(self):
        doc = RSTAdapter().parse(os.path.join(FIXTURES, "headings.rst"),
                                 "headings.rst", "fixtures")
        self.assertEqual(doc.title, "Widget Guide")
        self.assertGreater(len(doc.chunks), 0)


class CLITests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "knowledge_compiler",
                               *args], cwd=_ROOT, capture_output=True,
                              text=True)

    def test_scan(self):
        r = self._run("scan", FIXTURES)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["adapter"], "rst")
        self.assertGreaterEqual(out["document_count"], 5)

    def test_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = os.path.join(tmp, "out")
            r = self._run("extract", FIXTURES, "--output", outdir)
            self.assertEqual(r.returncode, 0)
            extracted = os.path.join(outdir, "extracted")
            self.assertTrue(os.path.isdir(extracted))
            self.assertTrue(any(f.endswith(".json")
                                for f in os.listdir(extracted)))

    def test_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = os.path.join(tmp, "out")
            r = self._run("candidates", FIXTURES, "--output", outdir)
            self.assertEqual(r.returncode, 0)
            summary = json.loads(r.stdout)
            self.assertGreaterEqual(summary["candidate_total"], 1)


if __name__ == "__main__":
    unittest.main()