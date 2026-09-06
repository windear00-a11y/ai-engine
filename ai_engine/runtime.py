"""Runtime helpers (Persistent Intelligence Core).

Generic, optional helpers that expose Memory operations as deterministic tool
callables for use by generic registries or optional plugins. This module does
not import any domain implementation (no task engine, no coding tools) and
contains no domain fallback behavior.
"""

from ai_engine.memory import Memory


def _memory_tool_specs():
    """Return spec for memory tools (for validation, not enforced here)."""
    return {
        "memory.recall": {"args": ["query", "limit"], "read_only": True},
        "memory.remember": {"args": ["text", "type"], "read_only": False},
        "memory.get": {"args": ["node_id"], "read_only": True},
    }


def make_memory_tools(memory):
    """Create tool callables for a Memory instance.

    Returns dict {tool_name: callable}.
    Callables are thin wrappers that translate tool inputs to the Memory API
    and return structured results.

    These tools are deterministic, read through Memory's per-project stores,
    and respect vocabulary validation.
    """
    if memory is not None and not isinstance(memory, Memory):
        raise ValueError("memory must be Memory instance or None")

    def _recall(query, limit=None, context=None, candidate_limit=None):
        kwargs = {}
        if limit is not None:
            kwargs["limit"] = limit
        if context is not None:
            kwargs["context"] = context
        if candidate_limit is not None:
            kwargs["candidate_limit"] = candidate_limit
        lim = limit if isinstance(limit, int) else 20
        res = memory.recall(query=query, limit=lim, context=context) if memory else {"ok": False, "code": "internal_error", "error": "no memory bound"}
        return res

    def _remember(text=None, payload=None, type=None, **kwargs):
        if memory is None:
            return {"ok": False, "code": "internal_error", "error": "no memory bound"}
        return memory.remember(text=text, payload=payload, type=type, **kwargs)

    def _get(node_id):
        if memory is None:
            return {"ok": False, "code": "internal_error", "error": "no memory bound"}
        node = memory.get_node(node_id)
        if node is None:
            return {"ok": False, "code": "node_not_found", "error": f"node {node_id!r} not found"}
        return {"ok": True, "node": node}

    return {
        "memory.recall": _recall,
        "memory.remember": _remember,
        "memory.get": _get,
    }


def attach_memory_tools(target, memory):
    """Attach Memory tools into a ``{name: callable}`` mapping (generic).

    Additive only; fails closed on tool-name collision so an existing entry is
    never silently overwritten. Works with any mapping-style tool registry
    (including generic AdapterRegistry callers) and is the integration point
    optional plugins may use to expose memory operations.
    """
    if memory is not None and not isinstance(memory, Memory):
        raise ValueError("memory must be Memory instance or None")
    mem_tools = make_memory_tools(memory)
    for name, fn in mem_tools.items():
        if name in target:
            raise ValueError(f"tool {name!r} already registered")
        target[name] = fn
    return target