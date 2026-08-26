"""Phase 4: Multi-Domain Acceptance Testing for Universal Data Ingestion v1.

Proves the universal ingestion pipeline behaves consistently across domains.
Uses realistic test-only datasets for 8 domain types with cross-domain
relationships.  Every dataset tested through the full pipeline:
  validate -> dry-run -> stage -> preview -> experimental apply -> Contract v1

IMPORTANT: No production database is ever modified.
"""

import hashlib
import http.client
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from external_import.validator import validate_external
from external_import.dry_run import dry_run
from external_import.staging import create_staging
from external_import.preview import preview, human_preview_summary
from external_import.apply import apply, _sha256

KNOWLEDGE_DB = os.path.join(_ROOT, "database", "knowledge.db")
PROD_HASH = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


# --- Dataset helpers ---

def _src(name, version="1.0"):
    return {"name": name, "version": version, "location": f"file:///{name}"}


def _nd(nid, ntype, name, desc, **kw):
    n = {"id": nid, "type": ntype, "name": name, "description": desc}
    if kw:
        n["metadata"] = kw
    return n


def _rl(src, rtype, tgt, label=None):
    r = {"source_node_id": src, "relationship_type": rtype, "target_node_id": tgt}
    if label is not None:
        r["label"] = label
    return r


# --- 8 Domain Datasets ---

PERSON_DATA = {
    "source": _src("domain-persons"),
    "nodes": [
        _nd("person-ada-lovelace", "person", "Ada Lovelace",
            "English mathematician and writer, first computer programmer.",
            birth_year=1815, nationality="English"),
        _nd("person-alan-turing", "person", "Alan Turing",
            "English mathematician and logician, father of computer science.",
            birth_year=1912, nationality="English"),
    ],
    "relationships": [],
}

COMPANY_DATA = {
    "source": _src("domain-companies"),
    "nodes": [
        _nd("company-babbage", "company", "Babbage Computing",
            "Charles Babbage's analytical engine enterprise.",
            founded=1837, industry="mechanical_computing"),
        _nd("company-ibm", "company", "IBM",
            "International Business Machines Corporation.",
            founded=1911, industry="technology"),
    ],
    "relationships": [],
}

PRODUCT_DATA = {
    "source": _src("domain-products"),
    "nodes": [
        _nd("product-analytical-engine", "product", "Analytical Engine",
            "General-purpose mechanical computer designed by Charles Babbage.",
            version="1.0", category="hardware"),
        _nd("product-colossus", "product", "Colossus",
            "First programmable electronic digital computer.",
            version="1.0", category="hardware"),
    ],
    "relationships": [],
}

DOCUMENT_DATA = {
    "source": _src("domain-documents"),
    "nodes": [
        _nd("doc-lovelace-note", "document",
            "Notes on the Analytical Engine",
            "Ada Lovelace's notes describing the first algorithm.", year=1843),
    ],
    "relationships": [],
}

RESEARCH_PAPER_DATA = {
    "source": _src("domain-research-papers"),
    "nodes": [
        _nd("paper-computable-numbers", "research_paper",
            "On Computable Numbers, with an Application to the Entscheidungsproblem",
            "Foundational paper on computation and Turing machines.",
            year=1936, venue="London Mathematical Society"),
        _nd("paper-turing-test", "research_paper",
            "Computing Machinery and Intelligence",
            "Paper introducing the Turing Test.", year=1950, venue="Mind"),
    ],
    "relationships": [
        _rl("paper-turing-test", "references", "paper-computable-numbers",
            "extends"),
    ],
}

EVENT_DATA = {
    "source": _src("domain-events"),
    "nodes": [
        _nd("event-royal-society-1843", "event",
            "Royal Society Lecture 1843",
            "Ada Lovelace's presentation on the Analytical Engine.",
            date="1843-01-01"),
        _nd("event-dartmouth-1956", "event",
            "Dartmouth Conference 1956",
            "Founding event of artificial intelligence.", date="1956-06-18"),
    ],
    "relationships": [],
}

LOCATION_DATA = {
    "source": _src("domain-locations"),
    "nodes": [
        _nd("loc-london", "location", "London, England",
            "Capital city of England."),
        _nd("loc-cambridge", "location", "Cambridge, England",
            "University city, home to the University of Cambridge."),
    ],
    "relationships": [],
}

CUSTOM_DATA = {
    "source": _src("domain-custom"),
    "nodes": [
        _nd("disc-mechanics", "discipline", "Mechanics",
            "Branch of physics dealing with motion and forces."),
        _nd("disc-logic", "discipline", "Formal Logic",
            "Study of valid reasoning and inference."),
    ],
    "relationships": [
        _rl("disc-logic", "extends", "disc-mechanics"),
    ],
}

# Cross-domain relationships (all referenced nodes exist in their domain datasets)
CROSS_DOMAIN_RELS = [
    _rl("person-ada-lovelace", "authored", "doc-lovelace-note", "1843"),
    _rl("person-alan-turing", "authored", "paper-computable-numbers", "1936"),
    _rl("person-alan-turing", "works_at", "company-ibm"),
    _rl("company-babbage", "created", "product-analytical-engine"),
    _rl("company-ibm", "created", "product-colossus", "Team at Bletchley Park"),
    _rl("event-royal-society-1843", "located_at", "loc-london"),
    _rl("person-alan-turing", "attended", "event-dartmouth-1956"),
    _rl("person-alan-turing", "located_at", "loc-cambridge"),
]


def _all_datasets():
    return [
        ("person", PERSON_DATA), ("company", COMPANY_DATA),
        ("product", PRODUCT_DATA), ("document", DOCUMENT_DATA),
        ("research_paper", RESEARCH_PAPER_DATA), ("event", EVENT_DATA),
        ("location", LOCATION_DATA), ("custom", CUSTOM_DATA),
    ]


def _combined():
    nodes, rels = [], []
    for _, d in _all_datasets():
        nodes.extend(d["nodes"])
        rels.extend(d["relationships"])
    rels.extend(CROSS_DOMAIN_RELS)
    return {"source": {"name": "phase4-acceptance", "version": "1.0",
                       "location": "file:///phase4-acceptance"},
            "nodes": nodes, "relationships": rels}


# --- Test helpers ---

def _db_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _make_prod_copy(tmp):
    dst = os.path.join(tmp, "knowledge.db")
    shutil.copy2(KNOWLEDGE_DB, dst)
    return dst


def _prod_counts(db_path):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {"nodes": c.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                "relationships": c.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
                "sources": c.execute("SELECT COUNT(*) FROM sources").fetchone()[0]}
    finally:
        c.close()


def _apply_to_copy(data, tmp):
    prod = _make_prod_copy(tmp)
    staging = os.path.join(tmp, "staging.db")
    sr = create_staging(data, staging, prod)
    ar = apply(staging, prod)
    return sr, ar, prod


def _api_call(host, port, op, args=None, timeout=10):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    body = {"operation": op}
    if args:
        body["arguments"] = args
    raw = json.dumps(body).encode()
    conn.request("POST", "/v1/execute", body=raw,
                 headers={"Content-Type": "application/json",
                          "Content-Length": str(len(raw))})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return resp.status, data


def _start_server(db_path):
    from http_server.server import KnowledgeHTTPServer
    srv = KnowledgeHTTPServer(("127.0.0.1", 0), db_path=db_path)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def _close_server(srv):
    srv.shutdown()
    srv.server_close()
    srv.close()


# ======================================================================
# A-F: Per-Domain Acceptance Tests (via mixin)
# ======================================================================

class _DomainAcceptanceMixin:
    """Shared tests: validate, dry-run, stage, preview, apply, Contract v1.
    Subclasses set: dataset_name, dataset, primary_node, search_term.
    Optional: primary_rel (skip relationship tests if None).
    """

    primary_rel = None

    def _copy(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        return tmp, _make_prod_copy(tmp)

    # A. Validate
    def test_a_valid_accepted(self):
        r = validate_external(self.dataset)
        self.assertTrue(r.valid, f"{self.dataset_name}: {r.errors}")

    def test_a_malformed_rejected(self):
        self.assertFalse(validate_external({"source": "bad"}).valid)

    def test_a_custom_type_accepted(self):
        d = {"source": _src(f"ct-{self.dataset_name}"),
             "nodes": [_nd("cx-1", "my_type", "CX", "Custom.")],
             "relationships": []}
        self.assertTrue(validate_external(d).valid)

    # B. Dry-run
    def test_b_correct_counts(self):
        r = dry_run(self.dataset, db_path=KNOWLEDGE_DB)
        self.assertTrue(r.safe)
        self.assertEqual(r.nodes_total, len(self.dataset["nodes"]))
        self.assertEqual(r.relationships_total, len(self.dataset["relationships"]))

    def test_b_deterministic(self):
        self.assertEqual(dry_run(self.dataset, db_path=KNOWLEDGE_DB).as_dict(),
                         dry_run(self.dataset, db_path=KNOWLEDGE_DB).as_dict())

    def test_b_no_prod_mutation(self):
        h = _db_hash(KNOWLEDGE_DB)
        dry_run(self.dataset, db_path=KNOWLEDGE_DB)
        self.assertEqual(h, _db_hash(KNOWLEDGE_DB))

    # C. Stage
    def test_c_correct_nodes(self):
        tmp, prod = self._copy()
        r = create_staging(self.dataset, os.path.join(tmp, "s.db"), prod)
        self.assertTrue(r.success)
        self.assertEqual(r.nodes_staged, len(self.dataset["nodes"]))

    def test_c_correct_relationships(self):
        tmp, prod = self._copy()
        r = create_staging(self.dataset, os.path.join(tmp, "s.db"), prod)
        self.assertEqual(r.relationships_staged, len(self.dataset["relationships"]))

    def test_c_no_unrelated_records(self):
        tmp, prod = self._copy()
        sp = os.path.join(tmp, "s.db")
        create_staging(self.dataset, sp, prod)
        c = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                             len(self.dataset["nodes"]))
        finally:
            c.close()

    # D. Preview
    def test_d_human_readable(self):
        tmp, prod = self._copy()
        sp = os.path.join(tmp, "s.db")
        create_staging(self.dataset, sp, prod)
        s = human_preview_summary(preview(sp, prod))
        self.assertIn("SAFE TO APPLY", s)

    def test_d_machine_readable(self):
        tmp, prod = self._copy()
        sp = os.path.join(tmp, "s.db")
        create_staging(self.dataset, sp, prod)
        d = preview(sp, prod).as_dict()
        self.assertTrue(d["safe"])
        self.assertIn("new_nodes", d)

    # E. Experimental Apply
    def test_e_nodes_inserted(self):
        tmp, prod = self._copy()
        _, ar, _ = _apply_to_copy(self.dataset, tmp)
        self.assertTrue(ar.committed)
        self.assertEqual(ar.imported["nodes"], len(self.dataset["nodes"]))

    def test_e_relationships_inserted(self):
        tmp, prod = self._copy()
        _, ar, _ = _apply_to_copy(self.dataset, tmp)
        self.assertEqual(ar.imported["relationships"], len(self.dataset["relationships"]))

    def test_e_source_provenance(self):
        tmp, prod = self._copy()
        _, ar, _ = _apply_to_copy(self.dataset, tmp)
        self.assertEqual(ar.source["name"], self.dataset["source"]["name"])

    def test_e_existing_nodes_intact(self):
        tmp, prod = self._copy()
        b = _prod_counts(prod)
        _apply_to_copy(self.dataset, tmp)
        a = _prod_counts(prod)
        self.assertEqual(a["nodes"], b["nodes"] + len(self.dataset["nodes"]))
        self.assertEqual(a["relationships"],
                         b["relationships"] + len(self.dataset["relationships"]))
        self.assertEqual(a["sources"], b["sources"] + 1)

    def test_e_existing_example_of_unchanged(self):
        tmp, prod = self._copy()
        c = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
        before = c.execute(
            "SELECT COUNT(*) FROM relationships WHERE relationship_type='example_of'"
        ).fetchone()[0]
        c.close()
        _apply_to_copy(self.dataset, tmp)
        c = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
        after = c.execute(
            "SELECT COUNT(*) FROM relationships WHERE relationship_type='example_of'"
        ).fetchone()[0]
        c.close()
        self.assertEqual(before, after)

    # F. Contract v1 — node operations (always run)
    def test_f_search(self):
        tmp, prod = self._copy()
        _apply_to_copy(self.dataset, tmp)
        from api.knowledge_api import KnowledgeAPI
        api = KnowledgeAPI(db_path=prod)
        try:
            ids = [r["id"] for r in api.search(self.search_term)]
            self.assertIn(self.primary_node[0], ids)
        finally:
            api.close()

    def test_f_get(self):
        tmp, prod = self._copy()
        _apply_to_copy(self.dataset, tmp)
        from api.knowledge_api import KnowledgeAPI
        api = KnowledgeAPI(db_path=prod)
        try:
            n = api.get(self.primary_node[0])
            self.assertEqual(n["type"], self.primary_node[1])
            self.assertEqual(n["name"], self.primary_node[2])
        finally:
            api.close()

    def test_f_related(self):
        tmp, prod = self._copy()
        _apply_to_copy(self.dataset, tmp)
        from api.knowledge_api import KnowledgeAPI
        api = KnowledgeAPI(db_path=prod)
        try:
            rel = api.related(self.primary_node[0])
            self.assertIsInstance(rel, list)
            for r in rel:
                self.assertIn("node", r)
                self.assertIn("via", r)
        finally:
            api.close()

    def test_f_provenance(self):
        tmp, prod = self._copy()
        _apply_to_copy(self.dataset, tmp)
        from api.knowledge_api import KnowledgeAPI
        api = KnowledgeAPI(db_path=prod)
        try:
            p = api.provenance(self.primary_node[0])
            self.assertIsNotNone(p)
            self.assertEqual(p["source_name"], self.dataset["source"]["name"])
        finally:
            api.close()

    def test_f_inspect(self):
        tmp, prod = self._copy()
        _apply_to_copy(self.dataset, tmp)
        from api.knowledge_api import KnowledgeAPI
        api = KnowledgeAPI(db_path=prod)
        try:
            s = api.inspect()
            self.assertIn(self.primary_node[1], s["nodes_by_type"])
        finally:
            api.close()

    def test_f_envelope_version(self):
        tmp, prod = self._copy()
        _apply_to_copy(self.dataset, tmp)
        from api.tools import ToolInterface
        t = ToolInterface(db_path=prod)
        try:
            r = t.execute({"operation": "search",
                           "arguments": {"query": self.search_term}})
            self.assertTrue(r["ok"])
            self.assertEqual(r["contract_version"], "1")
        finally:
            t.close()

    def test_f_error_envelope(self):
        tmp, prod = self._copy()
        _apply_to_copy(self.dataset, tmp)
        from api.tools import ToolInterface
        t = ToolInterface(db_path=prod)
        try:
            r = t.execute({"operation": "get",
                           "arguments": {"node_id": "nonexistent-x"}})
            self.assertFalse(r["ok"])
            self.assertEqual(r["contract_version"], "1")
            self.assertIn("error", r)
        finally:
            t.close()

    def test_f_follow(self):
        if self.primary_rel is None:
            self.skipTest("No intra-domain relationships")
        tmp, prod = self._copy()
        _apply_to_copy(self.dataset, tmp)
        from api.knowledge_api import KnowledgeAPI
        api = KnowledgeAPI(db_path=prod)
        try:
            results = api.follow(self.primary_rel[0],
                                 relationship_type=self.primary_rel[1])
            self.assertTrue(len(results) > 0)
            targets = [r["target_node_id"] for r in results]
            self.assertIn(self.primary_rel[2], targets)
        finally:
            api.close()


# --- Concrete domain classes ---

class TestPerson(_DomainAcceptanceMixin, unittest.TestCase):
    dataset_name = "person"
    dataset = PERSON_DATA
    primary_node = ("person-ada-lovelace", "person", "Ada Lovelace")
    search_term = "Ada Lovelace"


class TestCompany(_DomainAcceptanceMixin, unittest.TestCase):
    dataset_name = "company"
    dataset = COMPANY_DATA
    primary_node = ("company-ibm", "company", "IBM")
    search_term = "IBM"


class TestProduct(_DomainAcceptanceMixin, unittest.TestCase):
    dataset_name = "product"
    dataset = PRODUCT_DATA
    primary_node = ("product-analytical-engine", "product", "Analytical Engine")
    search_term = "Analytical Engine"


class TestDocument(_DomainAcceptanceMixin, unittest.TestCase):
    dataset_name = "document"
    dataset = DOCUMENT_DATA
    primary_node = ("doc-lovelace-note", "document",
                    "Notes on the Analytical Engine")
    search_term = "Notes on the Analytical Engine"


class TestResearchPaper(_DomainAcceptanceMixin, unittest.TestCase):
    dataset_name = "research_paper"
    dataset = RESEARCH_PAPER_DATA
    primary_node = ("paper-computable-numbers", "research_paper",
                    "On Computable Numbers, with an Application to the Entscheidungsproblem")
    primary_rel = ("paper-turing-test", "references", "paper-computable-numbers")
    search_term = "Computable Numbers"


class TestEvent(_DomainAcceptanceMixin, unittest.TestCase):
    dataset_name = "event"
    dataset = EVENT_DATA
    primary_node = ("event-dartmouth-1956", "event", "Dartmouth Conference 1956")
    search_term = "Dartmouth Conference"


class TestLocation(_DomainAcceptanceMixin, unittest.TestCase):
    dataset_name = "location"
    dataset = LOCATION_DATA
    primary_node = ("loc-cambridge", "location", "Cambridge, England")
    search_term = "Cambridge"


class TestCustom(_DomainAcceptanceMixin, unittest.TestCase):
    dataset_name = "custom"
    dataset = CUSTOM_DATA
    primary_node = ("disc-logic", "discipline", "Formal Logic")
    primary_rel = ("disc-logic", "extends", "disc-mechanics")
    search_term = "Formal Logic"


# ======================================================================
# G-K: Cross-Domain Acceptance Tests
# ======================================================================

class TestCrossDomainValidation(unittest.TestCase):
    """G: Cross-domain dataset validates (all nodes defined across domains)."""

    def test_cross_domain_validates(self):
        r = validate_external(_combined())
        self.assertTrue(r.valid, f"{r.errors}")

    def test_cross_domain_node_count(self):
        r = validate_external(_combined())
        total = sum(len(d["nodes"]) for _, d in _all_datasets())
        self.assertEqual(len(r.nodes), total)

    def test_cross_domain_rel_count(self):
        r = validate_external(_combined())
        total_intra = sum(len(d["relationships"]) for _, d in _all_datasets())
        self.assertEqual(len(r.relationships),
                         total_intra + len(CROSS_DOMAIN_RELS))


class TestCrossDomainDryRun(unittest.TestCase):
    """H: Cross-domain dry-run produces safe report."""

    def test_cross_domain_safe(self):
        r = dry_run(_combined(), db_path=KNOWLEDGE_DB)
        self.assertTrue(r.safe)
        self.assertEqual(r.nodes_total,
                         sum(len(d["nodes"]) for _, d in _all_datasets()))
        self.assertEqual(r.relationships_total,
                         sum(len(d["relationships"]) for _, d in _all_datasets())
                         + len(CROSS_DOMAIN_RELS))

    def test_cross_domain_no_prod_mutation(self):
        h = _db_hash(KNOWLEDGE_DB)
        dry_run(_combined(), db_path=KNOWLEDGE_DB)
        self.assertEqual(h, _db_hash(KNOWLEDGE_DB))


class TestCrossDomainApply(unittest.TestCase):
    """I: Cross-domain experimental apply with verification."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.prod = _make_prod_copy(self.tmp)
        self.before = _prod_counts(self.prod)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_j_all_nodes_inserted(self):
        combined = _combined()
        staging = os.path.join(self.tmp, "s.db")
        create_staging(combined, staging, self.prod)
        ar = apply(staging, self.prod)
        self.assertTrue(ar.committed)
        self.assertEqual(ar.imported["nodes"], len(combined["nodes"]))
        self.assertEqual(ar.imported["relationships"], len(combined["relationships"]))

    def test_k_all_types_queryable(self):
        combined = _combined()
        staging = os.path.join(self.tmp, "s.db")
        create_staging(combined, staging, self.prod)
        apply(staging, self.prod)
        from api.knowledge_api import KnowledgeAPI
        api = KnowledgeAPI(db_path=self.prod)
        try:
            searches = [
                ("person", "Ada Lovelace"), ("company", "IBM"),
                ("product", "Analytical Engine"), ("document", "Notes on the Analytical Engine"),
                ("research_paper", "Computable Numbers"), ("event", "Dartmouth"),
                ("location", "Cambridge"), ("discipline", "Formal Logic"),
            ]
            for ntype, query in searches:
                results = api.search(query, node_type=ntype)
                self.assertTrue(len(results) > 0, f"{ntype}: no results for '{query}'")
        finally:
            api.close()

    def test_l_cross_domain_follow(self):
        combined = _combined()
        staging = os.path.join(self.tmp, "s.db")
        create_staging(combined, staging, self.prod)
        apply(staging, self.prod)
        from api.knowledge_api import KnowledgeAPI
        api = KnowledgeAPI(db_path=self.prod)
        try:
            results = api.follow("person-ada-lovelace", "authored")
            self.assertTrue(len(results) > 0)
            targets = [r["target_node_id"] for r in results]
            self.assertIn("doc-lovelace-note", targets)
        finally:
            api.close()


class TestConflictResolution(unittest.TestCase):
    """M: Conflict resolution across domains."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.prod = _make_prod_copy(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_identical_reimport_skips(self):
        combined = _combined()
        staging1 = os.path.join(self.tmp, "s1.db")
        create_staging(combined, staging1, self.prod)
        apply(staging1, self.prod)

        staging2 = os.path.join(self.tmp, "s2.db")
        r = create_staging(combined, staging2, self.prod)
        self.assertTrue(r.success)
        self.assertTrue(r.nodes_skipped > 0, "Identical nodes should be skipped")
        self.assertEqual(r.nodes_staged, 0)
        self.assertEqual(r.relationships_staged, 0)

    def test_exclude_conflict_not_in_staging(self):
        import copy
        d1 = copy.deepcopy(_combined())
        d2 = copy.deepcopy(_combined())
        d2["nodes"][0]["description"] = "MODIFIED"

        staging1 = os.path.join(self.tmp, "s1.db")
        create_staging(d1, staging1, self.prod)
        apply(staging1, self.prod)

        staging2 = os.path.join(self.tmp, "s2.db")
        r = create_staging(d2, staging2, self.prod)
        self.assertTrue(r.success)
        self.assertTrue(r.nodes_conflicting > 0, "Expected at least one conflict")
        self.assertIn(d2["nodes"][0]["id"], r.conflicting_node_ids)

    def test_existing_relationship_skip(self):
        import copy
        combined = _combined()
        staging1 = os.path.join(self.tmp, "s1.db")
        create_staging(combined, staging1, self.prod)
        apply(staging1, self.prod)

        combined2 = copy.deepcopy(_combined())
        staging2 = os.path.join(self.tmp, "s2.db")
        r = create_staging(combined2, staging2, self.prod)
        self.assertTrue(r.relationships_skipped >= len(CROSS_DOMAIN_RELS),
                        f"Expected >= {len(CROSS_DOMAIN_RELS)} skipped rels, "
                        f"got {r.relationships_skipped}")


# ======================================================================
# Web App API Tests (via HTTP)
# ======================================================================

class TestWebAppAPI(unittest.TestCase):
    """N: HTTP webapp API works correctly with multi-domain data."""

    @classmethod
    def setUpClass(cls):
        import copy
        cls.tmp = tempfile.mkdtemp()
        cls.prod = _make_prod_copy(cls.tmp)
        combined = copy.deepcopy(_combined())
        staging = os.path.join(cls.tmp, "s.db")
        create_staging(combined, staging, cls.prod)
        apply(staging, cls.prod)
        cls.srv, cls.port = _start_server(cls.prod)

    @classmethod
    def tearDownClass(cls):
        _close_server(cls.srv)
        shutil.rmtree(cls.tmp)

    def _post(self, op, args=None):
        return _api_call("127.0.0.1", self.port, op, args)

    def test_search_person(self):
        status, data = self._post("search", {"query": "Ada Lovelace"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        ids = [r["id"] for r in data["result"]]
        self.assertIn("person-ada-lovelace", ids)

    def test_search_product(self):
        status, data = self._post("search", {"query": "Analytical Engine"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        ids = [r["id"] for r in data["result"]]
        self.assertIn("product-analytical-engine", ids)

    def test_get_location(self):
        status, data = self._post("get", {"node_id": "loc-cambridge"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["type"], "location")

    def test_get_event(self):
        status, data = self._post("get", {"node_id": "event-dartmouth-1956"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["name"], "Dartmouth Conference 1956")

    def test_related_company(self):
        status, data = self._post("related", {"node_id": "company-ibm"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["result"], list)

    def test_follow_authored(self):
        status, data = self._post("follow", {
            "node_id": "person-ada-lovelace",
            "relationship_type": "authored"
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        targets = [r["target_node_id"] for r in data["result"]]
        self.assertIn("doc-lovelace-note", targets)

    def test_follow_works_at(self):
        status, data = self._post("follow", {
            "node_id": "person-alan-turing",
            "relationship_type": "works_at"
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        targets = [r["target_node_id"] for r in data["result"]]
        self.assertIn("company-ibm", targets)

    def test_follow_located_at(self):
        status, data = self._post("follow", {
            "node_id": "person-alan-turing",
            "relationship_type": "located_at"
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        targets = [r["target_node_id"] for r in data["result"]]
        self.assertIn("loc-cambridge", targets)

    def test_provenance(self):
        status, data = self._post("provenance", {"node_id": "disc-logic"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["source_name"], "phase4-acceptance")

    def test_inspect(self):
        status, data = self._post("inspect")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        for ntype in ("person", "company", "product", "document",
                      "research_paper", "event", "location", "discipline"):
            self.assertIn(ntype, data["result"]["nodes_by_type"])

    def test_error_envelope(self):
        status, data = self._post("get", {"node_id": "nonexistent-xyz"})
        self.assertIn(status, (200, 404))
        self.assertFalse(data["ok"])
        self.assertEqual(data["contract_version"], "1")
        self.assertIn("error", data)

    def test_invalid_operation(self):
        status, data = self._post("bogus_op")
        self.assertIn(status, (200, 404))
        self.assertFalse(data["ok"])
        self.assertIn("error", data)
