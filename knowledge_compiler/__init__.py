"""Knowledge Compiler pipeline.

Deterministic, AI-free compilation of raw documentation source into extracted
structure and evidence-backed knowledge candidates. The pipeline is strictly
separated into states:

    RAW -> EXTRACTED -> CANDIDATE -> VALIDATED -> (IMPORTED: manual/opt-in)

Nothing in this package writes to the knowledge database.
"""

__version__ = "1.0.0"