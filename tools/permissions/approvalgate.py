"""Deterministic approval gate for non-read operations.

The gate is a pure decision function PLUS an optional persistent lifecycle
recorder. It models the approval state machine:

    PROPOSED -> PENDING_APPROVAL -> ACCEPTED  |  DENIED

and operation lifecycle states::

    requested, approved, denied, executing, completed, failed, rolled_back

Rules (Phase 1A, explicit-approval-by-default, no auto-approve):
* READ           : allowed within the permitted workspace.
* T0 diff/preview: no approval (non-mutating).
* T1/T2/T3 WRITE : require explicit approval (T2/T3 additionally require a
                   snapshot before the write may proceed).
* EXECUTE        : require explicit approval for every allowed command.
* readonly/blocked paths: deny (no approval can override).
* No auto-approve mode exists in Phase 1A.

Approval is granted through a caller-supplied provider callback
(``approve(proposed) -> bool``). No identity/RBAC is involved. Approval ids
are deterministic (derived from operation + content), never random.
"""

from tools.permissions.decisions import (
    DecisionKind, Domain,
    allow, deny, require_approval,
    REASON_BLOCKED, REASON_READONLY, REASON_NO_APPROVAL,
    REASON_NO_SNAPSHOT, REASON_OUTSIDE_WORKSPACE,
    REASON_NETWORK_DENIED, REASON_PUBLISH_DENIED, REASON_GIT_DENIED,
)
from tools.permissions.operations import WriteTier, classify_write
from tools.permissions.journal import deterministic_id
import os


class ApprovalStatus:
    PROPOSED = "proposed"
    PENDING = "pending_approval"
    ACCEPTED = "accepted"
    DENIED = "denied"


class OpStatus:
    REQUESTED = "requested"
    APPROVED = "approved"
    DENIED = "denied"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


def _default_approver(proposed):
    """Default approver: deny everything (explicit-approval, fail-closed).

    The engine never silently grants approval.
    """
    return False


class ApprovalGate:
    """Evaluates whether a proposed non-read operation may proceed.

    Parameters
    ----------
    path_policy : PathPolicy
        Provides the workspace + zone classification source of truth.
    state : EngineState, optional
        When provided, audit records and snapshots are persisted.
    approver : callable, optional
        ``approve(proposed_dict) -> bool``. Defaults to denying everything.
    """

    def __init__(self, path_policy=None, state=None, approver=None):
        self.path_policy = path_policy
        self.state = state
        self.approver = approver if approver is not None else _default_approver
        self._last_operation_id = None

    # -- write path resolution ---------------------------------------------

    def _resolve_write(self, path):
        if self.path_policy is None:
            return {"abs_path": None, "info": None, "error":
                    "no path policy configured"}
        try:
            abs_path = self.path_policy.resolve(path)
        except Exception as e:
            return {"abs_path": None, "info": None, "error": str(e)}
        info = self.path_policy.mode_for(abs_path)
        return {"abs_path": abs_path, "info": info, "error": None}

    # -- core check --------------------------------------------------------

    def check(self, domain, target, operation=None, content=None,
              multi_file=False, snapshot_provider=None):
        """Return a Decision (allow/deny/require_approval) for a proposal.

        This is a PURE evaluation: it returns a Decision and may record an
        audit entry, but it NEVER performs the write/execute itself.

        domain      : a Domain enum (read/write/execute/git/network/publish)
        target      : human/path identifier of the subject
        operation   : tool op name (write only)
        content     : proposed new bytes (for checksum/approval id) (write)
        multi_file  : flags a T3 batch (write)
        snapshot_provider : callable(abs_path) -> bytes (write, T2/T3)
        """
        self._last_operation_id = None

        if domain == Domain.READ:
            # Allow reads within the workspace unless blocked.
            if self.path_policy is not None:
                try:
                    res = self.path_policy.read_decision(target)
                    self._audit(domain, target, "read", res, "proposed")
                    return res
                except Exception:
                    return deny(REASON_BLOCKED)
            return allow()

        # For non-read domains the policy must exist.
        if self.path_policy is None:
            return deny(REASON_BLOCKED)

        if domain in (Domain.GIT, Domain.NETWORK, Domain.PUBLISH):
            reason = {"git": REASON_BLOCKED, "network": REASON_NETWORK_DENIED,
                      "publish": REASON_PUBLISH_DENIED}[domain.value]
            d = deny(reason)
            self._audit(domain, target, domain.value, d, "proposed")
            return d

        if domain == Domain.WRITE:
            return self._check_write(target, operation, content, multi_file,
                                     snapshot_provider)

        if domain == Domain.EXECUTE:
            return self._check_execute(target)

        d = deny(REASON_NOT_ALLOWED)
        self._audit(domain, target, domain.value, d, "proposed")
        return d

    # -- write -------------------------------------------------------------

    def _check_write(self, path, operation, content, multi_file,
                     snapshot_provider):
        target = path
        resolved = self._resolve_write(path)
        if resolved["error"]:
            d = deny(REASON_OUTSIDE_WORKSPACE)
            self._audit(Domain.WRITE, target, operation, d, "proposed")
            return d
        info = resolved["info"]
        abs_path = resolved["abs_path"]

        if info is None or info["mode"] == "deny":
            d = deny(REASON_BLOCKED)
            self._audit(Domain.WRITE, target, operation, d, "proposed")
            return d
        if info["mode"] == "read_only":
            d = deny(REASON_READONLY)
            self._audit(Domain.WRITE, target, operation, d, "proposed")
            return d

        tier, needs_approval, needs_snapshot = classify_write(
            operation or "file.write", multi_file=multi_file,
            exists=os.path.isfile(abs_path))

        # Hard invariant: never write the production knowledge DB.
        from tools.permissions.pathpolicy import hard_write_guard
        if hard_write_guard(abs_path, self.path_policy.root):
            d = deny(REASON_BLOCKED)
            self._audit(Domain.WRITE, target, operation, d, "proposed")
            return d

        # If a snapshot is required but cannot be produced, fail closed BEFORE
        # approval is even requested. This enforces "no snapshot, no write".
        snapshot_ref = None
        if needs_snapshot:
            snapshot_ref = self._capture_snapshot(abs_path, tier,
                                                  snapshot_provider)
            if not snapshot_ref:
                d = deny(REASON_NO_SNAPSHOT)
                self._audit(Domain.WRITE, target, operation, d, "proposed")
                return d

        if not needs_approval:
            d = allow()
            self._audit(Domain.WRITE, target, operation, d, "proposed",
                        checksum_ref=snapshot_ref)
            return d

        # Explicit approval required.
        op_id = self._propose_id(domain=Domain.WRITE, target=target,
                                 operation=operation, content=content)
        proposal = {
            "domain": "write", "target": target, "operation": operation,
            "tier": tier, "multi_file": multi_file, "operation_id": op_id,
            "snapshot_ref": snapshot_ref,
        }
        approved = False
        try:
            approved = bool(self.approver(dict(proposal)))
        except Exception:
            approved = False

        if approved:
            d = require_approval(REASON_NO_APPROVAL, approval_id=op_id,
                                 required_scope=proposal)
            self._last_operation_id = op_id
            self._audit(Domain.WRITE, target, operation, d, "approved",
                        checksum_ref=snapshot_ref, approval_id=op_id)
            return d
        d = deny(REASON_NO_APPROVAL)
        self._audit(Domain.WRITE, target, operation, d, "denied",
                    approval_id=op_id)
        return d

    # -- execute -----------------------------------------------------------

    def _check_execute(self, command):
        # Execute requires explicit approval for every allowed command.
        op_id = self._propose_id(domain=Domain.EXECUTE, target=command,
                                 operation="execute", content=None)
        proposal = {"domain": "execute", "command": command,
                    "operation_id": op_id}
        approved = False
        try:
            approved = bool(self.approver(dict(proposal)))
        except Exception:
            approved = False
        if approved:
            d = require_approval(REASON_NO_APPROVAL, approval_id=op_id,
                                 required_scope=proposal)
            self._last_operation_id = op_id
            self._audit(Domain.EXECUTE, command, "execute", d, "approved",
                        approval_id=op_id)
            return d
        d = deny(REASON_NO_APPROVAL)
        self._audit(Domain.EXECUTE, command, "execute", d, "denied",
                    approval_id=op_id)
        return d

    # -- helpers -----------------------------------------------------------

    def authorize_write(self, path, operation, content=None,
                        snapshot_provider=None, multi_file=False):
        """Integration helper for the WriteTools layer.

        Evaluates a write proposal and returns ``(allowed, decision, error)``.
        ``error`` is a human-readable reason when ``allowed`` is False. After
        this returns True, the caller has explicit approval (and, for
        destructive writes, a stored snapshot) and may perform the write.
        """
        d = self.check(Domain.WRITE, path, operation=operation,
                       content=content, multi_file=multi_file,
                       snapshot_provider=snapshot_provider)
        if d.kind == DecisionKind.DENY:
            return (False, d,
                    f"write denied: {d.reason_code}"
                    + (f" ({d.required_scope})" if d.required_scope else ""))
        return (True, d, None)

    def authorize_execute(self, command, args=None):
        """Integration helper for the ExecutionRunner layer.

        ``command`` is the safe command string / description to approve.
        Returns ``(allowed, decision, error)``.
        """
        d = self.check(Domain.EXECUTE, command, operation=None)
        if d.kind == DecisionKind.DENY:
            return (False, d, f"execute denied: {d.reason_code}")
        return (True, d, None)

    def _propose_id(self, domain, target, operation, content):
        csum = ""
        if isinstance(content, bytes):
            from tools.permissions.journal import checksum_bytes
            csum = checksum_bytes(content)
        elif isinstance(content, str):
            from tools.permissions.journal import checksum_bytes
            csum = checksum_bytes(content.encode("utf-8"))
        return deterministic_id("op", domain.value, str(target),
                                str(operation), csum)

    def _capture_snapshot(self, abs_path, tier, snapshot_provider):
        """Capture pre-write bytes and store a journal record.

        Returns a checksum reference string (for the audit) or None on
        failure (fail-closed).
        """
        if self.state is None:
            # No persistent store: still require a snapshot from provider in
            # memory so the contract is honoured ("no snapshot, no write").
            if snapshot_provider is None:
                return None
            try:
                data = snapshot_provider(abs_path)
                if data is None:
                    return None
                from tools.permissions.journal import checksum_bytes
                return checksum_bytes(data if isinstance(data, bytes)
                                      else data.encode("utf-8"))
            except Exception:
                return None

        import os as _os
        rel = _os.path.relpath(abs_path, self.path_policy.root) \
            if self.path_policy else abs_path
        try:
            data = None
            if snapshot_provider is not None:
                data = snapshot_provider(abs_path)
            if data is None and _os.path.isfile(abs_path):
                with open(abs_path, "rb") as f:
                    data = f.read()
            if data is None:
                return None
            rec = self.state.add_journal(self._last_operation_id or "op",
                                         rel, tier, data)
            if rec.get("ok"):
                return rec.get("checksum")
            return None
        except Exception:
            return None

    def _audit(self, domain, target, permission, decision, status,
               checksum_ref=None, approval_id=None):
        if self.state is None:
            return
        try:
            self.state.add_audit(
                operation_id=decision.approval_id or self._last_operation_id
                or "op",
                domain=domain.value, target=str(target),
                permission=permission, decision=decision.kind.value,
                status=status, approval_id=approval_id or
                decision.approval_id, checksum_ref=checksum_ref)
        except Exception:
            pass
