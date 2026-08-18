"""Tests for the Knowledge Acceptance Layer v1.

Covers: exactly-one decision per candidate, ACCEPT/HOLD/REJECT for every
candidate kind, reason codes, provenance/evidence preservation, deterministic
identity fingerprints, duplicate identities across documents (one canonical
identity + preserved provenance), same-summary/different-meaning separation,
identity conflicts -> HOLD, safe Knowledge Schema mapping, unmappable -> HOLD,
preview never mutates SQLite, and deterministic repeated evaluation.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from acceptance.loader import load_candidates, CandidateLoadError
from acceptance.evaluator import EvaluationResult
from acceptance.report import build_summary, build_preview, build_audit
from acceptance import policy, mapping
from acceptance.safety import (
    guard_sqlite_read_only, SqliteWriteForbiddenError, SQLITE_WRITES_FORBIDDEN,
)
from acceptance.types import (
    ACCEPT, HOLD, REJECT,
    compute_identity, content_signature, normalize_code,
)
from ingestion.validator import validate_source
from ingestion.source_format import build_source, build_node
from retrieval.repository import KnowledgeRepository
from retrieval.knowledge import VALID_TYPES, RELATIONSHIP_KINDS

# Compiled course corpus: the Python tutorial candidate output.
TUTORIAL_OUT = os.path.join(_ROOT, "output", "candidates")


def make_candidate(cid, kind, summary, evidence, document, meta,
                   confidence="high", section_path=None, location=None,
                   state="VALIDATED"):
    return {
        "candidate_id": cid,
        "kind": kind,
        "summary": summary,
        "evidence": evidence,
        "document": document,
        "section_path": list(section_path or []),
        "location": location or {
            "path": document, "line_start": 1, "line_end": 1},
        "confidence": confidence,
        "meta": dict(meta),
        "state": state,
    }


def evaluate(cands, valid=None):
    return EvaluationResult(cands, valid or {})


def decisions(cands, valid=None):
    return evaluate(cands, valid).decisions


def decision_of(cands, cid, valid=None):
    for d in decisions(cands, valid):
        if d.candidate_id == cid:
            return d
    raise AssertionError(f"no decision for {cid!r}")


def write_candidate_file(tmp, rel, candidates, validations=None):
    """Write one file in the exact knowledge_compiler candidates format."""
    base = rel[:-len(".rst")] if rel.endswith(".rst") else rel
    path = os.path.join(tmp, "candidates", base + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "document": rel,
        "title": "Doc",
        "count": len(candidates),
        "candidates": candidates,
        "validations": validations or [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


class ExactlyOneDecisionTests(unittest.TestCase):
    def test_every_candidate_gets_exactly_one_decision(self):
        cands = [
            make_candidate("a1", "dependency", "import math", "import math",
                           "d1.rst", {"module": "math", "origin": "stdlib"}),
            make_candidate("a2", "api_declaration", "def f()", "def f()",
                           "d1.rst", {"directive": "function", "name": "f"}),
            make_candidate("a3", "reference", ":func:`len`", ":func:`len`",
                           "d1.rst", {"kind": "role", "role": "func",
                                      "target": "len"}),
        ]
        ds = decisions(cands)
        self.assertEqual(len(ds), 3)
        for d in ds:
            self.assertIn(d.decision, (ACCEPT, HOLD, REJECT))
            self.assertTrue(d.candidate_id)
            self.assertTrue(d.reason_code)
            self.assertTrue(d.reason)
            self.assertTrue(d.evidence)
            self.assertTrue(d.provenance)

    def test_exactly_one_decision_and_no_silent_drops(self):
        # REJECT is not a delete: rejected candidates stay in the decision set.
        cands = [
            make_candidate("r1", "api_declaration", "def f()", "def f()",
                           "d1.rst", {"directive": "function", "name": "f"}),
            make_candidate("r2", "reference", ":ref:`guide`", ":ref:`guide`",
                           "d1.rst", {"kind": "role", "role": "ref",
                                      "target": "guide"}),
        ]
        ds = decisions(cands)
        self.assertEqual({d.candidate_id for d in ds}, {"r1", "r2"})


class AcceptPoliciesTests(unittest.TestCase):
    def test_api_declaration_accepted(self):
        c = make_candidate("c1", "api_declaration", "import math", "math",
                           "d.rst", {"directive": "module", "name": "math"})
        d = decision_of([c], "c1")
        self.assertEqual(d.decision, ACCEPT)
        self.assertEqual(d.reason_code, policy.ACCEPT_API)

    def test_inheritance_accepted(self):
        # An extends edge is accepted only when BOTH endpoints resolve to
        # accepted class nodes -- nothing is invented to satisfy the edge.
        cands = [
            make_candidate("c1", "api_declaration", "A", "A", "d.rst",
                           {"directive": "class", "name": "A",
                            "signature": "A"}),
            make_candidate("c2", "api_declaration", "Base", "Base", "d.rst",
                           {"directive": "class", "name": "Base",
                            "signature": "Base"}),
            make_candidate("c3", "inheritance", "Base", "class A(Base)",
                           "d.rst", {"class": "A", "base": "Base"}),
        ]
        d = decision_of(cands, "c3")
        self.assertEqual(d.decision, ACCEPT)
        self.assertEqual(d.reason_code, policy.ACCEPT_INHERITANCE)

    def test_inheritance_held_when_endpoint_is_not_an_accepted_node(self):
        # The source class has no accepted node, so the edge is HELD rather
        # than trusting a dangling/invented endpoint.
        cands = [
            make_candidate("c1", "api_declaration", "Base", "Base", "d.rst",
                           {"directive": "class", "name": "Base",
                            "signature": "Base"}),
            make_candidate("c2", "inheritance", "Base", "class A(Base)",
                           "d.rst", {"class": "A", "base": "Base"}),
        ]
        d = decision_of(cands, "c2")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_INHERITANCE_SOURCE)

    def test_inheritance_held_when_target_has_no_declaration(self):
        # The base class has no declaration evidence in the corpus at all.
        cands = [
            make_candidate("c1", "api_declaration", "A", "A", "d.rst",
                           {"directive": "class", "name": "A",
                            "signature": "A"}),
            make_candidate("c2", "inheritance", "Base", "class A(Base)",
                           "d.rst", {"class": "A", "base": "Base"}),
        ]
        d = decision_of(cands, "c2")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_INHERITANCE_TARGET_UNRESOLVED)

    def test_inheritance_held_when_target_declared_but_not_accepted(self):
        # The base is declared (as a module) but that declaration maps to a
        # technology node, not an accepted class node for the edge.
        cands = [
            make_candidate("c1", "api_declaration", "A", "A", "d.rst",
                           {"directive": "class", "name": "A",
                            "signature": "A"}),
            make_candidate("c2", "api_declaration", "Base", "Base", "d.rst",
                           {"directive": "module", "name": "Base",
                            "signature": "Base"}),
            make_candidate("c3", "inheritance", "Base", "class A(Base)",
                           "d.rst", {"class": "A", "base": "Base"}),
        ]
        d = decision_of(cands, "c3")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_INHERITANCE_TARGET_NOT_ACCEPTED)

    def test_definition_accepted(self):
        c = make_candidate("c3", "definition", "mutable", "mutable",
                           "d.rst", {"term": "mutable", "definition": "x"})
        d = decision_of([c], "c3")
        self.assertEqual(d.decision, ACCEPT)
        self.assertEqual(d.reason_code, policy.ACCEPT_DEFINITION)

    def test_stdlib_dependency_accepted(self):
        c = make_candidate("c4", "dependency", "from math import pi",
                           "from math import pi", "d.rst",
                           {"module": "math", "origin": "stdlib"})
        d = decision_of([c], "c4")
        self.assertEqual(d.decision, ACCEPT)
        self.assertEqual(d.reason_code, policy.ACCEPT_DEPENDENCY)

    def test_procedure_accepted(self):
        c = make_candidate("c5", "procedure", "Creating a Widget",
                           "Creating a Widget", "d.rst",
                           {"heading": "Creating a Widget", "level": 2})
        d = decision_of([c], "c5")
        self.assertEqual(d.decision, ACCEPT)
        self.assertEqual(d.reason_code, policy.ACCEPT_PROCEDURE)

    def test_genuine_code_example_accepted(self):
        c = make_candidate("c6", "code_example", "x = 1", "x = 1",
                           "d.rst", {"source": "literal", "language": "python"})
        d = decision_of([c], "c6")
        self.assertEqual(d.decision, ACCEPT)
        self.assertEqual(d.reason_code, policy.ACCEPT_EXAMPLE)

    def test_reference_to_knowledge_role_is_held(self):
        c = make_candidate("c7", "reference", ":func:`len`", ":func:`len`",
                           "d.rst", {"kind": "role", "role": "func",
                                      "target": "len"})
        d = decision_of([c], "c7")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_REFERENCE)


class HoldPoliciesTests(unittest.TestCase):
    def test_local_dependency_is_held(self):
        for origin in ("unknown", None):
            c = make_candidate("c1", "dependency", "import fibo", "import fibo",
                               "d.rst", {"module": "fibo",
                                         "origin": origin or "unknown"})
            d = decision_of([c], "c1")
            self.assertEqual(d.decision, HOLD, origin)
            self.assertEqual(d.reason_code, policy.HOLD_DEPENDENCY)
            self.assertEqual(d.confidence, "high")

    def test_low_confidence_dependency_is_held(self):
        c = make_candidate("c2", "dependency", "import fibo", "import fibo",
                           "d.rst", {"module": "fibo", "origin": "unknown"},
                           confidence="low")
        d = decision_of([c], "c2")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_DEPENDENCY)

    def test_ambiguous_code_fragment_is_held(self):
        c = make_candidate("c3", "code_example",
                           "... except RuntimeError:", "... except RuntimeError:",
                           "d.rst", {"source": "literal", "language": "python"})
        d = decision_of([c], "c3")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_AMBIGUOUS)

    def test_non_high_confidence_api_is_held(self):
        c = make_candidate("c4", "api_declaration", "f", "f", "d.rst",
                           {"directive": "function", "name": "f"},
                           confidence="med")
        d = decision_of([c], "c4")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_CONFIDENCE)

    def test_imports_only_example_is_held(self):
        c = make_candidate("c5", "code_example",
                           "import os\nfrom math import pi",
                           "import os\nfrom math import pi", "d.rst",
                           {"source": "literal", "language": "python"})
        d = decision_of([c], "c5")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_SETUP)

    def test_imports_only_with_semicolon_is_not_setup(self):
        # A semicolon means the block does something beyond setup.
        c = make_candidate("c6", "code_example", "import pdb; pdb.set_trace()",
                           "import pdb; pdb.set_trace()", "d.rst",
                           {"source": "literal", "language": "python"})
        d = decision_of([c], "c6")
        self.assertEqual(d.decision, ACCEPT)
        self.assertEqual(d.reason_code, policy.ACCEPT_EXAMPLE)

    def test_definition_without_definition_text_is_held(self):
        c = make_candidate("c7", "definition", "t-string", "t-string",
                           "d.rst", {"term": "t-string", "definition": ""})
        d = decision_of([c], "c7")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_DEFINITION)

    def test_future_dependency_is_held(self):
        c = make_candidate("c8", "dependency", "from __future__ import print_function",
                           "from __future__ import print_function", "d.rst",
                           {"module": "__future__", "origin": "stdlib"})
        d = decision_of([c], "c8")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_DEPENDENCY)


class RejectPoliciesTests(unittest.TestCase):
    def test_navigation_link_reference_rejected(self):
        c = make_candidate("c1", "reference", "docs", "`docs <url>`_", "d.rst",
                           {"kind": "link", "url": "https://docs.python.org"})
        d = decision_of([c], "c1")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_NAV)

    def test_ui_reference_rejected(self):
        c = make_candidate("c2", "reference", ":option:`-q`", ":option:`-q`",
                           "d.rst", {"kind": "role", "role": "option",
                                      "target": "-q"})
        d = decision_of([c], "c2")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_UI)

    def test_navigation_role_reference_rejected(self):
        c = make_candidate("c3", "reference", ":ref:`guide`", ":ref:`guide`",
                           "d.rst", {"kind": "role", "role": "ref",
                                      "target": "guide"})
        d = decision_of([c], "c3")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_NAV)

    def test_generated_test_block_rejected(self):
        c = make_candidate("c4", "code_example", "hidden = 1", "hidden = 1",
                           "d.rst", {"source": "testcode",
                                     "language": "python"})
        d = decision_of([c], "c4")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_TEST)

    def test_shell_transcript_rejected(self):
        for lang in ("shell", "shell-session", "console", "text", "none",
                     "sh", "bash", "zsh", "fish", "cmd", "doscon", "ps1con",
                     "powershell"):
            c = make_candidate("c5", "code_example", "$ pip install x",
                               "$ pip install x", "d.rst",
                               {"source": "code-block", "language": lang})
            d = decision_of([c], "c5")
            self.assertEqual(d.decision, REJECT, lang)
            self.assertEqual(d.reason_code, policy.REJECT_SHELL)

    def test_shell_first_line_rejected(self):
        # No shell language marker, but the first code line is a shell command.
        c = make_candidate("c5", "code_example", "$ python setup.py sdist",
                           "$ python setup.py sdist", "d.rst",
                           {"source": "literal", "language": "python"})
        d = decision_of([c], "c5")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_SHELL)

    def test_shebang_first_line_rejected(self):
        c = make_candidate("c5", "code_example", "#!/usr/bin/env python3",
                           "#!/usr/bin/env python3\nprint('hi')", "d.rst",
                           {"source": "literal", "language": "python"})
        d = decision_of([c], "c5")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_SHELL)

    def test_gdb_transcript_rejected(self):
        # Debugger prompt on the first line -> transcript, not an example.
        c = make_candidate("c5", "code_example", "(gdb) run", "(gdb) run\n(gdb) bt",
                           "d.rst", {"source": "literal", "language": "text"})
        d = decision_of([c], "c5")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_SHELL)

    def test_unknown_kind_rejected_as_malformed(self):
        c = make_candidate("c6", "shiny_new_kind", "x", "x", "d.rst", {})
        d = decision_of([c], "c6")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_MALFORMED)

    def test_missing_provenance_rejected(self):
        c = make_candidate("c7", "api_declaration", "f", "f", "", {})
        d = decision_of([c], "c7")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_MALFORMED)

    def test_empty_summary_rejected_as_artifact(self):
        c = make_candidate("c8", "api_declaration", "", "f", "d.rst",
                           {"directive": "function", "name": "f"})
        d = decision_of([c], "c8")
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_ARTIFACT)

    def test_validation_failure_rejected(self):
        c = make_candidate("c9", "api_declaration", "f", "f", "d.rst",
                           {"directive": "function", "name": "f"})
        d = decision_of([c], "c9", valid={"c9": False})
        self.assertEqual(d.decision, REJECT)
        self.assertEqual(d.reason_code, policy.REJECT_VALIDATION)


class ProvenanceAndEvidenceTests(unittest.TestCase):
    def test_decision_preserves_provenance(self):
        c = make_candidate("c1", "definition", "mutable", "mutable", "g.rst",
                           {"term": "mutable", "definition": "x"},
                           section_path=["Guide", "Glossary"],
                           location={"path": "g.rst", "line_start": 5,
                                     "line_end": 9})
        d = decision_of([c], "c1")
        self.assertEqual(d.provenance["document"], "g.rst")
        self.assertEqual(d.provenance["section_path"], ["Guide", "Glossary"])
        self.assertEqual(d.provenance["location"]["line_start"], 5)
        self.assertEqual(d.evidence, "mutable")

    def test_preview_nodes_preserve_all_evidence(self):
        cands = [
            make_candidate("m1", "dependency", "import math", "import math",
                           "a.rst", {"module": "math", "origin": "stdlib"}),
            make_candidate("m2", "dependency", "import math", "import math",
                           "b.rst", {"module": "math", "origin": "stdlib"}),
        ]
        preview = build_preview(evaluate(cands))
        self.assertEqual(preview["proposed_nodes_count"], 1)
        node = preview["proposed_nodes"][0]
        self.assertEqual(len(node["provenance"]), 2)
        self.assertEqual({p["document"] for p in node["provenance"]},
                         {"a.rst", "b.rst"})
        self.assertTrue(all(p["evidence"] == "import math"
                            for p in node["provenance"]))


class IdentityTests(unittest.TestCase):
    def _ex1(self, cid, body):
        return make_candidate(cid, "code_example", body.splitlines()[0] if body
                              else "", body, "d.rst",
                              {"source": "literal", "language": "python"})

    def test_identical_examples_same_identity_different_bodies_do_not_merge(self):
        a = self._ex1("x1", "x = 1\ny = 2")
        b = self._ex1("x2", "x = 1\ny = 3")
        self.assertNotEqual(compute_identity("code_example", a),
                            compute_identity("code_example", b))
        # Same first-line summary, different meaning -> separate identities.
        self.assertEqual(a["summary"], b["summary"])
        ds = decisions([a, b])
        self.assertEqual(len({d.identity for d in ds}), 2)

    def test_identical_examples_share_identity(self):
        a = self._ex1("x1", "x = 1\ny = 2")
        b = self._ex1("x2", "x = 1\ny = 2")
        self.assertEqual(compute_identity("code_example", a),
                        compute_identity("code_example", b))

    def test_identity_uses_strong_fields_not_summary(self):
        # Two API declarations with the same summary text but different names
        # must NOT share an identity.
        a = make_candidate("x1", "api_declaration", "def add(a, b):",
                           "def add(a, b):", "d.rst",
                           {"directive": "function", "name": "a_add",
                            "signature": "a_add(a, b)"})
        b = make_candidate("x2", "api_declaration", "def add(a, b):",
                           "def add(a, b):", "d.rst",
                           {"directive": "function", "name": "b_add",
                            "signature": "b_add(a, b)"})
        self.assertNotEqual(compute_identity("api_declaration", a),
                            compute_identity("api_declaration", b))
        self.assertEqual(a["summary"], b["summary"])

    def test_deterministic_fingerprints_across_runs(self):
        c = make_candidate("c1", "dependency", "import os", "import os",
                           "d.rst", {"module": "os", "origin": "stdlib"})
        self.assertEqual(compute_identity("dependency", dict(c)),
                        compute_identity("dependency", dict(c)))
        self.assertEqual(compute_identity("dependency", c),
                        compute_identity("dependency", c))


class DeduplicationTests(unittest.TestCase):
    def test_same_knowledge_across_documents_one_canonical_identity(self):
        cands = [
            make_candidate("d1", "api_declaration", "math", "math",
                           "a.rst", {"directive": "module", "name": "math",
                                     "signature": "math"}),
            make_candidate("d2", "api_declaration", "math", "math",
                           "b.rst", {"directive": "module", "name": "math",
                                     "signature": "math"}),
        ]
        res = evaluate(cands)
        accepted = res.accepted_groups()
        self.assertEqual(len(accepted), 1)
        grp = accepted[0]
        self.assertEqual(grp.documents, ["a.rst", "b.rst"])
        self.assertEqual(grp.member_count, 2)
        # two decisions (never dropped), one canonical identity
        self.assertEqual(len([d for d in res.decisions if d.decision == ACCEPT]),
                         2)

    def test_conflicting_definition_identity_is_held(self):
        cands = [
            make_candidate("c1", "definition", "mutable", "A", "a.rst",
                           {"term": "mutable", "definition": "can change"}),
            make_candidate("c2", "definition", "mutable", "B", "b.rst",
                           {"term": "mutable", "definition": "cannot change"}),
        ]
        res = evaluate(cands)
        self.assertEqual(len(res.accepted_groups()), 0)
        for d in res.decisions:
            self.assertEqual(d.decision, HOLD)
            self.assertEqual(d.reason_code, policy.HOLD_CONFLICT)

    def test_same_dependency_holds_provenance_across_documents(self):
        cands = [
            make_candidate("s1", "dependency", "import sys", "import sys",
                           "e.rst", {"module": "sys", "origin": "stdlib"}),
            make_candidate("s2", "dependency", "import sys", "import sys",
                           "f.rst", {"module": "sys", "origin": "stdlib"}),
            make_candidate("s3", "dependency", "import sys", "import sys",
                           "g.rst", {"module": "sys", "origin": "stdlib"}),
        ]
        preview = build_preview(evaluate(cands))
        self.assertEqual(preview["proposed_nodes_count"], 1)
        node = preview["proposed_nodes"][0]
        self.assertEqual(node["type"], "dependency")
        self.assertEqual(len(node["provenance"]), 3)


class SchemaMappingTests(unittest.TestCase):
    def _accepted(self, kind, meta, summary, evidence, cid="id1"):
        return make_candidate(cid, kind, summary, evidence, "d.rst", meta)

    def test_every_accepted_kind_maps_to_existing_node_type(self):
        table = {
            ("api_declaration", "module"): "technology",
            ("api_declaration", "class"): "entity",
            ("api_declaration", "exception"): "entity",
            ("api_declaration", "c:type"): "entity",
            ("api_declaration", "c:struct"): "entity",
            ("definition", None): "concept",
            ("dependency", None): "dependency",
            ("procedure", None): "procedure",
            ("code_example", None): "example",
        }
        for (kind, directive), ntype in table.items():
            meta = {"name": "x", "term": "x", "module": "x", "heading": "x",
                    "source": "literal", "directive": directive,
                    "signature": "x"}
            cand = self._accepted(kind, meta, "x", "x")
            self.assertTrue(mapping.is_mappable(kind, meta), (kind, directive))
            node = mapping.proposed_node("identity-1", kind, meta, "x", "x")
            self.assertIsNotNone(node, (kind, directive))
            if node is not None:
                self.assertEqual(node["type"], ntype, (kind, directive))
            self.assertIn(ntype, VALID_TYPES)

    def test_function_directive_api_is_unmappable_and_held(self):
        # A plain function/method/data/... is an interface surface with no
        # deterministic node type: it must stay HELD, not be forced in.
        meta = {"directive": "function", "name": "f", "signature": "f"}
        self.assertFalse(mapping.is_mappable("api_declaration", meta))
        self.assertIsNone(mapping.proposed_node("identity-1",
                                                "api_declaration", meta,
                                                "f", "f"))
        cand = self._accepted("api_declaration", meta, "f", "f")
        d = decision_of([cand], "id1")
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_UNMAPPED)

    def test_same_name_different_directive_no_false_merge(self):
        # The audit bug: ``int`` declared as c:type / c:var / class merged into
        # one identity (or conflicted). The directive is now part of identity,
        # so they resolve to separate groups: no false merge, no false
        # conflict, and c:type/class each map to entity while c:var stays HELD.
        cands = [
            self._accepted("api_declaration",
                           {"directive": "c:type", "name": "int",
                            "signature": "int"}, "int", "int", cid="m1"),
            self._accepted("api_declaration",
                           {"directive": "c:var", "name": "int",
                            "signature": "int"}, "int", "int", cid="m2"),
            self._accepted("api_declaration",
                           {"directive": "class", "name": "int",
                            "signature": "int"}, "int", "int", cid="m3"),
        ]
        identities = {compute_identity("api_declaration", c) for c in cands}
        self.assertEqual(len(identities), 3)
        res = evaluate(cands)
        accepted = res.accepted_groups()
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len([d for d in res.decisions if d.decision == HOLD]),
                         1)
        self.assertEqual(len([d for d in res.decisions if d.decision == ACCEPT]),
                         2)
        self.assertFalse(any(g.conflict for g in res.groups))
        held = [d for d in res.decisions if d.decision == HOLD][0]
        self.assertEqual(held.reason_code, policy.HOLD_UNMAPPED)

    def test_inheritance_maps_to_relationship_extends(self):
        cands = [
            self._accepted("api_declaration",
                           {"directive": "class", "name": "Widget",
                            "signature": "Widget"}, "Widget", "class Widget",
                           cid="ew"),
            self._accepted("api_declaration",
                           {"directive": "class", "name": "Base",
                            "signature": "Base"}, "Base", "class Base",
                           cid="eb"),
            self._accepted("inheritance", {"class": "Widget", "base": "Base"},
                           "Base", "class Widget(Base)", cid="ei"),
        ]
        res = evaluate(cands)
        preview = build_preview(res)
        self.assertEqual(preview["proposed_relationships_count"], 1)
        rel = preview["proposed_relationships"][0]
        self.assertEqual(rel["relationship_type"], "extends")
        self.assertEqual(rel["target_name"], "Base")
        # endpoints follow the api_declaration identity convention
        from acceptance.mapping import _api_identity
        self.assertEqual(rel["source_node_id"], _api_identity("Widget"))
        self.assertEqual(rel["target_node_id"], _api_identity("Base"))

    def test_preview_node_ids_are_deterministic(self):
        cands = [
            self._accepted("api_declaration", {"directive": "module",
                                               "name": "math",
                                               "signature": "math"},
                          "math", "math", cid="p1"),
            self._accepted("api_declaration", {"directive": "module",
                                               "name": "math",
                                               "signature": "math"},
                          "math", "math", cid="p2"),
        ]
        a = build_preview(evaluate(cands))
        b = build_preview(evaluate(cands))
        self.assertEqual(a["proposed_nodes"], b["proposed_nodes"])

    def test_unmappable_accepted_candidate_is_held(self):
        cand = self._accepted("api_declaration",
                              {"directive": "function", "name": "f",
                               "signature": "f"}, "f", "f")
        with mock.patch.object(mapping, "is_mappable", return_value=False):
            res = evaluate([cand])
        d = res.decisions[0]
        self.assertEqual(d.decision, HOLD)
        self.assertEqual(d.reason_code, policy.HOLD_UNMAPPED)


class PreviewIntegrationTests(unittest.TestCase):
    def test_preview_never_touches_sqlite(self):
        db_path = os.path.join(tempfile.mkdtemp(), "knowledge.db")
        repo = KnowledgeRepository(db_path)
        repo.initialize()
        repo.add_source("seed")
        repo.add_node("seed-node", "concept", "Seed", "a seeded node",
                      source_id=1)
        before_nodes = repo.count_nodes()
        before_sources = repo.conn.execute(
            "SELECT COUNT(*) FROM sources").fetchone()[0]

        cands = [
            make_candidate("q1", "api_declaration", "def f()", "def f()",
                           "d.rst", {"directive": "function", "name": "f"}),
        ]
        build_preview(evaluate(cands))
        build_summary(evaluate(cands))

        self.assertEqual(repo.count_nodes(), before_nodes)
        after_sources = repo.conn.execute(
            "SELECT COUNT(*) FROM sources").fetchone()[0]
        self.assertEqual(after_sources, before_sources)
        repo.close()

    def test_preview_produces_valid_ingestion_source_shape(self):
        cands = [
            make_candidate("n1", "api_declaration", "math", "math",
                           "a.rst", {"directive": "module", "name": "math",
                                     "signature": "math"}),
            make_candidate("n2", "procedure", "Creating a Widget",
                           "Creating a Widget", "b.rst",
                           {"heading": "Creating a Widget"}),
        ]
        preview = build_preview(evaluate(cands))
        nodes = [{
            "id": n["id"],
            "type": n["type"],
            "name": n["name"],
            "description": n["description"],
            "relationships": [],
        } for n in preview["proposed_nodes"]]
        source = build_source("preview", nodes,
                              location="<acceptance-preview>")
        result = validate_source(source)
        self.assertTrue(result.valid, [e.as_dict() for e in result.errors])
        node_types = {n["type"] for n in preview["proposed_nodes"]}
        self.assertLessEqual(node_types, VALID_TYPES)

    def test_no_knowledge_db_created_by_preview(self):
        cands = [
            make_candidate("z1", "definition", "mutable", "mutable", "d.rst",
                           {"term": "mutable", "definition": "x"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            os.chdir(tmp)
            try:
                build_preview(evaluate(cands))
                found = [p for _, _, fs in os.walk(tmp)
                         for p in fs if p.endswith(".db") or p.endswith(".sqlite")]
                self.assertEqual(found, [])
            finally:
                os.chdir(_ROOT)


class DeterminismTests(unittest.TestCase):
    def test_repeated_evaluation_identical(self):
        cands = [
            make_candidate("d1", "dependency", "import math", "import math",
                           "a.rst", {"module": "math", "origin": "stdlib"}),
            make_candidate("d2", "code_example", "x = 1", "x = 1", "a.rst",
                           {"source": "literal", "language": "python"}),
            make_candidate("d3", "reference", ":func:`len`", ":func:`len`",
                           "b.rst", {"kind": "role", "role": "func",
                                     "target": "len"}),
        ]
        s1 = build_summary(evaluate(cands))
        s2 = build_summary(evaluate(cands))
        self.assertEqual(json.dumps(s1, sort_keys=True),
                         json.dumps(s2, sort_keys=True))


class LoaderTests(unittest.TestCase):
    def _write_corpus(self, tmp):
        c1 = make_candidate("l1", "api_declaration", "def f()", "def f()",
                            "a.rst", {"directive": "function", "name": "f"})
        write_candidate_file(tmp, "a.rst", [c1],
                             validations=[{"candidate_id": "l1", "valid": True}])
        c2 = make_candidate("l2", "api_declaration", "def g()", "def g()",
                            "b.rst", {"directive": "function", "name": "g"})
        write_candidate_file(tmp, "b.rst", [c2],
                             validations=[{"candidate_id": "l2", "valid": False}])
        return tmp

    def test_loads_directory_of_candidate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_corpus(tmp)
            cands, valid = load_candidates(tmp)
            self.assertEqual(len(cands), 2)
            self.assertEqual(valid["l1"], True)
            self.assertEqual(valid["l2"], False)

    def test_loads_single_candidate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_corpus(tmp)
            single = os.path.join(tmp, "candidates", "a.json")
            cands, valid = load_candidates(single)
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0]["candidate_id"], "l1")

    def test_missing_dir_raises(self):
        with self.assertRaises(CandidateLoadError):
            load_candidates("/nonexistent/candidate/output")

    def test_loader_ignores_compiler_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_corpus(tmp)
            with open(os.path.join(tmp, "compiler_report.json"), "w") as f:
                f.write("{}")
            cands, _ = load_candidates(tmp)
            self.assertEqual(len(cands), 2)


class CLITests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "acceptance", *args],
                              cwd=_ROOT, capture_output=True, text=True)

    def test_evaluate_returns_json_summary(self):
        # Uses the pre-compiled tutorial candidate output already in the repo.
        r = self._run("evaluate", TUTORIAL_OUT)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["total_candidates"],
                         out["accepted"] + out["held"] + out["rejected"])
        self.assertIn("provenance_coverage", out)
        self.assertIn("counts_by_kind", out)
        self.assertIn("counts_by_reason", out)

    def test_preview_returns_json_preview(self):
        r = self._run("preview", TUTORIAL_OUT)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("proposed_nodes_count", out)
        self.assertIn("proposed_relationships_count", out)
        self.assertEqual(out["proposed_nodes_count"],
                         len(out["proposed_nodes"]))

    def test_evaluate_with_output_writes_full_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = os.path.join(tmp, "acc")
            r = self._run("evaluate", TUTORIAL_OUT, "--output", outdir)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(outdir, "acceptance_evaluation.json")) as f:
                data = json.load(f)
            self.assertEqual(len(data["decisions"]),
                             data["summary"]["total_candidates"])

    def test_missing_input_returns_error_json(self):
        r = self._run("evaluate", "/nonexistent/nowhere")
        self.assertEqual(r.returncode, 1)
        out = json.loads(r.stdout)
        self.assertIn("error", out)

    def test_repeated_cli_evaluation_is_identical(self):
        import subprocess as sp
        def run():
            p = sp.run([sys.executable, "-m", "acceptance", "evaluate",
                        TUTORIAL_OUT], cwd=_ROOT, capture_output=True, text=True)
            return p.stdout
        self.assertEqual(run(), run())


class AuditReportTests(unittest.TestCase):
    def _corpus(self):
        return [
            make_candidate("a1", "api_declaration", "math", "math", "d.rst",
                           {"directive": "module", "name": "math",
                            "signature": "math"}),
            make_candidate("a2", "api_declaration", "int", "int", "d.rst",
                           {"directive": "c:type", "name": "int",
                            "signature": "int"}),
            make_candidate("a3", "api_declaration", "int", "int", "d.rst",
                           {"directive": "class", "name": "int",
                            "signature": "int"}),
            make_candidate("a4", "code_example", "x = 1", "x = 1", "d.rst",
                           {"source": "literal", "language": "python"}),
            make_candidate("a5", "dependency", "import os", "import os", "d.rst",
                           {"module": "os", "origin": "stdlib"}),
            make_candidate("a6", "inheritance", "Base", "class Widget(Base)",
                           "d.rst", {"class": "Widget", "base": "Base"}),
            make_candidate("a7", "api_declaration", "Widget", "Widget", "d.rst",
                           {"directive": "class", "name": "Widget",
                            "signature": "Widget"}),
            make_candidate("a8", "api_declaration", "Base", "Base", "d.rst",
                           {"directive": "class", "name": "Base",
                            "signature": "Base"}),
        ]

    def test_audit_breaks_down_accepted_identities_by_node_type(self):
        audit = build_audit(evaluate(self._corpus()))
        by_type = audit["accepted_identities_by_node_type"]
        self.assertEqual(by_type["technology"]["accepted_identities"], 1)
        self.assertEqual(by_type["entity"]["accepted_identities"], 4)
        self.assertEqual(by_type["example"]["accepted_identities"], 1)
        self.assertEqual(by_type["dependency"]["accepted_identities"], 1)
        # inheritance maps to a relationship, never a node
        rels = audit["accepted_identities_relationship_only"]
        self.assertEqual(rels["extends"]["accepted_identities"], 1)
        self.assertEqual(audit["unmappable_accepted_identities"], [])

    def test_audit_exposes_shell_rejections_and_setup_holds(self):
        cands = self._corpus() + [
            make_candidate("b1", "code_example", "$ pip install x",
                           "$ pip install x", "d.rst",
                           {"source": "code-block", "language": "bash"}),
            make_candidate("b2", "code_example", "import os",
                           "import os", "d.rst",
                           {"source": "literal", "language": "python"}),
            make_candidate("b3", "api_declaration", "def f()", "def f()",
                           "d.rst", {"directive": "function", "name": "f",
                                     "signature": "f"}),
        ]
        audit = build_audit(evaluate(cands))
        reasons = audit["decisions_by_reason_code"]
        self.assertGreater(reasons.get(policy.REJECT_SHELL, 0), 0)
        self.assertGreater(reasons.get(policy.HOLD_SETUP, 0), 0)
        self.assertGreater(reasons.get(policy.HOLD_UNMAPPED, 0), 0)

    def test_audit_includes_example_categories(self):
        cands = self._corpus() + [
            make_candidate("c1", "code_example",
                           "Traceback (most recent call last):",
                           "Traceback (most recent call last):\n  ValueError",
                           "d.rst", {"source": "literal", "language": "python"}),
            make_candidate("c2", "code_example", "# a comment",
                           "# a comment", "d.rst",
                           {"source": "literal", "language": "python"}),
        ]
        audit = build_audit(evaluate(cands))
        cats = audit["accepted_examples_by_category"]
        self.assertEqual(cats.get("traceback_demo", 0), 1)
        self.assertEqual(cats.get("comment_only", 0), 1)


class SafetyGuardTests(unittest.TestCase):
    def test_guard_returns_when_writes_forbidden_flag_is_true(self):
        self.assertTrue(SQLITE_WRITES_FORBIDDEN)
        self.assertIsNone(guard_sqlite_read_only())

    def test_guard_raises_when_writes_would_be_allowed(self):
        with mock.patch("acceptance.safety.SQLITE_WRITES_FORBIDDEN", False):
            with self.assertRaises(SqliteWriteForbiddenError):
                guard_sqlite_read_only()


class PrepareCommandTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "acceptance", *args],
                              cwd=_ROOT, capture_output=True, text=True)

    def test_audit_command_returns_json(self):
        r = self._run("audit", TUTORIAL_OUT)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("accepted_identities_by_node_type", out)
        self.assertIn("decisions_by_reason_code", out)
        self.assertIn("summary", out)

    def test_prepare_requires_output(self):
        r = self._run("prepare", TUTORIAL_OUT)
        self.assertEqual(r.returncode, 2)  # argparse error

    def test_prepare_writes_import_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = os.path.join(tmp, "plan")
            r = self._run("prepare", TUTORIAL_OUT, "--output", outdir)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(outdir, "import_plan.json")) as f:
                plan = json.load(f)
            self.assertTrue(plan["read_only_guard"]["sqlite_writes_forbidden"])
            self.assertIn("proposed_nodes", plan["preview"])
            self.assertIn("decisions", plan)
            # the plan must NOT be a database write of any kind
            found = [p for _, _, fs in os.walk(outdir)
                     for p in fs if p.endswith(".db") or p.endswith(".sqlite")]
            self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()