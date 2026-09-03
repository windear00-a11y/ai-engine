"""Experience type definitions (Phase 3).

Experience is the *interpreted* summary of what happened, what strategy was
used, in what context, with what outcome, and with what evidence. It is NOT
the raw execution log.

Domains and task types are open-ended (anchored by the execution system), so
they are represented as strings rather than a closed enum. This module
provides the canonical strings used across the intelligence layer.
"""

DOMAIN_EXECUTION = "execution"
DOMAIN_KNOWLEDGE = "knowledge"
DOMAIN_CODING = "coding"
DOMAIN_UNKNOWN = "unknown"

# Task-type categories commonly recorded.
TASK_BUG_FIX = "bug_fix"
TASK_REFACTOR = "refactor"
TASK_FEATURE = "feature"
TASK_VERIFICATION = "verification"
TASK_UNKNOWN = "unknown"
