"""Generic AdapterRegistry / plugin registry foundation (Phase 6).

Categories:
    - CaptureAdapter  (ai_engine.capture.CaptureAdapter)
    - EffectAdapter   (TaskEngine tool: name -> callable)
    - Verifier        (outcome verification: name -> callable/object with verify)
    - Ranker          (ai_engine.ranker.Ranker)

Generic, stdlib-only, no hardcoded coding adapters, no network, no arbitrary
execution. Unknown/untrusted adapters fail closed.

Registry is in-memory, explicit registration only (no entry_points auto-load
in Phase 6). Future phases may add discovery, but Phase 6 is explicit and
additive — existing CaptureAdapter/Memory/Ranker boundaries are preserved and
only migrated where strictly required.

Thread-safety: not required for Phase 6 (single-threaded tests). Registry is
per-process; per-project isolation is handled by Memory/Activity stores, not
by registry (adapters are stateless).

Existing functionality migration (strictly required only):
    - CaptureAdapter (ManualCaptureAdapter) and Ranker (KeywordRanker) are
      available via registry helpers get_default_registry() for convenience,
      but direct instantiation still works (no forced migration).
    - TaskEngine's hardcoded registry is NOT replaced; registry EffectAdapters
      are additive via attach calls.
"""

from ai_engine.capture import CaptureAdapter, ManualCaptureAdapter
from ai_engine.ranker import Ranker, KeywordRanker


class AdapterRegistry:
    """Generic, explicit registry for 4 adapter categories.

    All registrations are explicit, fail closed on duplicate or invalid,
    and never auto-create unknown adapters.
    """

    def __init__(self):
        self._captures = {}   # adapter_id -> CaptureAdapter instance
        self._effects = {}    # tool_name -> callable
        self._verifiers = {}  # verifier_name -> verifier
        self._rankers = {}    # ranker_name -> Ranker instance

    # -- Capture -----------------------------------------------------------
    def register_capture(self, adapter):
        if not isinstance(adapter, CaptureAdapter):
            raise ValueError("adapter must be CaptureAdapter instance")
        aid = adapter.adapter_id
        if not isinstance(aid, str) or not aid.strip():
            raise ValueError("adapter.adapter_id must be non-empty string")
        aid = aid.strip()
        if aid in self._captures:
            # Fail closed: duplicate id with different instance -> error
            # Same instance re-registration is idempotent
            if self._captures[aid] is not adapter:
                raise ValueError(f"capture adapter {aid!r} already registered")
            return False  # idempotent, not a new registration
        self._captures[aid] = adapter
        return True

    def get_capture(self, adapter_id):
        if not isinstance(adapter_id, str) or not adapter_id.strip():
            raise ValueError("adapter_id must be non-empty string")
        return self._captures.get(adapter_id.strip())

    def has_capture(self, adapter_id):
        return adapter_id.strip() in self._captures if isinstance(adapter_id, str) else False

    def list_captures(self):
        return sorted(self._captures.keys())

    def unregister_capture(self, adapter_id):
        """Remove a capture adapter (for testing). Returns True if removed."""
        if adapter_id in self._captures:
            del self._captures[adapter_id]
            return True
        return False

    # -- Effect ------------------------------------------------------------
    def register_effect(self, tool_name, handler, spec=None):
        """Register an EffectAdapter (TaskEngine tool).

        tool_name: str, must be non-empty, no path traversal
        handler: callable
        spec: optional dict for validation (not enforced in Phase 6)
        """
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be non-empty string")
        tool_name = tool_name.strip()
        if "/" in tool_name or "\\" in tool_name or ".." in tool_name:
            raise ValueError(f"tool_name {tool_name!r} contains path traversal")
        if not callable(handler):
            raise ValueError("handler must be callable")
        if tool_name in self._effects:
            if self._effects[tool_name] is not handler:
                raise ValueError(f"effect {tool_name!r} already registered")
            return False
        self._effects[tool_name] = handler
        return True

    def get_effect(self, tool_name):
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be non-empty string")
        return self._effects.get(tool_name.strip())

    def has_effect(self, tool_name):
        return tool_name.strip() in self._effects if isinstance(tool_name, str) else False

    def list_effects(self):
        return sorted(self._effects.keys())

    # -- Verifier ----------------------------------------------------------
    def register_verifier(self, name, verifier):
        """Register a Verifier.

        verifier: object with verify method or callable. Stored as-is.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("verifier name must be non-empty string")
        name = name.strip()
        if name in self._verifiers:
            if self._verifiers[name] is not verifier:
                raise ValueError(f"verifier {name!r} already registered")
            return False
        # Minimal validation: verifier must be callable or have verify attribute
        if not callable(verifier) and not hasattr(verifier, "verify"):
            raise ValueError("verifier must be callable or have verify method")
        self._verifiers[name] = verifier
        return True

    def get_verifier(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("verifier name must be non-empty string")
        return self._verifiers.get(name.strip())

    def has_verifier(self, name):
        return name.strip() in self._verifiers if isinstance(name, str) else False

    def list_verifiers(self):
        return sorted(self._verifiers.keys())

    # -- Ranker ------------------------------------------------------------
    def register_ranker(self, name, ranker):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("ranker name must be non-empty string")
        name = name.strip()
        if not isinstance(ranker, Ranker):
            raise ValueError("ranker must be Ranker instance")
        if name in self._rankers:
            if self._rankers[name] is not ranker:
                raise ValueError(f"ranker {name!r} already registered")
            return False
        self._rankers[name] = ranker
        return True

    def get_ranker(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("ranker name must be non-empty string")
        return self._rankers.get(name.strip())

    def has_ranker(self, name):
        return name.strip() in self._rankers if isinstance(name, str) else False

    def list_rankers(self):
        return sorted(self._rankers.keys())

    # -- Utilities ---------------------------------------------------------
    def clear(self):
        """Clear all registrations (for testing)."""
        self._captures.clear()
        self._effects.clear()
        self._verifiers.clear()
        self._rankers.clear()

    def is_empty(self):
        return not (self._captures or self._effects or self._verifiers or self._rankers)


# Default registry with generic adapters pre-registered (not coding-specific)
_default_registry = None

def get_default_registry():
    """Return process-wide default registry with generic adapters.

    Pre-registers:
        - manual capture (ManualCaptureAdapter)
        - keyword ranker (KeywordRanker as 'keyword')

    Idempotent, no duplication on repeated calls. Generic only.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = AdapterRegistry()
        try:
            _default_registry.register_capture(ManualCaptureAdapter())
        except ValueError:
            pass
        try:
            _default_registry.register_ranker("keyword", KeywordRanker())
        except ValueError:
            pass
    return _default_registry


def reset_default_registry():
    """Reset default registry (for testing). Clears and re-registers generics."""
    global _default_registry
    if _default_registry is not None:
        _default_registry.clear()
    # Re-create on next get_default_registry call
    _default_registry = None
    return get_default_registry()
