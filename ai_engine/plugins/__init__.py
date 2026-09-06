"""Plugins — generic domain/plugin boundary (Phase 7).

This package is the plugin *boundary* for domain-specific extensions.
Core Persistent Intelligence (Memory, Vocabulary, Capture, Activity, Context,
Ranker, Registry, Runtime) never imports from here. Domain plugins import
from core, not vice versa.

Phase 24 extraction: this boundary is generic-only. There are no domain
implementations shipped in the Core repository. Optional domains (e.g. Code)
live outside this repository and register against the public boundary
(:class:`ai_engine.registry.AdapterRegistry`, CaptureAdapter, Effect,
Verifier, Ranker) explicitly and under Core-controlled authority.
"""

__all__ = []