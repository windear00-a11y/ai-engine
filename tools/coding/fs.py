"""Filesystem safety and path-validation helpers for the Coding Tools Layer.

All coding tools must resolve paths through a :class:`Workspace` so that
paths can never escape the configured root. This is the single place where
path-trust decisions are made; individual tools never call ``os.path``
joining on raw user input directly.
"""

import os


class PathError(Exception):
    """Raised when a path violates the workspace boundary or is invalid."""


class Workspace:
    """An explicit, enforced filesystem root.

    ``resolve`` turns a (possibly relative or malicious) path into an absolute
    path that is guaranteed to live inside ``root``. Anything that would escape
    (``../``, absolute paths outside root, symlink escapes) raises
    :class:`PathError`.
    """

    def __init__(self, root):
        root = os.path.abspath(os.path.realpath(str(root)))
        if not os.path.isdir(root):
            raise PathError(f"workspace root is not a directory: {root}")
        self.root = root

    def resolve(self, path):
        """Return an absolute, in-root path for ``path`` or raise PathError."""
        if path is None:
            raise PathError("path must not be None")
        raw = str(path)
        if os.path.isabs(raw):
            candidate = os.path.realpath(raw)
        else:
            candidate = os.path.realpath(os.path.join(self.root, raw))
        try:
            common = os.path.commonpath([self.root, candidate])
        except ValueError:
            raise PathError(f"path escapes workspace root: {raw}")
        if common != self.root:
            raise PathError(f"path escapes workspace root: {raw}")
        return candidate

    def rel(self, abs_path):
        """Return ``abs_path`` relative to the workspace root."""
        return os.path.relpath(abs_path, self.root)


def is_binary(path, chunk=8192):
    """Heuristic: True if ``path`` looks like a binary (non-text) file."""
    try:
        with open(path, "rb") as f:
            data = f.read(chunk)
    except OSError:
        return False
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
