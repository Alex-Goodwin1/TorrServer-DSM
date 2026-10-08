"""Guards against dead code and leftovers coming back.

Run:  python3 -m unittest discover -s tests -v
"""
import ast
import glob
import json
import os
import re
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(ROOT, "src", "helper", "helper.py")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


HELPER_SOURCE = read("src", "helper", "helper.py")
TREE = ast.parse(HELPER_SOURCE)
SHELL_FILES = sorted(
    glob.glob(os.path.join(ROOT, "src", "scripts", "*"))
    + [os.path.join(ROOT, "build-package.sh"), os.path.join(ROOT, "src", "INFO.sh")]
    + glob.glob(os.path.join(ROOT, ".github", "scripts", "*.sh"))
)
# The six scripts DSM starts by name; each just calls the function of that name.
DSM_ENTRY_POINTS = {"preinst", "postinst", "preuninst", "postuninst", "preupgrade", "postupgrade"}


class PythonHygiene(unittest.TestCase):
    def test_no_unused_imports(self):
        imported = {}
        for node in ast.walk(TREE):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported[alias.asname or alias.name] = node.lineno
        used = {n.id for n in ast.walk(TREE) if isinstance(n, ast.Name)}
        self.assertEqual(sorted(k for k in imported if k not in used), [])

    def test_no_unreferenced_functions_or_constants(self):
        definitions = {}
        for node in TREE.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                definitions[node.name] = node.lineno
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        definitions[target.id] = node.lineno
        references = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store):
                references.add(node.id)
            elif isinstance(node, ast.Attribute):
                references.add(node.attr)
        dead = sorted(n for n in definitions if n not in references and n != "__name__")
        self.assertEqual(dead, [], "defined in helper.py but never used (remove them): %s" % dead)

    def test_no_unused_local_variables(self):
        problems = []
        for fn in (n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)):
            stores, loads = {}, set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Name):
                    if isinstance(node.ctx, ast.Store):
                        stores.setdefault(node.id, node.lineno)
                    else:
                        loads.add(node.id)
            problems += ["%s() line %d: %s" % (fn.name, line, name)
                         for name, line in stores.items()
                         if name not in loads and not name.startswith("_")]
        self.assertEqual(problems, [])

    def test_no_function_is_defined_twice(self):
        names = [n.name for n in TREE.body if isinstance(n, ast.FunctionDef)]
        self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])


class StylesheetHygiene(unittest.TestCase):
    def test_every_styled_class_is_used_by_the_page(self):
        start = HELPER_SOURCE.index("<style>\n")
        end = HELPER_SOURCE.index("</style>", start)
        css = HELPER_SOURCE[start:end].replace("{{", "{").replace("}}", "}")
        outside = HELPER_SOURCE[:start] + HELPER_SOURCE[end:]
        classes = set(re.findall(r"\.([A-Za-z][\w-]*)", re.sub(r"\{[^}]*\}", "{}", css)))
        self.assertTrue(classes)
        orphans = sorted(
            c for c in classes
            if not re.search(r"(?<![\w-])" + re.escape(c) + r"(?![\w-])", outside))
        self.assertEqual(orphans, [], "CSS classes that no markup or script uses")

    def test_the_stylesheet_is_well_formed(self):
        start = HELPER_SOURCE.index("<style>\n")
        css = HELPER_SOURCE[start:HELPER_SOURCE.index("</style>", start)]
        css = css.replace("{{", "{").replace("}}", "}")
        self.assertEqual(css.count("{"), css.count("}"))
        self.assertEqual(re.findall(r"\{\s*\}", css), [], "empty rules")


class TranslationHygiene(unittest.TestCase):
    def test_every_translation_key_is_used_somewhere(self):
        constants = " ".join(
            n.value for n in ast.walk(TREE) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        # prepare-directory prints messages that the Helper shows to the user as they are
        constants += " " + read("src", "scripts", "prepare-directory")
        plain = lambda text: re.sub(r"<[^>]+>", "", text)
        for lang in ("en", "ru", "uk", "lt", "pl"):
            keys = json.loads(read("src", "helper", "locales", lang + ".json"))
            unused = [k for k in keys if k not in constants and plain(k) not in plain(constants)]
            self.assertEqual(unused, [], "%s.json has translations for text that no longer exists" % lang)


class ShellHygiene(unittest.TestCase):
    def texts(self):
        return {f: open(f, encoding="utf-8").read() for f in SHELL_FILES}

    def test_every_shell_function_is_called_somewhere(self):
        texts = self.texts()
        everything = "\n".join(texts.values()) + read("tests", "test_scripts.sh")
        dead = []
        for path, text in texts.items():
            for match in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)", text, re.M):
                name = match.group(1)
                calls = len(re.findall(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", everything)) - 1
                if calls < 1 and name not in DSM_ENTRY_POINTS:
                    dead.append("%s: %s" % (os.path.relpath(path, ROOT), name))
        self.assertEqual(dead, [])

    def test_every_hook_that_is_called_by_name_is_defined(self):
        texts = self.texts()
        everything = "\n".join(texts.values())
        defined = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)", everything, re.M))
        called = set(re.findall(r'call_func\s+"([A-Za-z_][A-Za-z0-9_]*)"', everything))
        self.assertEqual(sorted(called - defined), [], "call_func on a hook that does not exist")

    def test_top_level_variables_are_read(self):
        texts = self.texts()
        everything = "\n".join(texts.values())
        unused = []
        for path, text in texts.items():
            for match in re.finditer(r"^([A-Z][A-Z0-9_]*)=", text, re.M):
                name = match.group(1)
                if not re.search(r"\$\{?" + name + r"\b", everything):
                    unused.append("%s: %s" % (os.path.relpath(path, ROOT), name))
        self.assertEqual(unused, [])

    def test_no_debugging_leftovers(self):
        offenders = []
        for path, text in self.texts().items():
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(r"\bset -x\b|debug|DEBUG|echo \"(here|test)\b|TODO|FIXME", line):
                    offenders.append("%s:%d: %s" % (os.path.relpath(path, ROOT), number, line.strip()))
        self.assertEqual(offenders, [])

    def test_nothing_refers_to_dsm_versions_the_package_does_not_support(self):
        info = read("src", "INFO.sh")
        self.assertRegex(info, r'OS_MIN_VER="7\.')
        for path, text in self.texts().items():
            if path.endswith(("INFO.sh", "build-package.sh")):
                continue
            self.assertNotRegex(text, r"SYNOPKG_DSM_VERSION_MAJOR\"?\s*-lt\s*[0-9]",
                                os.path.relpath(path, ROOT) + " branches on an unsupported DSM version")


class RepositoryHygiene(unittest.TestCase):
    def test_the_package_ships_no_build_artifacts_or_tests(self):
        script = read("build-package.sh")
        self.assertIn("__pycache__", script, "build-package.sh must strip __pycache__ from the package")
        for folder in ("tests", ".github"):
            self.assertNotIn("cp -r %s" % folder, script)

    def test_every_script_the_package_ships_is_referenced(self):
        referenced = read("src", "conf", "resource") + read("src", "conf", "privilege") \
            + "\n".join(open(f, encoding="utf-8").read() for f in SHELL_FILES) + HELPER_SOURCE \
            + read("src", "conf", "systemd", "pkg-TorrServer-restart.service")
        for path in glob.glob(os.path.join(ROOT, "src", "scripts", "*")):
            name = os.path.basename(path)
            if name in DSM_ENTRY_POINTS | {"start-stop-status"}:
                continue
            self.assertIn(name, referenced, "src/scripts/%s is not used by anything" % name)


if __name__ == "__main__":
    unittest.main()
