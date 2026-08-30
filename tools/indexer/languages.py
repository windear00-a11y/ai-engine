"""Project/Code Index — language classification.

Deterministic extension -> language map. Configuration files are treated as
``config`` data and are NEVER executed. Structural symbol extraction is only
attempted for languages we support (python in this approved slice). JS/TS files
are indexed (as files) but NOT structurally extracted (deferred, per plan).
"""

import os

from .types import (
    LANG_PYTHON, LANG_JAVASCRIPT, LANG_TYPESCRIPT, LANG_CONFIG, LANG_OTHER,
)

# Structural support.
STRUCTURAL_LANGUAGES = {LANG_PYTHON}

# JS / TS are recognised and indexed as files but explicitly excluded from
# structural symbol extraction in this approved slice (regex extraction is
# fragile and must not be presented as facts; structural support deferred).
NON_STRUCTURAL_RECOGNISED = {LANG_JAVASCRIPT, LANG_TYPESCRIPT}

# Config files are indexed as data (never executed).
CONFIG_TYPES = {
    "pyproject.toml": "pep621",
    "setup.py": "setup",
    "setup.cfg": "setup_cfg",
    "requirements.txt": "pip_requirements",
    "package.json": "npm",
    "tsconfig.json": "tsconfig",
    "Cargo.toml": "cargo",
    "go.mod": "gomod",
    "Makefile": "make",
    "Dockerfile": "docker",
    ".env.example": "dotenv",
    "tox.ini": "tox",
    "Pipfile": "pipfile",
}

PYTHON_EXT = {".py", ".pyi"}
JS_EXT = {".js", ".jsx", ".mjs", ".cjs"}
TS_EXT = {".ts", ".tsx", ".mts"}


def language_for(rel_path):
    """Return the deterministic language for a relative path."""
    base = os.path.basename(rel_path)
    if base in CONFIG_TYPES:
        return LANG_CONFIG
    ext = os.path.splitext(base)[1].lower()
    if ext in PYTHON_EXT:
        return LANG_PYTHON
    if ext in JS_EXT:
        return LANG_JAVASCRIPT
    if ext in TS_EXT:
        return LANG_TYPESCRIPT
    return LANG_OTHER


def config_type_for(rel_path):
    base = os.path.basename(rel_path)
    return CONFIG_TYPES.get(base, "")


def is_structural(language):
    return language in STRUCTURAL_LANGUAGES
