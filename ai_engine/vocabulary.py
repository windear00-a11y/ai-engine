"""Generic, injectable vocabulary for the Persistent Intelligence System (Phase 1).

Unlike the legacy retrieval.vocabulary which treats type as free-form,
this module provides an explicit Vocabulary object that can be loaded
from JSON (vocabularies/*.json) and injected into Memory, validator,
and repository paths.

- Any non-empty string remains technically valid in the legacy validator
  (backward compat). This module adds a *recommended* check that can be
  enforced at the Memory facade layer when a project declares a vocabulary.
- Relationship kinds follow the same principle.

No network, no AI, stdlib-only, deterministic JSON loading.
"""

import json
import os
import re

_VOCAB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vocabularies")
# Also check package-internal vocabularies (for installed package)
_PKG_VOCAB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vocabularies")

# Strict allow-list for vocabulary ids. No slashes, dots, or traversal
# syntax: ids are used inside filesystem paths (vocabularies/<id>.json).
_VOCAB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _validate_vocab_id(vocab_id):
    if not isinstance(vocab_id, str) or not vocab_id.strip():
        raise ValueError("vocab_id must be a non-empty string")
    vocab_id = vocab_id.strip()
    if not _VOCAB_ID_RE.match(vocab_id):
        raise ValueError(
            f"invalid vocab_id {vocab_id!r}: must match {_VOCAB_ID_RE.pattern}"
        )
    return vocab_id


def _resolve_vocab_path(vocab_id):
    # Prefer package-internal, then top-level. Id is validated and the
    # resolved path is contained within one of the two vocab dirs.
    vocab_id = _validate_vocab_id(vocab_id)
    for base in [_PKG_VOCAB_DIR, _VOCAB_DIR]:
        p = os.path.join(base, f"{vocab_id}.json")
        if os.path.realpath(p).startswith(os.path.realpath(base) + os.sep):
            if os.path.exists(p):
                return p
    return os.path.join(_VOCAB_DIR, f"{vocab_id}.json")

class Vocabulary:
    """Injectable vocabulary.

    Fields:
        id: str (e.g. "diary_v1", "code_v1")
        types: frozenset[str]
        relationship_kinds: frozenset[str]
        description, version
    """

    def __init__(self, vocab_id, types, relationship_kinds, description=None, version=None, raw=None):
        if not isinstance(vocab_id, str) or not vocab_id.strip():
            raise ValueError("vocab_id must be non-empty string")
        self.id = vocab_id.strip()
        self.types = frozenset(t.strip() for t in (types or []) if isinstance(t, str) and t.strip())
        self.relationship_kinds = frozenset(r.strip() for r in (relationship_kinds or []) if isinstance(r, str) and r.strip())
        self.description = description
        self.version = version
        self.raw = raw or {}

        if not self.types:
            raise ValueError("vocabulary must declare at least one type")
        if not self.relationship_kinds:
            raise ValueError("vocabulary must declare at least one relationship kind")

    def is_valid_type(self, value):
        """Whether `value` is a member of this vocabulary (strict)."""
        return isinstance(value, str) and value.strip() in self.types

    def is_valid_relationship_kind(self, value):
        return isinstance(value, str) and value.strip() in self.relationship_kinds

    def is_recommended_type(self, value):
        # Alias for strict in this layer; legacy retrieval.vocabulary keeps free-form
        return self.is_valid_type(value)

    def is_recommended_relationship_kind(self, value):
        return self.is_valid_relationship_kind(value)

    def to_dict(self):
        return {
            "id": self.id,
            "description": self.description,
            "version": self.version,
            "types": sorted(self.types),
            "relationship_kinds": sorted(self.relationship_kinds),
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("vocabulary data must be dict")
        vocab_id = data.get("id")
        types = data.get("types")
        rels = data.get("relationship_kinds")
        if not isinstance(types, list) or not isinstance(rels, list):
            raise ValueError("vocabulary must contain 'types' and 'relationship_kinds' lists")
        return cls(vocab_id, types, rels, description=data.get("description"), version=data.get("version"), raw=data)

    @classmethod
    def from_file(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def load(cls, vocab_id):
        """Load by id, e.g. 'diary_v1' -> vocabularies/diary_v1.json"""
        vocab_id = _validate_vocab_id(vocab_id)
        path = _resolve_vocab_path(vocab_id)
        if not os.path.exists(path):
            raise ValueError(f"vocabulary not found: {vocab_id!r}")
        return cls.from_file(path)


def get_vocab_dir():
    # Prefer package-internal if exists, else top-level
    if os.path.isdir(_PKG_VOCAB_DIR):
        return _PKG_VOCAB_DIR
    return _VOCAB_DIR


def list_vocabularies():
    """Return sorted list of available vocabulary ids."""
    dirs = []
    for d in [_PKG_VOCAB_DIR, _VOCAB_DIR]:
        if os.path.isdir(d):
            dirs.append(d)
    # Use first existing that has files
    for d in dirs:
        out = []
        for fn in os.listdir(d):
            if fn.endswith(".json"):
                out.append(fn[:-5])
        if out:
            return sorted(set(out))
    return []


# Convenience singletons (lazy)
_DIARY_V1 = None
_CODE_V1 = None

def diary_v1():
    global _DIARY_V1
    if _DIARY_V1 is None:
        _DIARY_V1 = Vocabulary.load("diary_v1")
    return _DIARY_V1

def code_v1():
    global _CODE_V1
    if _CODE_V1 is None:
        _CODE_V1 = Vocabulary.load("code_v1")
    return _CODE_V1
