"""Evidence type definitions (Phase 2).

Evidence records are the factual bridge between "something happened" and
"therefore we believe X." The type classifies the *origin* of the evidence.
"""

from enum import Enum


class EvidenceType(Enum):
    """Origin of an evidence record.

    FACT           -- directly observed / measured fact.
    HEURISTIC      -- a rule of thumb derived from observed patterns.
    IMPORTED       -- brought in from an external source (e.g. a doc/library).
    HUMAN_ASSERTED -- asserted by a human operator.
    """

    FACT = "fact"
    HEURISTIC = "heuristic"
    IMPORTED = "imported"
    HUMAN_ASSERTED = "human_asserted"
