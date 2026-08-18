"""Load an acceptance ``import_plan.json`` for verification / dry-run.

The plan is produced by ``python -m acceptance prepare`` and is strictly a
read-only preview artifact. This loader only READS it.
"""

import json
import os


class PlanLoadError(Exception):
    """Raised when the import plan cannot be loaded (missing/invalid)."""


def load_plan(path):
    """Load an import plan from ``path`` and return the parsed plan dict."""
    if not isinstance(path, str) or not path:
        raise PlanLoadError("import plan path must be a non-empty string")
    if not os.path.isfile(path):
        raise PlanLoadError(f"import plan not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise PlanLoadError(f"invalid JSON in import plan: {e}") from e
    except OSError as e:
        raise PlanLoadError(f"cannot read import plan: {e}") from e
    return load_plan_data(data)


def load_plan_data(data):
    """Validate that the parsed plan is a JSON object (structural only)."""
    if not isinstance(data, dict):
        raise PlanLoadError("import plan must be a JSON object")
    return data
