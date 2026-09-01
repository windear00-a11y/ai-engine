"""Declarative, versioned permission policy.

The policy is plain data (JSON-friendly), not code. It is loaded/validated
deterministically. It does NOT make decisions -- it only describes rules that
:mod:`tools.permissions.pathpolicy` and the approval gate interpret.

Changing policy never requires a code change and never touches the production
knowledge database.
"""

import json


POLICY_VERSION = 1

# Hard irreducible invariants: these cannot be overridden by policy for a
# given process's workspace root because the production knowledge database is
# precious and frozen. The write gate enforces these in addition to any policy.
# They are expressed relative to the workspace root. Protection is name-based
# at a fixed anchor so that database/knowledge.db is never written no matter
# how an operator edits the policy file.
HARD_WRITE_INVARIANTS = [
    ("database/knowledge.db", "blocked"),
    ("database/knowledge.db.backup", "blocked"),
]


def _default_policy_dict():
    return {
        "policy_version": POLICY_VERSION,
        "zones": {
            # Each zone maps a name to a list of relative path specifiers.
            # Specifiers are matched against the path relative to the
            # workspace root. Trailing "/**" means "everything under that
            # directory" (recursively); a bare name matches that exact path.
            "workspace_readonly": [
                "api/contract.py",
                "docs/public-api-v1.md",
                "knowledge/SCHEMA.md",
            ],
            "protected": [
                ".env",
                ".env/**",
                "**/.env",
                "**/.env/**",
                "**/.env.*",
                "*.pem",
                "*.key",
                "credentials",
                "credentials/**",
                "**/secrets/**",
                "*_rsa",
                "*.p12",
                "*.pfx",
                "*token*",
                "*Token*",
                "*TOKEN*",
            ],
            "blocked": [
                "database/knowledge.db",
                "database/knowledge.db.backup",
                ".git/**",
                ".git",
                "database/backups/**",
                "database/backups",
            ],
        },
        "commands": {
            # Only the current interpreter is allowed, using closed safe forms.
            "python": {
                "executable": "python",
                "forms": [
                    {"args": ["-m", "unittest"], "approval": "required"},
                    {"args": ["-m", "compileall"], "approval": "required"},
                ],
                # py_compile is allowed with exactly one positional local path.
                "py_compile_path": True,
                "timeout_ms": 60000,
                "stdout_limit": 1048576,
                "stderr_limit": 1048576,
            },
            "python3": {
                "executable": "python3",
                "forms": [
                    {"args": ["-m", "unittest"], "approval": "required"},
                    {"args": ["-m", "compileall"], "approval": "required"},
                ],
                "py_compile_path": True,
                "timeout_ms": 60000,
                "stdout_limit": 1048576,
                "stderr_limit": 1048576,
            },
            # Slice 4A — read-only linters/formatters, exact closed forms only.
            "flake8": {
                "executable": "flake8",
                "forms": [
                    {"args": ["."], "approval": "required"},
                    {"args": ["--count", "."], "approval": "required"},
                ],
                "timeout_ms": 60000,
                "stdout_limit": 1048576,
                "stderr_limit": 1048576,
            },
            "ruff": {
                "executable": "ruff",
                "forms": [
                    {"args": ["check", "."], "approval": "required"},
                ],
                "timeout_ms": 60000,
                "stdout_limit": 1048576,
                "stderr_limit": 1048576,
            },
            "black": {
                "executable": "black",
                "forms": [
                    {"args": ["--check", "."], "approval": "required"},
                ],
                "timeout_ms": 60000,
                "stdout_limit": 1048576,
                "stderr_limit": 1048576,
            },
            "isort": {
                "executable": "isort",
                "forms": [
                    {"args": ["--check-only", "."], "approval": "required"},
                ],
                "timeout_ms": 60000,
                "stdout_limit": 1048576,
                "stderr_limit": 1048576,
            },
        },
        "approvals": {
            # Whether a given (domain) defaults to requiring approval.
            # Overridden by specific rules for writes and executes.
            "require_approval_by_default": True,
        },
        "env_strip": [
            "*TOKEN*", "*PASSWORD*", "*SECRET*", "*PRIVATE_KEY*", "*API_KEY*",
        ],
        "network": "deny",
        "git": "deny",
        "publish": "deny",
    }


class Policy:
    """Validated permission policy. Immutable after construction."""

    def __init__(self, data=None):
        self.data = data if data is not None else _default_policy_dict()
        errors = validate_policy(self.data)
        if errors:
            raise ValueError("invalid policy: %s" % "; ".join(errors))
        self.policy_version = self.data.get("policy_version", POLICY_VERSION)
        self.zones = self.data.get("zones", {})
        self.commands = self.data.get("commands", {})
        self.require_approval_by_default = bool(
            self.data.get("approvals", {})
            .get("require_approval_by_default", True))
        self.env_strip = list(self.data.get("env_strip", []))
        self.network = self.data.get("network", "deny")
        self.git = self.data.get("git", "deny")
        self.publish = self.data.get("publish", "deny")

    def command_spec(self, name):
        """Return the command policy dict for ``name`` or None if not allowed."""
        return self.commands.get(name)

    def enabled(self, key):
        """True if a capability (e.g. 'git') is not denied."""
        value = getattr(self, key, "deny")
        return value != "deny"


def load_policy(path):
    """Load a policy from a JSON file (or return defaults on missing/bad file).

    Defaults are always usable, so the system is safe even with no policy
    file present. Explicitly, a missing or invalid file is not fatal -- the
    built-in safe defaults apply.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Policy(data)
    except (OSError, ValueError, TypeError):
        return Policy()


def validate_policy(data):
    """Return a list of validation errors (empty == valid)."""
    if not isinstance(data, dict):
        return ["policy must be a JSON object"]
    errors = []
    version = data.get("policy_version", POLICY_VERSION)
    if not isinstance(version, int) or version < 1:
        errors.append("policy_version must be a positive integer")
    zones = data.get("zones")
    if zones is not None:
        if not isinstance(zones, dict):
            errors.append("zones must be an object")
        else:
            for zname, specs in zones.items():
                if not isinstance(specs, list):
                    errors.append(f"zone {zname!r} must be a list")
                elif not all(isinstance(s, str) and s for s in specs):
                    errors.append(f"zone {zname!r} contains a non-string spec")
    commands = data.get("commands")
    if commands is not None and not isinstance(commands, dict):
        errors.append("commands must be an object")
    for key in ("approvals",):
        val = data.get(key)
        if val is not None and not isinstance(val, dict):
            errors.append(f"{key} must be an object")
    env_strip = data.get("env_strip")
    if env_strip is not None and not isinstance(env_strip, list):
        errors.append("env_strip must be a list")
    return errors
