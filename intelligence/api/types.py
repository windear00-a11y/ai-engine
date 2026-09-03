"""Intelligence API types (Phase 10)."""

from enum import Enum


class IntelligenceErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    UNKNOWN_OPERATION = "unknown_operation"
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    INTERNAL_ERROR = "internal_error"


class IntelligenceAPIError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
