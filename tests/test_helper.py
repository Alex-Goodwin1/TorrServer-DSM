"""Unit tests for src/helper/helper.py (no DSM required).

Run:  python3 -m unittest discover -s tests -v
"""
import html
import importlib.util
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(ROOT, "src", "helper", "helper.py")

VAR = tempfile.mkdtemp(prefix="torrserver-var-")
os.environ["TORRSERVER_DSM_VAR"] = VAR

spec = importlib.util.spec_from_file_location("helper", HELPER)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeTorrServer:
    """Mimics TorrServer's /settings: 'set' replaces the whole object."""

    def __init__(self, status=200):
        self.state = {
            "CacheSize": 134217728,
            "UseDisk": True,
            "TorrentsSavePath": "/old",
            "ConnectionsLimit": 50,
        }
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append(body)
                if status != 200:
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if body["action"] == "get":
                    out = json.dumps(outer.state).encode()
                else:
                    outer.state.clear()
                    outer.state.update(body["sets"])
                    out = b""
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        for name in os.listdir(VAR):
            path = os.path.join(VAR, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        # Deterministic, closed HTTPS port so only the HTTP endpoint matters.
        h.write_file(h.HTTPS_PORT_FILE, str(free_port()))


class CachePathTests(Base):
    def test_set_preserves_other_settings(self):
        ts = FakeTorrServer()
        self.addCleanup(ts.close)
        status, _ = h.set_cache_path("/volume1/TS/Cache", ts.port)
        self.assertEqual(status, "applied")
        self.assertEqual(ts.state["CacheSize"], 134217728)
        self.assertTrue(ts.state["UseDisk"])
        self.assertEqual(ts.state["ConnectionsLimit"], 50)
        self.assertEqual(ts.state["TorrentsSavePath"], "/volume1/TS/Cache")
        self.assertEqual([c["action"] for c in ts.calls], ["get", "set"])

    def test_unchanged_value_sends_no_set(self):
        ts = FakeTorrServer()
        self.addCleanup(ts.close)
        h.set_cache_path("/x", ts.port)
        ts.calls.clear()
        h.set_cache_path("/x", ts.port)
        self.assertEqual([c["action"] for c in ts.calls], ["get"])

    def test_key_case_is_respected(self):
        ts = FakeTorrServer()
        self.addCleanup(ts.close)
        ts.state.clear()
        ts.state.update({"torrentsSavePath": "/x", "CacheSize": 1})
        h.set_cache_path("/y", ts.port)
        self.assertEqual(ts.state.get("torrentsSavePath"), "/y")
        self.assertNotIn("TorrentsSavePath", ts.state)

    def test_redirect_is_not_success(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(307)
                self.send_header("Location", "https://127.0.0.1:1/")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        ok, _data, reachable = h.torrserver_request({"action": "get"}, srv.server_address[1])
        self.assertFalse(ok)
        self.assertTrue(reachable)

    def test_not_running_is_queued_then_applied(self):
        status, _ = h.set_cache_path("/volume1/TS/Cache", free_port())
        self.assertEqual(status, "pending")
        self.assertEqual(h.read_file(h.CACHE_PENDING_FILE), "/volume1/TS/Cache")

        ts = FakeTorrServer()
        self.addCleanup(ts.close)
        h.write_file(h.PORT_FILE, str(ts.port))
        self.assertTrue(h.apply_pending_cache_path())
        self.assertEqual(ts.state["TorrentsSavePath"], "/volume1/TS/Cache")
        self.assertEqual(ts.state["ConnectionsLimit"], 50)
        self.assertFalse(os.path.exists(h.CACHE_PENDING_FILE))

    def test_server_error_is_not_queued(self):
        ts = FakeTorrServer(status=500)
        self.addCleanup(ts.close)
        status, _ = h.set_cache_path("/x", ts.port)
        self.assertEqual(status, "error")
        self.assertFalse(os.path.exists(h.CACHE_PENDING_FILE))


class AccountTests(Base):
    def test_other_accounts_kept_and_primary_first(self):
        h.write_file(h.ACCS_FILE, json.dumps({"alice": "pw1", "kid": "pw2"}))
        h.save_account("bob", "pw3", previous_username="alice")
        accounts = json.load(open(h.ACCS_FILE))
        self.assertEqual(accounts, {"bob": "pw3", "kid": "pw2"})
        self.assertEqual(next(iter(accounts)), "bob")

    def test_file_mode_is_0600(self):
        h.save_account("bob", "pw")
        self.assertEqual(os.stat(h.ACCS_FILE).st_mode & 0o777, 0o600)

    def test_username_validation(self):
        self.assertTrue(h.valid_username("bob"))
        self.assertFalse(h.valid_username("a:b"))
        self.assertFalse(h.valid_username("a\nb"))
        self.assertFalse(h.valid_username(""))

class SaveSettingsTests(Base):
    BASE = dict(port="8090", torrserver_dir="/volume1/TS", https="0",
                https_port="8091", auth="0")

    def setUp(self):
        super().setUp()
        self.ts = FakeTorrServer()
        self.addCleanup(self.ts.close)
        self.patches = {
            "prepare_torrserver_directory": lambda d: (True, ""),
            "cache_browser_path": lambda p: p,
            "is_port_in_use": lambda n, allowed_ports=None: False,
            "get_port": lambda: self.ts.port,
        }
        self.saved = {k: getattr(h, k) for k in self.patches}
        for k, v in self.patches.items():
            setattr(h, k, v)
        self.addCleanup(lambda: [setattr(h, k, v) for k, v in self.saved.items()])

    def save(self, **overrides):
        data = dict(self.BASE, **overrides)
        return h.save_settings({k: [v] for k, v in data.items()})

    def test_valid_save_writes_files(self):
        ok, message = self.save()
        self.assertTrue(ok, message)
        self.assertEqual(h.read_file(h.PORT_FILE), "8090")
        self.assertEqual(h.read_file(h.TORRSERVER_DIR_FILE), "/volume1/TS")

    def test_rejected_input_writes_nothing(self):
        cases = [
            dict(torrserver_dir="/volume1/My Data"),
            dict(auth="1", username="a:b", password="x"),
            dict(auth="1", username="u", password=""),
            dict(port="80"),
        ]
        h.os.unlink(h.HTTPS_PORT_FILE)
        for case in cases:
            ok, message = self.save(**case)
            self.assertFalse(ok, case)
        self.assertFalse(os.path.exists(h.PORT_FILE))

    def test_settings_can_be_saved_without_a_directory(self):
        self.patches_called = []
        h.prepare_torrserver_directory = lambda d: self.patches_called.append(d) or (True, "")
        ok, message = self.save(torrserver_dir="", port="8095")
        self.assertTrue(ok, message)
        self.assertEqual(h.read_file(h.PORT_FILE), "8095")
        self.assertEqual(self.patches_called, [], "no directory means nothing to prepare")

    def test_fuse_still_needs_a_directory(self):
        ok, message = self.save(torrserver_dir="", fuse="1")
        self.assertFalse(ok)
        self.assertIn("Browse", message)

    def test_busy_https_port_only_matters_when_https_on(self):
        h.is_port_in_use = lambda n, allowed_ports=None: n == 8091
        self.assertTrue(self.save()[0])
        self.assertFalse(self.save(https="1")[0])

    def test_every_option_saves_without_any_extra_permissions(self):
        ok, message = self.save(port="9090", https="1", https_port="9443", force_https="1",
                                fuse="1", auth="1", username="alice", password="pw")
        self.assertTrue(ok, message)
        self.assertEqual(h.read_file(h.HTTPS_FILE), "1")
        self.assertEqual(h.read_file(h.FUSE_FILE), "1")
        self.assertEqual(json.load(open(h.ACCS_FILE)), {"alice": "pw"})

    def test_certificate_fields_of_an_old_form_are_ignored(self):
        ok, message = self.save(ssl_mode="dsm", ssl_cert="/usr/syno/c.pem", ssl_key="/usr/syno/k.pem")
        self.assertTrue(ok, message)
        self.assertEqual(sorted(os.listdir(VAR)), sorted(os.path.basename(f) for f in (
            h.PORT_FILE, h.TORRSERVER_DIR_FILE, h.AUTH_FILE, h.FUSE_FILE, h.HTTPS_PORT_FILE,
            h.HTTPS_FILE, h.FORCE_HTTPS_FILE)))

    def test_saving_never_touches_a_certificate_pair(self):
        for name in ("server.pem", "server.key"):
            open(os.path.join(VAR, name), "w").write("user supplied")
        ok, message = self.save(https="1")
        self.assertTrue(ok, message)
        for name in ("server.pem", "server.key"):
            self.assertEqual(open(os.path.join(VAR, name)).read(), "user supplied")

    def test_a_directory_the_service_user_cannot_write_is_explained(self):
        h.prepare_torrserver_directory = lambda d: (False, h.not_writable_message(d))
        ok, message = self.save()
        self.assertEqual((ok, message), (False, h.not_writable_message("/volume1/TS")))
        self.assertFalse(os.path.exists(h.PORT_FILE))

    def test_saves_when_torrserver_is_down(self):
        self.ts.close()
        h.get_port = lambda: free_port()
        ok, message = self.save()
        self.assertTrue(ok, message)
        self.assertEqual(h.read_file(h.CACHE_PENDING_FILE), "/volume1/TS/Cache")


class DirectoryTests(Base):
    """Cache/FUSE are created as the service user. There is no root fallback."""

    def setUp(self):
        super().setUp()
        self.base = tempfile.mkdtemp(prefix="ts-dir-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.sudo_calls = []
        self.real = (h.subprocess.run, os.mkdir, os.access)

        def run(cmd, **kwargs):
            self.sudo_calls.append(cmd)
            return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        h.subprocess.run = run
        self.addCleanup(lambda: (setattr(h.subprocess, "run", self.real[0]),
                                 setattr(os, "mkdir", self.real[1]),
                                 setattr(os, "access", self.real[2])))

    def deny_creation(self):
        def mkdir(path, mode=0o777, **kw):
            raise PermissionError(13, "Permission denied", path)
        os.mkdir = mkdir

    def test_the_service_user_creates_the_folders(self):
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertEqual((ok, message), (True, ""))
        for name in ("Cache", "FUSE"):
            self.assertTrue(os.path.isdir(os.path.join(self.base, name)), name)
        self.assertEqual(self.sudo_calls, [], "no other program is started")

    def test_existing_writable_directories_are_accepted(self):
        for name in ("Cache", "FUSE"):
            os.mkdir(os.path.join(self.base, name))
        self.assertEqual(h.prepare_torrserver_directory(self.base), (True, ""))
        self.assertEqual(self.sudo_calls, [])

    def test_not_writable_explains_the_standard_dsm_fix(self):
        self.deny_creation()
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertFalse(ok)
        self.assertEqual(message, h.not_writable_message(self.base))
        self.assertIn(self.base, message, "the message names the folder")
        self.assertIn("System internal user", message)
        self.assertNotIn("setup-permissions", message)
        self.assertEqual(self.sudo_calls, [], "never call sudo")

    def test_existing_directory_that_is_not_writable_is_explained(self):
        for name in ("Cache", "FUSE"):
            os.mkdir(os.path.join(self.base, name))
        os.access = lambda path, mode: False
        self.assertEqual(h.prepare_torrserver_directory(self.base), (False, h.not_writable_message(self.base)))
        self.assertEqual(self.sudo_calls, [])

    def test_symlinks_are_refused(self):
        os.symlink("/etc", os.path.join(self.base, "Cache"))
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertFalse(ok)
        self.assertIn("symbolic link", message)

    def test_a_file_where_a_directory_belongs_is_refused(self):
        open(os.path.join(self.base, "FUSE"), "w").close()
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertFalse(ok)
        self.assertIn("not a directory", message)

    def test_missing_parent_is_reported(self):
        ok, message = h.prepare_torrserver_directory(os.path.join(self.base, "missing"))
        self.assertFalse(ok)
        self.assertIn("Failed to create", message)
        self.assertEqual(self.sudo_calls, [])

    def test_empty_directory_tells_the_user_what_to_do(self):
        ok, message = h.prepare_torrserver_directory("")
        self.assertFalse(ok)
        self.assertIn("Browse", message)

    def test_concurrent_creation_is_tolerated(self):
        real = os.mkdir
        def racing(path, mode=0o777, **kw):
            real(path, mode)
            raise FileExistsError(17, "File exists", path)
        os.mkdir = racing
        self.assertEqual(h.prepare_torrserver_directory(self.base), (True, ""))


class FolderAccessMessageTests(Base):
    def test_the_message_names_the_folder_and_the_share_to_fix(self):
        message = h.not_writable_message("/volume1/docker/PlexTorr")
        self.assertIn("/volume1/docker/PlexTorr", message)
        self.assertIn("shared folder docker (", message)
        self.assertIn("System internal user", message)

    def test_the_share_is_the_first_folder_below_the_volume(self):
        self.assertEqual(h.share_of("/volume2/media/a/b"), "media")
        self.assertEqual(h.share_of("/volume1/docker"), "docker")
        self.assertEqual(h.share_of("/volume1"), "volume1")


class BrowseAccessTests(Base):
    """The folder chooser says so before Save when the TorrServer user has no write access."""

    def setUp(self):
        super().setUp()
        self.base = tempfile.mkdtemp(prefix="volume1-")
        self.addCleanup(shutil.rmtree, self.base, True)
        os.mkdir(os.path.join(self.base, "open"))
        os.mkdir(os.path.join(self.base, "locked"))
        self.denied = set()
        self.real = (os.access, h.cache_browser_path)
        self.addCleanup(lambda: (setattr(os, "access", self.real[0]), setattr(h, "cache_browser_path", self.real[1])))
        h.cache_browser_path = lambda p: p if p else "/"
        os.access = lambda p, mode, **kw: p not in self.denied and self.real[0](p, mode, **kw)

    def test_a_folder_without_write_access_is_marked_in_the_list(self):
        self.denied = {os.path.join(self.base, "locked")}
        page = h.cache_browser_page(self.base, "torrserver")
        row_locked = page[page.index(">locked/<"):page.index("</a>", page.index(">locked/<"))]
        row_open = page[page.index(">open/<"):page.index("</a>", page.index(">open/<"))]
        self.assertIn("no write access", row_locked)
        self.assertNotIn("no write access", row_open)

    def test_choosing_such_a_folder_warns_before_save_and_names_the_share(self):
        locked = os.path.join(self.base, "locked")
        self.denied = {locked}
        page = h.cache_browser_page(locked, "torrserver")
        self.assertIn(h.NOT_WRITABLE_A, page)
        self.assertIn(h.NOT_WRITABLE_C, page)
        self.assertIn(locked, page)
        self.assertIn(h.share_of(locked), page)

    def test_a_writable_folder_has_no_warning(self):
        page = h.cache_browser_page(os.path.join(self.base, "open"), "torrserver")
        self.assertNotIn(h.NOT_WRITABLE_A, page)
        self.assertNotIn("no write access", page)

    def test_folder_names_are_never_translated(self):
        os.mkdir(os.path.join(self.base, "Status"))
        h.write_file(h.LANGUAGE_FILE, "ru")
        self.assertIn(">Status/<", h.cache_browser_page(self.base, "torrserver"))


class FormTests(Base):
    """What the settings form shows. Everything works for the package user."""

    def render(self, privileged=False):
        return h.settings_page()

    def tag(self, page, pattern):
        match = re.search(pattern, page)
        self.assertIsNotNone(match, pattern)
        return match.group(0)

    def test_nothing_is_locked(self):
        page = self.render()
        for pattern in (r"<fieldset[^>]*>", r'<input[^>]*name="port"[^>]*>',
                        r'<input[^>]*name="torrserver_dir"[^>]*>', r'<input[^>]*name="fuse"[^>]*>',
                        r'<input[^>]*name="https"[^>]*>', r'<input[^>]*name="auth"[^>]*>',
                        r'<button type="submit"[^>]*>Save', r'<button[^>]*formaction="./restart"[^>]*>'):
            self.assertNotIn("disabled", self.tag(page, pattern), pattern)


class SslManualTests(FormTests):
    """The SSL card is a manual: no form fields, no root, two renewal options."""

    def card(self):
        page = self.render()
        card = page[page.index("SSL Certificate</span>"):]
        return card[:card.index("<script>")]

    def test_the_card_has_no_form_controls(self):
        card = self.card()
        for tag in ("<input", "<select", "<button", "<option"):
            self.assertNotIn(tag, card)
        page = self.render()
        for name in ("ssl_mode", "ssl_cert", "ssl_key", "sslMode", "sslDsm", "cert-fields"):
            self.assertNotIn(name, page)

    def test_the_card_explains_the_manual_upload_and_both_options(self):
        card = self.card()
        for text in ("Manual upload", "Settings, Additional, HTTPS", "Option 1: reverse proxy",
                     "Option 2: Task Scheduler", "User-defined script",
                     "/usr/syno/etc/certificate/_archive", "DEFAULT"):
            self.assertIn(text, card)

    def test_the_script_is_shown_as_it_is_and_never_translated(self):
        card = self.card()
        self.assertIn('<pre translate="no">#!/bin/sh', card)
        self.assertEqual(html.unescape(card[card.index("<pre"):card.index("</pre>")].split(">", 1)[1]),
                         h.SSL_SCRIPT.strip("\n"))

    def test_no_root_helper_is_mentioned_or_offered(self):
        page = self.render()
        for text in ("setup-permissions", "certificate-helper", "prepare-directory", "sudo",
                     "Check permissions", "DSM permissions"):
            self.assertNotIn(text, page)
        self.assertEqual([n for n in ("has_privileged_access", "get_dsm_certificates", "PREPARE_DIRECTORY",
                                      "CERTIFICATE_HELPER", "sudo_denied") if hasattr(h, n)], [])

    def test_the_restart_button_needs_no_permissions(self):
        tag = self.tag(self.render(), r'<button[^>]*formaction="./restart"[^>]*>')
        self.assertNotIn("disabled", tag)


class SettingsLayoutTests(FormTests):
    """Order of the cards and the inline FUSE / disk cache help."""

    def titles(self, page):
        return re.findall(r'<div class="settings-card-title">\s*<span class="metric-icon">.*?</span>\s*<span>([^<]*)</span>', page, re.S)

    def test_the_buttons_follow_the_forms_and_the_certificate_manual_comes_last(self):
        page = self.render(privileged=False)
        titles = self.titles(page)
        self.assertLess(titles.index("Authentication"), titles.index("SSL Certificate"))
        self.assertLess(page.index('<button type="submit">Save'), page.index("SSL Certificate</span>"))
        self.assertEqual(page.count('<button type="submit">Save'), 1)

    def test_no_media_server_window_any_more(self):
        page = self.render(privileged=False)
        self.assertNotIn("recommendations", page)
        self.assertFalse(hasattr(h, "media_recommendations_page"))

    def test_fuse_help_is_inline_and_follows_the_checkbox(self):
        page = self.render(privileged=False)
        self.assertIn('id="fuseHint"', page)
        self.assertIn("toggleFuseHint()", page)
        for text in ("Plex", "Emby", "Video preview thumbnails"):
            self.assertIn(text, page)

    def test_the_directory_help_explains_the_disk_cache(self):
        page = self.render(privileged=False)
        self.assertIn("Cache for the TorrServer disk cache", page)


class StoppedStatusTests(Base):
    def setUp(self):
        super().setUp()
        self.saved = (h.is_torrserver_running, h.TORRSERVER_LOG, dict(h.RESTART_STATE))
        h.is_torrserver_running = lambda: False
        h.TORRSERVER_LOG = os.path.join(VAR, "TorrServer.log")
        h.RESTART_STATE["started"] = None
        self.addCleanup(lambda: (setattr(h, "is_torrserver_running", self.saved[0]),
                                 setattr(h, "TORRSERVER_LOG", self.saved[1]),
                                 h.RESTART_STATE.update(self.saved[2])))

    def write(self, path, text, age=0):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(path, (time.time() - age, time.time() - age))

    def test_running_page_has_no_start_button(self):
        h.is_torrserver_running = lambda: True
        page = h.main_page("nas.local")
        self.assertNotIn(">Start<", page)
        self.assertIn("TorrServer is running normally.", page)

    def test_stopped_page_says_so_and_offers_start(self):
        page = h.main_page("nas.local")
        self.assertIn("TorrServer is not running.", page)
        self.assertNotIn("TorrServer is running normally.", page)
        self.assertEqual(page.count(">Start<"), 1)
        # on the right of the status card, where the Open buttons are while it runs
        self.assertRegex(page, r'<div class="web-ui-actions start-actions">\s*<div class="web-ui-action"><form method="post" action="./restart"><button type="submit">Start</button>')
        self.assertNotIn("Open HTTP", page)

    def test_the_last_lines_of_the_log_are_shown(self):
        self.write(h.TORRSERVER_LOG, "old one\nold two\na\nb\n\nlisten tcp :8090: bind: address already in use\nexit status 1\n")
        lines = h.last_problem_lines()
        self.assertEqual(lines[-2:], ["listen tcp :8090: bind: address already in use", "exit status 1"])
        self.assertEqual(len(lines), 3)
        self.assertNotIn("old two", "".join(lines))
        page = h.main_page("nas.local")
        self.assertIn("address already in use", page)
        self.assertIn("Last lines of the log:", page)

    def test_log_text_is_escaped_and_cut(self):
        self.write(h.TORRSERVER_LOG, "<script>alert(1)</script>" + "x" * 500 + "\n")
        self.assertTrue(all(len(line) <= 220 for line in h.last_problem_lines()))
        page = h.main_page("nas.local")
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;script&gt;", page)

    def test_no_logs_means_no_log_block(self):
        self.assertEqual(h.last_problem_lines(), [])
        self.assertNotIn("Last lines of the log:", h.main_page("nas.local"))

    def test_start_button_is_not_part_of_the_log_block(self):
        self.write(h.TORRSERVER_LOG, "boom\n")
        block = h.stopped_info_html()
        self.assertIn("boom", block)
        self.assertNotIn("Start", block)

    def test_while_restarting_there_is_no_start_button(self):
        h.RESTART_STATE["started"] = time.monotonic()
        self.assertNotIn(">Start<", h.main_page("nas.local"))


class RestartProgressTests(Base):
    def setUp(self):
        super().setUp()
        self.saved = (h.RESTART_LOCK, h.is_torrserver_running, dict(h.RESTART_STATE))
        h.RESTART_LOCK = os.path.join(VAR, "restart.lock")
        self.running = True
        h.is_torrserver_running = lambda: self.running
        self.addCleanup(lambda: (setattr(h, "RESTART_LOCK", self.saved[0]),
                                 setattr(h, "is_torrserver_running", self.saved[1]),
                                 h.RESTART_STATE.update(self.saved[2])))

    def started_ago(self, seconds):
        h.RESTART_STATE["started"] = time.monotonic() - seconds

    def test_nothing_to_wait_for_without_a_restart(self):
        h.RESTART_STATE["started"] = None
        self.assertTrue(h.restart_finished())

    def test_not_finished_during_the_first_seconds(self):
        self.started_ago(0.5)
        self.assertFalse(h.restart_finished(), "the script may not hold its lock yet")

    def test_not_finished_while_the_lock_is_held(self):
        self.started_ago(10)
        os.mkdir(h.RESTART_LOCK)
        self.assertFalse(h.restart_finished())

    def test_not_finished_while_torrserver_is_down(self):
        self.started_ago(10)
        self.running = False
        self.assertFalse(h.restart_finished())

    def test_finished_when_the_lock_is_gone_and_torrserver_runs(self):
        self.started_ago(10)
        self.assertTrue(h.restart_finished())

    def test_in_progress_until_finished_then_never_again(self):
        self.started_ago(10)
        os.mkdir(h.RESTART_LOCK)
        self.assertTrue(h.restart_in_progress())
        os.rmdir(h.RESTART_LOCK)
        self.assertFalse(h.restart_in_progress())
        os.mkdir(h.RESTART_LOCK)                      # a later lock must not revive the old restart
        self.assertFalse(h.restart_in_progress())

    def test_gives_up_when_torrserver_never_comes_back(self):
        self.started_ago(h.RESTART_GIVE_UP_SECONDS + 1)
        self.running = False
        self.assertFalse(h.restart_in_progress())

    def test_status_page_shows_restarting_and_watches(self):
        self.started_ago(10)
        os.mkdir(h.RESTART_LOCK)
        page = h.main_page("nas.local")
        self.assertIn("status-restarting", page)
        self.assertIn(">Restarting<", page)
        self.assertIn("TorrServer is restarting. This page will update automatically.", page)
        self.assertIn("./restart-status", page)
        self.assertNotIn("TorrServer is running normally.", page)

    def test_status_page_is_normal_when_not_restarting(self):
        h.RESTART_STATE["started"] = None
        page = h.main_page("nas.local")
        self.assertNotIn("status-restarting", page.split("</style>")[1])
        self.assertNotIn("./restart-status", page)

    def test_status_page_notices_a_stop_or_start_by_itself(self):
        h.RESTART_STATE["started"] = None
        h.is_torrserver_running = lambda: False
        page = h.main_page("nas.local")
        self.assertIn("./server-state", page)
        self.assertIn("var shown = 'stopped';", page)
        h.is_torrserver_running = lambda: True
        self.assertIn("var shown = 'running';", h.main_page("nas.local"))


class RestartTests(unittest.TestCase):
    """The restart needs neither root nor sudo."""

    def run_restart(self, script_exists=True):
        from unittest import mock
        calls = []

        class FakeProcess:
            pid = 4242

        def fake_popen(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return FakeProcess()

        with mock.patch.object(h.os.path, "isfile", return_value=script_exists), \
                mock.patch.object(h.subprocess, "Popen", fake_popen):
            return h.restart_torrserver(), calls

    def test_restart_does_not_use_sudo(self):
        (ok, _), calls = self.run_restart()
        self.assertTrue(ok)
        self.assertEqual(calls[0][0], ["/bin/sh", h.RESTART_SCRIPT])
        self.assertNotIn("sudo", " ".join(calls[0][0]))

    def test_restart_hands_over_the_helper_pid_and_detaches(self):
        _, calls = self.run_restart()
        kwargs = calls[0][1]
        self.assertEqual(kwargs["env"]["TORRSERVER_HELPER_PID"], str(os.getpid()))
        self.assertTrue(kwargs["start_new_session"])

    def test_missing_script_is_reported(self):
        (ok, message), calls = self.run_restart(script_exists=False)
        self.assertFalse(ok)
        self.assertEqual(calls, [])

    def test_restart_script_is_the_shipped_one(self):
        self.assertTrue(h.RESTART_SCRIPT.endswith("/scripts/restart-torrserver"))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "src", "scripts", "restart-torrserver")))


class LogRotationTests(Base):
    def small_limit(self):
        old = h.LOG_MAX_SIZE
        h.LOG_MAX_SIZE = 100
        self.addCleanup(lambda: setattr(h, "LOG_MAX_SIZE", old))

    def test_the_log_is_rotated(self):
        self.small_limit()
        with open(h.TORRSERVER_LOG, "w") as f:
            f.write("x" * 500)
        h.rotate_log_if_needed()
        self.assertEqual(os.path.getsize(h.TORRSERVER_LOG), 0)
        self.assertTrue(os.path.exists(h.TORRSERVER_LOG + ".1"))

    def test_writers_that_append_leave_no_hole_after_rotation(self):
        # TorrServer, the scripts and certificate-helper all append (O_APPEND)
        # to the same file; after copy + truncate the next line must start at 0.
        self.small_limit()
        with open(h.TORRSERVER_LOG, "ab") as writer:
            writer.write(b"x" * 500 + b"\n")
            writer.flush()
            h.rotate_log_if_needed()
            writer.write(b"after rotation\n")
            writer.flush()
        with open(h.TORRSERVER_LOG, "rb") as f:
            self.assertEqual(f.read(), b"after rotation\n")

    def test_the_logs_page_lists_only_existing_copies(self):
        for name in ("TorrServer.log.1", "TorrServer.log.2"):
            if os.path.exists(h.LOG_FILES[name]):
                os.remove(h.LOG_FILES[name])
        self.assertEqual(h.log_options().count("<option"), 1)
        with open(h.LOG_FILES["TorrServer.log.1"], "w") as f:
            f.write("old")
        self.assertEqual(h.log_options().count("<option"), 2)
        self.assertIn("TorrServer.log.1", h.logs_page())

    def test_log_names_whitelisted(self):
        self.assertIsNone(h.get_log_path("../../etc/passwd"))
        self.assertEqual(sorted(h.LOG_FILES), ["TorrServer.log", "TorrServer.log.1", "TorrServer.log.2"])


class LocalizeTests(Base):
    def setUp(self):
        super().setUp()
        h.write_file(h.LANGUAGE_FILE, "ru")

    def test_text_and_safe_attributes_are_translated(self):
        out = h.localize_html('<span>Status</span><input placeholder="Browse">')
        self.assertIn("<span>Статус</span>", out)
        self.assertIn('placeholder="Обзор"', out)

    def test_user_values_and_identifiers_are_untouched(self):
        page = (
            '<input name="torrserver_dir" value="/volume1/Browse/Status">'
            '<a href="./Status" id="Browse">x</a>'
            '<button onclick="openTorrServerBrowser()">Browse</button>'
            '<script>function openTorrServerBrowser(){ var a = "Browse"; }</script>'
            '<img src="data:image/png;base64,AAACPUAAA">'
            '<style>.Status{content:"Browse"}</style>'
        )
        out = h.localize_html(page)
        self.assertIn('value="/volume1/Browse/Status"', out)
        self.assertIn('href="./Status" id="Browse"', out)
        self.assertIn('onclick="openTorrServerBrowser()"', out)
        self.assertIn("function openTorrServerBrowser()", out)
        self.assertIn(">Обзор</button>", out)           # visible text is translated
        self.assertIn('var a = "Обзор"', out)           # JS string literal is translated
        self.assertIn("data:image/png;base64,AAACPUAAA", out)
        self.assertIn('.Status{content:"Browse"}', out)

    def test_markup_keys_are_translated(self):
        key = next(k for k in h.load_locale("en") if "<b>" in k)
        out = h.localize_html("<p>" + key + "</p>")
        self.assertNotIn(key, out)

    def test_english_is_identity(self):
        h.write_file(h.LANGUAGE_FILE, "en")
        page = '<span>Status</span><input value="Browse">'
        self.assertEqual(h.localize_html(page), page)


class HttpTests(Base):
    @classmethod
    def setUpClass(cls):
        # These tests are about request handling, so act as a logged-in admin.
        cls._orig_check = h.check_dsm_session
        h.check_dsm_session = lambda cookie, token, ip, host="": ("admin", "")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), h.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        h.check_dsm_session = cls._orig_check
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, method="GET", data=None, headers=None):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=data,
            method=method, headers=headers or {})
        opener = urllib.request.build_opener(h._NoRedirect())
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def raw(self, payload):
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            s.settimeout(3)
            s.sendall(payload)
            return s.recv(200).split(b"\r\n")[0].decode()

    def test_helper_binds_loopback_only(self):
        self.assertEqual(h.HOST, "127.0.0.1")

    def test_host_header_is_sanitised(self):
        original = h.main_page
        h.main_page = lambda host: "HOST=" + host
        self.addCleanup(lambda: setattr(h, "main_page", original))
        self.assertIn("HOST=nas.local", self.request("/", headers={"Host": "nas.local:5001"})[1])
        self.assertIn("HOST=[fe80::1]", self.request("/", headers={"Host": "[fe80::1]:5001"})[1])
        body = self.request("/", headers={"Host": '"><script>alert(1)</script>'})[1]
        self.assertNotIn("<script>", body)

    def test_csrf_origin_checks(self):
        form = b"language=ru"
        host = {"Host": "nas.local"}
        self.assertEqual(self.request("/language", "POST", form, host)[0], 403)
        self.assertEqual(self.request("/language", "POST", form, dict(host, Origin="https://evil.example"))[0], 403)
        self.assertEqual(self.request("/language", "POST", form, dict(host, Origin="null"))[0], 403)
        self.assertEqual(self.request("/language", "POST", form, dict(host, Origin="https://nas.local:5001"))[0], 302)
        self.assertEqual(self.request("/language", "POST", form, dict(host, Referer="https://nas.local/x"))[0], 302)

    def test_body_limits(self):
        headers = {"Host": "a", "Origin": "http://a"}
        self.assertEqual(self.request("/language", "POST", b"a" * (h.MAX_POST_BYTES + 1), headers)[0], 413)
        self.assertTrue(self.raw(b"POST /language HTTP/1.1\r\nHost: a\r\nOrigin: http://a\r\nContent-Length: abc\r\n\r\n").endswith("400 Bad Request"))
        self.assertTrue(self.raw(b"POST /language HTTP/1.1\r\nHost: a\r\nOrigin: http://a\r\nContent-Length: -5\r\n\r\n").endswith("400 Bad Request"))

    def test_log_name_whitelist(self):
        self.assertEqual(self.request("/read-log?name=../../etc/passwd")[0], 400)


class AuthTests(Base):
    """DSM session check. A stub authenticate.cgi stands in for DSM's."""

    CGI_TEMPLATE = """#!/bin/sh
# Records what the helper passes in, then behaves like DSM's authenticate.cgi:
# prints the user only for the session cookie "id=GOOD" (plus token "TOK" when
# a token is required).
echo "cookie=$HTTP_COOKIE token=$HTTP_X_SYNO_TOKEN addr=$REMOTE_ADDR" >> "{log}"
case "$HTTP_COOKIE" in
  *id=GOOD*)
    if [ -n "{need_token}" ] && [ "$HTTP_X_SYNO_TOKEN" != "{need_token}" ]; then exit 0; fi
    echo "{user}" ;;
  *id=HEADERS*) printf 'Content-Type: text/plain\\n\\n{user}\\n' ;;
esac
"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), h.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        super().setUp()
        h._AUTH_CACHE.clear()
        self.tmp = tempfile.mkdtemp(prefix="cgi-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = os.path.join(self.tmp, "calls.log")
        self.saved = (h.AUTH_CGI_PATHS, h.is_dsm_admin, h.main_page)
        self.addCleanup(lambda: (setattr(h, "AUTH_CGI_PATHS", self.saved[0]),
                                 setattr(h, "is_dsm_admin", self.saved[1]),
                                 setattr(h, "main_page", self.saved[2])))
        h.main_page = lambda host: "SECRET-SETTINGS-PAGE"
        h.is_dsm_admin = lambda user: user == "admin"
        self.install_cgi(user="admin")

    def install_cgi(self, user="admin", need_token=""):
        path = os.path.join(self.tmp, "authenticate.cgi")
        with open(path, "w") as f:
            f.write(self.CGI_TEMPLATE.format(log=self.log, user=user, need_token=need_token))
        os.chmod(path, 0o755)
        h.AUTH_CGI_PATHS = (os.path.join(self.tmp, "missing.cgi"), path)
        h._AUTH_CACHE.clear()

    def get(self, path="/", headers=None, method="GET", data=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     headers=dict({"Host": "nas.local"}, **(headers or {})),
                                     method=method, data=data)
        try:
            with urllib.request.build_opener(h._NoRedirect()).open(req, timeout=5) as r:
                return r.status, r.read().decode(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers

    def calls(self):
        return open(self.log).read() if os.path.exists(self.log) else ""

    # ---- the bug that was reported: no login at all
    def test_no_cookie_gets_nothing(self):
        status, body, _ = self.get()
        self.assertEqual(status, 403)
        self.assertNotIn("SECRET-SETTINGS-PAGE", body)
        self.assertEqual(self.calls(), "")  # not even worth running the CGI

    def test_every_route_and_method_is_protected(self):
        for path in ("/", "/settings", "/logs", "/browse?path=/", "/read-log?name=TorrServer.log",
                     "/download-log?name=TorrServer.log", "/recommendations", "/restart-status", "/server-state"):
            self.assertEqual(self.get(path)[0], 403, path)
        for path in ("/settings", "/restart", "/language"):
            status, _, _ = self.get(path, method="POST", data=b"x=1",
                                    headers={"Origin": "http://nas.local"})
            self.assertEqual(status, 403, path)

    def test_forged_cookie_is_rejected(self):
        self.assertEqual(self.get(headers={"Cookie": "id=FORGED"})[0], 403)

    def test_valid_admin_session_is_served(self):
        status, body, headers = self.get(headers={"Cookie": "id=GOOD"})
        self.assertEqual((status, body), (200, "SECRET-SETTINGS-PAGE"))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'self'", headers["Content-Security-Policy"])

    def test_non_admin_user_is_rejected(self):
        self.install_cgi(user="alice")
        self.assertEqual(self.get(headers={"Cookie": "id=GOOD"})[0], 403)

    def test_denial_page_leaks_nothing(self):
        _, body, _ = self.get()
        self.assertNotIn("authenticate", body.lower())
        self.assertNotIn("/usr/syno", body)

    # ---- fail closed
    def test_missing_cgi_fails_closed(self):
        h.AUTH_CGI_PATHS = (os.path.join(self.tmp, "nope.cgi"),)
        self.assertEqual(self.get(headers={"Cookie": "id=GOOD"})[0], 403)

    def test_crashing_cgi_fails_closed(self):
        path = os.path.join(self.tmp, "boom.cgi")
        with open(path, "w") as f:
            f.write("#!/bin/sh\nexit 3\n")
        os.chmod(path, 0o755)
        h.AUTH_CGI_PATHS = (path,)
        self.assertEqual(self.get(headers={"Cookie": "id=GOOD"})[0], 403)

    def test_hostile_cgi_output_is_not_trusted(self):
        self.assertEqual(h.parse_cgi_user("admin\n"), "admin")
        self.assertEqual(h.parse_cgi_user("admin\nroot"), "admin")      # first line only
        self.assertEqual(h.parse_cgi_user("DOMAIN\\\\bob"), "DOMAIN\\\\bob")
        for evil in ("", "   ", "../../etc", "<script>alert(1)</script>", "a:b", "x" * 200):
            self.assertEqual(h.parse_cgi_user(evil), "", repr(evil))

    # ---- what DSM's CGI actually receives
    def test_cookie_token_and_client_ip_reach_the_cgi(self):
        self.install_cgi(need_token="TOK")
        status, _, _ = self.get(headers={"Cookie": "id=GOOD", "X-SYNO-TOKEN": "TOK", "X-Real-IP": "192.168.1.50"})
        self.assertEqual(status, 200)
        self.assertIn("cookie=id=GOOD token=TOK addr=192.168.1.50", self.calls())

    def test_token_required_by_dsm_is_enforced(self):
        self.install_cgi(need_token="TOK")
        self.assertEqual(self.get(headers={"Cookie": "id=GOOD"})[0], 403)
        self.assertEqual(self.get(headers={"Cookie": "id=GOOD", "X-SYNO-TOKEN": "WRONG"})[0], 403)

    def test_cgi_that_prints_headers_first_is_understood(self):
        self.assertEqual(self.get(headers={"Cookie": "id=HEADERS"})[0], 200)

    def test_spoofed_ip_header_is_sanitised(self):
        self.get(headers={"Cookie": "id=GOOD", "X-Real-IP": "1.2.3.4; rm -rf /"})
        self.assertNotIn("rm -rf", self.calls())

    # ---- SynoToken hand-over from the DSM desktop iframe URL
    def test_token_in_url_becomes_cookie_and_clean_redirect(self):
        self.install_cgi(need_token="TOK")
        status, _, headers = self.get("/settings?SynoToken=TOK", headers={
            "Cookie": "id=GOOD", "X-Forwarded-Proto": "https"})
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "./settings")
        cookie = headers["Set-Cookie"]
        self.assertIn("TorrServerSynoToken=TOK", cookie)
        for flag in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/webman/3rdparty/TorrServer/"):
            self.assertIn(flag, cookie)

    def test_wrong_token_in_url_is_denied_without_cookie(self):
        self.install_cgi(need_token="TOK")
        status, _, headers = self.get("/?SynoToken=WRONG", headers={"Cookie": "id=GOOD"})
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get("Set-Cookie"))

    def test_token_cookie_authenticates_followup_requests(self):
        self.install_cgi(need_token="TOK")
        status, body, _ = self.get("/settings", headers={"Cookie": "id=GOOD; TorrServerSynoToken=TOK"})
        self.assertEqual(status, 200)

    def test_malformed_token_is_ignored(self):
        self.install_cgi(need_token="")
        self.get(headers={"Cookie": "id=GOOD", "X-SYNO-TOKEN": "a b;c"})
        self.assertIn("token= ", self.calls())

    # ---- caching
    def test_valid_sessions_are_cached_briefly(self):
        for _ in range(3):
            self.assertEqual(self.get(headers={"Cookie": "id=GOOD"})[0], 200)
        self.assertEqual(self.calls().count("\n"), 1)

    def test_denials_are_remembered_only_for_a_moment(self):
        # A user who has just logged in must not stay locked out.
        self.assertEqual(self.get(headers={"Cookie": "id=NEW"})[0], 403)
        now = time.monotonic()
        (expires, user, _reason), = h._AUTH_CACHE.values()
        self.assertEqual(user, "")
        self.assertLessEqual(expires - now, h.AUTH_DENIED_CACHE_SECONDS + 0.5)
        self.assertLess(h.AUTH_DENIED_CACHE_SECONDS, h.AUTH_CACHE_SECONDS)

    # ---- admin group membership
    def test_admin_group_membership(self):
        h.is_dsm_admin = self.saved[1]
        import grp, pwd
        real_grp, real_pwd, real_list = grp.getgrnam, pwd.getpwnam, os.getgrouplist
        group = type("G", (), {"gr_mem": ["root_like"], "gr_gid": 101})()
        entry = lambda gid: type("P", (), {"pw_gid": gid})()
        try:
            grp.getgrnam = lambda name: group
            pwd.getpwnam = lambda name: entry(100)
            os.getgrouplist = lambda name, gid: [100, 101] if name == "nested" else [100]
            self.assertTrue(h.is_dsm_admin("root_like"))        # listed member
            self.assertTrue(h.is_dsm_admin("nested"))           # member through NSS
            self.assertFalse(h.is_dsm_admin("alice"))
            pwd.getpwnam = lambda name: entry(101)
            self.assertTrue(h.is_dsm_admin("primary"))          # primary group
            grp.getgrnam = lambda name: (_ for _ in ()).throw(KeyError(name))
            self.assertFalse(h.is_dsm_admin("anyone"))          # no such group: fail closed
        finally:
            grp.getgrnam, pwd.getpwnam, os.getgrouplist = real_grp, real_pwd, real_list


    def test_admin_check_ignores_name_case(self):
        """DSM's authenticate.cgi printed "Vlad" for the account stored as "vlad"."""
        h.is_dsm_admin = self.saved[1]
        import grp, pwd
        real_grp, real_pwd, real_list = grp.getgrnam, pwd.getpwnam, os.getgrouplist
        group = type("G", (), {"gr_mem": ["vlad"], "gr_gid": 101})()

        def only_lowercase(name):
            if name != "vlad":
                raise KeyError(name)
            return type("P", (), {"pw_gid": 100})()

        try:
            grp.getgrnam = lambda name: group
            pwd.getpwnam = only_lowercase
            os.getgrouplist = lambda name, gid: [100, 101] if name == "vlad" else [100]
            self.assertTrue(h.is_dsm_admin("Vlad"))     # listed member, other case
            self.assertTrue(h.is_dsm_admin("VLAD"))
            # The group list alone must be enough (no passwd entry, no NSS):
            pwd.getpwnam = lambda name: (_ for _ in ()).throw(KeyError(name))
            self.assertTrue(h.is_dsm_admin("Vlad"))
            self.assertFalse(h.is_dsm_admin("Someone"))
            pwd.getpwnam = only_lowercase
            group.gr_mem = []
            self.assertTrue(h.is_dsm_admin("Vlad"))     # member only through NSS
            os.getgrouplist = lambda name, gid: [100]
            self.assertFalse(h.is_dsm_admin("Vlad"))    # case folding must not grant access
            self.assertFalse(h.is_dsm_admin("Someone"))
        finally:
            grp.getgrnam, pwd.getpwnam, os.getgrouplist = real_grp, real_pwd, real_list

    def test_session_of_mixed_case_admin_is_accepted_end_to_end(self):
        self.install_cgi(user="Vlad")
        h.is_dsm_admin = lambda user: user.lower() == "vlad"
        self.assertEqual(self.get(headers={"Cookie": "id=GOOD"})[0], 200)



class StatusPortRowsTests(Base):
    """The status card shows one row per protocol: its port, or Disabled."""

    def page(self, https, force, http_port=8090, https_port=8091):
        saved = (h.get_https_enabled, h.get_force_https, h.get_https_port, h.get_port)
        h.get_https_enabled = lambda: https
        h.get_force_https = lambda: force
        h.get_https_port = lambda: https_port
        h.get_port = lambda: http_port
        try:
            return h.main_page("nas.local")
        finally:
            (h.get_https_enabled, h.get_force_https, h.get_https_port, h.get_port) = saved

    def rows(self, page):
        import re
        return dict(re.findall(r"<td>(HTTPS?)</td>\s*<td>([^<]*)</td>", page))

    def test_http_only(self):
        self.assertEqual(self.rows(self.page(False, False)), {"HTTP": "8090", "HTTPS": "Disabled"})

    def test_http_and_https(self):
        self.assertEqual(self.rows(self.page(True, False)), {"HTTP": "8090", "HTTPS": "8091"})

    def test_https_only_shows_http_as_disabled(self):
        self.assertEqual(self.rows(self.page(True, True)), {"HTTP": "Disabled", "HTTPS": "8091"})

    def test_force_flag_is_ignored_while_https_is_off(self):
        self.assertEqual(self.rows(self.page(False, True)), {"HTTP": "8090", "HTTPS": "Disabled"})


class MemoryTests(Base):
    def meminfo(self, text):
        path = os.path.join(VAR, "meminfo")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_used_and_total(self):
        path = self.meminfo("MemTotal:  8388608 kB\nMemFree: 100 kB\nMemAvailable: 6291456 kB\n")
        self.assertEqual(h.get_memory_text(path), "2.0 / 8.0 GB")

    def test_unreadable_gives_dash(self):
        self.assertEqual(h.get_memory_text(os.path.join(VAR, "missing")), "-")
        self.assertEqual(h.get_memory_text(self.meminfo("nonsense\n")), "-")

    def test_status_page_shows_memory(self):
        page = h.main_page("nas.local")
        self.assertIn("<td>Memory</td>", page)


class CardIconTests(Base):
    def titles(self, page):
        import re
        return re.findall(r'<span class="metric-icon[^"]*">(.*?)</span>\s*<span>([^<]*)</span>', page, re.S)

    def test_every_card_title_has_an_svg_icon_and_no_leftover_marker(self):
        pages = [h.main_page("nas.local"), h.settings_page()]
        for page in pages:
            self.assertNotIn("data-icon", page)
            titles = self.titles(page)
            self.assertTrue(titles)
            for icon, name in titles:
                self.assertTrue(icon.startswith("<svg"), name)

    def test_icons_are_drawn_in_the_text_colour(self):
        out = h.inline_icons('<span class="metric-icon" data-icon="lock"></span>')
        self.assertIn("<svg", out)
        self.assertNotIn("data-icon", out)
        self.assertNotIn("🔒", out)

    def test_all_markers_in_the_source_have_a_drawing(self):
        import re
        src = open(h.__file__, encoding="utf-8").read()
        for name in set(re.findall(r'data-icon="([a-z]+)"', src)):
            self.assertIn(name, h.CARD_ICONS)


class StatusDirectoryTests(Base):
    def rows(self, directory, fuse):
        import re
        saved = (h.get_torrserver_dir, h.read_file)
        h.get_torrserver_dir = lambda: directory
        real = h.read_file
        h.read_file = lambda path, default="": ("1" if fuse else "0") if path == h.FUSE_FILE else real(path, default)
        try:
            page = h.main_page("nas.local")
        finally:
            h.get_torrserver_dir, h.read_file = saved
        return dict(re.findall(r"<td>(Directory|FUSE)</td>\s*<td[^>]*>([^<]*)</td>", page))

    def test_directory_and_fuse_rows(self):
        self.assertEqual(self.rows("/volume1/docker/PlexTorr", True),
                         {"Directory": "/volume1/docker/PlexTorr", "FUSE": "Enabled"})

    def test_no_directory_says_not_set(self):
        self.assertEqual(self.rows("", False), {"Directory": "Not set", "FUSE": "Disabled"})

    def test_directory_is_escaped(self):
        self.assertEqual(self.rows("/a/<b>", False)["Directory"], "/a/&lt;b&gt;")


class SidebarAndLayoutTests(Base):
    def test_every_page_uses_the_same_svg_sidebar(self):
        for page in (h.main_page("nas.local"), h.settings_page(), h.logs_page()):
            self.assertEqual(page.count('class="side-item'), 3)
            self.assertEqual(page.count('class="side-icon"><svg'), 3)
            for glyph in "▥⚙▤":
                self.assertNotIn(glyph, page)

    def test_save_bar_is_sticky_and_language_is_one_compact_row(self):
        page = h.settings_page()
        self.assertIn("save-bar", page)
        self.assertIn("language-card", page)
        self.assertEqual(page.count('<button type="submit">Save'), 1)
        card = page[page.index('<div class="settings-card language-card">'):page.index("</form>")]
        self.assertNotIn("settings-card-title", card)     # no card title, only the row label
        self.assertIn("<svg", card)                       # ...with the same icon tile as the other cards


class OutsideTextIsNotTranslatedTests(Base):
    """Log lines and folder names are data: a key like "Start" must not touch them."""

    def setUp(self):
        super().setUp()
        self.saved = (h.load_locale, h.is_torrserver_running, h.TORRSERVER_LOG, h.get_torrserver_dir)
        h.load_locale = lambda: {"Start": "Запустить", "Status": "Статус", "Disabled": "Выключено"}
        h.is_torrserver_running = lambda: False
        h.TORRSERVER_LOG = os.path.join(VAR, "TorrServer.log")
        self.addCleanup(lambda: (setattr(h, "load_locale", self.saved[0]),
                                 setattr(h, "is_torrserver_running", self.saved[1]),
                                 setattr(h, "TORRSERVER_LOG", self.saved[2]),
                                 setattr(h, "get_torrserver_dir", self.saved[3])))

    def test_marked_text_is_left_alone_and_the_rest_is_translated(self):
        out = h.localize_html('<button>Start</button><pre translate="no">Starting Status</pre><p>Status</p>')
        self.assertIn("<button>Запустить</button>", out)
        self.assertIn('<pre translate="no">Starting Status</pre>', out)
        self.assertIn("<p>Статус</p>", out)

    def test_log_lines_on_the_status_page(self):
        with open(h.TORRSERVER_LOG, "w") as f:
            f.write("UTC0 service: Starting TorrServer\n")
        page = h.main_page("nas.local")
        self.assertIn("service: Starting TorrServer", page)
        self.assertNotIn("Запуститьing", page)

    def test_folder_names_on_the_status_page(self):
        h.get_torrserver_dir = lambda: "/volume1/Status/Start"
        page = h.main_page("nas.local")
        self.assertIn("/volume1/Status/Start", page)


if __name__ == "__main__":
    unittest.main()
