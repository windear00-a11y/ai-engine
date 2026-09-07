"""Transport-independent Memory tool interface over the Memory API (v2).

Architecture (like api/tools.py for v1):
    Request -> validated operation (contract_v2) -> MemoryAPI -> structured response

No HTTP, no network, no trust bypass. Validation is strict via contract_v2.
"""

import argparse
import json
import sys

from api.contract_v2 import CONTRACT_VERSION as V2_CONTRACT_VERSION
from api.contract_v2 import validate_request as validate_v2_request
from api.errors import InternalError, KnowledgeError, ToolRequestError
from api.memory_api import MemoryAPI


def _error_payload(exc):
    d = dict(exc.as_dict())
    code = d.pop("error", "knowledge_error")
    message = d.pop("message", str(exc))
    payload = {"code": code, "message": message}
    payload.update(d)
    return payload


class MemoryToolInterface:
    """Executes validated v2 tool requests against a MemoryAPI instance.

    Usage:
        iface = MemoryToolInterface(data_root="/tmp/data")
        iface.execute({"operation": "remember", "arguments": {"text": "hello"}})
    """

    def __init__(self, data_root=None, default_vocabulary="diary_v1", api=None):
        if api is not None:
            self._api = api
            self._owns_api = False
        else:
            self._api = MemoryAPI(data_root=data_root, default_vocabulary=default_vocabulary)
            self._owns_api = True

    @property
    def api(self):
        return self._api

    def close(self):
        # MemoryAPI has no close (stateless), but handle if needed
        closer = getattr(self._api, "close", None)
        if closer and self._owns_api:
            try:
                closer()
            except Exception:
                pass

    def execute(self, request):
        try:
            operation, arguments = validate_v2_request(request)
            result = self._dispatch(operation, arguments)
        except KnowledgeError as exc:
            return self._failed(request, exc)
        except Exception:
            return self._failed(request, InternalError())
        return {
            "ok": True,
            "operation": operation,
            "contract_version": V2_CONTRACT_VERSION,
            "result": result,
        }

    def _dispatch(self, operation, arguments):
        # Normalize operation name: "context.get" -> "_op_context_get"
        method = "_op_" + operation.replace(".", "_")
        return getattr(self, method)(**arguments)

    @staticmethod
    def _failed(request, exc):
        operation = None
        if isinstance(request, dict) and isinstance(request.get("operation"), str):
            operation = request["operation"]
        return {
            "ok": False,
            "operation": operation,
            "contract_version": V2_CONTRACT_VERSION,
            "error": _error_payload(exc),
        }

    # -- operation handlers -------------------------------------------------
    def _op_remember(self, payload, context_hints=None, project_id=None):
        # Generic payload (arbitrary structured memory payload)
        kwargs = {}
        if context_hints is not None:
            kwargs["context_hints"] = context_hints
        if project_id is not None:
            kwargs["project_id"] = project_id
        return self._api.remember(payload=payload, **kwargs)

    def _op_recall(self, query, limit=None, candidate_limit=None, context=None,
                   project_id=None, vocabulary_id=None):
        return self._api.recall(query=query, limit=limit, candidate_limit=candidate_limit,
                                context=context, project_id=project_id, vocabulary_id=vocabulary_id)

    def _op_get(self, node_id, project_id=None, vocabulary_id=None):
        return self._api.get(node_id=node_id, project_id=project_id, vocabulary_id=vocabulary_id)

    def _op_provenance(self, node_id, project_id=None, vocabulary_id=None):
        return self._api.provenance(node_id=node_id, project_id=project_id, vocabulary_id=vocabulary_id)

    def _op_inspect(self, project_id=None, vocabulary_id=None):
        return self._api.inspect(project_id=project_id, vocabulary_id=vocabulary_id)

    def _op_context_get(self, context_id, project_id=None):
        return self._api.context_get(context_id=context_id, project_id=project_id)

    # -- Phase 26 lifecycle handlers --------------------------------------
    def _op_lifecycle_ingest(self, content, origin=None, source=None, uri=None,
                             role=None, actor=None, project_id=None,
                             context_hints=None):
        return self._api.lifecycle_ingest(
            content=content, origin=origin, source=source, uri=uri,
            role=role, actor=actor, project_id=project_id,
            context_hints=context_hints)

    def _op_lifecycle_experience(self, situation, attempt, result,
                                 context_id=None, evidence_ids=None,
                                 task_id=None, outcome_classification=None,
                                 outcome_id=None, task_type=None, domain=None,
                                 strategy_id=None, actor=None, source=None,
                                 project_id=None):
        return self._api.lifecycle_experience(
            situation=situation, attempt=attempt, result=result,
            context_id=context_id, evidence_ids=evidence_ids, task_id=task_id,
            outcome_classification=outcome_classification,
            outcome_id=outcome_id, task_type=task_type, domain=domain,
            strategy_id=strategy_id, actor=actor, source=source,
            project_id=project_id)

    def _op_lifecycle_learning(self, experience_ids, project_id=None):
        return self._api.lifecycle_learning(experience_ids=experience_ids,
                                            project_id=project_id)

    def _op_lifecycle_strategy(self, experience_ids, project_id=None,
                               min_samples=None):
        return self._api.lifecycle_strategy(
            experience_ids=experience_ids, project_id=project_id,
            min_samples=min_samples)

    def _op_lifecycle_trace(self, record_id, role=None, project_id=None,
                            max_depth=None):
        return self._api.lifecycle_trace(record_id=record_id, role=role,
                                         project_id=project_id,
                                         max_depth=max_depth)

    def _op_lifecycle_describe(self, record_id, role=None, project_id=None):
        return self._api.lifecycle_describe(record_id=record_id, role=role,
                                            project_id=project_id)

    def _op_lifecycle_summary(self, project_id=None):
        return self._api.lifecycle_summary(project_id=project_id)

    def _op_lifecycle_plan(self, situation, experience_ids, context_id=None,
                           constraints=None, max_steps=None, min_samples=None,
                           project_id=None):
        return self._api.lifecycle_plan(
            situation=situation, experience_ids=experience_ids,
            context_id=context_id, constraints=constraints,
            max_steps=max_steps, min_samples=min_samples,
            project_id=project_id)

    def _op_lifecycle_grant(self, plan_id, plan_step_ids, actor,
                            mechanism=None, evidence_ids=None, project_id=None):
        return self._api.lifecycle_grant(
            plan_id=plan_id, plan_step_ids=plan_step_ids, actor=actor,
            mechanism=mechanism, evidence_ids=evidence_ids,
            project_id=project_id)

    def _op_lifecycle_authorize(self, plan_id, plan_step_ids, actor,
                                policy=None, request_ref=None, project_id=None):
        return self._api.lifecycle_authorize(
            plan_id=plan_id, plan_step_ids=plan_step_ids, actor=actor,
            policy=policy, request_ref=request_ref, project_id=project_id)

    def _op_lifecycle_execute(self, plan_id, plan_step_id, actor,
                              request_id=None, policy=None, executors=None,
                              project_id=None):
        return self._api.lifecycle_execute(
            plan_id=plan_id, plan_step_id=plan_step_id, actor=actor,
            request_id=request_id, policy=policy, executors=executors,
            project_id=project_id)


def execute(request, data_root=None):
    iface = MemoryToolInterface(data_root=data_root)
    try:
        return iface.execute(request)
    finally:
        iface.close()


def _emit_json(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="api.memory_tools", description="Memory v2 tool runner (testing only).")
    parser.add_argument("request_file", nargs="?", help="path to request JSON, or '-' for stdin")
    parser.add_argument("--data-root", default=None, help="data root override")
    args = parser.parse_args(argv)
    try:
        if args.request_file and args.request_file != "-":
            with open(args.request_file, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            text = sys.stdin.read()
        try:
            request = json.loads(text)
        except ValueError as exc:
            _emit_json(MemoryToolInterface._failed({}, ToolRequestError("invalid JSON: %s" % exc)))
            return 1
    except OSError as exc:
        _emit_json(MemoryToolInterface._failed({}, ToolRequestError("unable to read request: %s" % exc)))
        return 1
    iface = MemoryToolInterface(data_root=args.data_root)
    try:
        response = iface.execute(request)
    finally:
        iface.close()
    _emit_json(response)
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
