"""KGHEER open-source foundation release tests.

Focused release-foundation checks: Apache-2.0 license presence and metadata,
required community files, DCO documentation, KGHEER public identity, and
repository hygiene (no accidental machine-specific paths or key material in
tracked public files).
"""

import os
import re
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(_ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


class LicenseFoundationTests(unittest.TestCase):
    def test_license_file_exists(self):
        self.assertTrue(
            os.path.isfile(os.path.join(_ROOT, "LICENSE")),
            "LICENSE file must exist at repository root")

    def test_license_is_apache_2_0(self):
        text = _read("LICENSE")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)
        self.assertIn("TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION",
                      text)

    def test_pyproject_declares_apache_2_0(self):
        text = _read("pyproject.toml")
        self.assertIn("Apache-2.0", text)
        self.assertNotIn('license = { text = "MIT" }', text)


class CommunityFoundationTests(unittest.TestCase):
    def test_required_files_exist(self):
        for rel in ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md",
                    "docs/OPEN_SOURCE_POLICY.md"):
            self.assertTrue(
                os.path.isfile(os.path.join(_ROOT, rel)),
                "%s must exist" % rel)

    def test_contributing_documents_dco(self):
        text = _read("CONTRIBUTING.md")
        self.assertIn("Developer Certificate of Origin", text)
        self.assertIn("Signed-off-by", text)
        self.assertIn("DCO", text)

    def test_contributing_state_apache_terms(self):
        text = _read("CONTRIBUTING.md")
        self.assertIn("Apache License", text)
        self.assertIn("no CLA", text)

    def test_security_md_has_reporting_process(self):
        text = _read("SECURITY.md")
        self.assertRegex(text, r"(?i)reporting a vulnerability")

    def test_code_of_conduct_present(self):
        text = _read("CODE_OF_CONDUCT.md")
        self.assertIn("Contributor Covenant", text)


class PublicIdentityTests(unittest.TestCase):
    def test_readme_uses_kgheer_identity(self):
        text = _read("README.md")
        self.assertIn("KGHEER Core", text)
        self.assertIn("KGHEER Diary", text)
        self.assertIn("KGHEER Notes", text)
        self.assertIn("KGHEER SDK", text)
        self.assertIn("KGHEER CLI", text)
        self.assertIn("KGHEER Plugins", text)

    def test_open_source_policy_states_free_and_commercial(self):
        text = _read("docs/OPEN_SOURCE_POLICY.md")
        self.assertIn("free and open source", text)
        self.assertIn("no mandatory revenue share", text)
        self.assertIn("Apache License, Version 2.0", text)


class RepositoryHygieneTests(unittest.TestCase):
    """Scans tracked text files for accidental machine-specific paths and
    key material. Test files (which may use '/tmp' or dummy secrets) are
    excluded from the machine-path check; the public docs are always checked.
    Ellipsis stub examples such as `/root/...` are allowed in docs.
    """

    TRACKED = []
    _PRIVATE_KEY_RE = re.compile(
        r"BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY", re.IGNORECASE)
    _MACHINE_PATH_RE = re.compile(r"/(?:root|Users|home/mirror|mnt/sdcard)/(?!\.{3})")

    @classmethod
    def setUpClass(cls):
        out = subprocess.run(["git", "ls-files"], cwd=_ROOT,
                             capture_output=True, text=True)
        cls.TRACKED = [p for p in out.stdout.splitlines()
                       if p.endswith((".py", ".md", ".toml", ".txt", ".json"))]

    def test_no_private_keys_in_tracked_text(self):
        for rel in self.TRACKED:
            if "/tests/" in rel or rel.startswith("tests/"):
                continue
            try:
                text = _read(rel)
            except (OSError, UnicodeDecodeError):
                continue
            self.assertIsNone(
                self._PRIVATE_KEY_RE.search(text),
                "private key material found in %s" % rel)

    def test_no_dev_machine_paths_in_tracked_docs(self):
        suspicious = []
        for rel in self.TRACKED:
            if not rel.endswith(".md"):
                continue
            try:
                text = _read(rel)
            except (OSError, UnicodeDecodeError):
                continue
            if self._MACHINE_PATH_RE.search(text):
                suspicious.append(rel)
        self.assertEqual(
            suspicious, [],
            "machine-specific absolute paths in tracked docs: %s" % suspicious)


if __name__ == "__main__":
    unittest.main()