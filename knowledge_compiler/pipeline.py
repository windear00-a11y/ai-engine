"""Knowledge Compiler pipeline orchestration.

    RAW  ->  EXTRACTED  ->  CANDIDATE  ->  VALIDATED  ->  (IMPORTED manual)

Honors the strict separation between source evidence, candidates, and trusted
knowledge: nothing produced here is ever inserted into the knowledge database.
Import remains a deliberate, manual, opt-in step.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from knowledge_compiler.adapters import pick_adapter
from knowledge_compiler.scanner import discover, make_manifest
from knowledge_compiler.extractor import extract_candidates
from knowledge_compiler.validator import validate_candidates
from knowledge_compiler import output as out


def _source_name(source_path):
    base = os.path.basename(os.path.abspath(source_path).rstrip(os.sep))
    return base or "source"


def _resolve_adapter(source_path):
    if os.path.isfile(source_path):
        adapter = pick_adapter(source_path)
        if adapter is None:
            raise ValueError(
                f"no adapter can process {source_path!r} (expected RST for .rst)")
        return adapter
    # Directory: pick the first adapter that discovers any supported files.
    from knowledge_compiler.adapters import ADAPTERS
    for cls in ADAPTERS:
        inst = cls()
        found = discover(inst, source_path)
        if found:
            return inst
    raise ValueError(
        f"no adapter can process {source_path!r} (expected RST for .rst)")


class Pipeline:
    def __init__(self, output_root="output", adapter=None):
        self.output_root = output_root
        self._adapter = adapter

    def _adapter_for(self, source_path):
        if self._adapter is not None:
            return self._adapter
        return _resolve_adapter(source_path)

    def scan(self, source_path):
        adapter = self._adapter_for(source_path)
        found = discover(adapter, source_path)
        manifest = make_manifest(source_path, found)
        manifest["adapter"] = adapter.name
        self._manifest = manifest
        return manifest

    def extract(self, source_path, report=True, limit=None):
        adapter = self._adapter_for(source_path)
        found = discover(adapter, source_path)
        if limit is not None:
            found = found[:limit]
        source_name = _source_name(source_path)
        documents = []
        for abs_path, rel_path in found:
            documents.append(adapter.parse(abs_path, rel_path, source_name))
        if report:
            self._write_documents(documents)
        return documents

    def candidates(self, source_path, report=True, limit=None):
        documents = self.extract(source_path, report=report, limit=limit)
        results = []
        for doc in documents:
            cands = extract_candidates(doc)
            records = validate_candidates(doc, cands)
            results.append({"document": doc, "candidates": cands,
                            "records": records})
        if report:
            self._write_candidates(results)
        return results

    # -- reporting ---------------------------------------------------------

    def _write_documents(self, documents):
        for doc in documents:
            out.write_document(self.output_root, doc)

    def _write_candidates(self, results):
        for r in results:
            out.write_candidates(self.output_root, r["document"],
                                 r["candidates"], r["records"])

    def summarize(self, source_path, command, limit=None):
        adapter = self._adapter_for(source_path)
        found = discover(adapter, source_path)
        if limit is not None:
            found = found[:limit]
        source_name = _source_name(source_path)
        documents = [adapter.parse(a, rel, source_name) for a, rel in found]

        from collections import Counter
        summary = {
            "command": command,
            "source": os.path.abspath(source_path),
            "source_name": source_name,
            "adapter": adapter.name,
            "documents": len(documents),
            "documents_with_errors": sum(1 for d in documents if d.errors),
            "errors": [(d.rel_path, [e[1] for e in d.errors]) for d in documents if d.errors],
            "chunk_counts": dict(Counter(c.kind for d in documents for c in d.chunks)),
        }
        if command == "candidates":
            from collections import Counter
            cand_kinds = Counter()
            validated = 0
            total = 0
            for d in documents:
                for c in extract_candidates(d):
                    cand_kinds[c.kind] += 1
                    total += 1
                    rec = [x for x in validate_candidates(d, [c])][0]
                    if rec.valid:
                        validated += 1
            summary["candidate_counts"] = dict(cand_kinds)
            summary["candidate_total"] = total
            summary["candidate_validated"] = validated
            summary["candidate_invalid"] = total - validated
        return summary