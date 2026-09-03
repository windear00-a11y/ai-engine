"""Experience Engine (Phase 3).

Synthesizes execution journal records into reusable, interpreted experience
records that link a task to its context, outcome, and supporting evidence.
Experience is NOT the raw execution log.
"""

from intelligence.experience.extractor import (
    extract_experiences_from_completed_tasks,
    synthesize_experience,
)
from intelligence.experience.retriever import (
    experience_for_strategy,
    experience_for_task_type,
    get_experience,
)
from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.experience.store import ExperienceStore, default_experience_db_path

__all__ = [
    "synthesize_experience",
    "extract_experiences_from_completed_tasks",
    "get_experience",
    "experience_for_task_type",
    "experience_for_strategy",
    "ExperienceRecord",
    "ExperienceStore",
    "derive_experience_id",
    "default_experience_db_path",
]
