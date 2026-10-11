"""Keeps README, CHANGELOG and the code from drifting apart.

Run:  python3 -m unittest discover -s tests -v
"""
import importlib.util
import os
import re
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


README = read("README.md")
CHANGELOG = read("CHANGELOG.md")
MAKEFILE = read("Makefile")


def heading_anchors(markdown):
    anchors = set()
    for line in markdown.splitlines():
        match = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if match:
            slug = re.sub(r"[^\w\s-]", "", match.group(1).lower())
            anchors.add(re.sub(r"\s+", "-", slug.strip()))
    return anchors


class ReadmeTests(unittest.TestCase):
    def test_make_commands_exist(self):
        targets = set(re.findall(r"^([A-Za-z0-9_%-]+):", MAKEFILE, re.M))
        self.assertTrue(targets)
        for command in re.findall(r"^\s{4}make (\S+)", README, re.M):
            if command.startswith("torrserver-"):
                command = "torrserver-%"
            self.assertIn(command, targets, "README mentions `make %s`" % command)

    def test_referenced_files_exist(self):
        for path in re.findall(r"`?((?:tests|src|\.github)/[\w./-]+|checksums\.sha256|CHANGELOG\.md|Makefile)`?", README):
            path = path.rstrip(".,)")
            self.assertTrue(os.path.exists(os.path.join(ROOT, path)), "README mentions %s" % path)

    def test_documented_test_commands_exist(self):
        for command in re.findall(r"^\s{4}(?:sh|bash|node|python3 -m unittest discover -s)\s+(\S+)", README, re.M):
            if command in ("-s", "tests"):
                continue
            self.assertTrue(os.path.exists(os.path.join(ROOT, command)), command)

    def test_installed_scripts_named_in_readme_are_shipped(self):
        for name in re.findall(r"/var/packages/TorrServer/scripts/([\w-]+)", README):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "src", "scripts", name)), name)

    def test_internal_links_point_to_real_headings(self):
        anchors = heading_anchors(README)
        for link in re.findall(r"\]\(#([\w-]+)\)", README):
            self.assertIn(link, anchors, "broken link #" + link)

    def test_folder_permission_instruction_matches_what_the_helper_tells_users(self):
        os.environ.setdefault("TORRSERVER_DSM_VAR", tempfile.mkdtemp(prefix="docs-var-"))
        spec = importlib.util.spec_from_file_location(
            "helper_docs", os.path.join(ROOT, "src", "helper", "helper.py"))
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        self.assertIn("System internal user", README)
        self.assertIn("System internal user", helper.NOT_WRITABLE_C)

    def test_sections_a_user_needs_are_top_level(self):
        top_level = re.findall(r"^## (.+)$", README, re.M)
        for section in ("Features", "Requirements", "Installation", "Upgrading", "Security",
                        "Troubleshooting", "Development"):
            self.assertIn(section, top_level)
        self.assertTrue(any(s.startswith("Folder access") for s in top_level))


class VersionTests(unittest.TestCase):
    def test_package_version_ends_with_the_torrserver_build(self):
        """PKG_VERSION is <package version>.<TorrServer build>, e.g. 2.3.145.2."""
        torrserver = re.search(r"^TORRSERVER_VERSION\s*:=\s*(\S+)", MAKEFILE, re.M).group(1)
        package = re.search(r"^PKG_VERSION\s*:=\s*(\S+)", MAKEFILE, re.M).group(1)
        build = re.sub(r"^\D+\.", "", torrserver)          # MatriX.145.2 -> 145.2
        self.assertRegex(
            package, r"^\d+\.\d+\." + re.escape(build) + r"$",
            "PKG_VERSION is %s but TORRSERVER_VERSION is %s: the package version must be "
            "<your version>.%s (raise the first two numbers for a new package release; "
            "the last ones are the TorrServer build)" % (package, torrserver, build))


class ChangelogTests(unittest.TestCase):
    def entries(self):
        return re.findall(r"^## (\S+) \((\d{4}-\d{2}-\d{2})\)\s*$", CHANGELOG, re.M)

    def test_top_entry_matches_the_package_version(self):
        version = re.search(r"^PKG_VERSION\s*:=\s*(\S+)", MAKEFILE, re.M).group(1)
        entries = self.entries()
        self.assertTrue(entries, "CHANGELOG.md needs entries like '## 2.0.1 (2026-01-31)'")
        self.assertEqual(
            entries[0][0], version,
            "Makefile PKG_VERSION is %s but the top CHANGELOG.md entry is %s: "
            "add an entry for the release (its text becomes the release notes)"
            % (version, entries[0][0]))

    def test_every_heading_is_well_formed(self):
        headings = re.findall(r"^## .*$", CHANGELOG, re.M)
        self.assertEqual(len(headings), len(self.entries()), headings)

    def test_versions_are_unique_and_the_top_entry_has_content(self):
        versions = [v for v, _ in self.entries()]
        self.assertEqual(len(versions), len(set(versions)))
        body = re.split(r"^## .*$", CHANGELOG, flags=re.M)[1]
        self.assertGreater(len(body.strip()), 40)


if __name__ == "__main__":
    unittest.main()
