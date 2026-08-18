"""Knowledge Compiler adapter interface.

A *source adapter* knows how to turn one kind of raw source document into the
adapter-agnostic :class:`~knowledge_compiler.types.Document` representation.
The rest of the pipeline (scanner, extractor, validator, output) never touches
the raw format -- it only consumes the intermediate representation. This is what
keeps the pipeline generic and independent of Python/RST (or any future format).
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from knowledge_compiler.types import Document


class SourceAdapter(ABC):
    """Interface every source adapter must implement."""

    name = "abstract"
    extensions = ()

    @abstractmethod
    def supports(self, path):
        """Return True if this adapter can parse the given file path."""

    @abstractmethod
    def parse(self, path, rel_path, source_name) -> "Document":
        """Parse ``path`` into a :class:`~knowledge_compiler.types.Document`.

        Must never raise for a malformed file: decode/parse issues are recorded
        in ``document.errors`` and the document is still returned in EXTRACTED
        state with whatever chunks could be recovered.
        """