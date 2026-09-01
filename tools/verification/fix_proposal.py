"""FixProposal model — Layer 5 5B-1a.

Proposal-only data, no execution, no file writes, no DB mutation.
"""

from dataclasses import dataclass
from typing import Optional
import hashlib

from tools.indexer.types import stable_id


@dataclass
class FixProposal:
    id: str
    rule_id: str
    diagnostic_id: str
    file: str
    description: str
    precondition: dict
    patch: dict
    diff_preview: str
    certainty: str
    risk: str
    expected_verification: str
    ordering_key: tuple

    def as_dict(self):
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "diagnostic_id": self.diagnostic_id,
            "file": self.file,
            "description": self.description,
            "precondition": self.precondition,
            "patch": self.patch,
            "diff_preview": self.diff_preview,
            "certainty": self.certainty,
            "risk": self.risk,
            "expected_verification": self.expected_verification,
            "ordering_key": self.ordering_key,
        }


def make_fix_proposal(rule_id, diagnostic_id, file, line, column, old_text, new_text, diff_preview, expected_hash, certainty="fact", risk="low", description=None, expected_verification=None):
    # Deterministic id: stable hash of rule + diagnostic + file + line/col + old->new hash
    # Use expected_hash as part of identity so same diagnostic + same source state => identical id
    content_hash = hashlib.sha256((old_text + "->" + new_text).encode("utf-8")).hexdigest()[:12]
    did = stable_id("fix", rule_id, diagnostic_id, file, str(line), str(column) if column is not None else "", content_hash, expected_hash)
    desc = description or f"{rule_id} for {file}:{line}"
    exp_ver = expected_verification or f"lint at {file}:{line} should not emit {rule_id} after apply"
    ordering = (file, line, rule_id, did)
    # Cap diff_preview to 2000 for determinism
    diff_capped = diff_preview[:2000] if diff_preview is not None else ""
    return FixProposal(
        id=did,
        rule_id=rule_id,
        diagnostic_id=diagnostic_id,
        file=file,
        description=desc,
        precondition={"file": file, "line": line, "column": column, "expected_hash": expected_hash},
        patch={"old_text": old_text, "new_text": new_text, "line": line, "column": column},
        diff_preview=diff_capped,
        certainty=certainty,
        risk=risk,
        expected_verification=exp_ver,
        ordering_key=ordering,
    )
