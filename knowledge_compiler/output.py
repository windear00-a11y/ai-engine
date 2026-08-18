"""Deterministic JSON output for extracted documents and candidates."""

import json
import os


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def stable_dump(obj, path):
    """Write JSON with stable key ordering and 2-space indent (deterministic)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def rel_out_path(rel_path, suffix=".json"):
    base = rel_path
    if base.endswith(".rst"):
        base = base[: -len(".rst")]
    return base + suffix


def write_document(out_dir, document):
    target = os.path.join(out_dir, "extracted", rel_out_path(document.rel_path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    stable_dump(document.as_dict(), target)
    return target


def write_candidates(out_dir, document, candidates, records=None):
    target = os.path.join(out_dir, "candidates", rel_out_path(document.rel_path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    payload = {
        "document": document.rel_path,
        "title": document.title,
        "count": len(candidates),
        "candidates": [c.as_dict() for c in candidates],
        "validations": [r.as_dict() for r in (records or [])],
    }
    stable_dump(payload, target)
    return target


def write_report(out_dir, report):
    path = _ensure_dir(out_dir)
    target = os.path.join(path, "compiler_report.json")
    stable_dump(report, target)
    return target