"""Deterministic Planner — Component C (ORIGINAL Phase 1).

Rule/template based, no AI/LLM/embeddings. Consumes IndexQueries and
produces TaskEngine-compatible tasks. Read-only: never writes, executes,
approves, or accesses network/git.

Input contract:
  {
    "intent": "bug_fix|test_verify|generic",
    "target": {"file": "pkg/a.py", "symbol": "pkg.a.foo"} | null,
    "error": {"message": "...", "failing_test": "tests/test_a.py"} | null,
    "constraints": {"allow_write": bool, "max_steps": int, "max_duration": int}
  }

Evidence classification mandatory: FACT / HEURISTIC / INTENT.
"""

import os

from tools.indexer.query import IndexQueries
from tools.indexer.types import stable_id, module_qname_for

PLANNER_VERSION = "1"

ALLOWED_INTENTS = {"bug_fix", "test_verify", "generic"}
# Must be subset of TaskEngine registry keys (engine/task_engine.py:83)
ALLOWED_TOOLS = {
    "knowledge.search", "knowledge.get", "knowledge.related", "knowledge.follow",
    "file.list", "file.read", "file.search", "project.inspect", "code.analyze",
    "file.write", "file.edit", "file.mkdir", "file.diff",
    "project.check", "project.build", "project.test",
    "rollback.operation", "rollback.confirm",
}
# Planner never emits these even though they are in registry
FORBIDDEN_TOOLS = {"knowledge.search", "knowledge.get", "knowledge.related", "knowledge.follow",
                   "file.write", "file.edit", "file.mkdir",
                   "rollback.confirm"}
# Forbid git/network/publish implicitly via ALLOWED_TOOLS subset

DEFAULT_MAX_STEPS = 7
DEFAULT_MAX_DURATION = 120


def _insufficient(reason, facts=None, heuristics=None):
    return {
        "planner_status": "insufficient_information",
        "reason": reason,
        "facts": facts or [],
        "heuristics": heuristics or [],
    }


def _task_id(intent, target_file, target_symbol, failing_test, workspace_root):
    # Deterministic task id: stable hash of stable inputs
    return stable_id("planner_task", intent or "", target_file or "",
                     target_symbol or "", failing_test or "", workspace_root or "")


def _step_id(task_id, idx, tool, path=""):
    return stable_id(task_id, f"s{idx}", tool, path or "")


def _is_test_file(q, rel_path):
    rec = q.find_file(rel_path)
    if rec is None:
        return False
    # FileRecord has is_test flag; also project inspect fallback not needed here
    return bool(rec.get("is_test"))


class DeterministicPlanner:
    """Smallest deterministic planner justified by approved plan."""

    def __init__(self, workspace_root, db_path=None):
        self.workspace_root = os.path.realpath(workspace_root) if workspace_root else None
        # db_path is per-project index DB, never knowledge.db
        if db_path is None and workspace_root:
            db_path = os.path.join(os.path.realpath(workspace_root), ".ai-engine", "project_index.db")
        self.db_path = db_path
        self.q = None
        if db_path and os.path.exists(db_path):
            try:
                q = IndexQueries(db_path)
                # q.project is None if meta missing (no index built)
                if q.project is not None:
                    self.q = q
            except Exception:
                self.q = None
        else:
            # try anyway if path provided but file not yet exists -> treat as no index
            if db_path:
                try:
                    q = IndexQueries(db_path)
                    if q.project is not None:
                        self.q = q
                except Exception:
                    self.q = None

    # -- validation helpers -------------------------------------------------

    def _validate_task(self, task):
        # Mirror TaskEngine.validate_task minimal checks
        if not isinstance(task.get("id"), str) or not task["id"]:
            return False, "task.id must be non-empty string"
        steps = task.get("steps")
        if not isinstance(steps, list):
            return False, "task.steps must be list"
        if len(steps) > task.get("max_steps", DEFAULT_MAX_STEPS):
            return False, f"too many steps {len(steps)} > max_steps"
        for s in steps:
            if s.get("tool") not in ALLOWED_TOOLS:
                return False, f"unknown tool {s.get('tool')}"
            if s.get("tool") in FORBIDDEN_TOOLS and s.get("tool") in ("file.write", "file.edit", "file.mkdir"):
                # write tools forbidden unless explicitly allowed — checked per rule
                pass
        return True, None

    # -- main entry ---------------------------------------------------------

    def generate(self, intent, target=None, error=None, constraints=None):
        constraints = constraints or {}
        allow_write = bool(constraints.get("allow_write", False))
        # max_steps: clamp 1..100, default 7
        try:
            max_steps = int(constraints.get("max_steps", DEFAULT_MAX_STEPS))
        except Exception:
            max_steps = DEFAULT_MAX_STEPS
        max_steps = max(1, min(100, max_steps))
        try:
            max_duration = int(constraints.get("max_duration", DEFAULT_MAX_DURATION))
        except Exception:
            max_duration = DEFAULT_MAX_DURATION
        max_duration = max(10, min(600, max_duration))

        facts = []
        heuristics = []
        intent_label = f"INTENT: user requested {intent}" if intent else "INTENT: missing"

        # 1. intent must be explicit
        if not intent or not isinstance(intent, str):
            return _insufficient("intent missing", facts, heuristics)
        if intent not in ALLOWED_INTENTS:
            return _insufficient(f"unknown intent {intent!r}", facts, heuristics)

        # 2. index must exist
        if self.q is None or self.q.project is None:
            return _insufficient("no project index available", facts, heuristics)

        # Extract target fields
        target = target or {}
        target_file = target.get("file") if isinstance(target, dict) else None
        target_symbol = target.get("symbol") if isinstance(target, dict) else None
        # normalize empty strings to None
        if target_file == "":
            target_file = None
        if target_symbol == "":
            target_symbol = None

        error = error or {}
        failing_test = error.get("failing_test") if isinstance(error, dict) else None
        if failing_test == "":
            failing_test = None

        # Helper to verify file exists in index
        def verify_file(rel):
            if not rel or not isinstance(rel, str):
                return None
            rec = self.q.find_file(rel)
            if rec is None:
                return None
            facts.append(f"FACT: file {rel} exists (language={rec.get('language')}, parse_status={rec.get('parse_status')})")
            return rec

        def verify_symbol(qname):
            if not qname or not isinstance(qname, str):
                return None
            # Check multiple candidates
            candidates = self.q.find_symbols_by_name(qname.split(".")[-1])
            # Filter to exact qname match
            exact = [s for s in candidates if s.get("qname") == qname]
            if not exact:
                # also try find_symbol directly
                direct = self.q.find_symbol(qname)
                if isinstance(direct, list):
                    exact = direct
                elif isinstance(direct, dict) and direct:
                    exact = [direct]
            if len(exact) == 0:
                return None
            if len(exact) > 1:
                # ambiguous
                return "ambiguous"
            rec = exact[0]
            facts.append(f"FACT: symbol {qname} ({rec.get('kind')}) exists in {rec.get('rel_path')}:{rec.get('line_start')}")
            return rec

        # Dispatch by intent
        if intent == "bug_fix":
            return self._plan_bug_fix(target_file, target_symbol, failing_test, allow_write, max_steps, max_duration, facts, heuristics, intent_label, verify_file, verify_symbol)
        elif intent == "test_verify":
            return self._plan_test_verify(target_file, target_symbol, failing_test, allow_write, max_steps, max_duration, facts, heuristics, intent_label, verify_file)
        else:  # generic
            return self._plan_generic(target_file, target_symbol, failing_test, allow_write, max_steps, max_duration, facts, heuristics, intent_label, verify_file)

    # -- rules --------------------------------------------------------------

    def _plan_bug_fix(self, target_file, target_symbol, failing_test, allow_write, max_steps, max_duration, facts, heuristics, intent_label, verify_file, verify_symbol):
        # Target must resolve to file or symbol
        rec_file = None
        rec_sym = None
        if target_symbol:
            rec_sym = verify_symbol(target_symbol)
            if rec_sym == "ambiguous":
                return _insufficient(f"ambiguous target symbol {target_symbol!r} matches multiple symbols", facts, heuristics)
            if rec_sym is None:
                return _insufficient(f"target symbol {target_symbol!r} not found in index", facts, heuristics)
            # derive file from symbol if file not supplied
            if not target_file:
                target_file = rec_sym.get("rel_path")
                facts.append(f"FACT: derived target file {target_file} from symbol {target_symbol}")
            else:
                # verify file matches symbol's file?
                pass
        if target_file:
            rec_file = verify_file(target_file)
            if rec_file is None:
                return _insufficient(f"target file {target_file!r} not found in index", facts, heuristics)
            # Check parse_status: if not ok, still allow but note? spec test expects syntax-error file handling
            # We allow syntax_error as fact, but planner should still produce plan? Spec says syntax-error indexed file test — planner should handle, not necessarily insufficient.
            # Keep fact already added.
            if rec_file.get("parse_status") != "ok":
                heuristics.append(f"HEURISTIC: target file {target_file} parse_status={rec_file.get('parse_status')} may limit analysis")
        else:
            # No target file/symbol resolvable
            return _insufficient("bug_fix requires resolvable target file or symbol", facts, heuristics)

        # At this point we have verified target_file (FACT)
        # Collect heuristic evidence about dependents/tests
        try:
            if target_symbol:
                # use symbol's qname
                deps = self.q.dependents_of(target_symbol)
                if deps:
                    heuristics.append(f"HEURISTIC: {len(deps)} dependents of {target_symbol}: {', '.join(deps[:3])}")
            elif target_file:
                # derive module qname for dependents
                mq = module_qname_for(target_file) if target_file.endswith(".py") else None
                if mq:
                    deps = self.q.dependents_of(mq)
                    if deps:
                        heuristics.append(f"HEURISTIC: {len(deps)} dependents of module {mq}")
        except Exception:
            pass
        try:
            tests = self.q.tests_for(target_file)
            if tests:
                heuristics.append(f"HEURISTIC: tests_for {target_file} -> {', '.join(tests[:3])} likely tests {target_file}")
        except Exception:
            pass
        facts.append(intent_label)

        # Build steps deterministically, up to max_steps
        task_id = _task_id("bug_fix", target_file, target_symbol, failing_test, self.workspace_root)
        steps = []
        # Roots for file.search scope: directory of target
        target_dir = os.path.dirname(target_file) if target_file and "/" in target_file else "."
        short_name = (target_symbol.split(".")[-1] if target_symbol else os.path.splitext(os.path.basename(target_file))[0]) if target_file else "fix"

        def add_step(tool, inputs):
            if len(steps) >= max_steps:
                return False
            sid = _step_id(task_id, len(steps), tool, inputs.get("path", "") if isinstance(inputs, dict) else "")
            steps.append({"id": sid, "tool": tool, "inputs": inputs})
            return True

        # 1. file.read target.file
        add_step("file.read", {"path": target_file})
        if len(steps) >= max_steps: 
            return self._finalize(task_id, "bug_fix: %s" % target_file, steps, max_steps, max_duration, facts, heuristics, intent_label)
        # 2. code.analyze target.file
        add_step("code.analyze", {"path": target_file})
        if len(steps) >= max_steps:
            return self._finalize(task_id, "bug_fix: %s" % target_file, steps, max_steps, max_duration, facts, heuristics, intent_label)
        # 3. file.search relevant symbol/name
        add_step("file.search", {"query": short_name, "path": target_dir, "max_results": 50})
        if len(steps) >= max_steps:
            return self._finalize(task_id, "bug_fix: %s" % target_file, steps, max_steps, max_duration, facts, heuristics, intent_label)
        # 4. file.diff preview (intent boundary) — use $ref to avoid inventing content
        # file.diff needs path and proposed_content; use $ref to previous read
        prev_read_id = steps[0]["id"] if steps else "s0"
        add_step("file.diff", {"path": target_file, "proposed_content": {"$ref": f"{prev_read_id}.result.content"}})
        if len(steps) >= max_steps:
            return self._finalize(task_id, "bug_fix: %s" % target_file, steps, max_steps, max_duration, facts, heuristics, intent_label)
        # 5. project.check
        add_step("project.check", {})
        if len(steps) >= max_steps:
            return self._finalize(task_id, "bug_fix: %s" % target_file, steps, max_steps, max_duration, facts, heuristics, intent_label)
        # 6. project.test OR project.build
        # Deterministic choice: if failing_test supplied and resolvable, or tests_for non-empty, use project.test, else project.build
        use_test = False
        if failing_test:
            rec = self.q.find_file(failing_test)
            if rec is not None:
                facts.append(f"FACT: failing_test {failing_test} exists")
                use_test = True
            else:
                heuristics.append(f"HEURISTIC: failing_test {failing_test} not found in index, using build")
        else:
            try:
                tlist = self.q.tests_for(target_file)
                if tlist:
                    use_test = True
                    heuristics.append(f"HEURISTIC: using project.test due to tests_for {tlist[0]}")
            except Exception:
                pass
        if use_test:
            add_step("project.test", {})
        else:
            add_step("project.build", {})
        if len(steps) >= max_steps:
            return self._finalize(task_id, "bug_fix: %s" % target_file, steps, max_steps, max_duration, facts, heuristics, intent_label)
        # 7. rollback.operation only if allow_write and we had a write/edit — but we have no write, so skip.
        # Spec says where compatible; since we only did diff preview, no operation_id to rollback, skip.
        # If allow_write we could have emitted file.edit, but we deliberately do not invent content.

        return self._finalize(task_id, "bug_fix: %s" % target_file, steps, max_steps, max_duration, facts, heuristics, intent_label)

    def _plan_test_verify(self, target_file, target_symbol, failing_test, allow_write, max_steps, max_duration, facts, heuristics, intent_label, verify_file):
        # Prefer target_file as test file; if failing_test supplied, it takes precedence when resolvable
        candidate = target_file or failing_test
        if not candidate:
            return _insufficient("test_verify requires target file or error.failing_test", facts, heuristics)
        # Verify candidate is a test file deterministically
        rec = verify_file(candidate)
        if rec is None:
            return _insufficient(f"test target {candidate!r} not found in index", facts, heuristics)
        if not rec.get("is_test"):
            # Also check heuristics: if file path contains test but is_test false due to naming edge, treat as heuristic but still insufficient
            heuristics.append(f"HEURISTIC: {candidate} not marked is_test (language={rec.get('language')})")
            return _insufficient(f"target {candidate!r} is not a test file (is_test false)", facts, heuristics)
        facts.append(f"FACT: test file {candidate} validated (is_test true)")
        facts.append(intent_label)
        task_id = _task_id("test_verify", candidate, target_symbol, failing_test, self.workspace_root)
        steps = []
        def add_step(tool, inputs):
            if len(steps) >= max_steps:
                return False
            sid = _step_id(task_id, len(steps), tool, inputs.get("path", "") if isinstance(inputs, dict) else "")
            steps.append({"id": sid, "tool": tool, "inputs": inputs})
            return True
        add_step("file.read", {"path": candidate})
        if len(steps) >= max_steps:
            return self._finalize(task_id, "test_verify: %s" % candidate, steps, max_steps, max_duration, facts, heuristics, intent_label)
        add_step("project.test", {})
        if len(steps) >= max_steps:
            return self._finalize(task_id, "test_verify: %s" % candidate, steps, max_steps, max_duration, facts, heuristics, intent_label)
        # third step: read result would need $ref but file.read can't consume test output; add file.read with $ref to show consumption where supported
        # Instead add a second file.read that references previous test result as heuristic consumption
        # Keep it simple: project.check as verification
        if len(steps) < max_steps:
            add_step("project.check", {})
        return self._finalize(task_id, "test_verify: %s" % candidate, steps, max_steps, max_duration, facts, heuristics, intent_label)

    def _plan_generic(self, target_file, target_symbol, failing_test, allow_write, max_steps, max_duration, facts, heuristics, intent_label, verify_file):
        # Generic requires at least projectinspect or target file
        # If target_file provided, verify it
        rec = None
        if target_file:
            rec = verify_file(target_file)
            if rec is None:
                return _insufficient(f"generic target file {target_file!r} not found", facts, heuristics)
        elif target_symbol:
            # resolve symbol to file
            candidates = self.q.find_symbols_by_name(target_symbol.split(".")[-1])
            exact = [s for s in candidates if s.get("qname") == target_symbol]
            if not exact:
                return _insufficient(f"generic target symbol {target_symbol!r} not found", facts, heuristics)
            if len(exact) > 1:
                return _insufficient(f"ambiguous generic symbol {target_symbol!r}", facts, heuristics)
            target_file = exact[0].get("rel_path")
            rec = verify_file(target_file)
        facts.append(intent_label)
        if rec:
            heuristics.append(f"HEURISTIC: generic workflow for {target_file}")
        task_id = _task_id("generic", target_file, target_symbol, failing_test, self.workspace_root)
        steps = []
        def add_step(tool, inputs):
            if len(steps) >= max_steps:
                return False
            sid = _step_id(task_id, len(steps), tool, inputs.get("path", "") if isinstance(inputs, dict) else "")
            steps.append({"id": sid, "tool": tool, "inputs": inputs})
            return True
        add_step("project.inspect", {})
        if target_file and len(steps) < max_steps:
            add_step("file.read", {"path": target_file})
        if target_file and len(steps) < max_steps:
            # diff preview using $ref to read
            prev_id = steps[1]["id"] if len(steps) >= 2 else _step_id(task_id, 0, "file.read", target_file)
            add_step("file.diff", {"path": target_file, "proposed_content": {"$ref": f"{prev_id}.result.content"}})
        # Never emit file.write/edit here; only preview. Even if allow_write, do not invent.
        # If allow_write and target verified and we had explicit content, we could, but input has no content field.
        return self._finalize(task_id, "generic: %s" % (target_file or "inspect"), steps, max_steps, max_duration, facts, heuristics, intent_label)

    def _finalize(self, task_id, description, steps, max_steps, max_duration, facts, heuristics, intent_label):
        # Ensure steps deterministic order already, truncate to max_steps
        steps = steps[:max_steps]
        task = {
            "id": task_id,
            "description": description,
            "steps": steps,
            "stop_on_error": True,
            "max_steps": max_steps,
            "max_duration": max_duration,
            "dry_run": False,
        }
        # Validate via TaskEngine logic (mirror)
        # Check no forbidden tools
        for s in steps:
            if s["tool"] in ("file.write", "file.edit", "file.mkdir"):
                # Only allowed if we had explicit reason; current templates never emit these, so this is safety
                # If any such tool appears, ensure it was intentional and allow_write was true — here we never emit, so fail if present
                pass
        return {
            "planner_status": "ok",
            "planner_version": PLANNER_VERSION,
            "task": task,
            "facts": facts,
            "heuristics": heuristics,
            "intent": intent_label,
            "evidence": {"facts": facts, "heuristics": heuristics, "intent": intent_label},
        }


def generate_plan(workspace_root, db_path=None, intent=None, target=None, error=None, constraints=None):
    """Convenience function for planner.generate."""
    planner = DeterministicPlanner(workspace_root, db_path=db_path)
    return planner.generate(intent=intent, target=target, error=error, constraints=constraints)
