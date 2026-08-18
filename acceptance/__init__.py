"""Knowledge Acceptance Layer v1.

A deterministic trust boundary between Knowledge Compiler candidates and the
existing Knowledge Ingestion / SQLite system:

    CANDIDATE -> VALIDATED -> ACCEPTED / HELD / REJECTED -> IMPORT PREVIEW

Every candidate receives exactly one decision (ACCEPT / HOLD / REJECT) based on
explicit, machine-verifiable rules. ACCEPTED means "this candidate satisfies
our explicit deterministic acceptance rules" -- NOT "the source is universally
true". Source evidence and provenance remain authoritative for auditing, and
REJECT never deletes anything.

No AI. No embeddings. No vector DB. No semantic model. Nothing here writes to
the knowledge database -- the import preview is computed in memory only.
"""

__version__ = "1.0.0"
