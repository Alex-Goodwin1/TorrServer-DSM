"""Translation rules.

* Names of DSM's own interface follow DSM: the official wording where DSM has
  the language (ru, pl), English where DSM does not (uk, lt).
* Every message the code can show has a translation, and a translated
  sentence never contains pieces of another language by accident.

Run:  python3 -m unittest discover -s tests -v
"""
import ast
import html
import importlib.util
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["TORRSERVER_DSM_VAR"] = tempfile.mkdtemp(prefix="translations-var-")
spec = importlib.util.spec_from_file_location("helper_translations", os.path.join(ROOT, "src", "helper", "helper.py"))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

LANGS = ("ru", "uk", "lt", "pl")
DSM_LANGS = ("ru", "pl")            # languages DSM itself ships (Synology DSM 7.4 specification)
NO_DSM_LANGS = ("uk", "lt")         # DSM has no Ukrainian / Lithuanian interface
SOURCE = open(os.path.join(ROOT, "src", "helper", "helper.py"), encoding="utf-8").read()
TREE = ast.parse(SOURCE)


def locale(lang):
    with open(os.path.join(ROOT, "src", "helper", "locales", lang + ".json"), encoding="utf-8") as f:
        return json.load(f)


# English name of a DSM interface element -> how DSM writes it (regex, so that
# case endings such as "в Планировщике задач" are accepted).
# Source: Synology's own Russian and Polish help pages for DSM 7.
DSM_UI = {
    "Task Scheduler":        {"ru": r"Планировщик\w* задач",          "pl": r"Harmonogram\w* zadań"},
    "User-defined script":   {"ru": r"Пользовательск\w+ сценари\w+",   "pl": r"Skrypt\w* zdefiniowan\w+ przez użytkownika"},
    "Control Panel":         {"ru": r"Панел\w+ управления",            "pl": r"Panel\w* sterowania"},
    "Shared Folder":         {"ru": r"Общ\w+ папк\w+",                  "pl": r"[Ff]older\w* współdzielon\w+"},
    "Edit":                  {"ru": r"Редактировать",                   "pl": r"Edytuj"},
    "Permissions":           {"ru": r"Разрешения",                      "pl": r"Uprawnienia"},
    "System internal user":  {"ru": r"Внутренний пользователь системы", "pl": r"Wewnętrzny użytkownik systemu"},
    "Read/Write":            {"ru": r"Чтение/запись",                   "pl": r"Odczyt/Zapis"},
}
# Wording that was used by mistake and must not come back.
FORBIDDEN = {
    "ru": ["Диспетчер задач", "Диспетчере задач", "Планировщик заданий", "Планировщике заданий", "Панель задач"],
    "pl": ["Menedżer zadań", "Planista zadań"],
    "uk": ["Планувальник завдань", "Планувальнику завдань", "Панель керування", "Диспетчер завдань"],
    "lt": ["Užduočių planuoklis", "Užduočių planuoklyje", "Valdymo skydelis", "Užduočių tvarkytuvė"],
}
# Messages that are only logged or never shown.
NOT_SHOWN = {"", "no session cookie", "TorrServer is not running", "Settings saved",
             "Settings saved. The cache directory is applied when TorrServer starts"}


# The messages that tell the user where to click in DSM. Only these refer to
# DSM's own interface (other keys merely name this package's pages, such as
# "DSM Permissions", which is not a DSM screen).
INSTRUCTIONS = [
    "Option 2: Task Scheduler",
    "Create a Task Scheduler task of the type User-defined script. Run it as root, repeat it daily and use this script. It copies the DSM certificate to a folder of your choice and gives the TorrServer user access to it:",
    h.NOT_WRITABLE_C,
]
# These names are unmistakably DSM screens: a message that uses them must be listed above.
UNMISTAKABLE = ("Task Scheduler", "Control Panel", "Shared Folder", "Package Center")


def term_in(term, text):
    return re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", text) is not None


def english_keys_with(term):
    return [k for k in INSTRUCTIONS if term_in(term, k)]


class DsmTerminology(unittest.TestCase):
    def test_dsm_languages_use_the_official_dsm_names(self):
        checked = 0
        for term, forms in DSM_UI.items():
            for key in english_keys_with(term):
                for lang in DSM_LANGS:
                    text = locale(lang)[key]
                    self.assertRegex(text, forms[lang], "%s: %r must call %r the way DSM does" % (lang, key[:50], term))
                    checked += 1
        self.assertGreater(checked, 10, "the DSM terms are no longer found in the messages: update DSM_UI")

    def test_languages_without_dsm_keep_the_english_names_of_dsm(self):
        for term in DSM_UI:
            for key in english_keys_with(term):
                for lang in NO_DSM_LANGS:
                    self.assertIn(term, locale(lang)[key],
                                  "%s: DSM has no %s interface, keep %r as DSM shows it" % (lang, lang, term))

    def test_every_message_that_names_a_dsm_screen_is_covered_by_these_rules(self):
        for key in locale("en"):
            for term in UNMISTAKABLE:
                if term_in(term, key):
                    self.assertIn(key, INSTRUCTIONS, "%r mentions %r: add it to INSTRUCTIONS so its DSM wording is checked" % (key[:50], term))

    def test_wording_that_is_not_dsm_is_gone(self):
        for lang, words in FORBIDDEN.items():
            for key, text in locale(lang).items():
                for word in words:
                    self.assertNotIn(word, text, "%s: %r contains %r" % (lang, key[:50], word))

    def test_the_policy_names_the_right_languages(self):
        # Synology's DSM 7.4 specification lists Polish and Russian but neither Ukrainian nor Lithuanian.
        self.assertEqual(set(DSM_LANGS) & set(NO_DSM_LANGS), set())
        self.assertEqual(set(DSM_LANGS) | set(NO_DSM_LANGS), set(LANGS))


class MessagesAreTranslated(unittest.TestCase):
    @staticmethod
    def shown_messages():
        """Every text the code can show the user, as the translation key it needs."""
        messages = set()

        def add(text):
            if not isinstance(text, str) or text in NOT_SHOWN:
                return
            if "{}" in text:
                head, tail = text.split("{}", 1)
                if tail.strip():
                    raise AssertionError("placeholder in the middle of a message, put it last: %r" % text)
                text = head
            if text.strip():
                messages.add(text)

        for node in ast.walk(TREE):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) and len(node.value.elts) == 2:
                element = node.value.elts[1]
                if isinstance(element, ast.Constant):
                    add(element.value)
                elif isinstance(element, ast.Call) and isinstance(element.func, ast.Attribute) \
                        and element.func.attr == "format" and isinstance(element.func.value, ast.Constant):
                    add(element.func.value.value)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "send_message_page":
                for argument in node.args[:2]:
                    if isinstance(argument, ast.Constant):
                        add(argument.value)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if any(t.endswith("_PORT_ERRORS") for t in targets):
                    for element in node.value.elts:
                        add(element.value)
        for part in (h.NOT_WRITABLE_A, h.NOT_WRITABLE_B, h.NOT_WRITABLE_C, "no write access"):
            add(part)

        return messages

    def test_the_messages_were_found(self):
        self.assertGreater(len(self.shown_messages()), 25)

    def test_every_message_has_a_translation_in_every_language(self):
        english = locale("en")
        for text in sorted(self.shown_messages()):
            self.assertIn(text, english, "no translation key for the message %r" % text)
            for lang in LANGS:
                translation = locale(lang).get(text)
                self.assertTrue(translation, "%s: %r is missing" % (lang, text))
                self.assertNotEqual(translation, text, "%s: %r is still English" % (lang, text))

    def test_a_translated_message_keeps_what_follows_it(self):
        """Messages that end with a value ('...: ') keep the separator."""
        for text in self.shown_messages():
            if text.endswith(": "):
                for lang in LANGS:
                    self.assertTrue(locale(lang)[text].endswith(": "), "%s: %r" % (lang, text))


class NoMixedLanguages(unittest.TestCase):
    """What the user really reads must be in one language, not a patchwork."""

    ALLOWED = {"torrserver", "https", "helper", "root", "volumex", "volume", "http"}
    # Words that are spelled the same in English and in the language, e.g. DSM's own Polish
    # "Panel sterowania" and "Folder współdzielony".
    SHARED = {"pl": {"port", "panel", "folder", "status", "system", "administrator"}, "lt": set(), "ru": set(), "uk": set()}
    DSM_ENGLISH = {w.lower() for term in DSM_UI for w in re.findall(r"[A-Za-z]{4,}", term)} | {"create", "scheduled", "task", "defined"}

    def setUp(self):
        for name in os.listdir(os.environ["TORRSERVER_DSM_VAR"]):
            os.remove(os.path.join(os.environ["TORRSERVER_DSM_VAR"], name))
        self.saved = {n: getattr(h, n) for n in ("cache_browser_path",
                                                 "is_port_in_use", "prepare_torrserver_directory", "torrserver_request",
                                                 "get_port", "check_dsm_session")}
        self.addCleanup(lambda: [setattr(h, n, v) for n, v in self.saved.items()])
        h.cache_browser_path = lambda p: p
        h.is_port_in_use = lambda n, allowed_ports=None: n in (8091, 12345)
        h.prepare_torrserver_directory = lambda d: (True, "")
        h.torrserver_request = lambda *a, **k: (False, "Connection refused", True)
        h.get_port = lambda: 8090

    def leftovers(self, lang, shown, english):
        """English words that survived in *shown*.

        Cyrillic text should contain no other Latin words at all. Polish and
        Lithuanian are Latin script, so there a word is a leftover when it
        occurs in the English original of the same message.
        """
        def words(text):
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            text = re.sub(r"/[\w./-]+", " ", text)                  # paths and commands are not prose
            return {w for w in re.findall(r"[A-Za-z]{4,}", text)}

        found = words(shown)
        if lang not in ("ru", "uk"):
            source = {w.lower() for w in words(english)}
            found = {w for w in found if w.lower() in source}
        allowed = set(self.ALLOWED) | self.SHARED[lang]
        if lang in NO_DSM_LANGS:
            allowed |= self.DSM_ENGLISH                               # DSM's names stay English there
        return sorted(w for w in found if w.lower() not in allowed)

    def message(self, lang, **override):
        h.write_file(h.LANGUAGE_FILE, lang)
        data = dict(port="8090", torrserver_dir="/volume1/TS", https="0", https_port="8091", auth="0")
        data.update(override)
        ok, english = h.save_settings({k: [v] for k, v in data.items()})
        self.assertFalse(ok, override)
        return h.localize_html('<div class="notice">%s</div>' % html.escape(english)), english

    SCENARIOS = {
        "reserved web port": dict(port="42777"),
        "web port in use": dict(port="12345"),
        "https port reserved": dict(https="1", https_port="42777"),
        "https port in use": dict(https="1", https_port="8091"),
        "https equals web": dict(https="1", https_port="8090"),
        "bad port": dict(port="abc"),
        "port out of range": dict(port="80"),
        "bad https port": dict(https_port="abc"),
        "spaces in the directory": dict(torrserver_dir="/volume1/My Data"),
        "user name missing": dict(auth="1"),
        "user name with a colon": dict(auth="1", username="a:b", password="x"),
        "password missing": dict(auth="1", username="u"),
        "TorrServer unreachable": dict(),
    }

    def test_error_messages_are_in_one_language(self):
        problems = []
        for lang in LANGS:
            for name, override in self.SCENARIOS.items():
                if name == "TorrServer unreachable":
                    # TorrServer answers with an error: the user gets our translated prefix + TorrServer's own detail
                    h.torrserver_request = lambda *a, **k: (False, "HTTP Error 500", True)
                shown, english = self.message(lang, **override)
                shown = shown.replace("HTTP Error 500", "")
                english = english.replace("HTTP Error 500", "")
                left = self.leftovers(lang, shown, english)
                if left:
                    problems.append("%s | %s | %s | %s" % (lang, name, left, re.sub(r"<[^>]+>", "", shown)[:80]))
        self.assertEqual(problems, [])

    def test_permission_instructions_are_in_one_language(self):
        problems = []
        for lang in LANGS:
            h.write_file(h.LANGUAGE_FILE, lang)
            for text in (h.not_writable_message("/volume1/ab"),
                         "Create a Task Scheduler task of the type User-defined script. Run it as root, repeat it daily and use this script. It copies the DSM certificate to a folder of your choice and gives the TorrServer user access to it:"):
                shown = h.localize_html("<p>%s</p>" % html.escape(text))
                left = self.leftovers(lang, shown, text)
                if left:
                    problems.append("%s | %s | %s" % (lang, text[:40], left))
        self.assertEqual(problems, [])

    def test_the_access_denied_pages_are_translated(self):
        h.check_dsm_session = lambda cookie, token, ip, host="": ("", "denied")
        server = h.ThreadingHTTPServer(("127.0.0.1", 0), h.Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]

        def get(method="GET", data=None, headers=None):
            request = urllib.request.Request("http://127.0.0.1:%d/" % port, data=data, method=method,
                                             headers=dict({"Host": "nas.local"}, **(headers or {})))
            try:
                urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8")

        denied = "Access denied Sign in to DSM as an administrator and open the TorrServer DSM application."
        for lang in LANGS:
            h.write_file(h.LANGUAGE_FILE, lang)
            code, body = get()
            self.assertEqual(code, 403)
            self.assertIn(locale(lang)["Access denied"], body, lang)
            self.assertIn(locale(lang)["Sign in to DSM as an administrator and open the TorrServer DSM application."], body, lang)
            self.assertEqual(self.leftovers(lang, body, denied), [], lang)

        h.check_dsm_session = lambda cookie, token, ip, host="": ("admin", "")
        rejected = "Forbidden Cross-site request rejected. Reload the page and try again."
        for lang in LANGS:
            h.write_file(h.LANGUAGE_FILE, lang)
            code, body = get("POST", b"language=ru", {"Cookie": "id=x"})          # no Origin: rejected
            self.assertEqual(code, 403)
            self.assertIn(locale(lang)["Forbidden"], body, lang)
            self.assertIn(locale(lang)["Cross-site request rejected. Reload the page and try again."], body, lang)
            self.assertEqual(self.leftovers(lang, body, rejected), [], lang)


class TranslationQuality(unittest.TestCase):
    # (English key, UI labels it refers to). The translation must name them as the UI does.
    REFERENCES = [
        ("Choose the TorrServer directory with the Browse button", ["Browse"]),
    ]

    def test_messages_name_buttons_and_pages_exactly_as_the_ui_does(self):
        for key, labels in self.REFERENCES:
            for lang in LANGS:
                locale_data = locale(lang)
                for label in labels:
                    self.assertIn(locale_data[label], locale_data[key], "%s: %r must use %r" % (lang, key[:50], locale_data[label]))

    def test_every_locale_has_the_same_keys(self):
        english = set(locale("en"))
        for lang in LANGS:
            self.assertEqual(english ^ set(locale(lang)), set(), lang)

    def test_every_translation_keeps_paths_and_commands(self):
        path_token = re.compile(r"(?<![\w<])/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
        for lang in ("en",) + LANGS:
            for key, value in locale(lang).items():
                for token in path_token.findall(key):
                    self.assertIn(token, value, "%s: %r" % (lang, key[:50]))

    def test_different_messages_do_not_share_a_translation(self):
        for lang in LANGS:
            owners = {}
            for key, value in locale(lang).items():
                if value != key:
                    owners.setdefault(value, set()).add(key.lower())          # case variants may share
            for value, keys in owners.items():
                self.assertEqual(len(keys), 1, "%s: %r shared by %r" % (lang, value[:40], sorted(keys)))

    def test_no_translation_is_left_in_english(self):
        proper = {"HTTPS", "DSM", "CPU", "Plex", "Emby", "Status", "System", "FUSE"}     # same word in some languages
        for lang in LANGS:
            same = [k for k, v in locale(lang).items()
                    if k == v and k not in proper and re.search(r"[A-Za-z]{3,}", k)]
            self.assertEqual(same, [], lang)


if __name__ == "__main__":
    unittest.main()
