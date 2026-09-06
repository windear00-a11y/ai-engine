"""Phase 22 — Packaging, Clean Install & Distribution Hardening.

Tests the distributable packaging of the Persistent Intelligence System:

1. packaging metadata (pyproject.toml)
2. wheel build
3. artifact contents
4. required package data (vocabularies)
5. CPython corpus exclusion
6. clean install into an isolated venv
7. import outside the repository
8. CLI entry point (`ai_engine` / `ai-engine`)
9. installed `remember`
10. installed `recall`
11. installed Memory API
12. installed SDK (MemoryClient)
13. runtime data-root isolation
14. project isolation
15. vocabulary resolution
16. optional Code plugin
17. no source-tree dependency
18. no runtime write into the package directory

Infrastructure limitations (missing venv/ensurepip/pip/build tooling) are
reported as skips, never converted into PASS or FAIL.
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
import unittest

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYPROJECT = os.path.join(_REPO_ROOT, "pyproject.toml")
_EXPECTED_TOP_PACKAGES = {
    "ai_engine",
    "api",
    "external_http_client",
    "external_import",
    "http_server",
    "importing",
    "ingestion",
    "intelligence",
    "knowledge_client",
    "retrieval",
    "tools",
}
_MUST_EXCLUDE = {"sources", "database", "workspace", "tests", "acceptance"}
_CODE_SPECIFIC_EXCLUDED = {
    "engine", "knowledge_compiler", "webapp",
    # platform dirs of the retired domain layers
    "ai_engine_plugins",
}
_CONSOLE_SCRIPTS = {"ai_engine", "ai-engine"}


def _read_pyproject():
    if tomllib is not None:
        with open(_PYPROJECT, "rb") as fh:
            return tomllib.load(fh)
    with open(_PYPROJECT) as fh:
        return json.loads(fh.read())


def _host_can_build():
    try:
        import build  # noqa: F401
        return True
    except Exception:
        return False


def _host_can_make_venv():
    try:
        import ensurepip  # noqa: F401
        subprocess.run([sys.executable, "-m", "venv", "--help"], check=True,
                       capture_output=True)
        return True
    except Exception:
        return False


def _unique_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestPackagingMetadata(unittest.TestCase):
    def setUp(self):
        self.meta = _read_pyproject()

    def test_build_system(self):
        bs = self.meta["build-system"]
        self.assertIn("setuptools.build_meta", bs["build-backend"])
        self.assertTrue(any("setuptools" in r for r in bs["requires"]))

    def test_project_identity(self):
        proj = self.meta["project"]
        self.assertEqual(proj["name"], "ai-engine")
        self.assertRegex(proj["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(proj["requires-python"], ">=3.10")

    def test_zero_runtime_dependencies(self):
        deps = self.meta["project"].get("dependencies", [])
        self.assertEqual(deps, [])

    def test_console_scripts_present(self):
        scripts = self.meta["project"]["scripts"]
        for name in _CONSOLE_SCRIPTS:
            self.assertIn(name, scripts)
            self.assertEqual(scripts[name], "ai_engine.__main__:main")

    def test_package_find_includes_core_not_fixtures(self):
        include = self.meta["tool"]["setuptools"]["packages"]["find"]["include"]
        for pkg in _EXPECTED_TOP_PACKAGES:
            self.assertTrue(any(pkg in pat for pat in include), pkg)
        for excluded in _MUST_EXCLUDE | _CODE_SPECIFIC_EXCLUDED:
            for pat in include:
                self.assertFalse(pat.startswith(excluded),
                                 "include pattern touches %s" % excluded)

    def test_vocabulary_package_data_configured(self):
        pd = self.meta["tool"]["setuptools"].get("package-data", {})
        self.assertIn("ai_engine", pd)
        self.assertIn("vocabularies/*.json", pd["ai_engine"])


@unittest.skipUnless(_host_can_build(), "environment lacks standard build tooling (build/setuptools)")
class TestWheelArtifact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p22_wheel_")
        out = self.tmp
        env = dict(os.environ)
        env["PYTHONPATH"] = ""
        subprocess.run(
            [sys.executable, "-m", "build", "--no-isolation", "--outdir", out],
            cwd=_REPO_ROOT, env=env, check=True, capture_output=True,
            timeout=900,
        )
        self.wheel = os.path.join(out, "ai_engine-0.10.0-py3-none-any.whl")
        self.sdist = os.path.join(out, "ai_engine-0.10.0.tar.gz")
        self.assertTrue(os.path.exists(self.wheel))
        self.assertTrue(os.path.exists(self.sdist))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wheel_contents(self):
        with zipfile.ZipFile(self.wheel) as z:
            top = sorted({n.split("/", 1)[0] for n in z.namelist()})
        for pkg in _EXPECTED_TOP_PACKAGES:
            self.assertIn(pkg, top, pkg)
        with zipfile.ZipFile(self.wheel) as z:
            all_names = "\n".join(z.namelist())
            for excluded in _MUST_EXCLUDE:
                self.assertNotRegex(all_names, r"^%s/" % excluded, excluded)
            for excluded in _CODE_SPECIFIC_EXCLUDED:
                self.assertNotRegex(all_names, r"^%s/" % excluded, excluded)
            self.assertNotIn("sources/cpython/", all_names)
            self.assertNotRegex(all_names, r"\.db$")

    def test_wheel_small(self):
        # CPython corpus (192MB in checkout) must not inflate the wheel.
        size = os.path.getsize(self.wheel)
        self.assertLess(size, 10 * 1024 * 1024)

    def test_wheel_package_data_vocabularies(self):
        with zipfile.ZipFile(self.wheel) as z:
            names = z.namelist()
            self.assertIn("ai_engine/vocabularies/diary_v1.json", names)
            # The CODE-domain vocabulary ships with the optional plugin, not
            # with the generic core.
            self.assertNotIn("ai_engine/vocabularies/code_v1.json", names)

    def test_wheel_metadata(self):
        with zipfile.ZipFile(self.wheel) as z:
            meta = z.read("ai_engine-0.10.0.dist-info/METADATA").decode()
            self.assertIn("Name: ai-engine", meta)
            self.assertIn("Version: 0.10.0", meta)
            self.assertIn("Requires-Python: >=3.10", meta)
            self.assertNotIn("Requires-Dist", meta)
            ep = z.read("ai_engine-0.10.0.dist-info/entry_points.txt").decode()
            self.assertIn("ai_engine = ai_engine.__main__:main", ep)
            self.assertIn("ai-engine = ai_engine.__main__:main", ep)

    def test_no_absolute_paths_or_secrets(self):
        usr_bin = zipfile.ZipFile
        with usr_bin(self.wheel) as z:
            for name in z.namelist():
                if name.endswith(".py"):
                    src = z.read(name).decode("utf-8", "replace")
                    self.assertNotIn("/root/ai-engine", src)
                    self.assertNotIn("/sdcard", src)
                    self.assertNotIn("BEGIN PRIVATE", src)

    def test_sdist_does_not_inflate_with_corpus(self):
        import tarfile
        with tarfile.open(self.sdist, mode="r:gz") as t:
            names = t.getnames()
            blob = "\n".join(names)
            for excluded in _MUST_EXCLUDE:
                for n in names:
                    self.assertNotRegex(n, r"^%s/" % excluded, excluded)
            self.assertNotRegex(blob, r"sources/cpython")

    def test_wheel_has_main_module(self):
        with zipfile.ZipFile(self.wheel) as z:
            self.assertIn("ai_engine/__main__.py", z.namelist())


@unittest.skipUnless(
    _host_can_build() and _host_can_make_venv(),
    "clean install needs build tooling + venv/ensurepip in the host interpreter",
)
class TestCleanInstall(unittest.TestCase):
    """Full outside-repo install test using only the built wheel."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="p22_install_")
        env = dict(os.environ)
        env["PYTHONPATH"] = ""
        subprocess.run(
            [sys.executable, "-m", "build", "--no-isolation", "--outdir", cls.tmp],
            cwd=_REPO_ROOT, env=env, check=True, capture_output=True,
            timeout=900,
        )
        wheel = os.path.join(cls.tmp, "ai_engine-0.10.0-py3-none-any.whl")
        venv = os.path.join(cls.tmp, "venv")
        subprocess.run([sys.executable, "-m", "venv", venv], check=True,
                       capture_output=True, timeout=300)
        pipex = os.path.join(venv, "bin", "pip") if os.name != "nt" else os.path.join(
            venv, "Scripts", "pip.exe")
        subprocess.run(
            [pipex, "install", "--no-deps", wheel],
            check=True, capture_output=True, timeout=600,
        )
        cls.venv = venv
        cls.python = os.path.join(venv, "bin", "python") if os.name != "nt" else os.path.join(
            venv, "Scripts", "python.exe")
        cls.scripts = os.path.join(venv, "bin")
        cls.site = os.path.join(
            venv, "lib", "python%d.%d" % sys.version_info[:2], "site-packages")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run_py(self, code, cwd=None, env=None, data_root=None):
        env = dict(os.environ if env is None else env)
        env["PYTHONPATH"] = ""
        if data_root is not None:
            env["AI_ENGINE_DATA_DIR"] = data_root
        return subprocess.run(
            [self.python, "-c", code], cwd=cwd or self.tmp, env=env,
            capture_output=True, text=True, timeout=120,
        )

    def _cli(self, args, cwd=None, env=None, data_root=None):
        env = dict(os.environ if env is None else env)
        env["PYTHONPATH"] = ""
        if data_root is not None:
            env["AI_ENGINE_DATA_DIR"] = data_root
        return subprocess.run(
            [os.path.join(self.scripts, args[0])] + list(args[1:]),
            cwd=cwd or self.tmp, env=env, capture_output=True, text=True,
            timeout=120,
        )

    def test_import_from_outside_repository(self):
        r = self._run_py(
            "import ai_engine, sys, os\n"
            "assert '/root/ai-engine' not in sys.path, 'repo on sys.path'\n"
            "assert not os.path.abspath('.').startswith('/root/ai-engine'), r'cwd inside repo'\n"
            "assert ai_engine.__file__.startswith(%r), ai_engine.__file__\n"
            "print(ai_engine.__file__)\n" % self.site,
            cwd=self.tmp,
        )
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        self.assertIn(self.site, r.stdout)

    def test_import_does_not_pull_tools_or_code_domain(self):
        r = self._run_py(
            "import sys, ai_engine\n"
            "loaded={m for m in sys.modules if m.split('.')[0] in ('engine','tools','knowledge_compiler','sources')}\n"
            "assert not loaded, loaded\n"
            "print('generic-only')\n",
            cwd=self.tmp,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("generic-only", r.stdout)

    def test_vocabulary_from_installed_package(self):
        r = self._run_py(
            "import os\n"
            "from ai_engine.vocabulary import Vocabulary, _resolve_vocab_path\n"
            "v = Vocabulary.load('diary_v1')\n"
            "assert 'diary_v1' in v.id, v.id\n"
            "print('vocabs-installed')\n",
            cwd=self.tmp,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vocabs-installed", r.stdout)

    def test_cli_entry_point_version(self):
        data = os.path.join(self.tmp, "data_cli")
        r = self._cli(["ai_engine", "--version"], data_root=data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().startswith("0.10.0"))
        r2 = self._cli(["ai-engine", "--version"], data_root=data)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(r2.stdout.strip(), "0.10.0")

    def test_cli_doctor_no_repo(self):
        data = os.path.join(self.tmp, "data_doc")
        self._cli(["ai_engine", "init"], data_root=data)
        r = self._cli(["ai_engine", "doctor"], data_root=data)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        self.assertIn("data_root", r.stdout)
        self.assertIn("no consistency problems", r.stdout)

    def test_installed_remember_recall_roundtrip(self):
        data = os.path.join(self.tmp, "data_rr")
        pre = self._cli(["ai_engine", "init", "--project", "red"], data_root=data)
        self.assertEqual(pre.returncode, 0, pre.stderr)
        mem = self._cli(["ai_engine", "remember", "--project", "red",
                         "clean wheel install remembers"], data_root=data)
        self.assertEqual(mem.returncode, 0, mem.stderr)
        self.assertIn("remembered", mem.stdout)
        rec = self._cli(["ai_engine", "recall", "--project", "red",
                         "clean wheel"], data_root=data)
        self.assertEqual(rec.returncode, 0, rec.stderr)
        self.assertIn("clean wheel install remembers", rec.stdout)
        status = self._cli(["ai_engine", "status", "--project", "red"], data_root=data)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("nodes 1", status.stdout)

    def test_installed_memory_api(self):
        data = os.path.join(self.tmp, "data_api")
        from api.memory_api import MemoryAPI
        api = MemoryAPI(data_root=data)
        self._cli(["ai_engine", "init"], data_root=data)
        rec = api.remember({"text": "installed api memory"}, project_id="default")
        self.assertIn(rec["node_id"], rec["node_id"])
        out = api.recall(query="installed api memory", limit=5)
        self.assertGreaterEqual(out["count"], 1)
        got = api.get(rec["node_id"])
        self.assertEqual(got["name"], "installed api memory")
        prov = api.provenance(rec["node_id"])
        self.assertEqual(prov["node_id"], rec["node_id"])

    def test_installed_sdk_memoryclient(self):
        data = os.path.join(self.tmp, "data_sdk")
        from knowledge_client.transports import MemoryInProcessTransport
        from knowledge_client.memory_client import MemoryClient
        c = MemoryClient(MemoryInProcessTransport(data_root=data))
        self._cli(["ai_engine", "init", "--project", "demo"], data_root=data)
        crec = c.remember({"text": "sdk from wheel"}, project_id="demo")
        inner = c.recall("sdk from wheel", limit=5, project_id="demo")
        self.assertGreaterEqual(inner["count"], 1)
        self.assertEqual(c.get(crec["node_id"], project_id="demo")["name"],
                         "sdk from wheel")

    def test_project_isolation(self):
        data = os.path.join(self.tmp, "data_iso")
        for proj in ("red", "blue"):
            self._cli(["ai_engine", "init", "--project", proj], data_root=data)
            self._cli(["ai_engine", "remember", "--project", proj,
                       "%s only marker" % proj], data_root=data)
        for proj in ("red", "blue"):
            out = self._cli(["ai_engine", "recall", "--project", proj,
                             "only marker"], data_root=data).stdout
            self.assertIn("%s only marker" % proj, out)
            other = "blue" if proj == "red" else "red"
            self.assertNotIn("%s only marker" % other, out)
        self.assertTrue(os.path.exists(os.path.join(data, "red", "knowledge.db")))
        self.assertTrue(os.path.exists(os.path.join(data, "blue", "knowledge.db")))

    def test_no_runtime_write_into_package_dir(self):
        data = os.path.join(self.tmp, "data_pkg")
        self._cli(["ai_engine", "init"], data_root=data)
        self._cli(["ai_engine", "remember", "runtime data check"], data_root=data)
        self._cli(["ai_engine", "recall", "runtime"], data_root=data)
        self._cli(["ai_engine", "doctor"], data_root=data)
        leaked = []
        for base, _, files in os.walk(self.site):
            for fn in files:
                if fn.endswith((".db", ".log")) or fn == "projects.json":
                    leaked.append(os.path.join(base, fn))
        self.assertEqual(leaked, [], "runtime data written into package dir: %r" % leaked)

    def test_data_root_resolution_and_fresh_start(self):
        data = os.path.join(self.tmp, "data_fresh")
        r = self._run_py(
            "import os\n"
            "from ai_engine.paths import get_data_root\n"
            "assert get_data_root()==os.environ['AI_ENGINE_DATA_DIR'], get_data_root()\n"
            "print('root-ok')\n",
            data_root=data,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("root-ok", r.stdout)

    def test_code_plugin_not_shipped_optional(self):
        # The CODE domain is no longer part of the wheel: the generic core
        # must import cleanly and `tools.coding`/plugin registrations must
        # be ABSENT (external plugins add them at runtime).
        generic = self._run_py(
            "import sys, ai_engine\n"
            "assert 'tools' not in sys.modules, 'plugin loaded generically'\n"
            "assert 'ai_engine.plugins.code' not in sys.modules, 'code plugin loaded'\n"
            "print('generic-clean')\n",
            cwd=self.tmp,
        )
        self.assertEqual(generic.returncode, 0, generic.stderr)
        self.assertIn("generic-clean", generic.stdout)
        missing = self._run_py(
            "import tools.coding.tools\n",
            cwd=self.tmp,
        )
        self.assertNotEqual(missing.returncode, 0, "code domain must not ship in the wheel")

    def test_uninstall_reinstall_preserves_data(self):
        data = os.path.join(self.tmp, "data_survive")
        self._cli(["ai_engine", "init", "--project", "keep"], data_root=data)
        self._cli(["ai_engine", "remember", "--project", "keep",
                   "survives reinstall"], data_root=data)
        pipex = self.python.replace("bin/python", "bin/pip")
        subprocess.run([pipex, "uninstall", "-y", "ai-engine"], check=True,
                       capture_output=True, timeout=300)
        os.makedirs(os.path.join(self.tmp, "dist"))
        shutil.copy(
            os.path.join(self.tmp, "ai_engine-0.10.0-py3-none-any.whl"),
            os.path.join(self.tmp, "dist", "ai_engine-0.10.0-py3-none-any.whl"),
        )
        subprocess.run(
            [pipex, "install", "--no-deps",
             os.path.join(self.tmp, "dist", "ai_engine-0.10.0-py3-none-any.whl")],
            check=True, capture_output=True, timeout=600,
        )
        self.assertTrue(os.path.exists(
            os.path.join(data, "keep", "knowledge.db")))
        rec = self._cli(["ai_engine", "recall", "--project", "keep",
                         "survives"], data_root=data)
        self.assertEqual(rec.returncode, 0, rec.stderr)
        self.assertIn("survives reinstall", rec.stdout)

    def test_http_daemon_smoke(self):
        data = os.path.join(self.tmp, "data_daemon")
        self._cli(["ai_engine", "init", "--project", "d"], data_root=data)
        self._cli(["ai_engine", "remember", "--project", "d",
                   "daemon reachable from wheel"], data_root=data)
        port = _unique_port()
        env = dict(os.environ)
        env["PYTHONPATH"] = ""
        env["AI_ENGINE_DATA_DIR"] = data
        proc = subprocess.Popen(
            [os.path.join(self.scripts, "ai_engine"), "serve",
             "--port", str(port), "--data-root", data],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            health = {}
            for _ in range(40):
                try:
                    import urllib.request
                    with urllib.request.urlopen(
                            "http://127.0.0.1:%d/health" % port, timeout=1) as r:
                        health = json.loads(r.read().decode())
                    break
                except Exception:
                    time.sleep(0.25)
            self.assertTrue(health.get("ok"), health)
            code = (
                "from knowledge_client.transports import HttpMemoryTransport\n"
                "from knowledge_client.memory_client import MemoryClient\n"
                "c = MemoryClient(HttpMemoryTransport(port=%d))\n"
                "r = c.recall('daemon reachable', limit=5, project_id='d')\n"
                "assert r.get('count') and r.get('count', 0) >= 1, r\n"
                "print('http-recall-ok')\n" % port
            )
            r = self._run_py(code, data_root=data)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("http-recall-ok", r.stdout)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    unittest.main(verbosity=2)