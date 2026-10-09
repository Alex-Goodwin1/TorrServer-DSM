"""Unit tests for src/helper/helper.py (no DSM required).

Run:  python3 -m unittest discover -s tests -v
"""
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
        h._PRIVILEGE_CACHE["time"] = 0.0


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

    def test_volume_path_validation(self):
        self.assertTrue(h.valid_volume_path("/volume1/certs/a.pem"))
        self.assertFalse(h.valid_volume_path("/volume1/../etc/shadow"))
        self.assertFalse(h.valid_volume_path("/etc/shadow"))


class SaveSettingsTests(Base):
    BASE = dict(port="8090", torrserver_dir="/volume1/TS", https="0",
                https_port="8091", ssl_mode="self", auth="0")

    def setUp(self):
        super().setUp()
        self.ts = FakeTorrServer()
        self.addCleanup(self.ts.close)
        self.patches = {
            "has_privileged_access": lambda use_cache=True: True,
            "prepare_torrserver_directory": lambda d: (True, ""),
            "get_dsm_certificates": lambda: [],
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
            dict(ssl_mode="manual", ssl_cert="/volume1/../etc/shadow", ssl_key="/volume1/k"),
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

    def test_settings_save_without_any_extra_permissions(self):
        """The common case: no sudo rule, self-signed certificate."""
        h.has_privileged_access = lambda use_cache=True: False
        ok, message = self.save()
        self.assertTrue(ok, message)
        self.assertEqual(h.read_file(h.PORT_FILE), "8090")

    def test_every_non_certificate_option_saves_without_permissions(self):
        h.has_privileged_access = lambda use_cache=True: False
        ok, message = self.save(port="9090", https="1", https_port="9443", force_https="1",
                                fuse="1", auth="1", username="alice", password="pw")
        self.assertTrue(ok, message)
        self.assertEqual(h.read_file(h.HTTPS_FILE), "1")
        self.assertEqual(h.read_file(h.FUSE_FILE), "1")
        self.assertEqual(json.load(open(h.ACCS_FILE)), {"alice": "pw"})

    def test_dsm_and_manual_certificates_need_the_optional_permissions(self):
        h.has_privileged_access = lambda use_cache=True: False
        h.get_dsm_certificates = lambda: [{"cert": "/usr/syno/c.pem", "key": "/usr/syno/k.pem", "label": "x"}]
        for mode, cert, key in (("dsm", "/usr/syno/c.pem", "/usr/syno/k.pem"),
                                ("manual", "/volume1/c.pem", "/volume1/k.pem")):
            ok, message = self.save(ssl_mode=mode, ssl_cert=cert, ssl_key=key)
            self.assertFalse(ok, mode)
            self.assertIn("DSM permissions", message)
        self.assertFalse(os.path.exists(h.PORT_FILE), "nothing may be written")

    def test_certificates_work_once_permissions_exist(self):
        h.has_privileged_access = lambda use_cache=True: True
        ok, message = self.save(ssl_mode="manual", ssl_cert="/volume1/c.pem", ssl_key="/volume1/k.pem")
        self.assertTrue(ok, message)

    def test_a_directory_the_service_user_cannot_write_is_explained(self):
        h.has_privileged_access = lambda use_cache=True: False
        h.prepare_torrserver_directory = lambda d: (False, h.NOT_WRITABLE)
        ok, message = self.save()
        self.assertEqual((ok, message), (False, h.NOT_WRITABLE))
        self.assertFalse(os.path.exists(h.PORT_FILE))

    def test_saves_when_torrserver_is_down(self):
        self.ts.close()
        h.get_port = lambda: free_port()
        ok, message = self.save()
        self.assertTrue(ok, message)
        self.assertEqual(h.read_file(h.CACHE_PENDING_FILE), "/volume1/TS/Cache")


class PrivilegeTests(Base):
    """The Helper must notice a sudoers rule that covers only some scripts."""

    DENIED = "sudo: a password is required\n"
    USAGE = 64   # prepare-directory's exit code for "no directory given"

    def setUp(self):
        super().setUp()
        self.calls = []
        self.real_run, self.real_isfile = h.subprocess.run, os.path.isfile
        self.addCleanup(lambda: (setattr(h.subprocess, "run", self.real_run),
                                 setattr(os.path, "isfile", self.real_isfile)))
        os.path.isfile = lambda p: True

    def fake_sudo(self, cert=(0, ""), prepare=(64, "")):
        def run(cmd, **kwargs):
            self.calls.append(cmd)
            rc, err = cert if cmd[-1] == h.CERTIFICATE_HELPER else prepare
            return type("R", (), {"returncode": rc, "stderr": err, "stdout": ""})()
        h.subprocess.run = run

    def test_exit_code_matches_the_script(self):
        script = open(os.path.join(ROOT, "src", "scripts", "prepare-directory")).read()
        self.assertEqual(h.PREPARE_USAGE_EXIT, self.USAGE)
        self.assertIn("exit %d" % self.USAGE, script)

    def test_fully_configured(self):
        self.fake_sudo(cert=(0, ""), prepare=(self.USAGE, ""))
        self.assertTrue(h.has_privileged_access(use_cache=False))

    def test_old_rule_without_prepare_directory_is_detected(self):
        """The reported case: certificate-helper is allowed, prepare-directory is not."""
        self.fake_sudo(cert=(0, ""), prepare=(1, self.DENIED))
        self.assertFalse(h.has_privileged_access(use_cache=False))

    def test_no_rule_at_all(self):
        self.fake_sudo(cert=(1, self.DENIED), prepare=(1, self.DENIED))
        self.assertFalse(h.has_privileged_access(use_cache=False))

    def test_certificate_helper_denied_only(self):
        self.fake_sudo(cert=(1, "sudo: sorry, you are not allowed\n"), prepare=(self.USAGE, ""))
        self.assertFalse(h.has_privileged_access(use_cache=False))

    def test_harmless_sudo_warnings_do_not_lock_the_helper(self):
        """sudo may print notices on stderr even when it runs the command."""
        warning = "sudo: unable to resolve host SYNONAS\n"
        self.fake_sudo(cert=(0, warning), prepare=(self.USAGE, warning))
        self.assertTrue(h.has_privileged_access(use_cache=False))

    def test_localized_refusal_is_still_a_refusal(self):
        self.fake_sudo(cert=(0, ""), prepare=(1, "sudo: требуется пароль\n"))
        self.assertFalse(h.has_privileged_access(use_cache=False))

    def test_unexpected_exit_code_is_not_trusted(self):
        self.fake_sudo(cert=(0, ""), prepare=(1, ""))      # e.g. an old script version
        self.assertFalse(h.has_privileged_access(use_cache=False))
        self.fake_sudo(cert=(0, ""), prepare=(0, ""))
        self.assertFalse(h.has_privileged_access(use_cache=False))

    def test_the_check_never_prompts_and_never_restarts(self):
        self.fake_sudo()
        h.has_privileged_access(use_cache=False)
        self.assertTrue(all(h.RESTART_SCRIPT not in cmd for cmd in self.calls))
        self.assertTrue(all("-n" in cmd for cmd in self.calls), "sudo must never prompt")

    def test_result_is_cached(self):
        self.fake_sudo()
        h._PRIVILEGE_CACHE["time"] = 0.0
        h.has_privileged_access()
        count = len(self.calls)
        h.has_privileged_access()
        self.assertEqual(len(self.calls), count)

    def patch(self, name, value):
        old = getattr(h, name)
        setattr(h, name, value)
        self.addCleanup(setattr, h, name, old)

    def test_raw_sudo_error_becomes_actionable_message(self):
        self.patch("prepare_directory_directly", lambda d: False)     # service user cannot write
        self.patch("has_privileged_access", lambda use_cache=True: True)
        self.fake_sudo(prepare=(1, self.DENIED))
        ok, message = h.prepare_torrserver_directory("/volume1/TS")
        self.assertFalse(ok)
        self.assertNotIn("a password is required", message)
        self.assertIn("setup-permissions", message)

    def test_real_script_errors_are_passed_through(self):
        self.patch("prepare_directory_directly", lambda d: False)
        self.patch("has_privileged_access", lambda use_cache=True: True)
        self.fake_sudo(prepare=(1, "Invalid TorrServer directory.\n"))
        self.assertEqual(h.prepare_torrserver_directory("/etc"), (False, "Invalid TorrServer directory."))

    def test_empty_directory_tells_the_user_what_to_do(self):
        self.fake_sudo()
        ok, message = h.prepare_torrserver_directory("")
        self.assertFalse(ok)
        self.assertIn("Browse", message)


class DirectoryTests(Base):
    """Cache/FUSE are created as the service user; root is only a fallback."""

    def setUp(self):
        super().setUp()
        self.base = tempfile.mkdtemp(prefix="ts-dir-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.sudo_calls = []
        self.privileged = False
        self.real = (h.subprocess.run, h.has_privileged_access, os.mkdir, os.access)
        h.has_privileged_access = lambda use_cache=True: self.privileged
        # The helper checks that the root script exists. Do not rely on the
        # machine running the tests having the package installed.
        tools = tempfile.mkdtemp(prefix="ts-tools-")
        self.addCleanup(shutil.rmtree, tools, True)
        self.script = os.path.join(tools, "prepare-directory")
        with open(self.script, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(self.script, 0o755)
        self.real_script = h.PREPARE_DIRECTORY
        h.PREPARE_DIRECTORY = self.script
        self.addCleanup(setattr, h, "PREPARE_DIRECTORY", self.real_script)

        def run(cmd, **kwargs):
            self.sudo_calls.append(cmd)
            return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        h.subprocess.run = run
        self.addCleanup(lambda: (setattr(h.subprocess, "run", self.real[0]),
                                 setattr(h, "has_privileged_access", self.real[1]),
                                 setattr(os, "mkdir", self.real[2]),
                                 setattr(os, "access", self.real[3])))

    def deny_creation(self):
        def mkdir(path, mode=0o777, **kw):
            raise PermissionError(13, "Permission denied", path)
        os.mkdir = mkdir

    def test_no_root_needed_when_the_service_user_can_write(self):
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertEqual((ok, message), (True, ""))
        for name in ("Cache", "FUSE"):
            self.assertTrue(os.path.isdir(os.path.join(self.base, name)), name)
        self.assertEqual(self.sudo_calls, [], "sudo must not be involved")

    def test_existing_writable_directories_are_accepted(self):
        for name in ("Cache", "FUSE"):
            os.mkdir(os.path.join(self.base, name))
        self.assertEqual(h.prepare_torrserver_directory(self.base), (True, ""))
        self.assertEqual(self.sudo_calls, [])

    def test_not_writable_without_permissions_explains_the_standard_dsm_fix(self):
        self.deny_creation()
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertFalse(ok)
        self.assertEqual(message, h.NOT_WRITABLE)
        self.assertIn("System internal user", message)
        self.assertEqual(self.sudo_calls, [], "never call sudo when it is not configured")

    def test_not_writable_with_permissions_falls_back_to_root(self):
        self.deny_creation()
        self.privileged = True
        ok, _ = h.prepare_torrserver_directory(self.base)
        self.assertTrue(ok)
        (cmd,) = self.sudo_calls
        self.assertEqual(cmd, ["/bin/sudo", "-n", self.script, self.base])

    def test_missing_root_script_is_reported_without_calling_sudo(self):
        self.deny_creation()
        self.privileged = True
        os.unlink(self.script)
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertFalse(ok)
        self.assertEqual(message, "Directory preparation script not found")
        self.assertEqual(self.sudo_calls, [])

    def test_existing_directory_that_is_not_writable_needs_root(self):
        for name in ("Cache", "FUSE"):
            os.mkdir(os.path.join(self.base, name))
        os.access = lambda path, mode: False
        self.assertEqual(h.prepare_torrserver_directory(self.base), (False, h.NOT_WRITABLE))

    def test_symlinks_are_refused_even_with_root_available(self):
        self.privileged = True
        os.symlink("/etc", os.path.join(self.base, "Cache"))
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertFalse(ok)
        self.assertIn("symbolic link", message)
        self.assertEqual(self.sudo_calls, [])

    def test_a_file_where_a_directory_belongs_is_refused(self):
        open(os.path.join(self.base, "FUSE"), "w").close()
        ok, message = h.prepare_torrserver_directory(self.base)
        self.assertFalse(ok)
        self.assertIn("not a directory", message)

    def test_missing_parent_is_reported_not_sent_to_root(self):
        self.privileged = True
        ok, message = h.prepare_torrserver_directory(os.path.join(self.base, "missing"))
        self.assertFalse(ok)
        self.assertIn("Failed to create", message)
        self.assertEqual(self.sudo_calls, [])

    def test_concurrent_creation_is_tolerated(self):
        real = os.mkdir
        def racing(path, mode=0o777, **kw):
            real(path, mode)
            raise FileExistsError(17, "File exists", path)
        os.mkdir = racing
        self.assertEqual(h.prepare_torrserver_directory(self.base), (True, ""))


class FormTests(Base):
    """What the settings form lets an ordinary (no extra permissions) user do."""

    def render(self, privileged):
        self.saved = (h.has_privileged_access, h.get_dsm_certificates)
        self.addCleanup(lambda: (setattr(h, "has_privileged_access", self.saved[0]),
                                 setattr(h, "get_dsm_certificates", self.saved[1])))
        h.has_privileged_access = lambda use_cache=True: privileged
        h.get_dsm_certificates = lambda: []
        return h.settings_page()

    def tag(self, page, pattern):
        match = re.search(pattern, page)
        self.assertIsNotNone(match, pattern)
        return match.group(0)

    def test_nothing_that_needs_no_root_is_locked(self):
        page = self.render(privileged=False)
        for pattern in (r"<fieldset[^>]*>", r'<input[^>]*name="port"[^>]*>',
                        r'<input[^>]*name="torrserver_dir"[^>]*>', r'<input[^>]*name="fuse"[^>]*>',
                        r'<input[^>]*name="https"[^>]*>', r'<option value="self"[^>]*>',
                        r'<input[^>]*name="auth"[^>]*>', r'<button type="submit"[^>]*>Save',
                        r'<button[^>]*formaction="./restart"[^>]*>'):
            self.assertNotIn("disabled", self.tag(page, pattern), pattern)

    def test_root_only_choices_are_locked_without_permissions(self):
        page = self.render(privileged=False)
        for pattern in (r'<option value="dsm"[^>]*>', r'<option value="manual"[^>]*>'):
            self.assertIn("disabled", self.tag(page, pattern), pattern)

    def test_everything_is_available_with_permissions(self):
        page = self.render(privileged=True)
        for pattern in (r'<option value="dsm"[^>]*>', r'<option value="manual"[^>]*>',
                        r'<button[^>]*formaction="./restart"[^>]*>', r"<fieldset[^>]*>"):
            self.assertNotIn("disabled", self.tag(page, pattern), pattern)



class PermissionsInSettingsTests(FormTests):
    """The permission help lives inside the certificate card; there is no extra window."""

    def test_no_separate_permissions_window(self):
        page = self.render(privileged=False)
        self.assertNotIn("openPermissions", page)
        self.assertNotIn("Setup permissions", page)
        self.assertFalse(hasattr(h, "permissions_page"))

    def test_without_permissions_the_help_is_in_the_certificate_card(self):
        page = self.render(privileged=False)
        card = page[page.index("SSL Certificate</span>"):]
        card = card[:card.index('<button type="submit">Save')]
        for text in ("/var/packages/TorrServer/scripts/setup-permissions", "Task Scheduler",
                     "reverse proxy", "Check permissions", "<details"):
            self.assertIn(text, card)

    def test_with_permissions_only_a_short_confirmation_is_shown(self):
        page = self.render(privileged=True)
        self.assertIn("Extended DSM permissions are configured.", page)
        self.assertNotIn("setup-permissions", page)
        self.assertNotIn("permission-help\"", page.split("</style>")[1])

    def test_the_restart_button_needs_no_permissions(self):
        # render() patches module state and must be called once per test.
        tag = self.tag(self.render(privileged=False), r'<button[^>]*formaction="./restart"[^>]*>')
        self.assertNotIn("disabled", tag)


class SettingsLayoutTests(FormTests):
    """Order of the cards and the inline FUSE / disk cache help."""

    def titles(self, page):
        return re.findall(r'<div class="settings-card-title">\s*<span class="metric-icon">[^<]*</span>\s*<span>([^<]*)</span>', page)

    def test_authentication_comes_before_the_certificate_and_the_buttons_come_last(self):
        page = self.render(privileged=False)
        titles = self.titles(page)
        self.assertLess(titles.index("Authentication"), titles.index("SSL Certificate"))
        self.assertLess(page.index("SSL Certificate</span>"), page.index('<button type="submit">Save'))
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
        self.saved = (h.is_torrserver_running, h.TORRSERVER_LOG, h.SERVICE_LOG, dict(h.RESTART_STATE))
        h.is_torrserver_running = lambda: False
        h.TORRSERVER_LOG = os.path.join(VAR, "TorrServer.log")
        h.SERVICE_LOG = os.path.join(VAR, "service.log")
        h.RESTART_STATE["started"] = None
        self.addCleanup(lambda: (setattr(h, "is_torrserver_running", self.saved[0]),
                                 setattr(h, "TORRSERVER_LOG", self.saved[1]),
                                 setattr(h, "SERVICE_LOG", self.saved[2]),
                                 h.RESTART_STATE.update(self.saved[3])))

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
        self.assertRegex(page, r'<form method="post" action="./restart"><button type="submit">Start</button>')

    def test_the_last_lines_of_the_newest_log_are_shown(self):
        self.write(h.TORRSERVER_LOG, "old one\nold two\n", age=100)
        self.write(h.SERVICE_LOG, "a\nb\n\nlisten tcp :8090: bind: address already in use\nexit status 1\n")
        lines = h.last_problem_lines()
        self.assertEqual(lines[-2:], ["listen tcp :8090: bind: address already in use", "exit status 1"])
        self.assertEqual(len(lines), 3)
        self.assertNotIn("old one", "".join(lines))
        page = h.main_page("nas.local")
        self.assertIn("address already in use", page)
        self.assertIn("Last lines of the log:", page)

    def test_log_text_is_escaped_and_cut(self):
        self.write(h.SERVICE_LOG, "<script>alert(1)</script>" + "x" * 500 + "\n")
        self.assertTrue(all(len(line) <= 220 for line in h.last_problem_lines()))
        page = h.main_page("nas.local")
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;script&gt;", page)

    def test_no_logs_means_no_log_block(self):
        self.assertEqual(h.last_problem_lines(), [])
        self.assertNotIn("Last lines of the log:", h.main_page("nas.local"))

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
    def test_both_logs_rotated(self):
        old = h.LOG_MAX_SIZE
        h.LOG_MAX_SIZE = 100
        self.addCleanup(lambda: setattr(h, "LOG_MAX_SIZE", old))
        for path in h.ROTATED_LOGS:
            with open(path, "w") as f:
                f.write("x" * 500)
        h.rotate_log_if_needed()
        for path in h.ROTATED_LOGS:
            self.assertEqual(os.path.getsize(path), 0)
            self.assertTrue(os.path.exists(path + ".1"))

    def test_log_names_whitelisted(self):
        self.assertIsNone(h.get_log_path("../../etc/passwd"))
        self.assertIn("service.log", h.LOG_FILES)


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
                     "/download-log?name=TorrServer.log", "/recommendations", "/restart-status"):
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

if __name__ == "__main__":
    unittest.main()
