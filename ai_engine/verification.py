"""Canonical, deterministic Verification model (Phase 29).

Pure domain module: stdlib only, no database, no network, no LLM.

Verification answers: **did observed reality satisfy the expected result
defined by the plan/decision?**

Results:
    VERIFIED_SUCCESS        expected result matches observed reality and there
                            is documented evidence
    VERIFIED_FAILURE        observed reality contradicts the expected result
    PARTIAL                 part of the expected result was satisfied
    UNKNOWN                 cannot be decided
    INSUFFICIENT_EVIDENCE   conditions/observations/evidence are missing
    CONFLICTING_EVIDENCE    observations disagree on the same expected key

Trust invariant: a caller-provided ``success=true`` is NOT verification
evidence. The engine refuses to evaluate expected-condition keys drawn from
the claim vocabulary, so a claimed status can never satisfy verification.
"""

import hashlib
import json

VERIFIED_SUCCESS = "verified_success"
VERIFIED_FAILURE = "verified_failure"
PARTIAL = "partial"
UNKNOWN = "unknown"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
CONFLICTING_EVIDENCE = "conflicting_evidence"

VERIFICATION_RESULTS = (VERIFIED_SUCCESS, VERIFIED_FAILURE, PARTIAL, UNKNOWN,
                        INSUFFICIENT_EVIDENCE, CONFLICTING_EVIDENCE)

# Claim vocabulary: values that are assertions rather than observed evidence.
CLAIM_KEYS = ("success", "ok", "passed", "verified", "succeeded")

CALLER_CLAIM_REJECTION = (
    "caller-provided success is not verification evidence; expected "
    "conditions must reference observed data")


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def derive_verification_id(action_id, expectations):
    """Deterministic verification identity over its expected conditions."""
    digest = hashlib.sha256(_canonical({
        "action_id": action_id,
        "expectations": _canonical_conditions(expectations),
    }).encode("utf-8")).hexdigest()
    return "vf_" + digest[:32]


def _canonical_conditions(expectations):
    if not isinstance(expectations, dict):
        return {}
    return {str(k): v for k, v in expectations.items()}


def verify(expected_conditions=None, observed_claims=None,
           documented_evidence=None):
    """Deterministically verify observed reality against expected conditions.

    ``expected_conditions``: dict of ``{condition_key: expected_value}``.
    ``observed_claims``: list of ``{claim_key, value, source}`` derived from
    observations of reality (never from the caller's stated outcome).
    ``documented_evidence``: sequence of evidence record ids; a result of
    VERIFIED_SUCCESS additionally requires at least one piece of documented
    verification evidence.
    """
    expected = _canonical_conditions(expected_conditions)
    claims = list(observed_claims or [])
    evidence = sorted({str(x) for x in (documented_evidence or ())})

    for key in sorted(expected):
        if key in CLAIM_KEYS:
            return _result(INSUFFICIENT_EVIDENCE, expected, evidence,
                           "expected condition %r uses a call-claim key; %s"
                           % (key, CALLER_CLAIM_REJECTION))

    if not expected:
        return _result(INSUFFICIENT_EVIDENCE, expected, evidence,
                       "no expected result conditions were provided")

    by_key = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        key = str(claim.get("claim_key"))
        by_key.setdefault(key, []).append(claim)

    missing = []
    matched = []
    unmatched = []
    conflicting = []
    for key in sorted(expected):
        entries = by_key.get(key, [])
        if not entries:
            missing.append(key)
            continue
        distinct_values = {_canonical(e.get("value")) for e in entries}
        if len(distinct_values) > 1:
            conflicting.append(key)
            continue
        if entries[0].get("value") == expected[key]:
            matched.append(key)
        else:
            unmatched.append(key)

    if conflicting:
        return _result(CONFLICTING_EVIDENCE, expected, evidence,
                       "observations conflict on expected key(s): %s"
                       % ", ".join(sorted(conflicting)))
    if unmatched and not matched:
        return _result(VERIFIED_FAILURE, expected, evidence,
                       "observed reality contradicted the expected result "
                       "on: %s" % ", ".join(sorted(unmatched)))
    if matched and not unmatched and not missing:
        if evidence:
            return _result(VERIFIED_SUCCESS, expected, evidence,
                           "observed reality satisfied the expected result "
                           "and is backed by recorded evidence")
        return _result(INSUFFICIENT_EVIDENCE, expected, evidence,
                       "observed reality satisfied the expected result but "
                       "there is no documented verification evidence")
    if matched and (unmatched or missing):
        return _result(PARTIAL, expected, evidence,
                       "expected result only partially satisfied "
                       "(matched: %s; unmatched: %s; missing: %s)"
                       % (sorted(matched), sorted(unmatched),
                          sorted(missing)))
    if not claims:
        return _result(INSUFFICIENT_EVIDENCE, expected, evidence,
                       "no observations were recorded for verification")
    return _result(UNKNOWN, expected, evidence,
                   "verification could not be determined")


def _result(result, expected, evidence, rationale):
    return {
        "result": result,
        "expected_conditions": dict(expected),
        "documented_evidence": sorted(evidence),
        "rationale": rationale,
    }