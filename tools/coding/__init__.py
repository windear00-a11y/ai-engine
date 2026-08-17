"""Coding Tools Layer (public API).

Exposes the :class:`CodingTools` facade plus module-level convenience
functions that operate on the default workspace (``workspace/`` under the
repo root). For any real use, construct ``CodingTools(root)`` with an explicit
workspace root so boundaries are unambiguous.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .fs import Workspace, PathError, is_binary
from .tools import (
    CodingTools, FileTools, ProjectTools, CodeTools, WriteTools,
    ExecutionRunner, ProjectVerificationTools,
)

_DEFAULT = None
_DEFAULT_ROOT = os.path.join(_ROOT, "workspace")


def _default_tools():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CodingTools(_DEFAULT_ROOT)
    return _DEFAULT


# Convenience wrappers (``file.list`` / ``project.inspect`` / ``code.analyze``).
def file_list(path=".", depth=1, include_dirs=True, include_files=True):
    return _default_tools().file_list(path, depth, include_dirs, include_files)


def file_read(path, max_size=None):
    return _default_tools().file_read(path, max_size)


def file_search(query, path=".", max_results=200, include_ext=None,
                case_sensitive=False):
    return _default_tools().file_search(query, path, max_results, include_ext,
                                        case_sensitive)


def file_write(path, content, overwrite=True):
    return _default_tools().file_write(path, content, overwrite)


def file_edit(path, old_text, new_text, replace_all=False):
    return _default_tools().file_edit(path, old_text, new_text, replace_all)


def file_mkdir(path, parents=True):
    return _default_tools().file_mkdir(path, parents)


def file_diff(path, proposed_content):
    return _default_tools().file_diff(path, proposed_content)


def project_inspect():
    return _default_tools().project_inspect()


def code_analyze(path):
    return _default_tools().code_analyze(path)


def execute(name, args=None, cwd=None, timeout=None):
    return _default_tools().execute(name, args, cwd, timeout)


def project_test():
    return _default_tools().project_test()


def project_build():
    return _default_tools().project_build()


def project_check():
    return _default_tools().project_check()


__all__ = [
    "Workspace", "PathError", "is_binary",
    "CodingTools", "FileTools", "ProjectTools", "CodeTools", "WriteTools",
    "ExecutionRunner", "ProjectVerificationTools",
    "file_list", "file_read", "file_search", "file_write", "file_edit",
    "file_mkdir", "file_diff", "project_inspect", "code_analyze",
    "execute", "project_test", "project_build", "project_check",
]
