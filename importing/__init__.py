"""Knowledge Import Verification Layer (Safe Import Dry-Run v1).

The import verification layer sits between the acceptance layer's
``import_plan.json`` and a future explicit import command:

    ACCEPTED  ->  MAPPED  ->  import_plan.json  ->  VERIFY + DRY-RUN  ->  IMPORT

* ``plan_loader`` loads an acceptance ``import_plan.json``.
* ``verifier``   statically validates the whole plan (structure, node types,
                 identity uniqueness, relationships, provenance, metadata,
                 primary keys, safe paths, read-only guard).
* ``dry_run``    simulates the full transaction in an isolated in-memory
                 ``KnowledgeRepository`` -- never the production database --
                 proving the plan is actually compatible with the repository's
                 constraints, atomically.
* ``report``     turns the result into the machine-readable report and a
                 human-readable summary.
* ``__main__``   exposes ``python -m importing verify`` and
                 ``python -m importing dry-run`` -- both strictly read-only.

Architectural boundary: acceptance decides WHAT is eligible, mapping decides
HOW it maps to the schema, and this layer decides WHETHER the resulting plan
can safely enter the repository. NO write/import command is created here.
"""

from importing.plan_loader import load_plan, load_plan_data, PlanLoadError
from importing.verifier import verify_plan, VerificationResult, Issue
from importing.dry_run import dry_run, DryRunResult, apply_plan
from importing.report import build_report, human_summary

__all__ = [
    "load_plan", "load_plan_data", "PlanLoadError",
    "verify_plan", "VerificationResult", "Issue",
    "dry_run", "DryRunResult", "apply_plan",
    "build_report", "human_summary",
]
