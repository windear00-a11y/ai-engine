"""Request Handler: User Request → Verified Work (AI Engine v1).

Thin orchestration: Request → StructuredIntent → Intelligence Loop → Result.
Uses existing intelligence, does not duplicate it. Model-independent.
"""

import time
from engine.request_contract import validate_request
from engine.request_adapter import RuleAdapter

# Reuse existing loop
from intelligence.loop.engine import execute_intelligence_loop


def handle_request(request_dict, workspace_root=None, stores=None,
                   operator_approval=None, knowledge_nodes=None):
    """Handle a user request and return a Verified Result.

    Parameters
    ----------
    request_dict : dict with at least {"request": str}
    workspace_root : str | None (overrides request workspace_root)
    stores : dict of intelligence stores (for :memory: testing) or None for defaults
    operator_approval : callable(decision_dict) -> bool (server-side authority)
    knowledge_nodes : optional list of knowledge nodes to pass to loop

    Returns dict Result (see spec section 14). Never raises: errors become ok=False.
    """
    # Validate request envelope (rejects forbidden keys like approved)
    try:
        operation, args = validate_request(request_dict)
    except Exception as e:
        code = getattr(e, "code", "invalid_request")
        msg = getattr(e, "message", str(e))
        return {
            "ok": False,
            "operation": "request.execute",
            "error": {"code": code, "message": msg},
            "status": "invalid_request",
            "request_id": request_dict.get("request_id") if isinstance(request_dict, dict) else None,
        }

    raw_request = args["request"]
    ws = workspace_root or args.get("workspace_root")
    constraints = args.get("constraints", {}) or {}
    request_id = args.get("request_id")

    # Clamp constraints like DeterministicPlanner
    try:
        if "max_steps" in constraints:
            ms = int(constraints["max_steps"])
            constraints["max_steps"] = max(1, min(100, ms))
    except Exception:
        constraints["max_steps"] = 7
    try:
        if "max_duration" in constraints:
            md = int(constraints["max_duration"])
            constraints["max_duration"] = max(10, min(600, md))
    except Exception:
        constraints["max_duration"] = 120
    if "allow_write" in constraints:
        constraints["allow_write"] = bool(constraints["allow_write"])

    # Parse via RuleAdapter (deterministic)
    adapter = RuleAdapter()
    try:
        intent_obj, parse_report = adapter.parse(raw_request, workspace_root=ws, request_id=request_id)
        # Merge constraints from request
        intent_obj["constraints"] = constraints
    except Exception as e:
        return {
            "ok": False,
            "operation": "request.execute",
            "error": {"code": "internal_error", "message": str(e)},
            "status": "internal_error",
            "request_id": request_id,
        }

    # Map StructuredIntent to loop task
    task = {
        "task_id": intent_obj["request_id"],
        "task_type": intent_obj["intent"],
        "domain": "lint" if "lint" in raw_request.lower() or "e302" in raw_request.lower() else "",
        "target": intent_obj["target"] or {},
        "error": intent_obj["error"] or {},
        "description": raw_request,
        "constraints": constraints,
        "intent": intent_obj["intent"],
    }
    # Preserve error message in target for loop's E302 detection
    if intent_obj["error"] and intent_obj["error"].get("message"):
        if isinstance(task["target"], dict):
            task["target"] = dict(task["target"])
            task["target"]["error"] = intent_obj["error"]["message"]

    # Unpack stores
    stores = stores or {}
    # Call existing cognitive loop (real TaskEngine when has_index, else synthetic)
    try:
        loop_res = execute_intelligence_loop(
            task,
            workspace_root=ws,
            operator_approval=operator_approval,
            knowledge_nodes=knowledge_nodes,
            experience_store=stores.get("experience_store"),
            strategy_store=stores.get("strategy_store"),
            outcome_store=stores.get("outcome_store"),
            evidence_store=stores.get("evidence_store"),
            reasoning_store=stores.get("reasoning_store"),
            decision_store=stores.get("decision_store"),
            learning_store=stores.get("learning_store"),
            context_store=stores.get("context_store"),
            max_steps=constraints.get("max_steps", 7),
            max_duration=constraints.get("max_duration", 120),
        )
    except Exception as e:
        return {
            "ok": False,
            "operation": "request.execute",
            "error": {"code": "internal_error", "message": str(e)},
            "status": "internal_error",
            "request_id": intent_obj["request_id"],
            "intent": intent_obj,
        }

    # Build Result envelope (v1.1 additive retrieval)
    result = {
        "ok": bool(loop_res.ok),
        "operation": "request.execute",
        "request_id": intent_obj["request_id"],
        "intent": intent_obj,
        "parse_report": parse_report,
        "context_id": loop_res.context_id,
        "reasoning_id": loop_res.reasoning_id,
        "decision_id": loop_res.decision_id,
        "plan_id": loop_res.plan_id,
        "outcome_id": loop_res.outcome_id,
        "experience_id": loop_res.experience_id,
        "learning_event_id": loop_res.learning_event_id,
        "status": loop_res.status,
        "approval_required": loop_res.approval_required,
        "risk_level": getattr(loop_res, "risk_level", None) or (stores.get("decision_store") and None),
        "errors": loop_res.errors,
        "adaptations": loop_res.adaptations,
        "fallback_used": loop_res.fallback_used,
        "retrieval": getattr(loop_res, "retrieval", {}),
    }
    # Add decision details if available
    if loop_res.decision_id and stores.get("decision_store"):
        try:
            dec = stores["decision_store"].get(loop_res.decision_id)
            if dec:
                result["risk_level"] = dec.get("risk_level")
                result["approval_required"] = dec.get("approval_required", result["approval_required"])
        except Exception:
            pass
    # Handle insufficient_information and awaiting_approval as ok=False but not error
    if loop_res.status in ("awaiting_approval", "insufficient_information"):
        result["ok"] = False
    return result
