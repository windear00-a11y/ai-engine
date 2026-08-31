"""Deterministic Planner — Component C.

Non-AI, rule/template based planner that consumes the Persistent Project/Code
Index via IndexQueries and produces TaskEngine-compatible tasks.
"""

from .deterministic import DeterministicPlanner, generate_plan

__all__ = ["DeterministicPlanner", "generate_plan"]
