"""Acceptance evaluation: candidate records -> exactly one decision each.

The evaluator composes the deterministic policy with identity resolution and
the Knowledge Schema mapping into a single, repeatable pass:

1. Load candidate records (Knowledge Compiler JSON output, plus validation).
2. Compute a strong, kind-specific identity fingerprint for every candidate.
3. Group by identity and detect conflicts (same identity, different content).
4. Apply the policy to get a base decision per candidate.
5. Override:
   * duplicate-identity conflicts  -> HOLD (cannot be safely resolved)
   * ACCEPT with no safe schema mapping -> HOLD (unmappable_to_schema)
6. Produce one :class:`DecisionRecord` per candidate and the resolved
   :class:`IdentityGroup` set.

Nothing here writes to any database -- it is a pure, deterministic
transformation, so repeated evaluation is byte-identical.
"""

from acceptance.types import (
    ACCEPT, HOLD, REJECT,
    ACCEPTED, HELD, REJECTED,
    DecisionRecord, IdentityGroup,
    compute_identity, content_signature,
)
from acceptance import policy, mapping

_DECISION_STATE = {ACCEPT: ACCEPTED, HOLD: HELD, REJECT: REJECTED}


def _classify_target(base, name_to_node_ids, declared_directives):
    """Deterministic reason code for a non-accepted inheritance target.

    Precedence: multiple ACCEPTED node identities -> ambiguous; multiple
    declared directives -> ambiguous; no declaration evidence -> unresolved;
    otherwise the target is declared in the corpus but is not an accepted node.
    """
    accepted_ids = name_to_node_ids.get(base) or []
    if len(accepted_ids) > 1:
        return policy.HOLD_INHERITANCE_TARGET_AMBIGUOUS
    dirs = sorted(declared_directives.get(base, set()))
    if len(dirs) > 1:
        return policy.HOLD_INHERITANCE_TARGET_AMBIGUOUS
    if not dirs:
        return policy.HOLD_INHERITANCE_TARGET_UNRESOLVED
    return policy.HOLD_INHERITANCE_TARGET_NOT_ACCEPTED


class EvaluationResult:
    """Deterministic outcome of evaluating a candidate set."""

    def __init__(self, candidates, valid_by_id):
        self.candidates = candidates
        self.valid_by_id = valid_by_id
        self.decisions = []
        self.groups = []
        self._evaluate()

    def _evaluate(self):
        # -- base decisions + identity -----------------------------------
        groups = {}
        per_candidate = {}
        for cand in self.candidates:
            kind = cand.get("kind", "")
            identity = compute_identity(kind, cand)
            cand["identity"] = identity
            decision, code = policy.decide(cand, self.valid_by_id.get(
                cand.get("candidate_id"), True))
            per_candidate[cand["candidate_id"]] = {
                "decision": decision, "reason_code": code,
            }
            grp = groups.get(identity)
            if grp is None:
                grp = IdentityGroup(identity=identity, kind=kind,
                                    canonical_candidate_id=cand["candidate_id"])
                groups[identity] = grp
            grp.members.append(cand)

        # -- identity conflict detection ---------------------------------
        for grp in groups.values():
            signatures = {content_signature(grp.kind, m) for m in grp.members}
            grp.conflict = len(signatures) > 1
            grp.members.sort(key=lambda m: m.get("candidate_id", ""))
            grp.canonical_candidate_id = grp.members[0]["candidate_id"]

        # -- phase A: provisional decisions ------------------------------
        # REJECT is final; conflicts and unmappable ACCEPTs are resolved here.
        # Inheritance endpoint enforcement happens in phase C because it needs
        # the accepted-node set, which depends on these decisions.
        provisional = {}
        for cand in self.candidates:
            grp = groups[cand["identity"]]
            base = per_candidate[cand["candidate_id"]]
            decision, code = base["decision"], base["reason_code"]
            if decision == REJECT:
                pass  # REJECT is final
            elif grp.conflict:
                decision, code = HOLD, policy.HOLD_CONFLICT
            elif decision == ACCEPT and not mapping.is_mappable(
                    cand["kind"], cand.get("meta") or {}):
                decision, code = HOLD, policy.HOLD_UNMAPPED
            provisional[cand["candidate_id"]] = (decision, code)

        # -- accepted node identities ------------------------------------
        # Identities that resolve to actual proposed nodes (any ACCEPTed,
        # conflict-free kind with a canonical node type). Inheritance groups
        # never contribute here -- they propose relationships only.
        name_to_node_ids = {}
        declared_directives = {}
        accepted_node_ids = set()
        for grp in groups.values():
            if grp.kind == "inheritance":
                continue
            if grp.conflict:
                continue
            if provisional.get(grp.canonical_candidate_id, ("HOLD", ""))[0] != ACCEPT:
                continue
            canonical = self.canonical_member(grp)
            if mapping.node_type_for(grp.kind, canonical.get("meta") or {}) is None:
                continue
            accepted_node_ids.add(grp.identity)
            if grp.kind == "api_declaration":
                nm = (canonical.get("meta") or {}).get("name")
                if isinstance(nm, str) and nm:
                    name_to_node_ids.setdefault(nm, []).append(grp.identity)
        for nm in name_to_node_ids:
            name_to_node_ids[nm] = sorted(set(name_to_node_ids[nm]))

        # -- declared (but not necessarily accepted) api directives -------
        for cand in self.candidates:
            if cand.get("kind") != "api_declaration":
                continue
            meta = cand.get("meta") or {}
            nm = meta.get("name")
            directive = meta.get("directive")
            if isinstance(nm, str) and nm and isinstance(directive, str):
                declared_directives.setdefault(nm, set()).add(directive)

        # -- phase C: inheritance endpoint enforcement --------------------
        inheritance_resolved = {}
        for cand in self.candidates:
            grp = groups[cand["identity"]]
            decision, code = provisional[cand["candidate_id"]]
            if decision == ACCEPT and cand.get("kind") == "inheritance":
                meta = cand.get("meta") or {}
                source_id, target_id = mapping.resolve_inheritance_endpoints(
                    meta, name_to_node_ids)
                if source_id is None:
                    decision, code = HOLD, policy.HOLD_INHERITANCE_SOURCE
                elif target_id is None:
                    decision, code = HOLD, _classify_target(
                        meta.get("base"), name_to_node_ids, declared_directives)
                else:
                    inheritance_resolved[grp.identity] = {
                        "source_node_id": source_id,
                        "target_node_id": target_id,
                    }
            if decision == ACCEPT and grp.identity in inheritance_resolved:
                grp.resolved_endpoints = inheritance_resolved[grp.identity]

            self.decisions.append(self._record(cand, decision, code, grp))

        self.decisions.sort(key=lambda d: (d.document, d.candidate_id))
        self.groups = sorted(groups.values(), key=lambda g: g.identity)

    @staticmethod
    def _record(cand: dict, decision: str, code: str, grp) -> DecisionRecord:
        """Build a DecisionRecord from a candidate and its final decision."""
        return DecisionRecord(
            candidate_id=cand.get("candidate_id") or "",
            kind=cand.get("kind") or "",
            decision=decision,
            reason_code=code,
            reason=policy.reason_text(code),
            confidence=cand.get("confidence") or "high",
            identity=cand["identity"],
            summary=cand.get("summary") or "",
            document=cand.get("document") or "",
            section_path=list(cand.get("section_path") or []),
            location=dict(cand.get("location") or {}),
            evidence=cand.get("evidence") or "",
        )

    # -- derived views ----------------------------------------------------

    def decisions_for(self, decision):
        return [d for d in self.decisions if d.decision == decision]

    def accepted_groups(self):
        """Canonical accepted identities (conflict-free, schema-mapped)."""
        accepted_ids = {d.candidate_id for d in self.decisions_for(ACCEPT)}
        out = []
        for grp in self.groups:
            if grp.conflict:
                continue
            if grp.canonical_candidate_id in accepted_ids:
                out.append(grp)
        return out

    def canonical_member(self, grp):
        for m in grp.members:
            if m.get("candidate_id") == grp.canonical_candidate_id:
                return m
        return grp.members[0]
