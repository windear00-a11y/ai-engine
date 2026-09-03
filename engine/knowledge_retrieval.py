"""Knowledge-Aware Retrieval for v1.1 — deterministic, model-independent.

Uses only KnowledgeClient abstraction, never SQLite. Deterministic ranking
per spec §3, context matching §4, lifecycle §9, bounds §12.
"""

import hashlib
import json
import os
import time

# Reuse token logic from reasoning for consistency
def _tokens(text):
    if not text:
        return set()
    stop = {"the", "a", "an", "is", "of", "for", "this", "in", "on", "to", "and", "or"}
    tokens = set()
    for tok in text.replace("-", " ").replace("_", " ").replace("/", " ").split():
        tok = tok.strip(".,:;()[]{}\"'").lower()
        if tok and tok not in stop:
            tokens.add(tok)
    return tokens

def build_query_terms(intent_obj):
    """Deterministic query terms from StructuredIntent only (no raw request)."""
    terms = set()
    intent = intent_obj.get("intent") or ""
    if intent:
        terms.add(intent.lower())
    target = intent_obj.get("target") or {}
    if isinstance(target, dict):
        file_val = target.get("file")
        if file_val:
            # basename without extension
            base = os.path.basename(file_val)
            name = base.rsplit(".", 1)[0] if "." in base else base
            if name:
                terms.add(name.lower())
            # also add full file token lower
            terms.add(file_val.lower())
        sym = target.get("symbol")
        if sym:
            last = sym.split(".")[-1] if "." in sym else sym
            terms.add(last.lower())
            terms.add(sym.lower())
    error = intent_obj.get("error") or {}
    if isinstance(error, dict):
        msg = error.get("message")
        if msg:
            terms.add(msg.lower())
        failing = error.get("failing_test")
        if failing:
            base = os.path.basename(failing)
            name = base.rsplit(".", 1)[0] if "." in base else base
            terms.add(name.lower())
    domain = intent_obj.get("domain")
    if domain:
        terms.add(domain.lower())
    # Filter empty, sort canonical, dedup, limit 10
    cleaned = sorted(t for t in terms if t and len(t) <= 64)[:10]
    return cleaned

def _get_lifecycle(node):
    return node.get("lifecycle") or {}

def _lifecycle_confidence(node):
    lc = _get_lifecycle(node)
    try:
        return float(lc.get("confidence", 1.0))
    except Exception:
        return 1.0

def _evidence_quality(node):
    # Best evidence quality supporting node: use lifecycle evidence_quality or chain_quality
    lc = _get_lifecycle(node)
    eq = lc.get("evidence_quality")
    if eq is not None:
        try:
            return float(eq)
        except Exception:
            pass
    # Fallback: use lifecycle confidence as proxy, or 1.0
    return 1.0

def _recency_factor(node):
    lc = _get_lifecycle(node)
    last = lc.get("last_verified")
    if last is None:
        # Use created_at or assume recent
        return 1.0
    try:
        ts = float(last)
    except Exception:
        return 1.0
    age_days = (time.time() - ts) / 86400.0
    return 1.0 if age_days <= 90 else 0.8

def _context_factor(context_snapshot, node):
    # Use existing context matching logic from decision
    try:
        from intelligence.decision.context import strategy_context_applicable
        # Create a mock strategy-like object with node's restrictions
        class _NodeCtx:
            def __init__(self, restrictions):
                self.context_restrictions = restrictions
        restrictions = _get_lifecycle(node).get("context_restrictions")
        if not restrictions:
            return 1.0
        # Check applicability: if not applicable => 0.0 (eliminate)
        mock = _NodeCtx(restrictions)
        # Need context_snapshot
        if context_snapshot is None:
            return 1.0
        # Use strategy_context_applicable logic
        applicable = strategy_context_applicable(mock, context_snapshot)
        if not applicable:
            return 0.0
        # Partial: if restrictions exist but not fully mismatched, treat as 0.5?
        # For now, if applicable and restrictions non-empty, check if exact vs partial
        # We treat any applicable with restrictions as 1.0 if exact context_id match, else 0.5
        # This is deterministic and simple
        ctx_id = getattr(context_snapshot, "context_id", None) or (context_snapshot.get("context_id") if isinstance(context_snapshot, dict) else None)
        allowed = restrictions.get("allowed_contexts") if isinstance(restrictions, dict) else None
        if isinstance(allowed, (list, tuple)) and allowed:
            if ctx_id and ctx_id in allowed:
                return 1.0
            else:
                return 0.5
        return 1.0
    except Exception:
        return 1.0

def score_node(node, intent_obj, context_snapshot, relationship_relevance=0):
    """Compute raw_score, quality, final_score per spec §3."""
    # Token sets
    query_terms = build_query_terms(intent_obj)
    # Build node token sets for matching
    claim = node.get("name") or node.get("description") or ""
    subject = node.get("subject") or ""
    # Use _tokens
    node_claim_tokens = _tokens(claim)
    node_subject_tokens = _tokens(subject)
    node_type_tokens = _tokens(node.get("type"))
    node_desc_tokens = _tokens(node.get("description"))
    all_node_tokens = node_claim_tokens | node_subject_tokens | node_type_tokens | node_desc_tokens

    # Exact error match: error.message token in node tokens or substring in node text/id
    exact_error_match = 0
    err_msg = (intent_obj.get("error") or {}).get("message") if isinstance(intent_obj.get("error"), dict) else None
    if err_msg:
        err_tok = err_msg.lower()
        node_text_lower = " ".join([str(node.get("id") or ""), claim or "", subject or "", node.get("type") or "", node.get("description") or ""]).lower()
        if err_tok in all_node_tokens or err_tok in node_claim_tokens or err_tok in node_text_lower:
            exact_error_match = 1
        elif err_tok in (claim.lower() if claim else ""):
            exact_error_match = 1

    # Intent match
    intent_val = (intent_obj.get("intent") or "").lower()
    node_text_lower = " ".join([str(node.get("id") or ""), claim or "", subject or "", node.get("type") or "", node.get("description") or ""]).lower()
    intent_match = 1 if intent_val and (intent_val in all_node_tokens or intent_val in node_type_tokens or intent_val in node_text_lower) else 0

    # Target file match
    target_file = (intent_obj.get("target") or {}).get("file") if isinstance(intent_obj.get("target"), dict) else None
    target_file_match = 0
    if target_file:
        base = os.path.basename(target_file).rsplit(".", 1)[0].lower()
        if base and base in all_node_tokens:
            target_file_match = 1
        elif target_file.lower() in (claim.lower() if claim else ""):
            target_file_match = 1
        elif target_file.lower() in node_text_lower:
            target_file_match = 1

    # Target symbol match
    target_sym = (intent_obj.get("target") or {}).get("symbol") if isinstance(intent_obj.get("target"), dict) else None
    target_symbol_match = 0
    if target_sym:
        last = target_sym.split(".")[-1].lower() if "." in target_sym else target_sym.lower()
        if last and last in all_node_tokens:
            target_symbol_match = 1
        elif target_sym.lower() in (claim.lower() if claim else ""):
            target_symbol_match = 1
        elif target_sym.lower() in node_text_lower:
            target_symbol_match = 1

    # Domain match
    domain = (intent_obj.get("domain") or "").lower()
    domain_match = 1 if domain and (domain in all_node_tokens or domain in node_text_lower) else 0

    raw = (10 * exact_error_match + 6 * intent_match + 4 * target_file_match + 3 * target_symbol_match + 2 * domain_match + 1 * relationship_relevance)
    lc_conf = _lifecycle_confidence(node)
    eq = _evidence_quality(node)
    ctx_f = _context_factor(context_snapshot, node)
    rec_f = _recency_factor(node)
    quality = lc_conf * eq * ctx_f * rec_f
    final = round(raw * quality, 6)
    # For eliminated context, final will be 0 due to ctx_f 0, but we also eliminate before scoring
    return {
        "raw_score": raw,
        "quality": round(quality, 6),
        "context_match": ctx_f,
        "final_score": final,
        "lifecycle_confidence": lc_conf,
        "evidence_quality": eq,
        "recency_factor": rec_f,
        "query_terms": query_terms,
    }

def retrieve_ranked_knowledge(intent_obj, context_snapshot, knowledge_client=None, candidate_limit=50, max_knowledge=5):
    """Retrieve and rank knowledge via KnowledgeClient only.

    Returns dict with keys: knowledge (list of top nodes), filtered, ambiguous, query_terms, evidence_chain_id.
    Never raises for retrieval failure; returns empty with error field.
    """
    query_terms = build_query_terms(intent_obj)
    query_str = " ".join(query_terms) if query_terms else intent_obj.get("intent") or "generic"
    candidate_limit = min(int(candidate_limit), 50)
    max_knowledge = min(int(max_knowledge), 5)

    # If no client provided, try to create InProcess KnowledgeClient
    client = knowledge_client
    close_client = False
    if client is None:
        try:
            from knowledge_client.transports import InProcessTransport
            from knowledge_client import KnowledgeClient
            from api.tools import ToolInterface  # fallback to direct if needed
            # Use InProcessTransport with default DB
            transport = InProcessTransport()
            client = KnowledgeClient(transport)
            close_client = True
        except Exception:
            return {"knowledge": [], "filtered": [], "ambiguous": False, "query_terms": query_terms, "evidence_chain_id": None, "error": "KnowledgeClient unavailable"}

    try:
        # Primary search
        try:
            candidates = client.search(query_str, limit=candidate_limit)
        except Exception as e:
            return {"knowledge": [], "filtered": [], "ambiguous": False, "query_terms": query_terms, "evidence_chain_id": None, "error": str(e)}

        # Expand via related for top hits (bounded 2 per hit, lifecycle only)
        expanded = list(candidates)
        relationship_relevance_map = {c.get("id"): 0 for c in candidates}
        # For top 3 hits, fetch related to expand
        for top in candidates[:3]:
            try:
                rel = client.related(top.get("id"), limit=2)
                for r in rel or []:
                    # r is RelatedEntry dict with node id?
                    # Related returns list of node dicts? Assume dict with id
                    nid = r.get("id") if isinstance(r, dict) else None
                    if nid and nid not in relationship_relevance_map:
                        # Fetch node via get
                        try:
                            node = client.get(nid)
                            if node:
                                expanded.append(node)
                                relationship_relevance_map[nid] = 1
                        except Exception:
                            pass
            except Exception:
                pass

        # Deduplicate by id
        seen = {}
        deduped = []
        for n in expanded:
            nid = n.get("id")
            if nid and nid not in seen:
                seen[nid] = True
                deduped.append(n)
        # Apply lifecycle and context filtering, scoring
        filtered = []
        scored = []
        for node in deduped:
            lc = _get_lifecycle(node)
            status = lc.get("status", "active")
            # Lifecycle elimination
            if status in ("superseded", "invalidated"):
                filtered.append({"id": node.get("id"), "reason": f"{status} by {lc.get('superseded_by') or lc.get('reason') or ''}".strip()})
                continue
            # Context elimination
            ctx_f = _context_factor(context_snapshot, node)
            if ctx_f == 0.0:
                filtered.append({"id": node.get("id"), "reason": "context_restricted"})
                continue
            # Candidate weaker
            rel = relationship_relevance_map.get(node.get("id"), 0)
            scores = score_node(node, intent_obj, context_snapshot, relationship_relevance=rel)
            # For candidate lifecycle, halve raw
            if status == "candidate":
                scores["raw_score"] = scores["raw_score"] // 2
                scores["final_score"] = round(scores["raw_score"] * scores["quality"], 6)
                scores["lifecycle"] = "candidate"
            else:
                scores["lifecycle"] = status
            # Evidence assembly: add provenance
            try:
                prov = client.provenance(node.get("id"))
            except Exception:
                prov = {}
            node_with_scores = dict(node)
            node_with_scores["_score"] = scores
            node_with_scores["_provenance"] = prov
            scored.append((scores["final_score"], node.get("id"), node_with_scores))

        # Sort by final_score desc, then node_id ASC
        scored.sort(key=lambda x: (-x[0], x[1]))
        top = [n for _, _, n in scored[:max_knowledge]]
        # Ambiguous if top 2 have equal final_score and raw_score
        ambiguous = False
        if len(scored) >= 2 and scored[0][0] == scored[1][0]:
            # Check raw
            if scored[0][2]["_score"]["raw_score"] == scored[1][2]["_score"]["raw_score"]:
                ambiguous = True
        # Evidence chain id deterministic
        import hashlib, json
        evidence_chain_id = "evc_" + hashlib.sha256(json.dumps({"query_terms": query_terms, "top_ids": [n.get("id") for n in top]}, sort_keys=True).encode()).hexdigest()[:16]
        return {
            "knowledge": top,
            "filtered": filtered,
            "ambiguous": ambiguous,
            "query_terms": query_terms,
            "evidence_chain_id": evidence_chain_id,
            "candidate_count": len(deduped),
        }
    finally:
        if close_client:
            try:
                client.close()
            except Exception:
                pass
