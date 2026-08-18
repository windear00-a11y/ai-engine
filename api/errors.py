"""Knowledge API error types.

Structured, catchable errors raised by the public Knowledge API. Each error
carries a stable machine-readable ``code`` and a ``message`` so clients (the
CLI, a future TUI/web UI, agents) can render them deterministically.
"""


class KnowledgeError(Exception):
    """Base class for all Knowledge API errors."""

    code = "knowledge_error"

    def as_dict(self):
        return {"error": self.code, "message": str(self)}


class NodeNotFoundError(KnowledgeError):
    """Raised when a node id does not exist in the knowledge database."""

    code = "node_not_found"

    def __init__(self, node_id):
        self.node_id = node_id
        super().__init__("node not found: %r" % node_id)

    def as_dict(self):
        return {"error": self.code, "node_id": self.node_id,
                "message": str(self)}


class KnowledgeArgumentError(KnowledgeError):
    """Raised for invalid arguments (bad query, limit, node type, ...)."""

    code = "invalid_argument"

    def __init__(self, message):
        super().__init__(message)

    def as_dict(self):
        return {"error": self.code, "message": str(self)}


class RelationshipTypeError(KnowledgeError):
    """Raised when ``relationship_type`` is not a canonical relationship kind."""

    code = "invalid_relationship_type"

    def __init__(self, relationship_type, valid):
        self.relationship_type = relationship_type
        self.valid = sorted(valid)
        super().__init__(
            "invalid relationship type %r; expected one of %s"
            % (relationship_type, self.valid))

    def as_dict(self):
        return {"error": self.code, "relationship_type": self.relationship_type,
                "valid": self.valid, "message": str(self)}