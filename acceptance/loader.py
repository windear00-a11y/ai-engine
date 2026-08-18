"""Load candidate records from Knowledge Compiler JSON output.

The acceptance layer consumes the exact output of ``python -m
knowledge_compiler candidates`` -- it NEVER re-parses or re-extracts source
documents. Loading is a pure, deterministic read of the existing candidate
records plus their validation status.

Input shapes accepted:

* a single candidates JSON file (one document), or
* a directory produced by ``knowledge_compiler candidates`` (its ``--output``
  root): every ``candidates/*.json`` file is loaded.

Each candidate dict is normalized to the shared dict shape consumed by the
policy/evaluator; validation status is taken from the ``validations`` list that
the compiler already emitted.
"""

import json
import os


class CandidateLoadError(Exception):
    pass


def _is_candidate_file(path):
    return path.endswith(".json") and os.path.basename(path) != "compiler_report.json"


def _candidate_files(target):
    """Deterministic list of candidate JSON files under ``target``.

    The candidates directory is walked recursively (``knowledge_compiler``
    preserves the source tree layout under ``candidates/``), deterministic
    order by rel_path.
    """
    target = os.path.abspath(target)
    if os.path.isfile(target):
        if _is_candidate_file(target):
            return [target]
        raise CandidateLoadError(
            f"not a candidate JSON file: {target!r}")
    if not os.path.isdir(target):
        raise CandidateLoadError(f"candidate output not found: {target!r}")
    files = []
    cand_dir = os.path.join(target, "candidates")
    base = cand_dir if os.path.isdir(cand_dir) else target
    for root, _, names in os.walk(base):
        if os.path.basename(root) in (".git", "__pycache__"):
            continue
        for name in sorted(names):
            if _is_candidate_file(name):
                files.append(os.path.join(root, name))
    files.sort()
    if not files:
        raise CandidateLoadError(
            f"no candidate files found under {target!r}")
    return files


def load_candidates(target):
    """Return (candidates, valid_by_id) from Knowledge Compiler JSON output.

    ``candidates`` is a list of candidate dicts (already in the compiler's
    ``Candidate.as_dict()`` shape, normalized); ``valid_by_id`` maps
    ``candidate_id`` -> bool using the compiler's validation records.
    """
    candidates = []
    valid_by_id = {}
    for path in _candidate_files(target):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for record in data.get("validations", []):
            cid = record.get("candidate_id")
            if cid:
                valid_by_id[cid] = bool(record.get("valid"))
        for cand in data.get("candidates", []):
            if not isinstance(cand, dict):
                continue
            candidates.append(_normalize(cand))
    candidates.sort(key=lambda c: (c.get("document", ""),
                                   c.get("candidate_id", "")))
    return candidates, valid_by_id


def _normalize(cand):
    """Normalize one candidate dict to the shape policies consume."""
    meta = cand.get("meta") or {}
    location = cand.get("location") or {}
    return {
        "candidate_id": cand.get("candidate_id") or "",
        "kind": cand.get("kind") or "",
        "summary": cand.get("summary") or "",
        "evidence": cand.get("evidence") or "",
        "document": cand.get("document") or "",
        "section_path": list(cand.get("section_path") or []),
        "location": {
            "path": location.get("path", ""),
            "line_start": location.get("line_start", 1),
            "line_end": location.get("line_end", 1),
        },
        "confidence": cand.get("confidence") or "high",
        "meta": dict(meta),
        "state": cand.get("state") or "",
    }
