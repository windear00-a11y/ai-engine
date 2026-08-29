"""Coding Tools Layer.

Deterministic inspection and controlled modification of a software workspace.
No AI/LLM, no shell execution, no network, no external dependencies. Read-only
tools (``file.list/read/search``, ``project.inspect``, ``code.analyze``) and
controlled write tools (``file.write/edit/mkdir/diff``) all return a
JSON-serializable ``dict`` and signal failure through a structured ``"error"``
field rather than raising uncontrolled exceptions.

Namespaced usage::

    coding = CodingTools(workspace_root)
    coding.file.list("src", depth=2)
    coding.file.read("src/app.py")
    coding.file.search("def main")
    coding.project.inspect()
    coding.code.analyze("src/app.py")
    coding.file.write("src/app.py", "print(1)\\n")
    coding.file.edit("src/app.py", "1", "2")
    coding.file.mkdir("src")
    coding.file.diff("src/app.py", "print(2)\\n")

All path handling is delegated to :class:`tools.coding.fs.Workspace`, so paths
can never escape the workspace root.
"""

import ast
import os
import re

from .fs import Workspace, PathError, is_binary
from .write_tools import WriteTools
from .exec_tools import ExecutionRunner, ProjectVerificationTools


# --------------------------------------------------------------------------
# file.* tools
# --------------------------------------------------------------------------

class FileTools:
    def __init__(self, root):
        self.ws = Workspace(root)

    def list(self, path=".", depth=1, include_dirs=True, include_files=True):
        if not isinstance(path, str):
            return {"path": path, "error": "path must be a string", "entries": []}
        try:
            abs_path = self.ws.resolve(path)
        except PathError as e:
            return {"path": path, "error": str(e), "entries": []}
        if not os.path.exists(abs_path):
            return {"path": path, "error": "no such path", "entries": []}
        if not os.path.isdir(abs_path):
            return {"path": path, "error": "not a directory", "entries": []}
        if not isinstance(depth, int) or depth < 0:
            return {"path": path, "error": "depth must be a non-negative int",
                    "entries": []}

        entries = []
        base_depth = abs_path.rstrip(os.sep).count(os.sep)
        for current, dirs, files in os.walk(abs_path):
            cur_depth = current.rstrip(os.sep).count(os.sep) - base_depth
            if cur_depth > depth:
                dirs[:] = []
                continue
            if include_dirs:
                for name in sorted(dirs):
                    child = os.path.join(current, name)
                    entries.append({
                        "name": name,
                        "path": self.ws.rel(child),
                        "type": "dir",
                    })
            if include_files:
                for name in sorted(files):
                    child = os.path.join(current, name)
                    entries.append({
                        "name": name,
                        "path": self.ws.rel(child),
                        "type": "file",
                    })
        return {
            "path": path,
            "root": self.ws.root,
            "count": len(entries),
            "entries": entries,
            "error": None,
        }

    def read(self, path, max_size=None):
        if not isinstance(path, str):
            return {"path": path, "error": "path must be a string",
                    "size": None, "content": None}
        try:
            abs_path = self.ws.resolve(path)
        except PathError as e:
            return {"path": path, "error": str(e), "size": None, "content": None}
        if not os.path.exists(abs_path):
            return {"path": path, "error": "file not found",
                    "size": None, "content": None}
        if os.path.isdir(abs_path):
            return {"path": path, "error": "is a directory",
                    "size": None, "content": None}
        size = os.path.getsize(abs_path)
        if max_size is not None and size > max_size:
            return {"path": path, "error": f"file too large ({size} > {max_size})",
                    "size": size, "content": None}
        if is_binary(abs_path):
            return {"path": path, "error": "binary file not readable as text",
                    "size": size, "content": None}
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            return {"path": path, "error": str(e), "size": None, "content": None}
        return {
            "path": path,
            "root": self.ws.root,
            "size": size,
            "content": content,
            "error": None,
        }

    def search(self, query, path=".", max_results=200, include_ext=None,
               case_sensitive=False):
        if not isinstance(query, str) or not query:
            return {"query": query,
                    "error": "query must be a non-empty string", "files": []}
        if not isinstance(path, str):
            return {"query": query, "error": "path must be a string",
                    "files": []}
        try:
            abs_path = self.ws.resolve(path)
        except PathError as e:
            return {"query": query, "error": str(e), "files": []}
        if not os.path.exists(abs_path):
            return {"query": query, "error": "no such path", "files": []}

        needle = query if case_sensitive else query.lower()
        exts = tuple(include_ext) if include_ext else None
        max_files = 2000
        files_out = []
        total = 0
        scanned = 0

        targets = [abs_path] if os.path.isfile(abs_path) else None
        if targets is None:
            targets = []
            for current, dirs, files in os.walk(abs_path):
                for name in files:
                    targets.append(os.path.join(current, name))

        for fpath in targets:
            if total >= max_results or scanned >= max_files:
                break
            scanned += 1
            if exts and not fpath.endswith(exts):
                continue
            if is_binary(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError:
                continue
            matches = []
            for i, line in enumerate(lines, 1):
                hay = line if case_sensitive else line.lower()
                idx = hay.find(needle)
                if idx != -1:
                    matches.append({
                        "line_number": i,
                        "line": line.rstrip("\n").rstrip("\r"),
                        "column": idx,
                    })
                    total += 1
                    if total >= max_results:
                        break
            if matches:
                files_out.append({
                    "path": self.ws.rel(fpath),
                    "match_count": len(matches),
                    "matches": matches,
                })

        return {
            "query": query,
            "root": self.ws.root,
            "count": len(files_out),
            "total_matches": total,
            "files": files_out,
            "error": None,
        }


# --------------------------------------------------------------------------
# project.* tools
# --------------------------------------------------------------------------

class ProjectTools:
    LANG_EXT = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript",
        ".cjs": "javascript", ".go": "go", ".rs": "rust", ".java": "java",
        ".rb": "ruby", ".c": "c", ".cpp": "cpp", ".cc": "cpp",
        ".cs": "csharp", ".php": "php", ".swift": "swift", ".kt": "kotlin",
        ".sh": "shell", ".bash": "shell", ".html": "html", ".css": "css",
        ".sql": "sql", ".md": "markdown", ".json": "json", ".yml": "yaml",
        ".yaml": "yaml",
    }
    CONFIG_FILES = {
        "package.json", "pyproject.toml", "requirements.txt", "setup.py",
        "setup.cfg", "Pipfile", "tsconfig.json", "Cargo.toml", "go.mod",
        "go.sum", "Dockerfile", "Makefile", ".gitignore", "README.md",
        "README.rst", "README.txt", "composer.json", "Gemfile", "pom.xml",
        "build.gradle", ".editorconfig", "LICENSE", "Cargo.lock",
    }
    CONFIG_TO_TYPE = {
        "package.json": "node",
        "pyproject.toml": "python", "requirements.txt": "python",
        "setup.py": "python", "setup.cfg": "python", "Pipfile": "python",
        "Cargo.toml": "rust", "Cargo.lock": "rust",
        "go.mod": "go", "Gemfile": "ruby", "composer.json": "php",
        "pom.xml": "java", "build.gradle": "java",
    }
    TEST_DIR_NAMES = {"test", "tests", "spec", "__tests__", "test_suite"}

    def __init__(self, root):
        self.ws = Workspace(root)

    def inspect(self):
        try:
            self.ws.resolve(".")
        except PathError as e:
            return {"root": self.ws.root, "error": str(e)}

        languages = {}
        config_files = []
        source_dirs = set()
        test_dirs = set()
        files = []
        max_files = 2000

        for current, dirs, fnames in os.walk(self.ws.root):
            for name in fnames:
                if len(files) >= max_files:
                    break
                fpath = os.path.join(current, name)
                rel = self.ws.rel(fpath)
                files.append(rel)
                ext = os.path.splitext(name)[1].lower()
                if ext in self.LANG_EXT:
                    lang = self.LANG_EXT[ext]
                    languages[lang] = languages.get(lang, 0) + 1
                    source_dirs.add(self.ws.rel(current))
                if name in self.CONFIG_FILES:
                    config_files.append({"name": name, "path": rel})
                if ext == ".py" and name == "__init__.py":
                    pass
            # detect test directories by name
            for d in dirs:
                drel = self.ws.rel(os.path.join(current, d))
                low = d.lower()
                if d in self.TEST_DIR_NAMES or low.startswith("test") \
                        or low.endswith("test") or low.endswith("_tests"):
                    test_dirs.add(drel)

        project_types = set()
        for name, ptype in self.CONFIG_TO_TYPE.items():
            if any(c["name"] == name for c in config_files):
                project_types.add(ptype)

        likely = [lang for lang, _ in sorted(
            languages.items(), key=lambda kv: (-kv[1], kv[0]))]
        if not project_types and likely:
            project_types = set(likely)

        return {
            "root": self.ws.root,
            "file_count": len(files),
            "files": files[:1000],
            "languages": languages,
            "likely_languages": likely,
            "project_types": sorted(project_types),
            "config_files": sorted(config_files, key=lambda c: c["path"]),
            "source_dirs": sorted(source_dirs),
            "test_dirs": sorted(test_dirs),
            "error": None,
        }


# --------------------------------------------------------------------------
# code.* tools
# --------------------------------------------------------------------------

_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
}


class CodeTools:
    def __init__(self, root):
        self.ws = Workspace(root)

    def analyze(self, path):
        if not isinstance(path, str):
            return {"path": path, "error": "path must be a string",
                    "language": None, "imports": [], "functions": [],
                    "classes": [], "exports": []}
        try:
            abs_path = self.ws.resolve(path)
        except PathError as e:
            return {"path": path, "error": str(e), "language": None,
                    "imports": [], "functions": [], "classes": [],
                    "exports": []}
        if not os.path.exists(abs_path):
            return {"path": path, "error": "file not found", "language": None,
                    "imports": [], "functions": [], "classes": [],
                    "exports": []}
        if os.path.isdir(abs_path):
            return {"path": path, "error": "is a directory", "language": None,
                    "imports": [], "functions": [], "classes": [],
                    "exports": []}
        if is_binary(abs_path):
            return {"path": path, "error": "binary file cannot be analyzed",
                    "language": None, "imports": [], "functions": [],
                    "classes": [], "exports": []}

        ext = os.path.splitext(abs_path)[1].lower()
        lang = _LANG_BY_EXT.get(ext)
        if lang is None:
            return {"path": path,
                    "error": f"unsupported language for extension {ext!r}",
                    "language": None, "imports": [], "functions": [],
                    "classes": [], "exports": []}

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError as e:
            return {"path": path, "error": str(e), "language": lang,
                    "imports": [], "functions": [], "classes": [],
                    "exports": []}

        if lang == "python":
            result = self._analyze_python(source)
        else:
            result = self._analyze_js_ts(source, lang)

        result["path"] = path
        result["root"] = self.ws.root
        result["language"] = lang
        result["error"] = None
        return result

    @staticmethod
    def _analyze_python(source):
        imports, functions, classes, exports = [], [], [], []
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return {"error": f"syntax error: {e}", "imports": [],
                    "functions": [], "classes": [], "exports": []}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod:
                    imports.append(mod)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({
                    "name": node.name,
                    "line": node.lineno,
                    "args": len(node.args.args) + len(node.args.kwonlyargs),
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                })
            elif isinstance(node, ast.ClassDef):
                bases = []
                for b in node.bases:
                    try:
                        bases.append(ast.unparse(b))
                    except Exception:
                        bases.append("?")
                methods = [
                    n.name for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "bases": bases,
                    "methods": methods,
                })
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "__all__":
                        if isinstance(node.value, ast.List):
                            try:
                                exports.extend(
                                    x.value for x in node.value.elts
                                    if isinstance(x, ast.Constant)
                                )
                            except Exception:
                                pass
        return {"imports": imports, "functions": functions,
                "classes": classes, "exports": exports}

    @staticmethod
    def _analyze_js_ts(source, lang):
        imports, functions, classes, exports = [], [], [], []

        imports += re.findall(
            r'import\s+(?:[^;{]*?\s+from\s+)?[\'"]([^\'"]+)[\'"]', source)
        imports += re.findall(
            r'require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)', source)
        imports += re.findall(
            r'export\s+(?:[^;]*?\s+from\s+)?[\'"]([^\'"]+)[\'"]', source)

        for m in re.finditer(
                r'function\s+([A-Za-z_$][\w$]*)\s*\(', source):
            functions.append({"name": m.group(1),
                              "line": source.count("\n", 0, m.start()) + 1})
        for m in re.finditer(
                r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*'
                r'(?:async\s*)?\([^)]*\)\s*=>', source):
            functions.append({"name": m.group(1),
                              "line": source.count("\n", 0, m.start()) + 1})
        for m in re.finditer(
                r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*function', source):
            functions.append({"name": m.group(1),
                              "line": source.count("\n", 0, m.start()) + 1})

        for m in re.finditer(r'class\s+([A-Za-z_$][\w$]*)', source):
            classes.append({"name": m.group(1),
                            "line": source.count("\n", 0, m.start()),
                            "bases": [], "methods": []})

        if re.search(r'export\s+default', source):
            exports.append("default")
        exports += re.findall(
            r'export\s+(?:default\s+)?(?:async\s+)?'
            r'(?:function|class|const|let|var|interface|type)\s+'
            r'([A-Za-z_$][\w$]*)', source)
        if "module.exports" in source:
            exports.append("module.exports")

        return {"imports": imports, "functions": functions,
                "classes": classes, "exports": exports}


# --------------------------------------------------------------------------
# Aggregate facade
# --------------------------------------------------------------------------

class CodingTools:
    def __init__(self, root, permissions=None, policy=None, approver=None):
        self.root = root
        self.permissions = permissions
        self.file = FileTools(root)
        self.project = ProjectTools(root)
        self.code = CodeTools(root)
        self.write = WriteTools(root, permissions=permissions)
        self.exec = ExecutionRunner(root, policy=policy,
                                    permissions=permissions,
                                    approver=approver)
        self.verify = ProjectVerificationTools(root, runner=self.exec)

    def file_list(self, path=".", depth=1, include_dirs=True,
                  include_files=True):
        return self.file.list(path, depth, include_dirs, include_files)

    def file_read(self, path, max_size=None):
        return self.file.read(path, max_size)

    def file_search(self, query, path=".", max_results=200,
                    include_ext=None, case_sensitive=False):
        return self.file.search(query, path, max_results, include_ext,
                                case_sensitive)

    def project_inspect(self):
        return self.project.inspect()

    def code_analyze(self, path):
        return self.code.analyze(path)

    def file_write(self, path, content, overwrite=True):
        return self.write.write(path, content, overwrite)

    def file_edit(self, path, old_text, new_text, replace_all=False):
        return self.write.edit(path, old_text, new_text, replace_all)

    def file_mkdir(self, path, parents=True):
        return self.write.mkdir(path, parents)

    def file_diff(self, path, proposed_content):
        return self.write.diff(path, proposed_content)

    def execute(self, name, args=None, cwd=None, timeout=None):
        return self.exec.run(name, args, cwd, timeout)

    def project_test(self):
        return self.verify.project_test()

    def project_build(self):
        return self.verify.project_build()

    def project_check(self):
        return self.verify.project_check()
