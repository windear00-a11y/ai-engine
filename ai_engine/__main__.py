"""ai-engine CLI — Generic Persistent Intelligence System (Phase 9).

Provides local CLI over Phase 0–8 APIs without changing contracts or core behavior.

Commands (per spec):
    ai-engine --version
    ai-engine init [--project <id>] [--vocabulary diary_v1]
    ai-engine remember "text" [--project <id>] [--source manual]
    ai-engine recall "query" [--limit 20] [--project <id>] [--json]
    ai-engine capture manual --text "..." [--project <id>]
    ai-engine context show <context_id> [--project <id>]
    ai-engine context diff <ctx_a> <ctx_b> [--project <id>]
    ai-engine status [--project <id>]
    ai-engine backup [--project <id>] [--output <file>]
    ai-engine export [--project <id>] [--format jsonl] [--output <file>]
    ai-engine import <file.jsonl> [--project <id>] [--dry-run]
    ai-engine doctor [--project <id>] [--repair]
    ai-engine migrate [--project <id>]

Plus legacy v1 commands for backward compat (search/get/related/follow/provenance/inspect).

All Memory operations go through ai_engine.memory.Memory (v2 generic payload) or
ai_engine.capture.run_capture (capture) — never raw SQLite for normal operations.
Backup/export/import/doctor/migrate reuse existing migration/ingestion logic.

Output:
    --json  -> machine-readable JSON on stdout (sort_keys, indent 2)
    normal  -> concise human-readable
Errors: non-zero exit, structured JSON {"ok": false, "code": ..., "message": ...} on stderr.
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ai_engine import __version__

# For legacy v1 commands
from retrieval.repository import DEFAULT_KNOWLEDGE_DB

def to_json(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

def eprint_json(payload):
    print(to_json(payload), file=sys.stderr)

def fail(code, message, extra=None):
    payload = {"ok": False, "code": code, "message": message}
    if extra:
        payload.update(extra)
    eprint_json(payload)
    return 1

# -- version ---------------------------------------------------------------
def cmd_version(args):
    print(__version__)
    return 0

# -- init ------------------------------------------------------------------
def cmd_init(args):
    from ai_engine.paths import ensure_default_project, create_project, get_project_entry, DEFAULT_PROJECT_ID
    from ai_engine.vocabulary import Vocabulary
    project_id = args.project or DEFAULT_PROJECT_ID
    vocab_id = args.vocabulary or "diary_v1"
    try:
        Vocabulary.load(vocab_id)
    except Exception as e:
        return fail("invalid_argument", f"vocabulary {vocab_id!r} not found: {e}")
    try:
        if project_id == DEFAULT_PROJECT_ID:
            ensure_default_project()
        else:
            ensure_default_project()
            if get_project_entry(project_id) is None:
                create_project(project_id, vocabulary_id=vocab_id)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    print(f"initialized project {project_id!r} with vocabulary {vocab_id!r}")
    return 0

# -- remember --------------------------------------------------------------
def cmd_remember(args):
    from ai_engine.memory import Memory
    project_id = getattr(args, "project", None) or "default"
    source = getattr(args, "source", "manual") or "manual"
    text = args.text
    if not isinstance(text, str) or not text.strip():
        return fail("invalid_argument", "text must be non-empty string")
    # Construct generic v2 payload correctly: {"text": ...}
    payload = {"text": text.strip()}
    try:
        mem = Memory(project_id=project_id)
        res = mem.remember(payload=payload, source=source)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    except Exception as e:
        return fail("internal_error", f"remember failed: {e}")
    if not res.get("ok"):
        return fail(res.get("code", "invalid_argument"), res.get("error", "remember failed"), {"details": res})
    # Success: concise human-readable + --json handling via caller
    if getattr(args, "json", False):
        print(to_json(res))
    else:
        print(f"remembered {res['node_id']} (activity {res['activity_id']}) in project {project_id!r}")
    return 0

# -- recall ----------------------------------------------------------------
def cmd_recall(args):
    from ai_engine.memory import Memory
    project_id = getattr(args, "project", None) or "default"
    query = args.query
    limit = getattr(args, "limit", 20) or 20
    as_json = getattr(args, "json", False)
    try:
        mem = Memory(project_id=project_id)
        res = mem.recall(query=query, limit=limit)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    except Exception as e:
        return fail("internal_error", f"recall failed: {e}")
    if not res.get("ok"):
        return fail(res.get("code", "invalid_argument"), res.get("error", "recall failed"))
    result = res["result"]
    if as_json:
        print(to_json(result))
    else:
        # Human-readable concise
        print(f"recall {len(result['knowledge'])} results for {query!r} in {project_id!r}:")
        for n in result["knowledge"][:limit]:
            prov = n.get("provenance", {})
            print(f"  {n.get('id')} [{n.get('type')}] {n.get('name')} — {n.get('description', '')[:80]} (source {prov.get('source_name','?')})")
            if "_score" in n:
                print(f"    score {n['_score']}")
    return 0

# -- capture ---------------------------------------------------------------
def cmd_capture(args):
    # Only manual supported per spec
    if args.capture_cmd != "manual":
        return fail("invalid_argument", f"unknown capture adapter {args.capture_cmd!r}")
    text = getattr(args, "text", None)
    if not isinstance(text, str) or not text.strip():
        return fail("invalid_argument", "capture manual --text must be non-empty")
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.capture import run_capture
    res = run_capture(raw=text.strip(), project_id=project_id, vocabulary_id="diary_v1", activity_type="manual", source="manual")
    if not res.get("ok"):
        return fail(res.get("code", "invalid_argument"), res.get("error", "capture failed"))
    if getattr(args, "json", False):
        print(to_json(res))
    else:
        print(f"captured {res['activity_id']} -> {res['node_id']}")
    return 0

# -- context show/diff -----------------------------------------------------
def cmd_lifecycle_ingest(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.lifecycle_service import LifecycleService
    svc = LifecycleService(project_id=project_id)
    try:
        if args.origin == "external":
            result = svc.ingest_external_knowledge(
                args.content, source=args.source or "external", uri=args.uri,
                actor=args.actor)
        else:
            result = svc.ingest_user_fact(args.content, source=args.source,
                                          actor=args.actor)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    if getattr(args, "json", False):
        print(to_json(result))
    else:
        print("%s %s -> %s (%s)" % (result["role"], result["record_id"],
                                    result["origin"],
                                    result["lifecycle_state"]))
    return 0


def cmd_lifecycle_experience(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.lifecycle_service import LifecycleService
    svc = LifecycleService(project_id=project_id)
    evidence = getattr(args, "evidence_ids", None)
    evidence_ids = [x.strip() for x in evidence.split(",")] if evidence else None
    try:
        result = svc.record_experience(
            args.situation, args.attempt, args.result,
            context_id=getattr(args, "context_id", None),
            evidence_ids=evidence_ids,
            task_id=getattr(args, "task_id", None),
            outcome_classification=getattr(args, "outcome_classification", None),
            task_type=getattr(args, "task_type", None),
            domain=getattr(args, "domain", None),
            actor=getattr(args, "actor", None),
            source=getattr(args, "source", None),
            project_id=project_id)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    if getattr(args, "json", False):
        print(to_json(result))
    else:
        print("experience %s (%s) outcome %s" % (
            result["experience_id"], result["origin"],
            result["outcome_classification"]))
    return 0


def cmd_lifecycle_learning(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.lifecycle_service import LifecycleService
    svc = LifecycleService(project_id=project_id)
    try:
        result = svc.derive_learning(args.experience_ids,
                                     project_id=project_id)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    if getattr(args, "json", False):
        print(to_json(result))
    else:
        print("learning %s from %d experiences" % (
            result["learning_id"], len(result["experience_ids"])))
    return 0


def cmd_lifecycle_strategy(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.lifecycle_service import LifecycleService
    svc = LifecycleService(project_id=project_id)
    try:
        result = svc.derive_strategies(
            args.experience_ids, project_id=project_id,
            min_samples=getattr(args, "min_samples", None))
    except ValueError as e:
        return fail("invalid_argument", str(e))
    if getattr(args, "json", False):
        print(to_json(result))
    else:
        print("derived %d strategy(ies)" % result["strategy_count"])
        for s in result["strategies"]:
            print("  %s (%s context %d) conf %.3f" % (
                s["strategy_id"], s["situation_pattern"],
                len(s["applicable_contexts"]), s["confidence"] or 0))
    return 0


def cmd_lifecycle_trace(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.lifecycle_service import LifecycleService
    svc = LifecycleService(project_id=project_id)
    try:
        result = svc.trace(args.record_id,
                           role=getattr(args, "role", None),
                           max_depth=getattr(args, "max_depth", None) or 16)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    if getattr(args, "json", False):
        print(to_json(result))
    else:
        print("trace for %s (%d node(s)):" % (args.record_id,
                                              result["meta"]["node_count"]))
        for n in result["records"]:
            print("  %s %s [%s]" % (n["role"], n["record_id"],
                                    n["lifecycle_state"]))
    return 0


def cmd_lifecycle_describe(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.lifecycle_service import LifecycleService
    svc = LifecycleService(project_id=project_id)
    try:
        result = svc.describe(args.record_id,
                              role=getattr(args, "role", None))
    except ValueError as e:
        return fail("invalid_argument", str(e))
    if getattr(args, "json", False):
        print(to_json(result))
    else:
        print("%s %s [%s/%s] %s" % (result["role"], result["record_id"],
                                    result["origin"],
                                    result["lifecycle_state"],
                                    result["subject"]))
    return 0


def cmd_lifecycle_summary(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.lifecycle_service import LifecycleService
    svc = LifecycleService(project_id=project_id)
    result = svc.summary(project_id=project_id)
    if getattr(args, "json", False):
        print(to_json(result))
    else:
        print("lifecycle summary for %s:" % project_id)
        print("  total records: %d" % result["total_records"])
        for role, count in sorted(result["by_role"].items()):
            print("  %s: %d" % (role, count))
    return 0


def cmd_context_show(args):
    project_id = getattr(args, "project", None) or "default"
    context_id = args.context_id
    from ai_engine.paths import get_context_db
    from intelligence.context.store import ContextStore
    try:
        store = ContextStore(db_path=get_context_db(project_id))
        snap = store.get(context_id)
        store.close()
    except Exception as e:
        return fail("internal_error", f"context show failed: {e}")
    if snap is None:
        return fail("node_not_found", f"context {context_id!r} not found")
    # Output machine-readable JSON (always) + human
    if getattr(args, "json", False):
        print(to_json(snap.as_dict()))
    else:
        print(to_json(snap.as_dict()))
    return 0

def cmd_context_diff(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.paths import get_context_db
    from intelligence.context.store import ContextStore
    from intelligence.context.diff import context_diff
    try:
        store = ContextStore(db_path=get_context_db(project_id))
        a = store.get(args.ctx_a)
        b = store.get(args.ctx_b)
        store.close()
    except Exception as e:
        return fail("internal_error", f"context diff failed: {e}")
    if a is None or b is None:
        missing = args.ctx_a if a is None else args.ctx_b
        return fail("node_not_found", f"context {missing!r} not found")
    diff = context_diff(a, b)
    if getattr(args, "json", False):
        print(to_json(diff))
    else:
        print(f"similarity {diff['similarity']:.3f} changed {diff['changed_dimensions']}")
        print(to_json(diff["dimensions"]))
    return 0

# -- status ----------------------------------------------------------------
def cmd_status(args):
    project_id = getattr(args, "project", None) or "default"
    from api.memory_api import MemoryAPI
    try:
        api = MemoryAPI()
        info = api.inspect(project_id=project_id)
    except Exception as e:
        return fail("internal_error", f"status failed: {e}")
    if getattr(args, "json", False):
        print(to_json(info))
    else:
        print(f"project {info.get('project_id')}: nodes {info.get('node_count')} contexts {info.get('context_count')} evidence {info.get('evidence_count')} activities {info.get('activity_count')}")
    return 0

# -- backup ----------------------------------------------------------------
def cmd_backup(args):
    project_id = getattr(args, "project", None) or "default"
    output = getattr(args, "output", None)
    from ai_engine.persistence import backup_project
    # Use hardened per-project backup (all 6 DBs, atomic, integrity-checked)
    try:
        res = backup_project(project_id, output=output)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    except Exception as e:
        return fail("internal_error", f"backup failed: {e}")
    if not res.get("ok"):
        eprint_json(res)
        return 1
    # Human-readable
    files = res.get("backup_files", {})
    if output and not os.path.isdir(output):
        print(f"backup to {output}")
    else:
        for db_name, path in files.items():
            print(f"backup {db_name} to {path}")
    return 0

# -- export ----------------------------------------------------------------
def cmd_export(args):
    project_id = getattr(args, "project", None) or "default"
    fmt = getattr(args, "format", "jsonl") or "jsonl"
    output = getattr(args, "output", None)
    from ai_engine.persistence import export_project
    try:
        res = export_project(project_id, output=output, fmt=fmt)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    except Exception as e:
        return fail("internal_error", f"export failed: {e}")
    if not res.get("ok"):
        eprint_json(res)
        return 1
    if res.get("output") and output:
        print(f"exported {res.get('node_count')} nodes to {res.get('output')}")
    elif not output:
        # When output is None, export_project returns nodes without writing; print to stdout
        for n in res.get("nodes", []):
            print(json.dumps(n, ensure_ascii=False, sort_keys=True))
    return 0

# -- import ----------------------------------------------------------------
def cmd_import(args):
    project_id = getattr(args, "project", None) or "default"
    dry_run = getattr(args, "dry_run", False)
    file_path = args.file
    from ai_engine.persistence import import_project
    try:
        res = import_project(file_path, project_id, dry_run=dry_run)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    except Exception as e:
        return fail("internal_error", f"import failed: {e}")
    if not res.get("ok"):
        eprint_json(res)
        return 1
    if dry_run:
        print(f"dry-run ok: {res.get('node_count')} nodes valid")
    else:
        print(f"imported {res.get('imported_count')} nodes to project {project_id!r}")
    return 0

# -- doctor ----------------------------------------------------------------
def cmd_doctor(args):
    project_id = getattr(args, "project", None) or "default"
    repair = getattr(args, "repair", False)
    as_json = getattr(args, "json", False)
    from ai_engine.persistence import doctor_project
    try:
        res = doctor_project(project_id)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    except Exception as e:
        return fail("internal_error", f"doctor failed: {e}")
    if as_json:
        print(to_json(res))
    else:
        # Human-readable
        print(f"project {res['project_id']} data_root {res['data_root']}")
        for db_name, info in res["databases"].items():
            status = "ok" if info.get("integrity") else ("missing" if not info.get("exists") else f"failed: {info.get('integrity_result')}")
            print(f"{db_name}: {status} ({info.get('path')})")
        if res["consistency_problems"]:
            print("problems:")
            for p in res["consistency_problems"]:
                print(f"  - {p}")
        else:
            print("no consistency problems")
        print(f"overall: {'ok' if res['ok'] else 'issues found'}")
    # Also print machine-readable to stderr for --json? For now, human is ok
    if not res["ok"] and not repair:
        return 1
    if repair:
        print("repair not implemented (no auto-repair), checked integrity only")
    return 0 if res["ok"] else 1

# -- migrate ---------------------------------------------------------------
def cmd_migrate(args):
    project_id = getattr(args, "project", None) or "default"
    from ai_engine.migration import migrate_project_databases
    try:
        res = migrate_project_databases(project_id)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    except Exception as e:
        return fail("internal_error", f"migrate failed: {e}")
    if not res.get("ok"):
        eprint_json(res)
        return 1
    print(f"migrated project {project_id!r}: {json.dumps(res['results'], sort_keys=True)}")
    return 0

# -- restore ---------------------------------------------------------------
def cmd_restore(args):
    project_id = getattr(args, "project", None) or "default"
    backup_file = args.backup_file
    from ai_engine.persistence import restore_project
    try:
        res = restore_project(backup_file, project_id)
    except ValueError as e:
        return fail("invalid_argument", str(e))
    except Exception as e:
        return fail("internal_error", f"restore failed: {e}")
    if not res.get("ok"):
        eprint_json(res)
        return 1
    print(f"restored project {project_id!r} from {backup_file!r} to {res.get('restored')}")
    return 0

# -- serve helpers ---------------------------------------------------------
def _get_daemon_paths(data_root=None, pid_file=None, log_file=None):
    from ai_engine.paths import get_data_root
    root = data_root or get_data_root()
    if pid_file:
        pid_path = os.path.abspath(pid_file)
    else:
        pid_path = os.path.join(root, "daemon.pid")
    if log_file:
        log_path = os.path.abspath(log_file)
    else:
        log_path = os.path.join(root, "daemon.log")
    return pid_path, log_path

def _is_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def _read_pid(pid_path):
    try:
        with open(pid_path, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None

def _remove_pid(pid_path):
    try:
        if os.path.exists(pid_path):
            os.remove(pid_path)
    except Exception:
        pass

def cmd_serve(args):
    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    port = getattr(args, "port", 8765) or 8765
    data_root = getattr(args, "data_root", None)
    db_path = getattr(args, "db", None)
    api_key = getattr(args, "api_key", None)
    daemon = getattr(args, "daemon", False)
    stop = getattr(args, "stop", False)
    pid_file = getattr(args, "pid_file", None)
    log_file = getattr(args, "log_file", None)

    # Security: default must remain localhost-only
    if host not in ("127.0.0.1", "localhost"):
        print(f"warning: binding to {host!r} exposes service beyond localhost", file=sys.stderr)

    pid_path, log_path = _get_daemon_paths(data_root, pid_file, log_file)

    if stop:
        pid = _read_pid(pid_path)
        if pid is None:
            print(f"no daemon PID file at {pid_path}", file=sys.stderr)
            return 1
        if not _is_running(pid):
            print(f"daemon PID {pid} not running, removing stale {pid_path}", file=sys.stderr)
            _remove_pid(pid_path)
            return 0
        try:
            os.kill(pid, 15)  # SIGTERM
            # Wait briefly for exit
            for _ in range(20):
                if not _is_running(pid):
                    break
                import time as _time
                _time.sleep(0.2)
            if _is_running(pid):
                os.kill(pid, 9)
            _remove_pid(pid_path)
            print(f"stopped daemon {pid}")
            return 0
        except Exception as e:
            return fail("internal_error", f"failed to stop daemon {pid}: {e}")

    if daemon:
        # Check if already running
        existing = _read_pid(pid_path)
        if existing is not None and _is_running(existing):
            return fail("invalid_argument", f"daemon already running with PID {existing} ({pid_path})")
        # Remove stale pid if any
        _remove_pid(pid_path)
        # Spawn background process
        import subprocess
        # Build command to re-invoke this CLI without --daemon, but with same host/port/data_root
        cmd = [sys.executable, "-m", "ai_engine", "serve", "--host", host, "--port", str(port)]
        if data_root:
            cmd.extend(["--data-root", data_root])
        if db_path:
            cmd.extend(["--db", db_path])
        if api_key:
            cmd.extend(["--api-key", api_key])
        if pid_file:
            cmd.extend(["--pid-file", pid_file])
        if log_file:
            cmd.extend(["--log-file", log_file])
        # Ensure data_root exists for log
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)) or ".", exist_ok=True)
            log_fd = open(log_path, "a")
        except Exception as e:
            return fail("internal_error", f"cannot open log {log_path}: {e}")
        try:
            proc = subprocess.Popen(cmd, stdout=log_fd, stderr=log_fd, close_fds=True, start_new_session=True)
        except Exception as e:
            log_fd.close()
            return fail("internal_error", f"failed to spawn daemon: {e}")
        # Write PID file (child's PID, not proc.pid which is immediate? proc.pid is the daemon's PID)
        try:
            with open(pid_path, "w") as f:
                f.write(str(proc.pid))
        except Exception as e:
            log_fd.close()
            return fail("internal_error", f"failed to write PID {pid_path}: {e}")
        log_fd.close()
        print(f"daemon started PID {proc.pid} log {log_path} pidfile {pid_path}")
        # Give it a moment to bind and check
        import time as _time
        _time.sleep(0.5)
        if not _is_running(proc.pid):
            return fail("internal_error", f"daemon PID {proc.pid} exited quickly, check log {log_path}")
        return 0

    # Foreground: run server directly
    # Ensure data_root exists
    if data_root:
        try:
            os.makedirs(data_root, exist_ok=True)
        except Exception:
            pass
    # Handle port already in use
    from http_server.server import KnowledgeHTTPServer
    try:
        server = KnowledgeHTTPServer((host, port), db_path=db_path or DEFAULT_KNOWLEDGE_DB, api_key=api_key, data_root=data_root)
    except OSError as e:
        return fail("internal_error", f"failed to bind {host}:{port}: {e}")
    print(f"serving on {host}:{port} (v1 /v1/execute, v2 /v2/execute, health /health) — Ctrl-C to stop", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0

# -- legacy v1 commands (for backward compat) ------------------------------
from api.knowledge_api import KnowledgeAPI
from api.errors import KnowledgeError as _KnowledgeError

def _run_legacy(args, operation, *op_args, present=None, **op_kwargs):
    db = getattr(args, "db", None) or DEFAULT_KNOWLEDGE_DB
    api = KnowledgeAPI(db_path=db)
    try:
        result = getattr(api, operation)(*op_args, **op_kwargs)
    except _KnowledgeError as e:
        api.close()
        print(to_json(e.as_dict()))
        print(f"error: {e}", file=sys.stderr)
        return 1
    api.close()
    payload = result if present is None else present(result)
    print(to_json(payload))
    return 0

def _search_limit(args):
    if args.limit is None:
        return 20
    if args.limit < 0:
        return args.limit
    return min(args.limit, 100)

def cmd_search_legacy(args):
    # reuse compact present
    def search_summary(node):
        summary = {"id": node.get("id"), "type": node.get("type")}
        name = node.get("name")
        if name:
            summary["name"] = name
        desc = (node.get("description") or "").strip()
        if desc:
            preview = desc[:160]
            if len(desc) > 160:
                preview += "..."
            summary["summary"] = preview
        score = node.get("_score")
        if score is not None:
            summary["score"] = score
        return summary
    present = lambda results: [search_summary(n) for n in results]
    return _run_legacy(args, "search", args.query, node_type=args.type, limit=_search_limit(args), present=present)

def cmd_get_legacy(args): return _run_legacy(args, "get", args.node_id)
def cmd_related_legacy(args): return _run_legacy(args, "related", args.node_id, limit=args.limit)
def cmd_follow_legacy(args): return _run_legacy(args, "follow", args.node_id, args.rel_type)
def cmd_provenance_legacy(args): return _run_legacy(args, "provenance", args.node_id)
def cmd_inspect_legacy(args): return _run_legacy(args, "inspect")

# -- main ------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(prog="ai-engine", description="Generic Persistent Intelligence System CLI (Phase 9)")
    parser.add_argument("--version", action="store_true", help="show version and exit")
    parser.add_argument("--verbose", action="store_true", help="verbose output (unused, for compat)")
    sub = parser.add_subparsers(dest="command")

    # init
    p = sub.add_parser("init", help="initialize project")
    p.add_argument("--project", dest="project", default=None, help="project id (default default)")
    p.add_argument("--vocabulary", dest="vocabulary", default=None, help="vocabulary id (default diary_v1)")
    p.set_defaults(func=cmd_init)

    # remember
    p = sub.add_parser("remember", help="remember a fact (generic v2 payload)")
    p.add_argument("text", help="text to remember")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.add_argument("--source", dest="source", default=None, help="source adapter id")
    p.add_argument("--json", dest="json", action="store_true", help="machine-readable JSON output")
    p.set_defaults(func=cmd_remember)

    # recall
    p = sub.add_parser("recall", help="recall memories")
    p.add_argument("query", help="search query")
    p.add_argument("--limit", dest="limit", type=int, default=None, help="limit (default 20, cap 100)")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.add_argument("--json", dest="json", action="store_true", help="machine-readable JSON output")
    p.set_defaults(func=cmd_recall)

    # capture
    cap = sub.add_parser("capture", help="capture via adapter")
    cap_sub = cap.add_subparsers(dest="capture_cmd", required=True)
    p2 = cap_sub.add_parser("manual", help="manual capture")
    p2.add_argument("--text", dest="text", required=True, help="text to capture")
    p2.add_argument("--project", dest="project", default=None, help="project id")
    p2.add_argument("--json", dest="json", action="store_true", help="JSON output")
    p2.set_defaults(func=cmd_capture)

    # context
    ctx = sub.add_parser("context", help="context commands")
    ctx_sub = ctx.add_subparsers(dest="ctx_cmd", required=True)
    p3 = ctx_sub.add_parser("show", help="show context")
    p3.add_argument("context_id", help="context id")
    p3.add_argument("--project", dest="project", default=None, help="project id")
    p3.add_argument("--json", dest="json", action="store_true", help="JSON output")
    p3.set_defaults(func=cmd_context_show)
    p4 = ctx_sub.add_parser("diff", help="diff two contexts")
    p4.add_argument("ctx_a", help="context a")
    p4.add_argument("ctx_b", help="context b")
    p4.add_argument("--project", dest="project", default=None, help="project id")
    p4.add_argument("--json", dest="json", action="store_true", help="JSON output")
    p4.set_defaults(func=cmd_context_diff)

    # lifecycle (Phase 26)
    lc = sub.add_parser("lifecycle", help="lifecycle commands")
    lc_sub = lc.add_subparsers(dest="lifecycle_cmd", required=True)
    lc1 = lc_sub.add_parser("ingest", help="ingest info (user fact / external knowledge)")
    lc1.add_argument("content", help="content to ingest")
    lc1.add_argument("--origin", dest="origin", default=None, help="origin (external -> advisory knowledge; else user fact)")
    lc1.add_argument("--source", dest="source", default=None, help="source string")
    lc1.add_argument("--uri", dest="uri", default=None, help="external uri")
    lc1.add_argument("--actor", dest="actor", default=None, help="actor")
    lc1.add_argument("--project", dest="project", default=None, help="project id")
    lc1.add_argument("--json", dest="json", action="store_true", help="JSON output")
    lc1.set_defaults(func=cmd_lifecycle_ingest)
    lc2 = lc_sub.add_parser("experience", help="record a lifecycle experience")
    lc2.add_argument("situation", help="situation problem")
    lc2.add_argument("attempt", help="attempt / approach")
    lc2.add_argument("result", help="result (or --outcome-classification)")
    lc2.add_argument("--outcome-classification", dest="outcome_classification", default=None, help="success/failure/partial/blocked")
    lc2.add_argument("--context-id", dest="context_id", default=None, help="context id")
    lc2.add_argument("--evidence-ids", dest="evidence_ids", default=None, help="comma-separated evidence ids")
    lc2.add_argument("--task-type", dest="task_type", default=None, help="task type")
    lc2.add_argument("--task-id", dest="task_id", default=None, help="task id")
    lc2.add_argument("--domain", dest="domain", default=None, help="domain")
    lc2.add_argument("--actor", dest="actor", default=None, help="actor")
    lc2.add_argument("--source", dest="source", default=None, help="source string")
    lc2.add_argument("--project", dest="project", default=None, help="project id")
    lc2.add_argument("--json", dest="json", action="store_true", help="JSON output")
    lc2.set_defaults(func=cmd_lifecycle_experience)
    lc3 = lc_sub.add_parser("learning", help="derive learning from experiences")
    lc3.add_argument("experience_ids", help="comma-separated experience ids")
    lc3.add_argument("--project", dest="project", default=None, help="project id")
    lc3.add_argument("--json", dest="json", action="store_true", help="JSON output")
    lc3.set_defaults(func=cmd_lifecycle_learning)
    lc4 = lc_sub.add_parser("strategy", help="derive strategies from experiences")
    lc4.add_argument("experience_ids", help="comma-separated experience ids")
    lc4.add_argument("--min-samples", dest="min_samples", type=int, default=None, help="minimum evidence samples")
    lc4.add_argument("--project", dest="project", default=None, help="project id")
    lc4.add_argument("--json", dest="json", action="store_true", help="JSON output")
    lc4.set_defaults(func=cmd_lifecycle_strategy)
    lc5 = lc_sub.add_parser("trace", help="provenance trace")
    lc5.add_argument("record_id", help="record id to trace")
    lc5.add_argument("--role", dest="role", default=None, help="record role")
    lc5.add_argument("--max-depth", dest="max_depth", type=int, default=None, help="max depth")
    lc5.add_argument("--project", dest="project", default=None, help="project id")
    lc5.add_argument("--json", dest="json", action="store_true", help="JSON output")
    lc5.set_defaults(func=cmd_lifecycle_trace)
    lc6 = lc_sub.add_parser("describe", help="describe a lifecycle record")
    lc6.add_argument("record_id", help="record id")
    lc6.add_argument("--role", dest="role", default=None, help="record role")
    lc6.add_argument("--project", dest="project", default=None, help="project id")
    lc6.add_argument("--json", dest="json", action="store_true", help="JSON output")
    lc6.set_defaults(func=cmd_lifecycle_describe)
    lc7 = lc_sub.add_parser("summary", help="lifecycle summary")
    lc7.add_argument("--project", dest="project", default=None, help="project id")
    lc7.add_argument("--json", dest="json", action="store_true", help="JSON output")
    lc7.set_defaults(func=cmd_lifecycle_summary)

    # status
    p = sub.add_parser("status", help="project status")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.add_argument("--json", dest="json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_status)

    # backup
    p = sub.add_parser("backup", help="backup project DB")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.add_argument("--output", dest="output", default=None, help="output file")
    p.set_defaults(func=cmd_backup)

    # export
    p = sub.add_parser("export", help="export nodes to jsonl")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.add_argument("--format", dest="format", default="jsonl", help="format (only jsonl)")
    p.add_argument("--output", dest="output", default=None, help="output file")
    p.set_defaults(func=cmd_export)

    # import
    p = sub.add_parser("import", help="import jsonl")
    p.add_argument("file", help="jsonl file")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="validate only")
    p.set_defaults(func=cmd_import)

    # doctor
    p = sub.add_parser("doctor", help="check integrity")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.add_argument("--repair", dest="repair", action="store_true", help="repair (no-op)")
    p.add_argument("--json", dest="json", action="store_true", help="JSON output (compat)")
    p.set_defaults(func=cmd_doctor)

    # serve
    p = sub.add_parser("serve", help="run local daemon (Memory v2)")
    p.add_argument("--host", dest="host", default="127.0.0.1", help="bind host (default 127.0.0.1, localhost only)")
    p.add_argument("--port", dest="port", type=int, default=8765, help="port (default 8765)")
    p.add_argument("--data-root", dest="data_root", default=None, help="data root override")
    p.add_argument("--db", dest="db", default=None, help="legacy db path for v1 (optional)")
    p.add_argument("--api-key", dest="api_key", default=None, help="API key for auth (optional)")
    p.add_argument("--daemon", dest="daemon", action="store_true", help="run in background (daemon)")
    p.add_argument("--stop", dest="stop", action="store_true", help="stop running daemon")
    p.add_argument("--pid-file", dest="pid_file", default=None, help="PID file path")
    p.add_argument("--log-file", dest="log_file", default=None, help="log file path")
    p.set_defaults(func=cmd_serve)

    # migrate
    p = sub.add_parser("migrate", help="migrate legacy DBs to per-project")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.set_defaults(func=cmd_migrate)

    # restore
    p = sub.add_parser("restore", help="restore project from backup file")
    p.add_argument("backup_file", help="backup file path")
    p.add_argument("--project", dest="project", default=None, help="project id")
    p.set_defaults(func=cmd_restore)

    # legacy v1 (hidden, for backward compat)
    p = sub.add_parser("search", help=argparse.SUPPRESS)
    p.add_argument("query", help="search query")
    p.add_argument("--type", dest="type", default=None)
    p.add_argument("--limit", dest="limit", type=int, default=None)
    p.add_argument("--verbose", dest="verbose", action="store_true")
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB)
    p.set_defaults(func=cmd_search_legacy)
    for name, func in (("get", cmd_get_legacy), ("provenance", cmd_provenance_legacy)):
        pp = sub.add_parser(name, help=argparse.SUPPRESS)
        pp.add_argument("node_id")
        pp.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB)
        pp.set_defaults(func=func)
    p = sub.add_parser("related", help=argparse.SUPPRESS)
    p.add_argument("node_id"); p.add_argument("--limit", dest="limit", type=int, default=None); p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB); p.set_defaults(func=cmd_related_legacy)
    p = sub.add_parser("follow", help=argparse.SUPPRESS)
    p.add_argument("node_id"); p.add_argument("--type", dest="rel_type", default=None); p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB); p.set_defaults(func=cmd_follow_legacy)
    p = sub.add_parser("inspect", help=argparse.SUPPRESS)
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB); p.set_defaults(func=cmd_inspect_legacy)

    args = parser.parse_args(argv)
    if getattr(args, "version", False):
        print(__version__)
        return 0
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
