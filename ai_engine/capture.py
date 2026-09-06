"""Generic Capture / Ingestion Foundation (Phase 3).

Provides domain-neutral pipeline:
    Raw input -> normalization -> vocabulary/type validation -> provenance
    -> deterministic identity / dedup -> persistence

- CaptureAdapter abstraction (extensible, no registry yet)
- ManualCaptureAdapter (generic, not code-specific)
- run_capture() pipeline that reuses existing ingestion/validator and
  repository infrastructure (no duplication).

All steps are deterministic, stdlib-only, per-project isolated.
Activity remains in activity.db (never engine_state.db).

No file watchers, no OCR, no embeddings, no LLM, no cloud.
"""

import abc
import hashlib
import json
import os
import time

from ai_engine.activity import ActivityStore, derive_activity_id
from ai_engine.paths import get_activity_db, get_context_db, get_evidence_db, get_knowledge_db
from ai_engine.vocabulary import Vocabulary

from intelligence.context.schema import ContextSnapshot
from intelligence.evidence.schema import EvidenceRecord, derive_evidence_id
from intelligence.evidence.types import EvidenceType


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def _sha12(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

def _stable_node_id(node_type, content):
    base = content.strip().lower() if isinstance(content, str) else _canonical(content)
    return f"{node_type.strip().lower()}_{_sha12(base)}"

def _truncate(s, n=80):
    s = s.strip()
    return s[:n] + ("..." if len(s) > n else "")


class CaptureAdapter(abc.ABC):
    """Abstract capture adapter — domain-neutral.

    Implementations must be deterministic and stdlib-only.
    No registry in Phase 3; adapters are instantiated directly.
    """

    @property
    @abc.abstractmethod
    def adapter_id(self) -> str:
        """Stable adapter identifier, e.g. 'manual'."""
        raise NotImplementedError

    @abc.abstractmethod
    def normalize(self, raw, context_hints=None):
        """Normalize raw input into canonical dict.

        Returns dict with at least:
            {"text": str, "type": str, "name": str, "description": str, "relationships": list}
        May include extra fields that become node metadata.

        Must not touch DB or network. Deterministic.
        """
        raise NotImplementedError


class ManualCaptureAdapter(CaptureAdapter):
    """Generic manual capture adapter — not code-specific.

    Accepts:
        - str -> {"text": str}
        - dict with "text" or "description" or "content"
          plus optional "type"/"name"/"relationships"

    Vocabulary is injected to choose default type generically (first sorted type
    if no explicit type), not hardcoded to "fact" unless vocab contains it.
    """

    @property
    def adapter_id(self):
        return "manual"

    def normalize(self, raw, context_hints=None):
        # Generic payload handling: raw can be str or dict with arbitrary structured memory payload.
        # Text is not mandatory; structured payloads like {"custom":42} are valid.
        # Empty-text invariant preserved: "   " and {"text":"   "} with no other meaningful fields remain ValueError.
        if isinstance(raw, str):
            content_text = raw.strip()
            if not content_text:
                raise ValueError("content text must be non-empty")
            payload = {"text": content_text}
        elif isinstance(raw, dict):
            payload = dict(raw)
            if not payload:
                raise ValueError("payload must be non-empty JSON object")
            # Derive content_text: prefer explicit text/description/content if non-empty
            candidate = ""
            for key in ("text", "description", "content"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    candidate = val.strip()
                    break
            if candidate:
                content_text = candidate
            else:
                # No explicit text — check for other meaningful fields
                # For empty text to be valid, there must be either a meaningful extra custom field
                # or a meaningful name/description (not just type/id which alone don't imply content)
                extras = {k: v for k, v in payload.items() if k not in ("text", "type", "name", "description", "relationships", "content", "id")}
                has_meaningful_extra = False
                for v in extras.values():
                    if isinstance(v, str):
                        if v.strip():
                            has_meaningful_extra = True
                            break
                    elif v is not None and v != "" and v != [] and v != {}:
                        has_meaningful_extra = True
                        break
                has_name_desc_content = False
                for k in ("name", "description", "content"):
                    v = payload.get(k)
                    if isinstance(v, str) and v.strip():
                        has_name_desc_content = True
                        break
                if not has_meaningful_extra and not has_name_desc_content:
                    raise ValueError("content text must be non-empty")
                # For structured payload, derive content_text from canonical JSON for hashing/naming
                try:
                    content_text = _canonical(payload)
                except Exception:
                    content_text = str(payload)
                if not content_text.strip():
                    raise ValueError("payload must be non-empty JSON object")
        else:
            raise ValueError("raw must be str or dict")

        # Determine type: explicit -> defer to pipeline, else None
        raw_type = payload.get("type")
        if isinstance(raw_type, str) and raw_type.strip():
            nt = raw_type.strip().lower()
        else:
            nt = None  # defer default to pipeline

        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            # For structured payload, name from content_text (truncate canonical)
            name = _truncate(content_text, 60)

        description = payload.get("description")
        if not isinstance(description, str) or not description.strip():
            # For structured, description is content_text (or canonical)
            description = content_text

        relationships = payload.get("relationships")
        if relationships is None:
            relationships = []
        if not isinstance(relationships, list):
            raise ValueError("relationships must be a list")

        # Preserve extra fields (excluding known) — entire generic payload extras are preserved
        extras = {k: v for k, v in payload.items() if k not in ("text", "type", "name", "description", "relationships", "content", "id")}

        out = {
            "text": content_text,
            "type": nt,
            "name": name.strip(),
            "description": description.strip(),
            "relationships": relationships,
            "extras": extras,
            "_raw_payload": payload,  # preserve original for provenance
        }
        if "id" in payload and isinstance(payload["id"], str) and payload["id"].strip():
            out["id"] = payload["id"].strip()
        return out


def _default_type_for_vocab(vocabulary):
    if vocabulary is not None and isinstance(vocabulary, Vocabulary):
        # Prefer "fact" if present (generic), else first sorted type
        if "fact" in vocabulary.types:
            return "fact"
        return sorted(vocabulary.types)[0]
    return "fact"


def run_capture(raw, project_id, data_root=None, vocabulary=None, vocabulary_id=None,
                adapter=None, activity_type="manual", source=None, context_hints=None,
                node_id=None, registry=None):
    """Generic capture pipeline.

    Steps:
        1. Normalize via adapter (ManualCaptureAdapter default)
        2. Vocabulary/type validation (fail closed, code=invalid_argument)
        3. Provenance: derive context_id (deterministic), activity_id, node_id, evidence_id
        4. Dedup: check activity and node existence
        5. Persistence: context (append-only), activity (append-only, not engine_state), knowledge (import_source), evidence

    Args:
        raw: str | dict — raw input
        project_id: str
        data_root: optional override
        vocabulary: Vocabulary instance or None (if None and vocabulary_id given, load)
        vocabulary_id: str id to load if vocabulary not supplied
        adapter: CaptureAdapter instance (default ManualCaptureAdapter)
        activity_type: str
        source: str (adapter_id, default adapter.adapter_id)
        context_hints: optional dict merged into context.task
        node_id: optional explicit node id

    Returns dict {ok: bool, activity_id, context_id, evidence_id, node_ids, ...} or {ok: False, code, error}
    Reuses ingestion.validator and retrieval.repository (no duplication).
    """
    # Validate project_id (fail closed, no path escape)
    try:
        from ai_engine.paths import _validate_project_id
        _validate_project_id(project_id)
    except ValueError as e:
        return {"ok": False, "code": "invalid_argument", "error": str(e)}

    # Resolve vocabulary
    if vocabulary is None and vocabulary_id is not None:
        try:
            vocabulary = Vocabulary.load(vocabulary_id)
        except Exception as e:
            return {"ok": False, "code": "invalid_argument", "error": f"vocabulary load failed: {e}"}
    # Adapter — optionally resolve via registry (additive, no hardcode)
    if registry is not None:
        # If adapter is a string id, resolve via registry
        if isinstance(adapter, str):
            try:
                resolved = registry.get_capture(adapter)
                if resolved is None:
                    return {"ok": False, "code": "invalid_argument", "error": f"unknown capture adapter {adapter!r}"}
                adapter = resolved
            except ValueError as e:
                return {"ok": False, "code": "invalid_argument", "error": str(e)}
        elif adapter is None:
            # Try default registry's manual
            try:
                default = registry.get_capture("manual")
                if default is not None:
                    adapter = default
            except Exception:
                pass
    if adapter is None:
        adapter = ManualCaptureAdapter()
    if isinstance(adapter, str):
        # String without registry -> fail closed
        return {"ok": False, "code": "invalid_argument", "error": f"unknown capture adapter {adapter!r} (no registry)"}
    if not isinstance(adapter, CaptureAdapter):
        return {"ok": False, "code": "invalid_argument", "error": "adapter must be CaptureAdapter"}
    src = source or adapter.adapter_id
    if not isinstance(src, str) or not src.strip():
        return {"ok": False, "code": "invalid_argument", "error": "source must be non-empty string"}
    src = src.strip()

    # Ensure per-project isolation dirs exist (idempotent, no duplication)
    try:
        from ai_engine.paths import ensure_default_project, get_project_entry, create_project, get_project_dir, get_snapshots_dir, get_backups_dir
        ensure_default_project(data_root)
        if project_id != "default" and get_project_entry(project_id, data_root) is None:
            try:
                create_project(project_id, data_root=data_root, vocabulary_id=(vocabulary.id if vocabulary else vocabulary_id or "diary_v1"))
            except ValueError:
                pass
        # Ensure project directory and subdirs exist for DBs
        proj_dir = get_project_dir(project_id, data_root)
        os.makedirs(proj_dir, exist_ok=True)
        os.makedirs(get_snapshots_dir(project_id, data_root), exist_ok=True)
        os.makedirs(get_backups_dir(project_id, data_root), exist_ok=True)
    except Exception as e:
        return {"ok": False, "code": "internal_error", "error": f"project init failed: {e}"}

    # 1. Normalize
    try:
        canonical = adapter.normalize(raw, context_hints=context_hints)
    except ValueError as e:
        return {"ok": False, "code": "invalid_argument", "error": str(e)}
    except Exception as e:
        return {"ok": False, "code": "internal_error", "error": f"normalize failed: {e}"}

    content_text = canonical.get("text", "")
    nt = canonical.get("type")
    if not nt:
        nt = _default_type_for_vocab(vocabulary)
        canonical["type"] = nt
    else:
        nt = nt.strip().lower()
        canonical["type"] = nt

    # 2. Vocabulary/type validation (fail closed)
    if vocabulary is not None and not vocabulary.is_valid_type(nt):
        return {"ok": False, "code": "invalid_argument", "error": f"node type {nt!r} not in vocabulary {vocabulary.id!r}"}
    # Relationship kinds validation
    for idx, rel in enumerate(canonical.get("relationships", [])):
        if not isinstance(rel, dict):
            return {"ok": False, "code": "invalid_argument", "error": f"relationship {idx} must be object"}
        rt = rel.get("type")
        if not isinstance(rt, str) or not rt.strip():
            return {"ok": False, "code": "invalid_argument", "error": f"relationship {idx} type must be non-empty"}
        if vocabulary is not None and not vocabulary.is_valid_relationship_kind(rt.strip()):
            return {"ok": False, "code": "invalid_argument", "error": f"relationship type {rt!r} not in vocabulary {vocabulary.id!r}"}
        tgt = rel.get("target")
        if not isinstance(tgt, str) or not tgt.strip():
            return {"ok": False, "code": "invalid_argument", "error": f"relationship {idx} target must be non-empty"}

    nm = canonical.get("name")
    desc = canonical.get("description")
    relationships = canonical.get("relationships", [])

    # 3. Provenance: generic context (8 dimensions, temporal excluded from id)
    now = time.time()
    environment = {"os": os.name, "device": "local"}
    project = {"project_id": project_id, "vocabulary_id": vocabulary.id if vocabulary else (vocabulary_id or "diary_v1")}
    source = {"adapter": src, "activity_type": activity_type, "payload_hash": _sha12(content_text)}
    actor = {}
    spatial = {}
    social = {}
    affective = {}
    temporal = {"captured_at_epoch": now}
    # Controlled context_hints merging (no uncontrolled dump)
    if context_hints is not None:
        if not isinstance(context_hints, dict):
            return {"ok": False, "code": "invalid_argument", "error": "context_hints must be a dict"}
        # Allowed generic keys (strict). adapter/activity_type are
        # provenance-authority fields and are intentionally not overridable
        # from context_hints (rejected rather than silently ignored).
        allowed = {"actor", "spatial", "social", "affective", "environment", "project", "source", "temporal", "user_id", "location", "with", "mood", "uri"}
        for k in context_hints.keys():
            if k not in allowed:
                return {"ok": False, "code": "invalid_argument", "error": f"context_hints key {k!r} not allowed (controlled)"}
            # Validate value type
            v = context_hints[k]
            if k in ("actor", "spatial", "social", "affective", "environment", "project", "source", "temporal"):
                if not isinstance(v, dict):
                    return {"ok": False, "code": "invalid_argument", "error": f"context_hints[{k!r}] must be a dict"}
            elif k in ("user_id", "location", "with", "mood", "uri"):
                if not isinstance(v, str):
                    return {"ok": False, "code": "invalid_argument", "error": f"context_hints[{k!r}] must be a string"}
        # Map flat hints to generic dims.
        # Authority-preserving merge: caller-provided values never override
        # the authoritative provenance fields computed by this pipeline
        # (project_id, vocabulary_id, adapter, activity_type, payload_hash,
        # captured_at_epoch).
        _RESERVED_PROJECT = {"project_id", "vocabulary_id"}
        _RESERVED_SOURCE = {"adapter", "activity_type", "payload_hash"}
        _RESERVED_TEMPORAL = {"captured_at_epoch"}
        if "actor" in context_hints and isinstance(context_hints["actor"], dict):
            actor.update(context_hints["actor"])
        if "user_id" in context_hints:
            actor["user_id"] = context_hints["user_id"]
        if "spatial" in context_hints and isinstance(context_hints["spatial"], dict):
            spatial.update(context_hints["spatial"])
        if "location" in context_hints:
            spatial["location"] = context_hints["location"]
        if "social" in context_hints and isinstance(context_hints["social"], dict):
            social.update(context_hints["social"])
        if "with" in context_hints:
            social["with"] = context_hints["with"]
        if "affective" in context_hints and isinstance(context_hints["affective"], dict):
            affective.update(context_hints["affective"])
        if "mood" in context_hints:
            affective["mood"] = context_hints["mood"]
        if "environment" in context_hints and isinstance(context_hints["environment"], dict):
            environment.update(context_hints["environment"])
        if "project" in context_hints and isinstance(context_hints["project"], dict):
            project.update({k: v for k, v in context_hints["project"].items() if k not in _RESERVED_PROJECT})
        if "source" in context_hints and isinstance(context_hints["source"], dict):
            source.update({k: v for k, v in context_hints["source"].items() if k not in _RESERVED_SOURCE})
        if "uri" in context_hints:
            source["uri"] = context_hints["uri"]
        # Temporal override (rare); captured_at_epoch stays authoritative
        if "temporal" in context_hints and isinstance(context_hints["temporal"], dict):
            temporal.update({k: v for k, v in context_hints["temporal"].items() if k not in _RESERVED_TEMPORAL})

    snapshot = ContextSnapshot.build(
        environment=environment,
        project=project,
        source=source,
        actor=actor,
        spatial=spatial,
        social=social,
        affective=affective,
        temporal=temporal,
        captured_at_epoch=now,
    )
    context_id = snapshot.context_id
    # Persist context per-project (reuse ContextStore, per-project db)
    try:
        from intelligence.context.store import ContextStore
        from ai_engine.paths import get_context_db
        cstore = ContextStore(db_path=get_context_db(project_id, data_root))
        cstore.save(snapshot)
        cstore.close()
    except Exception as e:
        return {"ok": False, "code": "internal_error", "error": f"context persist failed: {e}"}

    # 4. Deterministic identities + dedup
    payload_for_activity = {"text": content_text, "type": nt, "name": nm, "description": desc, "relationships": relationships}
    activity_id = derive_activity_id(project_id, activity_type, src, payload_for_activity, context_id)

    # Node id deterministic
    nid = node_id or canonical.get("id") or _stable_node_id(nt, content_text)

    # Build envelope for validator (reuse ingestion infrastructure)
    envelope = {
        "source": {"name": src, "version": "1.0", "location": f"{src}://{activity_id}"},
        "nodes": [
            {
                "id": nid,
                "type": nt,
                "name": nm,
                "description": desc,
                "relationships": relationships,
                "_context_id": context_id,
                "_activity_id": activity_id,
            }
        ],
    }
    # Include extras
    for k, v in canonical.get("extras", {}).items():
        envelope["nodes"][0][k] = v

    # 2b. Structural validation via ingestion.validator (no duplication)
    try:
        from ingestion.validator import validate_source
        res = validate_source(envelope)
        if not res.valid:
            return {"ok": False, "code": "invalid_argument",
                    "error": "; ".join(e.message for e in res.errors),
                    "validation_errors": [e.as_dict() for e in res.errors]}
    except Exception as e:
        return {"ok": False, "code": "internal_error", "error": f"validation failed: {e}"}

    # 5. Persistence: activity (append-only, separate from engine_state)
    try:
        astore = ActivityStore(project_id, data_root)
        a_res = astore.save(activity_id, activity_type, src, payload_for_activity, context_id, created_at_epoch=now)
        if not a_res.get("ok"):
            return {"ok": False, "code": "internal_error", "error": a_res.get("error")}
    except Exception as e:
        return {"ok": False, "code": "internal_error", "error": f"activity persist failed: {e}"}

    # Knowledge: per-project knowledge.db via repository (reuse, no duplication)
    try:
        from retrieval.repository import KnowledgeRepository
        from ai_engine.paths import get_knowledge_db
        repo = KnowledgeRepository(get_knowledge_db(project_id, data_root))
        repo.initialize()
        existing = repo.get_node(nid)
        if existing is not None:
            if existing.get("name") == nm and existing.get("description") == desc and existing.get("type") == nt:
                pass  # idempotent
            else:
                repo.close()
                return {"ok": False, "code": "invalid_argument", "error": f"node id {nid!r} exists with different content"}
        else:
            repo.import_source(envelope["source"]["name"], envelope["source"]["location"], res.nodes, source_version=envelope["source"]["version"])
        repo.close()
    except Exception as e:
        err = str(e)
        if "UNIQUE" in err or "duplicate" in err.lower():
            return {"ok": False, "code": "invalid_argument", "error": f"duplicate node id {nid!r}: {err}"}
        return {"ok": False, "code": "internal_error", "error": f"knowledge persist failed: {e}"}

    # Evidence: per-project evidence.db (provenance)
    try:
        claim = f"remembered {nid} in {context_id}"
        supporting = {"activity_id": activity_id, "project_id": project_id, "node_id": nid, "vocabulary_id": vocabulary.id if vocabulary else None, "payload_hash": _sha12(content_text), "source": src}
        evidence_id = derive_evidence_id(f"obs_{activity_id}", claim, context_id, EvidenceType.FACT, supporting)
        rec = EvidenceRecord(
            evidence_id=evidence_id,
            source_observation_id=f"obs_{activity_id}",
            claim=claim,
            context_id=context_id,
            evidence_type=EvidenceType.FACT,
            supporting_data=supporting,
            created_at_epoch=now,
        )
        from intelligence.evidence.store import EvidenceStore
        from ai_engine.paths import get_evidence_db
        estore = EvidenceStore(db_path=get_evidence_db(project_id, data_root))
        estore.save(rec)
        estore.close()
    except Exception as e:
        return {"ok": True, "activity_id": activity_id, "context_id": context_id, "evidence_id": None, "node_ids": [nid], "node_id": nid, "warning": f"evidence persist failed: {e}"}

    return {
        "ok": True,
        "activity_id": activity_id,
        "context_id": context_id,
        "evidence_id": evidence_id,
        "node_ids": [nid],
        "node_id": nid,
    }
