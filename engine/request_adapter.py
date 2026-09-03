"""RuleAdapter: deterministic Request → StructuredIntent (AI Engine v1).

Model-independent, no LLM/embeddings. Pure regex/keyword rules.
"""

import hashlib
import json
import re

ALLOWED_INTENTS = {"bug_fix", "test_verify", "generic"}

# File pattern: src/...py, tests/...py, or bare name
FILE_RE = re.compile(r"(?:src/|tests?/)?[\w\/.\-]+\.py\b")
# Symbol qname: pkg.mod.fn
SYMBOL_RE = re.compile(r"\b([a-zA-Z_][\w]*\.)+[a-zA-Z_][\w]*\b")
# Error pattern: E### or failing_test path
ERROR_RE = re.compile(r"\bE\d{3,4}\b")
FAILING_TEST_RE = re.compile(r"tests?/[\w\/.\-]+\.py\b")


def _derive_request_id(request, workspace_root=None):
    payload = {"request": request, "workspace_root": workspace_root or ""}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "rq_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _confidence_for(intent, has_file, has_error):
    # Signal, not authority: higher when we have explicit file/error
    if intent == "bug_fix" and has_file and has_error:
        return 0.92
    if intent == "bug_fix" and has_file:
        return 0.85
    if intent == "test_verify" and has_file:
        return 0.88
    if intent == "generic" and has_file:
        return 0.65
    return 0.55


def parse_request(request, workspace_root=None, request_id=None):
    """Deterministic parse: Request string → StructuredIntent.

    Returns (StructuredIntent dict, parse_report dict). Never raises for valid
    string; unknown patterns fall back to generic.
    """
    raw = request.strip() if isinstance(request, str) else ""
    lower = raw.lower()

    # Intent heuristics
    intent = "generic"
    if re.search(r"\bfix\b.*\bE\d+\b|\bE\d+\b.*\bfix\b|\blint\b.*\bfix\b|\bfix\b.*\blint\b", lower):
        intent = "bug_fix"
    elif "e302" in lower or "e30" in lower:
        intent = "bug_fix"
    elif re.search(r"\btest_verify\b|\bverify\b.*\btests?\b|\brun\b.*\btests?\b|\btests?\b.*\bverify\b", lower):
        intent = "test_verify"
    elif re.search(r"\bbug_fix\b", lower):
        intent = "bug_fix"

    # Target extraction
    target = {}
    file_match = FILE_RE.search(raw)
    if file_match:
        # Prefer src/ over tests/ for bug_fix, tests/ for test_verify
        files = FILE_RE.findall(raw)
        chosen = None
        for f in files:
            if intent == "test_verify" and f.startswith("tests"):
                chosen = f
                break
            if intent == "bug_fix" and f.startswith("src"):
                chosen = f
                break
        if not chosen:
            chosen = files[0]
        target["file"] = chosen
    # Symbol extraction (only if not already file)
    sym_match = SYMBOL_RE.search(raw)
    if sym_match and "file" not in target:
        # Avoid matching file-like with slash
        candidate = sym_match.group(0)
        if "/" not in candidate:
            target["symbol"] = candidate
    if not target:
        target = None

    # Error extraction
    error = None
    err_match = ERROR_RE.search(raw)
    failing = None
    # Find test file for failing_test
    if intent in ("test_verify", "generic"):
        ft_match = FAILING_TEST_RE.search(raw)
        if ft_match:
            failing = ft_match.group(0)
    if err_match or failing:
        error = {}
        if err_match:
            error["message"] = err_match.group(0)
        if failing:
            error["failing_test"] = failing
        if not error:
            error = None

    # Constraints: not derived from request string in rule adapter; caller provides via separate field

    rid = request_id or _derive_request_id(raw, workspace_root)
    has_file = bool(target and target.get("file"))
    has_error = bool(error and error.get("message"))
    confidence = _confidence_for(intent, has_file, has_error)

    intent_obj = {
        "intent": intent,
        "target": target,
        "error": error,
        "constraints": {},  # filled by handler from request.constraints if provided
        "raw_request": raw,
        "request_id": rid,
        "confidence": round(confidence, 4),
        "parse_method": "rule",
    }
    report = {
        "intent": intent,
        "has_file": has_file,
        "has_error": has_error,
        "file_match": target.get("file") if target else None,
        "error_match": error.get("message") if error else None,
    }
    return intent_obj, report


class RuleAdapter:
    """Callable adapter with same interface as future LLMAdapter."""

    def parse(self, request, workspace_root=None, request_id=None):
        return parse_request(request, workspace_root, request_id)

    def __call__(self, request, workspace_root=None, request_id=None):
        return parse_request(request, workspace_root, request_id)
