"""Ranker abstraction — deterministic, explainable, injectable (Phase 2).

Provides:
    Ranker (ABC) -> KeywordRanker (deterministic default)

- No embeddings, no LLM, no network, stdlib-only.
- Deterministic: same query_terms + candidates + context -> identical ordering
  (tie-break by node id ASC, stable sort).
- Explainable: each candidate gets _score dict with raw/context_match/quality/final
- Context-aware: optional context dict influences score via context_match factor
- Provenance preserved: caller keeps original node provenance

Memory.recall() uses Ranker via search_rankings -> hydrate -> rank -> slice.
"""

import abc
import hashlib
import json


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class Ranker(abc.ABC):
    """Abstract ranker — injectable.

    Implementations must be deterministic and have no side effects.
    """

    @abc.abstractmethod
    def rank(self, query_terms, candidates, context=None):
        """Rank candidates deterministically.

        Args:
            query_terms: list[str] — lowercased query tokens
            candidates: list[dict] — hydrated nodes (each has id, type, name, description, metadata, provenance...)
            context: optional dict — e.g. {"context_id": "ctx_..."} or {"project": {...}}

        Returns:
            list[dict] — candidates sorted descending by final_score, each annotated with _score dict.
            Must not mutate input dicts beyond adding _score.
        """
        raise NotImplementedError


class KeywordRanker(Ranker):
    """Deterministic keyword ranker — default for Phase 2.

    Scoring (per spec, explainable):
        raw_score = sum(1 for w in query_terms if w in node_text)
        node_text = " ".join(str(v) for v in (id, type, name, description, metadata_json)).lower()
        context_match = 1.0 if no context filter else (1.0 if node_context == filter_context else 0.0 or 0.5)
        quality = context_match * lifecycle_factor (lifecycle not yet used in Phase 2 -> 1.0)
        final_score = round(raw_score * quality, 6)

    Filtering:
        - raw_score == 0 -> excluded (no match)
        - if context contains context_id and candidate has _context_id field: candidates with
          mismatching context_id get quality 0.0 -> excluded when context_strict=True, else down-ranked.

    Determinism: sorted by (-final_score, node_id ASC) — stable across runs/machines.
    """

    def __init__(self, context_strict=False):
        # context_strict: if True, mismatching context -> filtered out (quality 0 -> exclude)
        # if False, mismatching -> down-ranked to 0.5 but still visible
        self.context_strict = bool(context_strict)

    def _node_text(self, node):
        # Mirrors retrieval/repository._scan_scores text construction
        meta = {}
        # Collect metadata extras (everything except structural keys)
        for k, v in node.items():
            if k not in ("id", "type", "name", "description", "source_id", "provenance", "relationships", "_score"):
                meta[k] = v
        parts = [str(node.get("id", "")), str(node.get("type", "")), str(node.get("name", "")), str(node.get("description", ""))]
        if meta:
            parts.append(json.dumps(meta, ensure_ascii=False))
        return " ".join(parts).lower()

    def rank(self, query_terms, candidates, context=None):
        if not isinstance(query_terms, list):
            query_terms = []
        # Determine context filter values (support both context_id and generic)
        ctx_id_filter = None
        if isinstance(context, dict):
            ctx_id_filter = context.get("context_id") or context.get("ctx") or (context.get("task") or {}).get("context_id") if isinstance(context.get("task"), dict) else None
            # Also support direct string context_id via context="ctx_..."
            if ctx_id_filter is None and isinstance(context.get("context_id"), str):
                ctx_id_filter = context["context_id"]

        scored = []
        for node in candidates:
            text = self._node_text(node)
            raw = sum(1 for w in query_terms if w in text) if query_terms else 0
            if raw == 0:
                continue

            # Context match factor
            if ctx_id_filter is not None:
                # Node may carry context in metadata: look for _context_id, context_id, or provenance-linked
                node_ctx = node.get("_context_id") or node.get("context_id") or (node.get("metadata") or {}).get("context_id") if isinstance(node.get("metadata"), dict) else None
                # Also check top-level extra fields (from _extras)
                if node_ctx is None:
                    # Search any field that looks like ctx_
                    for k, v in node.items():
                        if k in ("context_id", "_context_id") and isinstance(v, str) and v.startswith("ctx_"):
                            node_ctx = v
                            break
                if node_ctx is not None:
                    context_match = 1.0 if node_ctx == ctx_id_filter else (0.0 if self.context_strict else 0.5)
                else:
                    # Node has no context -> neutral (0.5 strict? or 1.0 lenient)
                    context_match = 0.5 if self.context_strict else 1.0
            else:
                context_match = 1.0

            # Lifecycle factor: Phase 2 not yet has lifecycle data; placeholder 1.0
            # Future: if node.metadata.lifecycle.status == "superseded" -> 0.0
            lifecycle_factor = 1.0
            # Check if node has lifecycle metadata indicating filtering
            meta_lifecycle = None
            if isinstance(node.get("metadata"), dict):
                meta_lifecycle = node["metadata"].get("lifecycle")
            elif isinstance(node.get("lifecycle"), dict):
                meta_lifecycle = node["lifecycle"]
            if isinstance(meta_lifecycle, dict):
                status = meta_lifecycle.get("status")
                if status in ("superseded", "invalidated"):
                    lifecycle_factor = 0.0

            quality = context_match * lifecycle_factor
            final = round(raw * quality, 6)

            # If strict context filtering and quality 0, exclude
            if final == 0 and self.context_strict and ctx_id_filter is not None:
                continue

            annotated = dict(node)  # shallow copy
            annotated["_score"] = {
                "raw_score": raw,
                "context_match": context_match,
                "lifecycle": lifecycle_factor,
                "quality": quality,
                "final_score": final,
            }
            scored.append((final, annotated.get("id", ""), annotated))

        # Deterministic ordering: -final, node_id ASC
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [node for _, _, node in scored]
