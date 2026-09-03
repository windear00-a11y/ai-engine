"""Context comparison: weighted similarity and structural diff (Phase 1).

Similarity model (Decision 4 in the architecture decisions)
------------------------------------------------------------
Three dimensions are compared with fixed weights:

  * system  0.4
  * project 0.4
  * task    0.2

For each dimension, values are compared over the union of keys between the
two dimension dicts. For an overlapping key:

  * exact match   -> 1.0
  * partial match -> 0.5   (see :func:`_partial_match`)
  * no match      -> 0.0
  * key present in only one side -> 0.0

The per-dimension score is the mean over the union of keys. The total
similarity is the weighted sum of the three per-dimension scores, in [0, 1].

Empty-vs-empty is treated as identical (1.0); empty-vs-nonempty is 0.0 for
that dimension. The temporal dimension is never compared.
"""


def _normalize(value):
    """Canonicalize a value for comparison."""
    if isinstance(value, dict):
        return tuple(sorted(value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(sorted(str(v) for v in value))
    return value


def _partial_match(a, b):
    """Deterministic partial-match test between two unequal values."""
    if isinstance(a, str) and isinstance(b, str):
        la, lb = len(a), len(b)
        if la == 0 or lb == 0:
            return False
        short, long_ = (a, b) if la < lb else (b, a)
        # one is a substring of the other
        if short in long_:
            return True
        # token-level overlap
        ta, tb = set(a.lower().split()), set(b.lower().split())
        return bool(ta & tb)
    if isinstance(a, (list, tuple, set)) and isinstance(b, (list, tuple, set)):
        return bool(set(a) & set(b))
    return False


def _dimension_similarity(dim_a: dict, dim_b: dict) -> float:
    a_keys = set(dim_a.keys())
    b_keys = set(dim_b.keys())
    union = a_keys | b_keys
    if not union:
        # both empty -> agree on everything
        return 1.0
    total = 0.0
    for key in union:
        if key not in a_keys or key not in b_keys:
            continue  # present in only one side -> no match (0.0)
        va, vb = _normalize(dim_a[key]), _normalize(dim_b[key])
        if va == vb:
            total += 1.0
        elif _partial_match(dim_a[key], dim_b[key]):
            total += 0.5
        # else 0.0
    return total / len(union)


DIMENSION_WEIGHTS = {
    "system": 0.4,
    "project": 0.4,
    "task": 0.2,
}


def context_similarity(a, b) -> float:
    """Weighted similarity between two ContextSnapshots in [0.0, 1.0].

    ``a`` and ``b`` may be ContextSnapshot objects or plain dicts with
    ``system``/``project``/``task`` keys.
    """
    sa = a.system if hasattr(a, "system") else a.get("system", {})
    pa = a.project if hasattr(a, "project") else a.get("project", {})
    ta = a.task if hasattr(a, "task") else a.get("task", {})
    sb = b.system if hasattr(b, "system") else b.get("system", {})
    pb = b.project if hasattr(b, "project") else b.get("project", {})
    tb = b.task if hasattr(b, "task") else b.get("task", {})
    total = 0.0
    for dim, weight in DIMENSION_WEIGHTS.items():
        da = {"system": sa, "project": pa, "task": ta}[dim]
        db = {"system": sb, "project": pb, "task": tb}[dim]
        total += weight * _dimension_similarity(da, db)
    return total


def context_diff(a, b) -> dict:
    """Structural diff: report which dimensions changed and how.

    Returns a dict with per-dimension ``changed`` booleans, the per-dimension
    similarity, any changed keys, and the overall similarity. Deterministic.
    """
    sa = a.system if hasattr(a, "system") else a.get("system", {})
    pa = a.project if hasattr(a, "project") else a.get("project", {})
    ta = a.task if hasattr(a, "task") else a.get("task", {})
    sb = b.system if hasattr(b, "system") else b.get("system", {})
    pb = b.project if hasattr(b, "project") else b.get("project", {})
    tb = b.task if hasattr(b, "task") else b.get("task", {})

    dims = {
        "system": (sa, sb),
        "project": (pa, pb),
        "task": (ta, tb),
    }
    detail = {}
    changed_dimensions = []
    for dim, (da, db) in dims.items():
        sim = _dimension_similarity(da, db)
        changed_keys = _changed_keys(da, db)
        changed = bool(changed_keys)
        if changed:
            changed_dimensions.append(dim)
        detail[dim] = {
            "changed": changed,
            "similarity": sim,
            "changed_keys": sorted(changed_keys),
        }
    return {
        "similarity": context_similarity(a, b),
        "changed_dimensions": sorted(changed_dimensions),
        "dimensions": detail,
    }


def _changed_keys(da: dict, db: dict) -> set:
    changed = set()
    for key in set(da) | set(db):
        if _normalize(da.get(key)) != _normalize(db.get(key)):
            changed.add(key)
    return changed
