"""Context comparison: weighted similarity and structural diff — generic Phase 4.

Generic similarity model (7 non-temporal dimensions):
    environment 0.25
    project     0.25
    source      0.20
    actor       0.10
    spatial     0.05
    social      0.05
    affective   0.10
Sum = 1.0. Temporal is never compared.

For backward compat, legacy snapshots with system/project/task are mapped:
    system -> environment
    task   -> source

Per-dimension scoring (same as before):
    exact match   -> 1.0
    partial match -> 0.5
    no match      -> 0.0
    key in only one side -> 0.0
Per-dimension score is mean over union of keys. Total is weighted sum.

Empty-vs-empty is 1.0; empty-vs-nonempty is 0.0 for that dimension.
"""

def _normalize(value):
    if isinstance(value, dict):
        return tuple(sorted(value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(sorted(str(v) for v in value))
    return value

def _partial_match(a, b):
    if isinstance(a, str) and isinstance(b, str):
        la, lb = len(a), len(b)
        if la == 0 or lb == 0:
            return False
        short, long_ = (a, b) if la < lb else (b, a)
        if short in long_:
            return True
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
        return 1.0
    total = 0.0
    for key in union:
        if key not in a_keys or key not in b_keys:
            continue
        va, vb = _normalize(dim_a[key]), _normalize(dim_b[key])
        if va == vb:
            total += 1.0
        elif _partial_match(dim_a[key], dim_b[key]):
            total += 0.5
    return total / len(union)

# Generic weights (7 dims, temporal excluded)
DIMENSION_WEIGHTS = {
    "environment": 0.25,
    "project": 0.25,
    "source": 0.20,
    "actor": 0.10,
    "spatial": 0.05,
    "social": 0.05,
    "affective": 0.10,
}

# Legacy weights for old snapshots (if needed, but we map)
LEGACY_WEIGHTS = {
    "system": 0.4,
    "project": 0.4,
    "task": 0.2,
}

def _get_dims(snapshot):
    """Extract 7 generic dims from snapshot, handling legacy mapping."""
    # Try generic first
    def _g(name, legacy=None):
        # Prefer attribute, then dict key
        if hasattr(snapshot, name):
            val = getattr(snapshot, name)
            if isinstance(val, dict):
                return val
        if isinstance(snapshot, dict):
            if name in snapshot:
                return snapshot.get(name, {}) or {}
            if legacy and legacy in snapshot:
                return snapshot.get(legacy, {}) or {}
        # Legacy fallback: system->environment, task->source
        if name == "environment" and hasattr(snapshot, "system"):
            return getattr(snapshot, "system") or {}
        if name == "source" and hasattr(snapshot, "task"):
            return getattr(snapshot, "task") or {}
        if isinstance(snapshot, dict):
            if name == "environment" and "system" in snapshot:
                return snapshot.get("system", {}) or {}
            if name == "source" and "task" in snapshot:
                return snapshot.get("task", {}) or {}
        return {}

    return {
        "environment": _g("environment", "system"),
        "project": _g("project"),
        "source": _g("source", "task"),
        "actor": _g("actor"),
        "spatial": _g("spatial"),
        "social": _g("social"),
        "affective": _g("affective"),
    }

def context_similarity(a, b) -> float:
    dims_a = _get_dims(a)
    dims_b = _get_dims(b)
    total = 0.0
    for dim, weight in DIMENSION_WEIGHTS.items():
        total += weight * _dimension_similarity(dims_a[dim], dims_b[dim])
    return total

def context_diff(a, b) -> dict:
    dims_a = _get_dims(a)
    dims_b = _get_dims(b)
    detail = {}
    changed_dimensions = []
    for dim in DIMENSION_WEIGHTS:
        da, db = dims_a[dim], dims_b[dim]
        sim = _dimension_similarity(da, db)
        changed_keys = _changed_keys(da, db)
        changed = bool(changed_keys)
        if changed:
            changed_dimensions.append(dim)
        detail[dim] = {"changed": changed, "similarity": sim, "changed_keys": sorted(changed_keys)}
    # Legacy aliases for backward compat: system <-> environment, task <-> source
    # Old tests expect "system" and "task" keys
    if "environment" in detail:
        detail["system"] = detail["environment"]
        if "environment" in changed_dimensions and "system" not in changed_dimensions:
            changed_dimensions.append("system")
    if "source" in detail:
        detail["task"] = detail["source"]
        if "source" in changed_dimensions and "task" not in changed_dimensions:
            changed_dimensions.append("task")
    return {"similarity": context_similarity(a, b), "changed_dimensions": sorted(changed_dimensions), "dimensions": detail}

def _changed_keys(da: dict, db: dict) -> set:
    changed = set()
    for key in set(da) | set(db):
        if _normalize(da.get(key)) != _normalize(db.get(key)):
            changed.add(key)
    return changed
