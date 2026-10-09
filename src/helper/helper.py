#!/usr/bin/env python3

import base64
import grp
import hashlib
import html
import json
import os
import platform
import pwd
import re
import shutil
import subprocess
import sys
import time
import threading
import ssl
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote


# The helper is only reachable through DSM nginx (see nginx/TorrServer.conf),
# which proxies to 127.0.0.1. It must never listen on a public interface: it
# has no authentication of its own.
HOST = "127.0.0.1"
HELPER_PORT = 42777

MAX_POST_BYTES = 64 * 1024
USERNAME_MAX_LENGTH = 64

# TORRSERVER_DSM_VAR exists for the test suite; DSM never sets it.
PACKAGE_VAR = os.environ.get("TORRSERVER_DSM_VAR") or "/var/packages/TorrServer/var"
TORRSERVER_BIN = "/var/packages/TorrServer/target/bin/TorrServer"
TORRSERVER_LOG = os.path.join(PACKAGE_VAR, "TorrServer.log")

SERVICE_LOG = os.path.join(PACKAGE_VAR, "service.log")

LOG_FILES = {
    "TorrServer.log": TORRSERVER_LOG,
    "TorrServer.log.1": TORRSERVER_LOG + ".1",
    "TorrServer.log.2": TORRSERVER_LOG + ".2",
    "service.log": SERVICE_LOG,
}
ROTATED_LOGS = (TORRSERVER_LOG, SERVICE_LOG)
LOG_MAX_SIZE = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 2

PORT_FILE = os.path.join(PACKAGE_VAR, "torrserver.port")
AUTH_FILE = os.path.join(PACKAGE_VAR, "torrserver.auth")
ACCS_FILE = os.path.join(PACKAGE_VAR, "accs.db")
TORRSERVER_DIR_FILE = os.path.join(PACKAGE_VAR, "torrserver.dir")
# Cache directory chosen while TorrServer was not running; applied by
# pending_cache_loop() as soon as the API answers.
CACHE_PENDING_FILE = os.path.join(PACKAGE_VAR, "cache.pending")
FUSE_FILE = os.path.join(PACKAGE_VAR, "torrserver.fuse")
HTTPS_FILE = os.path.join(PACKAGE_VAR, "torrserver.https")
HTTPS_PORT_FILE = os.path.join(PACKAGE_VAR, "torrserver.https.port")
FORCE_HTTPS_FILE = os.path.join(PACKAGE_VAR, "torrserver.force.https")
SSL_MODE_FILE = os.path.join(PACKAGE_VAR, "torrserver.ssl.mode")
SSL_CERT_FILE = os.path.join(PACKAGE_VAR, "torrserver.ssl.cert")
SSL_KEY_FILE = os.path.join(PACKAGE_VAR, "torrserver.ssl.key")

HELPER_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_LOGO_FILE = os.path.join(HELPER_DIR, "torrserver-status.png")

SSL_CERT_MODE_SELF = "self"
SSL_CERT_MODE_DSM = "dsm"
SSL_CERT_MODE_MANUAL = "manual"

RESTART_SCRIPT = "/var/packages/TorrServer/scripts/restart-torrserver"
RESTART_LOCK = os.path.join(PACKAGE_VAR, "restart.lock")
RESTART_MIN_SECONDS = 3
RESTART_GIVE_UP_SECONDS = 90
RESTART_STATE = {"started": None}
CERTIFICATE_HELPER = "/var/packages/TorrServer/scripts/certificate-helper"
PREPARE_DIRECTORY = "/var/packages/TorrServer/scripts/prepare-directory"

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
LANGUAGE_FILE = os.path.join(PACKAGE_VAR, "helper.language")
SUPPORTED_LANGUAGES = ("en", "ru", "lt", "pl", "uk")
LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Русский",
    "lt": "Lietuvių",
    "pl": "Polski",
    "uk": "Українська",
}


# ---------------------------------------------------------------------------
# Authentication
#
# nginx proxies /webman/3rdparty/TorrServer/helper/ to this server WITHOUT any
# DSM login check, so the helper must verify the DSM session itself. It asks
# DSM's own authenticate.cgi (the documented way for third-party CGIs) who the
# caller is and only serves DSM administrators. Everything fails closed.
# ---------------------------------------------------------------------------
AUTH_CGI_PATHS = (
    "/usr/syno/synoman/webman/modules/authenticate.cgi",
    "/usr/syno/synoman/webman/authenticate.cgi",
)
ADMIN_GROUP = "administrators"
AUTH_CACHE_SECONDS = 15
AUTH_DENIED_CACHE_SECONDS = 2
AUTH_CACHE_MAX = 256

# The DSM desktop passes its CSRF token (SynoToken) in the iframe URL; it is
# kept in a cookie scoped to the helper so later links and form posts carry it.
TOKEN_COOKIE = "TorrServerSynoToken"
TOKEN_COOKIE_PATH = "/webman/3rdparty/TorrServer/"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_\-+/=.]{1,256}$")
USER_PATTERN = re.compile(r"^[\w.@\\ -]{1,128}$")

_AUTH_CACHE = {}
_AUTH_CACHE_LOCK = threading.Lock()


def auth_log(message):
    try:
        print("helper-auth: {}".format(message), file=sys.stderr, flush=True)
    except Exception:
        pass


def parse_cgi_user(output):
    """Extract the user name printed by authenticate.cgi."""
    text = (output or "").replace("\r\n", "\n").strip()

    if not text:
        return ""

    # Some builds print CGI headers first.
    first = text.split("\n", 1)[0]
    if ":" in first and "\n\n" in text:
        text = text.split("\n\n", 1)[1].strip()

    line = text.split("\n", 1)[0].strip()
    return line if USER_PATTERN.match(line) else ""


def run_authenticate_cgi(cookie, token, remote_addr, host):
    """Return ``(user, reason)``; user is "" when the session is not valid."""
    reason = "authenticate.cgi not found"

    for path in AUTH_CGI_PATHS:
        if not os.access(path, os.X_OK):
            continue

        env = {
            "PATH": "/usr/syno/bin:/usr/syno/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
            "HTTP_COOKIE": cookie,
            "REMOTE_ADDR": remote_addr,
            "HTTP_HOST": host,
            "REQUEST_METHOD": "GET",
            "SERVER_PROTOCOL": "HTTP/1.1",
        }
        if token:
            env["HTTP_X_SYNO_TOKEN"] = token

        try:
            result = subprocess.run(
                [path],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                universal_newlines=True,
            )
        except Exception as e:
            reason = "{} failed: {}".format(os.path.basename(path), e)
            continue

        user = parse_cgi_user(result.stdout)
        if user:
            return user, ""

        reason = "{} rc={} returned no user (stdout {} bytes, stderr {} bytes)".format(
            path, result.returncode, len(result.stdout or ""), len(result.stderr or "")
        )

    return "", reason


def is_dsm_admin(username):
    """True when *username* belongs to the DSM administrators group.

    DSM user names are case-insensitive: authenticate.cgi may print "Vlad"
    while the account is stored as "vlad", so both spellings are tried.
    """
    try:
        group = grp.getgrnam(ADMIN_GROUP)
    except KeyError:
        return False

    wanted = username.lower()

    if any(member.lower() == wanted for member in group.gr_mem):
        return True

    entry = None
    system_name = username

    for candidate in dict.fromkeys((username, wanted)):
        try:
            entry = pwd.getpwnam(candidate)
            system_name = candidate
            break
        except KeyError:
            continue

    if entry is None:
        return False

    if entry.pw_gid == group.gr_gid:
        return True

    try:
        return group.gr_gid in os.getgrouplist(system_name, entry.pw_gid)
    except OSError:
        return False


def check_dsm_session(cookie, token, remote_addr, host=""):
    """Return ``(admin_user, reason)``; admin_user is "" when access is denied."""
    if not cookie:
        return "", "no session cookie"

    key = hashlib.sha256(
        "\0".join((cookie, token, remote_addr)).encode("utf-8", "replace")
    ).hexdigest()
    now = time.monotonic()

    with _AUTH_CACHE_LOCK:
        cached = _AUTH_CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1], cached[2]

    user, reason = run_authenticate_cgi(cookie, token, remote_addr, host)

    if user and not is_dsm_admin(user):
        reason = "user '{}' is not a DSM administrator".format(user)
        user = ""

    lifetime = AUTH_CACHE_SECONDS if user else AUTH_DENIED_CACHE_SECONDS

    with _AUTH_CACHE_LOCK:
        if len(_AUTH_CACHE) >= AUTH_CACHE_MAX:
            for stale in [k for k, v in _AUTH_CACHE.items() if v[0] <= now]:
                _AUTH_CACHE.pop(stale, None)
            if len(_AUTH_CACHE) >= AUTH_CACHE_MAX:
                _AUTH_CACHE.clear()
        _AUTH_CACHE[key] = (now + lifetime, user, reason)

    return user, reason


def get_language():
    value = read_file(LANGUAGE_FILE, "en").strip().lower()
    return value if value in SUPPORTED_LANGUAGES else "en"


def set_language(language):
    language = str(language or "").strip().lower()
    if language not in SUPPORTED_LANGUAGES:
        return False
    write_file(LANGUAGE_FILE, language)
    return True


def load_locale(language=None):
    language = language or get_language()
    path = os.path.join(LOCALE_DIR, language + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_HTML_TOKEN_RE = re.compile(
    r"(<script\b[^>]*>)(.*?)(</script>)"      # 1-3: script open / body / close
    r"|<style\b.*?</style>"                   # style: never translated
    r"|<!--.*?-->"                             # comments: never translated
    r"|<[^>]+>",                               # any other tag
    re.S | re.I,
)
_TRANSLATABLE_ATTR_RE = re.compile(
    r"""(\b(?:placeholder|title|alt|aria-label)\s*=\s*)("[^"]*"|'[^']*')""",
    re.I,
)


def localize_html(content):
    """Translate English source strings in rendered HTML.

    Locale files use the English text as key. Only text that is meant for the
    reader is translated:

    * text nodes,
    * the placeholder / title / alt / aria-label attributes,
    * string literals inside <script> (the key must start right after a quote).

    Everything else (``value``, ``href``, ``id``, ``onclick``, identifiers in
    scripts, CSS, ``data:`` URIs) is left untouched, so form values typed by
    the user, paths and embedded images can never be altered by a translation.
    """
    translations = {
        source: str(translated)
        for source, translated in load_locale().items()
        if source and source != str(translated)
    }
    if not translations:
        return content

    ordered = sorted(translations, key=len, reverse=True)

    # Keys that contain markup (e.g. <b>..</b>) span several tokens, so they
    # are applied to the whole document first. They are full sentences, so an
    # accidental match elsewhere is not a practical concern.
    for source in ordered:
        if "<" in source:
            content = content.replace(source, translations[source])

    plain = [source for source in ordered if "<" not in source]
    if not plain:
        return content

    text_re = re.compile("|".join(re.escape(source) for source in plain))
    script_re = re.compile(
        "(?<=['\"])(?:" + "|".join(re.escape(source) for source in plain) + ")"
    )

    def translate_text(text):
        return text_re.sub(lambda m: translations[m.group(0)], text)

    def translate_script(text):
        return script_re.sub(lambda m: translations[m.group(0)], text)

    def translate_tag(tag):
        return _TRANSLATABLE_ATTR_RE.sub(
            lambda m: m.group(1) + translate_text(m.group(2)), tag
        )

    parts = []
    position = 0

    for match in _HTML_TOKEN_RE.finditer(content):
        parts.append(translate_text(content[position:match.start()]))

        token = match.group(0)

        if match.group(1) is not None:
            parts.append(match.group(1))
            parts.append(translate_script(match.group(2)))
            parts.append(match.group(3))
        elif token.startswith("<") and not re.match(r"<(?:style|!--)", token, re.I):
            parts.append(translate_tag(token))
        else:
            parts.append(token)

        position = match.end()

    parts.append(translate_text(content[position:]))
    return "".join(parts)


def language_selector():
    current = get_language()
    options = []
    for code in SUPPORTED_LANGUAGES:
        selected = " selected" if code == current else ""
        options.append(
            '<option value="{}"{}>{}</option>'.format(
                code, selected, html.escape(LANGUAGE_NAMES[code])
            )
        )

    return """
<div class="settings-card">
    <div class="settings-card-title">
        <span class="metric-icon">文</span>
        <span>Language</span>
    </div>
    <div class="settings-card-body">
        <form method="post" action="./language">
            <div class="form-row">
                <label for="language">Language</label>
                <select id="language" name="language" onchange="this.form.submit()">
                    {}
                </select>
            </div>
        </form>
    </div>
</div>
""".format("".join(options))


def read_file(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return default


def write_file(path, value, mode=None):
    """Atomically replace *path*. *mode* (e.g. 0o600) is applied at creation."""
    tmp = path + ".tmp"

    try:
        os.unlink(tmp)
    except OSError:
        pass

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o644 if mode is None else mode)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_dsm_version_file():
    for path in ("/etc.defaults/VERSION", "/etc/VERSION"):
        try:
            data = {}

            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()

                    if "=" in line:
                        key, value = line.split("=", 1)
                        data[key.strip()] = value.strip().strip('"')

            if data:
                return data

        except Exception:
            pass

    return {}


def get_dsm_version():
    data = read_dsm_version_file()
    version = data.get("productversion", "")
    build = data.get("buildnumber", "")

    if not version:
        return "Unknown"

    return "{}-{}".format(version, build) if build else version


def get_nas_model():
    model = read_file("/proc/sys/kernel/syno_hw_version", "").strip()

    if model:
        return model

    data = read_dsm_version_file()

    return (
        data.get("modelname", "").strip()
        or data.get("unique", "").strip()
        or "Unknown"
    )


def get_cpu_model():
    hardware = ""

    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if ":" not in line:
                    continue

                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()

                if key == "model name" and value:
                    return value

                if key == "hardware" and value:
                    hardware = value

    except Exception:
        pass

    if hardware:
        return hardware

    try:
        value = platform.processor().strip()
        if value and not value.isdigit():
            return value
    except Exception:
        pass

    return "Unknown"


def get_cpu_cores():
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def get_architecture():
    try:
        return platform.machine()
    except Exception:
        return "Unknown"


def get_torrserver_version():
    try:
        result = subprocess.run(
            [TORRSERVER_BIN, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )

        output = result.stdout.strip()

        if output:
            return output

    except Exception:
        pass

    return "Unknown"


def get_port():
    port = 8090

    value = read_file(PORT_FILE, "")

    if value.isdigit():
        number = int(value)

        if 1 <= number <= 65535:
            port = number

    return port


def get_running_ports():
    result = {
        "http": None,
        "https": None,
        "ssl": False,
        "force_https": False,
    }

    try:
        pid_result = subprocess.run(
            ["pidof", "TorrServer"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )

        pids = pid_result.stdout.strip().split()
        if not pids:
            return result

        for pid in pids:
            try:
                with open("/proc/{}/cmdline".format(pid), "rb") as f:
                    raw_args = f.read().split(b"\0")

                args = [
                    item.decode("utf-8", errors="replace")
                    for item in raw_args
                    if item
                ]

                if not args:
                    continue

                executable = args[0]
                if not executable.endswith("/TorrServer"):
                    continue

                for index, arg in enumerate(args):
                    if arg == "-p" and index + 1 < len(args):
                        value = args[index + 1]
                        if value.isdigit():
                            port = int(value)
                            if 1 <= port <= 65535:
                                result["http"] = port

                    elif arg == "--ssl":
                        result["ssl"] = True

                    elif arg == "--sslport" and index + 1 < len(args):
                        value = args[index + 1]
                        if value.isdigit():
                            port = int(value)
                            if 1 <= port <= 65535:
                                result["https"] = port

                    elif arg == "--force-https":
                        result["force_https"] = True

                return result

            except (OSError, IOError):
                continue

    except Exception:
        pass

    return result


def get_auth_enabled():
    return read_file(AUTH_FILE, "0") == "1" and os.path.isfile(ACCS_FILE)


def get_https_enabled():
    return read_file(HTTPS_FILE, "0") == "1"


def get_https_port():
    port = 8091

    value = read_file(HTTPS_PORT_FILE, "")

    if value.isdigit():
        number = int(value)

        if 1 <= number <= 65535:
            port = number

    return port


def get_force_https():
    return read_file(FORCE_HTTPS_FILE, "0") == "1"


def get_ssl_mode():
    mode = read_file(SSL_MODE_FILE, SSL_CERT_MODE_SELF).strip().lower()
    if mode not in (SSL_CERT_MODE_SELF, SSL_CERT_MODE_DSM, SSL_CERT_MODE_MANUAL):
        return SSL_CERT_MODE_SELF
    return mode


def get_ssl_paths():
    return read_file(SSL_CERT_FILE, "").strip(), read_file(SSL_KEY_FILE, "").strip()


_PRIVILEGE_CACHE = {"time": 0.0, "value": False}
PRIVILEGE_CACHE_SECONDS = 10


# Must match the exit code prepare-directory uses for "no directory given".
PREPARE_USAGE_EXIT = 64


def sudo_denied(stderr):
    """True when sudo itself refused (not the script it was asked to run).

    Whatever the locale, sudo prefixes its own messages with "sudo:".
    """
    return "sudo:" in (stderr or "")


PERMISSIONS_OUTDATED = (
    "DSM permissions are missing or out of date. Run "
    "/var/packages/TorrServer/scripts/setup-permissions as root in DSM Task "
    "Scheduler (the permissions were extended in this version), then try again"
)


def has_privileged_access(use_cache=True):
    """Return True when the package may run its root-only scripts.

    Both scripts the Helper depends on are checked, because a sudoers rule
    written by an older package version covers only some of them. Each check
    spawns sudo, so the result is cached briefly (a page render asks several
    times). prepare-directory is started without arguments, which makes it
    print its usage text and do nothing; the restart script needs no root and is not probed.
    """
    now = time.monotonic()

    if use_cache and now - _PRIVILEGE_CACHE["time"] < PRIVILEGE_CACHE_SECONDS:
        return _PRIVILEGE_CACHE["value"]

    value = False

    if os.path.isfile(CERTIFICATE_HELPER) and os.path.isfile(PREPARE_DIRECTORY):
        try:
            certificates = subprocess.run(
                ["/bin/sudo", "-n", CERTIFICATE_HELPER],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=5,
            )
            directories = subprocess.run(
                ["/bin/sudo", "-n", PREPARE_DIRECTORY],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=5,
            )
            # sudo's own refusal exits with 1; prepare-directory without an
            # argument exits with PREPARE_USAGE_EXIT. Warnings that sudo may
            # print on stderr therefore cannot be mistaken for a refusal.
            value = (
                certificates.returncode == 0
                and directories.returncode == PREPARE_USAGE_EXIT
            )
        except Exception:
            value = False

    _PRIVILEGE_CACHE["time"] = now
    _PRIVILEGE_CACHE["value"] = value
    return value


def get_dsm_certificates():
    try:
        result = subprocess.run(
            ["/bin/sudo", "-n", CERTIFICATE_HELPER],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        result_items = []

        for item in data:
            subscriber = str(item.get("subscriber", "") or "").strip()
            service = str(item.get("service", "") or "").strip()

            for cert_item in item.get("certs", []):
                cert = str(cert_item.get("cert", "") or "").strip()
                chain = str(cert_item.get("chain", "") or "").strip()
                key = str(cert_item.get("key", "") or "").strip()

                if not cert or not key:
                    continue

                if "/ECC-" in cert:
                    cert_type = "ECC"
                elif "/RSA-" in cert:
                    cert_type = "RSA"
                else:
                    cert_type = "Certificate"

                label = subscriber or service or "DSM"

                result_items.append({
                    "label": "{} ({})".format(label, cert_type),
                    "cert": chain or cert,
                    "key": key,
                })

        unique = []
        seen = set()

        for item in result_items:
            pair = (item["cert"], item["key"])
            if pair in seen:
                continue
            seen.add(pair)
            unique.append(item)

        return sorted(unique, key=lambda item: item["label"].lower())

    except Exception:
        return []

def is_torrserver_running():
    try:
        result = subprocess.run(
            ["pidof", "TorrServer"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )

        return bool(result.stdout.strip())

    except Exception:
        return False


def get_status():
    return "Running" if is_torrserver_running() else "Stopped"


def get_torrserver_uptime():
    try:
        result = subprocess.run(
            ["pidof", "TorrServer"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
        pids = result.stdout.strip().split()
        if not pids:
            return "Stopped"

        with open("/proc/{}/stat".format(pids[0]), "r", encoding="utf-8") as f:
            stat_data = f.read().split()

        start_ticks = int(stat_data[21])

        with open("/proc/uptime", "r", encoding="utf-8") as f:
            system_uptime = float(f.read().split()[0])

        clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        elapsed = max(0, int(system_uptime - (start_ticks / clock_ticks)))

        days = elapsed // 86400
        hours = (elapsed % 86400) // 3600
        minutes = (elapsed % 3600) // 60

        if days:
            return "{}d {}h {}m".format(days, hours, minutes)
        if hours:
            return "{}h {}m".format(hours, minutes)
        return "{}m".format(minutes)

    except Exception:
        return "Unknown"


NOT_WRITABLE = (
    "The TorrServer service user cannot write to this folder. Give the TorrServer "
    "user Read/Write permission on the shared folder (DSM Control Panel → Shared "
    "Folder → Edit → Permissions → System internal user), or enable the optional "
    "DSM permissions on the DSM permissions page."
)

SUBDIRECTORIES = ("Cache", "FUSE")


def prepare_directory_directly(torrserver_dir):
    """Create Cache and FUSE as the service user, without root.

    Returns True when both exist and are writable, False when root is needed
    (the service user may not write there). Raises ValueError for a problem
    that root would not fix either.
    """
    for name in SUBDIRECTORIES:
        path = os.path.join(torrserver_dir, name)

        if os.path.islink(path):
            raise ValueError(
                "TorrServer subdirectory must not be a symbolic link: {}".format(path)
            )

        if os.path.exists(path):
            if not os.path.isdir(path):
                raise ValueError(
                    "TorrServer subdirectory is not a directory: {}".format(path)
                )

            if not os.access(path, os.W_OK | os.X_OK):
                return False

            continue

        try:
            os.mkdir(path, 0o755)
        except PermissionError:
            return False
        except FileExistsError:
            continue
        except OSError as e:
            raise ValueError("Failed to create TorrServer subdirectory: {} ({})".format(path, e))

    return True


def prepare_torrserver_directory(torrserver_dir):
    if not torrserver_dir:
        return False, "Choose the TorrServer directory with the Browse button"

    # Normal case: the user gave the service user access to the share (the
    # usual DSM way), so no root is needed at all.
    try:
        if prepare_directory_directly(torrserver_dir):
            return True, ""
    except ValueError as e:
        return False, str(e)

    # Otherwise ask the root helper, which needs the optional permissions.
    if not has_privileged_access():
        return False, NOT_WRITABLE

    if not os.path.isfile(PREPARE_DIRECTORY):
        return False, "Directory preparation script not found"

    try:
        result = subprocess.run(
            [
                "/bin/sudo",
                "-n",
                PREPARE_DIRECTORY,
                torrserver_dir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=15,
        )

        if result.returncode != 0:
            if sudo_denied(result.stderr):
                return False, PERMISSIONS_OUTDATED

            message = result.stderr.strip() or result.stdout.strip()
            return False, message or "Failed to prepare TorrServer directory"

        return True, ""

    except Exception as e:
        return False, str(e)


def restart_torrserver():
    """
    Restart the TorrServer process; no root and no sudo are involved.

    restart-torrserver stops the TorrServer binary and starts it again with
    the saved settings. The helper keeps running, so it is not touched, and
    its pid is handed over so the package pid file stays correct. The script
    gets its own session and finishes after this request is answered.
    """

    if not os.path.isfile(RESTART_SCRIPT):
        return False, "Restart script not found"

    environment = dict(os.environ)
    environment["TORRSERVER_HELPER_PID"] = str(os.getpid())

    try:
        process = subprocess.Popen(
            ["/bin/sh", RESTART_SCRIPT],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

        if process.pid <= 0:
            return False, "Failed to start restart script"

        RESTART_STATE["started"] = time.monotonic()
        return True, "Restarting..."

    except Exception as e:
        return False, str(e)


def restart_finished():
    """True once a restart started by this helper is over and TorrServer runs.

    The script takes its lock right after it starts, so a short grace period
    keeps the "no lock yet" moment from looking like "finished".
    """
    started = RESTART_STATE["started"]

    if started is None:
        return True

    if time.monotonic() - started < RESTART_MIN_SECONDS:
        return False

    if os.path.isdir(RESTART_LOCK):
        return False

    return is_torrserver_running()


def last_problem_lines(count=3, width=220):
    """The last lines of whichever service log was written most recently.

    That is where TorrServer's own start-up errors end up (for example a port
    that is already taken), so the status page can say why it is stopped.
    """
    newest = None

    for path in (TORRSERVER_LOG, SERVICE_LOG):
        try:
            modified = os.path.getmtime(path)
        except OSError:
            continue
        if newest is None or modified > newest[0]:
            newest = (modified, path)

    if newest is None:
        return []

    try:
        with open(newest[1], "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 16384))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line[:width] for line in lines[-count:]]


def stopped_info_html():
    lines = last_problem_lines()
    block = '<div class="stopped-info">'

    if lines:
        block += '<div class="status-subtitle">Last lines of the log:</div><pre>{}</pre>'.format(
            html.escape("\n".join(lines))
        )

    block += (
        '<form method="post" action="./restart">'
        '<button type="submit">Start</button>'
        '</form></div>'
    )
    return block


def restart_in_progress():
    """True while a restart started by this helper is still running.

    Gives up after RESTART_GIVE_UP_SECONDS, so a TorrServer that never comes
    back is shown as stopped instead of restarting forever.
    """
    started = RESTART_STATE["started"]

    if started is None:
        return False

    if time.monotonic() - started > RESTART_GIVE_UP_SECONDS or restart_finished():
        RESTART_STATE["started"] = None
        return False

    return True


def get_api_auth_header():
    if not get_auth_enabled():
        return None

    try:
        with open(ACCS_FILE, "r", encoding="utf-8") as f:
            accounts = json.load(f)

        if not accounts:
            return None

        username, password = next(iter(accounts.items()))
        token = base64.b64encode(
            ("{}:{}".format(username, password)).encode("utf-8")
        ).decode("ascii")
        return "Basic {}".format(token)

    except Exception:
        return None


def get_torrserver_dir():
    return read_file(TORRSERVER_DIR_FILE, "")


def get_cache_dir(torrserver_dir=None):
    root = get_torrserver_dir() if torrserver_dir is None else torrserver_dir
    if not root:
        return ""
    return os.path.join(root.rstrip("/"), "Cache")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would turn a redirected POST into a GET
    and we would mistake the HTML answer for success."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _local_tls_context():
    # Loopback only: TorrServer's own (self-signed or DSM) certificate cannot
    # match 127.0.0.1, so verification is intentionally disabled here.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def torrserver_request(payload, http_port=None):
    """POST *payload* to the running TorrServer ``/settings`` endpoint.

    Tries HTTP first (the running instance may still be HTTP although the
    saved flags already say HTTPS), then HTTPS. Returns
    ``(ok, data_or_error, reachable)``; *reachable* is False only when no
    endpoint could be contacted at all (TorrServer is not running).
    """
    http_port = get_port() if http_port is None else int(http_port)
    https_port = get_https_port()

    endpoints = [("http://127.0.0.1:{}/settings".format(http_port), None)]
    if https_port != http_port:
        endpoints.append((
            "https://127.0.0.1:{}/settings".format(https_port),
            _local_tls_context(),
        ))

    body = json.dumps(payload).encode("utf-8")
    auth_header = get_api_auth_header()
    last_error = None
    reachable = False

    for url, context in endpoints:
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if auth_header:
            request.add_header("Authorization", auth_header)

        handlers = [_NoRedirect()]
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)

        try:
            with opener.open(request, timeout=5) as response:
                raw = response.read().decode("utf-8", errors="replace")

            reachable = True

            if not raw.strip():
                return True, {}, True

            try:
                return True, json.loads(raw), True
            except ValueError:
                last_error = "unexpected response from TorrServer"

        except urllib.error.HTTPError as e:
            # The server answered (e.g. 401, or a redirect to HTTPS).
            reachable = True
            last_error = "HTTP Error {}: {}".format(e.code, e.reason)
        except Exception as e:
            last_error = str(e)

    return False, last_error or "request failed", reachable


def _find_key(data, name):
    lowered = name.lower()
    for key in data:
        if str(key).lower() == lowered:
            return key
    return None


def set_cache_path(cache_path, port=None):
    """Apply the cache directory to the running TorrServer.

    Returns ``(status, message)`` where status is ``"applied"``, ``"pending"``
    (TorrServer is not running; applied automatically once it is) or
    ``"error"``.
    """
    # TorrServer's "set" replaces the whole settings object, so read the
    # current settings, change one field and send everything back. Sending a
    # partial object would reset every other TorrServer setting.
    ok, data, reachable = torrserver_request({"action": "get"}, port)

    if not ok:
        if not reachable:
            write_file(CACHE_PENDING_FILE, cache_path)
            return "pending", "TorrServer is not running"

        return "error", "Unable to read TorrServer settings: {}".format(data)

    if not isinstance(data, dict) or not data:
        return "error", "TorrServer returned empty settings"

    key = _find_key(data, "TorrentsSavePath") or "TorrentsSavePath"

    if data.get(key) != cache_path:
        data[key] = cache_path
        ok, result, _reachable = torrserver_request(
            {"action": "set", "sets": data}, port
        )
        if not ok:
            return "error", "Unable to apply cache directory: {}".format(result)

    remove_file(CACHE_PENDING_FILE)
    return "applied", ""


def remove_file(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def apply_pending_cache_path():
    """Apply a cache directory saved while TorrServer was down.

    Returns True when nothing is left to do.
    """
    cache_path = read_file(CACHE_PENDING_FILE, "")
    if not cache_path:
        return True

    status, _message = set_cache_path(cache_path)
    return status == "applied"


PASSWORD_PLACEHOLDER = "••••••••"


def load_accounts():
    try:
        with open(ACCS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_saved_account():
    accounts = load_accounts()
    if not accounts:
        return "", ""

    username, password = next(iter(accounts.items()))
    return str(username), str(password)


def save_account(username, password, previous_username=""):
    """Store the helper-managed account first, keeping other TorrServer users."""
    accounts = load_accounts()

    if previous_username and previous_username != username:
        accounts.pop(previous_username, None)

    ordered = {username: password}
    for name, value in accounts.items():
        if name != username:
            ordered[name] = value

    write_file(ACCS_FILE, json.dumps(ordered), mode=0o600)


def valid_username(username):
    # ":" would break HTTP Basic auth; control characters are never valid.
    return bool(re.match(r"^[^:\x00-\x1f\x7f]{1,%d}$" % USERNAME_MAX_LENGTH, username))


def valid_volume_path(path):
    parts = path.split("/")
    return (
        bool(re.match(r"^/volume[0-9]+/[^\x00]+$", path))
        and ".." not in parts
    )


def get_listening_tcp_ports():
    ports = set()

    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path, "r", encoding="ascii") as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) < 4 or parts[3] != "0A":
                        continue

                    try:
                        _, port_hex = parts[1].rsplit(":", 1)
                        ports.add(int(port_hex, 16))
                    except (ValueError, TypeError):
                        continue
        except (OSError, IOError):
            continue

    return ports


def is_port_in_use(port_number, allowed_ports=None):
    allowed_ports = set(allowed_ports or ())
    return port_number in (get_listening_tcp_ports() - allowed_ports)


# Every message is a complete sentence so it can be translated as a whole
# (translations are matched as text; a number in the middle would prevent that).
WEB_PORT_ERRORS = ("Web port is invalid", "Web port must be between 1024 and 65535")
HTTPS_PORT_ERRORS = ("HTTPS port is invalid", "HTTPS port must be between 1024 and 65535")


def parse_port(value, errors):
    invalid, out_of_range = errors

    if not value.isdigit():
        return None, invalid

    number = int(value)
    if number < 1024 or number > 65535:
        return None, out_of_range

    return number, ""


def save_settings(params):
    def field(name, default=""):
        return params.get(name, [default])[0]

    port = field("port").strip()
    auth = field("auth", "0")
    username = field("username").strip()
    password = field("password")
    torrserver_dir = field("torrserver_dir").strip()
    fuse = field("fuse", "0")
    https = field("https", "0")
    https_port = field("https_port", "8091").strip()
    force_https = field("force_https", "0")
    ssl_mode = field("ssl_mode", SSL_CERT_MODE_SELF).strip().lower()
    ssl_cert = field("ssl_cert").strip()
    ssl_key = field("ssl_key").strip()

    old_port = get_port()
    old_https_port = get_https_port()
    saved_username, saved_password = get_saved_account()

    # ---- 1. Validate everything first; nothing is changed until all pass.

    if ssl_mode not in (SSL_CERT_MODE_SELF, SSL_CERT_MODE_DSM, SSL_CERT_MODE_MANUAL):
        return False, "Invalid certificate mode"

    # Everything else works without root. Only certificates that have to be
    # copied from DSM (or from a root-readable path) need the optional rule.
    if ssl_mode != SSL_CERT_MODE_SELF and not has_privileged_access():
        return False, "Additional DSM permissions are required for DSM and manual certificates."

    # The directory is optional: without one TorrServer keeps its data in the
    # package folder. Only FUSE needs a place to mount.
    if not torrserver_dir and fuse == "1":
        return False, "Choose the TorrServer directory with the Browse button"

    if torrserver_dir:
        if len(torrserver_dir) > 1:
            torrserver_dir = torrserver_dir.rstrip("/")

        if re.search(r"\s", torrserver_dir):
            return False, "TorrServer directory must not contain spaces"

        if cache_browser_path(torrserver_dir) != torrserver_dir:
            return False, "Invalid TorrServer directory"

    if ssl_mode == SSL_CERT_MODE_MANUAL:
        if not ssl_cert or not ssl_key:
            return False, "Certificate and key paths are required"
        if not valid_volume_path(ssl_cert) or not valid_volume_path(ssl_key):
            return False, "Manual certificate and key must be inside /volumeX/"

    if ssl_mode == SSL_CERT_MODE_DSM:
        valid = {(x["cert"], x["key"]) for x in get_dsm_certificates()}
        if (ssl_cert, ssl_key) not in valid:
            return False, "Invalid DSM certificate selection"

    port_number, error = parse_port(port, WEB_PORT_ERRORS)
    if port_number is None:
        return False, error

    https_port_number, error = parse_port(https_port, HTTPS_PORT_ERRORS)
    if https_port_number is None:
        return False, error

    if port_number == HELPER_PORT:
        return False, "Web port is reserved for TorrServer Helper: {}".format(port_number)

    if is_port_in_use(port_number, allowed_ports={old_port}):
        return False, "Web port is already in use: {}".format(port_number)

    # The HTTPS port only matters when HTTPS is enabled.
    if https == "1":
        if https_port_number == HELPER_PORT:
            return False, "HTTPS port is reserved for TorrServer Helper: {}".format(https_port_number)

        if https_port_number == port_number:
            return False, "HTTPS port must differ from Web port"

        if is_port_in_use(https_port_number, allowed_ports={old_https_port}):
            return False, "HTTPS port is already in use: {}".format(https_port_number)

    if auth == "1":
        if not username:
            return False, "Username is required"

        if not valid_username(username):
            return False, "Username must not contain a colon or control characters"

        if password == PASSWORD_PLACEHOLDER or not password:
            # The stored password may only be reused for the same account.
            if saved_password and username == saved_username:
                password = saved_password
            else:
                return False, "Password is required"

    # ---- 2. Apply. Side effects start only after validation succeeded.

    status = "applied"

    if torrserver_dir:
        ok, directory_message = prepare_torrserver_directory(torrserver_dir)
        if not ok:
            return False, directory_message

        # TorrServer is still listening on old_port at this point. If it is
        # not running at all the change is queued and applied when it starts.
        status, cache_message = set_cache_path(get_cache_dir(torrserver_dir), old_port)
        if status == "error":
            return False, cache_message

    try:
        if auth == "1":
            save_account(username, password, saved_username)

        write_file(TORRSERVER_DIR_FILE, torrserver_dir)
        write_file(AUTH_FILE, "1" if auth == "1" else "0")
        write_file(FUSE_FILE, "1" if fuse == "1" else "0")
        write_file(PORT_FILE, str(port_number))
        write_file(HTTPS_PORT_FILE, str(https_port_number))
        write_file(HTTPS_FILE, "1" if https == "1" else "0")
        write_file(FORCE_HTTPS_FILE, "1" if force_https == "1" and https == "1" else "0")
        write_file(SSL_MODE_FILE, ssl_mode)
        write_file(SSL_CERT_FILE, ssl_cert)
        write_file(SSL_KEY_FILE, ssl_key)
    except OSError as e:
        return False, "Unable to write settings: {}".format(e)

    if status == "pending":
        return True, "Settings saved. The cache directory is applied when TorrServer starts"

    return True, "Settings saved"


def get_log_path(name):
    return LOG_FILES.get(name)


def get_log_by_name(name):
    log_path = get_log_path(name)
    if not log_path:
        return "Invalid log file"

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()

        if len(data) > 200000:
            data = data[-200000:]

        return data

    except FileNotFoundError:
        return "Log file not found: {}".format(name)
    except Exception as e:
        return "Unable to read log: {}".format(e)


def rotate_log(path):
    try:
        if not os.path.isfile(path):
            return

        if os.path.getsize(path) < LOG_MAX_SIZE:
            return

        oldest = "{}.{}".format(path, LOG_BACKUP_COUNT)
        if os.path.exists(oldest):
            os.remove(oldest)

        for number in range(LOG_BACKUP_COUNT - 1, 0, -1):
            source = "{}.{}".format(path, number)
            target = "{}.{}".format(path, number + 1)

            if os.path.exists(source):
                os.replace(source, target)

        # copy + truncate: the writer keeps its file descriptor, so the file
        # cannot be renamed away.
        shutil.copyfile(path, "{}.1".format(path))
        os.truncate(path, 0)

    except Exception:
        pass


def rotate_log_if_needed():
    for path in ROTATED_LOGS:
        rotate_log(path)


def page_header(title="TorrServer"):
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{}</title>
<style>
* {{
    box-sizing: border-box;
}}

body {{
    font-family: Arial, Helvetica, sans-serif;
    margin: 0;
    background: #eef0f3;
    color: #222;
    font-size: 14px;
}}

.container {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 14px 18px 30px;
}}

.card {{
    background: #fff;
    border: 1px solid #d6d9de;
    border-radius: 6px;
    padding: 20px;
    margin-bottom: 12px;
    box-shadow: 0 1px 2px rgba(0,0,0,.08);
}}

.card h1 {{
    margin: 0 0 18px;
    font-size: 25px;
    font-weight: 600;
}}

.card h2 {{
    margin: 0 0 12px;
    font-size: 20px;
    font-weight: 600;
}}

table {{
    width: 100%;
    border-collapse: collapse;
}}

td {{
    padding: 9px 6px;
    border-bottom: 1px solid #e5e7eb;
    vertical-align: middle;
}}

td:first-child {{
    width: 230px;
    font-weight: 600;
}}

input[type=text],
input[type=password],
input[type=number],
select {{
    height: 36px;
    width: 100%;
    max-width: 520px;
    padding: 7px 10px;
    border: 1px solid #bfc4cb;
    border-radius: 3px;
    background: #fff;
    color: #222;
    font-size: 14px;
}}

input:focus,
select:focus {{
    outline: none;
    border-color: #1677ff;
    box-shadow: 0 0 0 2px rgba(22,119,255,.12);
}}

button,
.button {{
    display: inline-block;
    min-height: 36px;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 8px 15px;
    cursor: pointer;
    text-decoration: none;
    background: #1677ff;
    color: #fff;
    font-size: 14px;
    line-height: 18px;
}}

button:hover,
.button:hover {{
    filter: brightness(.96);
}}

button.secondary,
.button.secondary {{
    background: #6b6f75;
}}

button.danger,
.button.danger {{
    background: #d32f2f;
}}

button.danger:disabled,
.button.danger:disabled {{
    background: #777;
    opacity: 0.55;
    cursor: not-allowed;
}}

.status-running {{
    color: #16803c;
    font-weight: 600;
}}

.status-stopped {{
    color: #c62828;
    font-weight: 600;
}}

.media-hint {{
    margin: 2px 0 14px;
    padding: 12px 14px;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    background: #f8fafc;
}}

.media-hint summary {{
    cursor: pointer;
    font-weight: 600;
    color: #30415e;
}}

.media-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
    margin-top: 10px;
}}

.media-hint ul {{
    margin: 6px 0 0;
    padding-left: 18px;
    color: #40536f;
}}

.media-hint li {{
    margin-bottom: 4px;
}}

@media (max-width: 800px) {{
    .media-grid {{
        grid-template-columns: 1fr;
    }}
}}

.permission-help {{
    margin: 10px 0;
}}

.permission-help summary {{
    cursor: pointer;
    font-weight: 600;
    color: #30415e;
    margin-bottom: 8px;
}}

.permission-help pre {{
    min-height: 0;
    padding: 10px 12px;
    margin: 8px 0;
    font-size: 12px;
}}

.permission-help .notice {{
    margin: 8px 0;
}}

.stopped-info {{
    margin-top: 10px;
}}

.stopped-info pre {{
    min-height: 0;
    font-size: 12px;
    padding: 10px 12px;
    margin: 6px 0 10px;
}}

.status-restarting {{
    color: #b45309;
    font-weight: 600;
}}

.notice {{
    background: #fff7d6;
    border: 1px solid #e6cf75;
    border-left: 4px solid #e0b100;
    border-radius: 4px;
    padding: 12px 14px;
    margin-bottom: 16px;
    color: #5f4b00;
}}

.notice strong {{
    color: #4d3d00;
}}

.form-row {{
    margin-bottom: 16px;
}}

.form-row label {{
    display: block;
    margin-bottom: 6px;
    font-weight: 600;
}}

.help {{
    margin-top: 6px;
    color: #6b7280;
    font-size: 13px;
}}

.actions {{
    display: flex;
    gap: 8px;
    align-items: center;
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid #d9dce1;
}}

pre {{
    white-space: pre-wrap;
    word-break: break-word;
    background: #111;
    color: #ddd;
    padding: 15px;
    border-radius: 4px;
    overflow-x: auto;
    min-height: 180px;
    margin: 0;
    font-family: Consolas, "Courier New", monospace;
    font-size: 13px;
    line-height: 1.35;
}}

.checkbox-row {{
    margin: 8px 0;
}}

.app-shell {{
    display: flex;
    min-height: calc(100vh - 28px);
    margin: -14px -18px -30px;
    background: #f1f5f9;
}}

.app-sidebar {{
    width: 220px;
    flex: 0 0 220px;
    background: #ffffff;
    border-right: 1px solid #d6dee8;
    padding: 18px 10px;
}}

.side-item {{
    display: flex;
    align-items: center;
    gap: 12px;
    height: 44px;
    margin: 3px 0;
    padding: 0 13px;
    border-radius: 5px;
    color: #30415e;
    text-decoration: none;
    font-size: 14px;
}}

.side-item:hover {{
    background: #edf5ff;
}}

.side-item.active {{
    background: #e5f1ff;
    color: #1167c9;
    box-shadow: inset 3px 0 0 #1677ff;
}}

.side-icon {{
    width: 22px;
    text-align: center;
    font-size: 18px;
}}

.app-content {{
    flex: 1;
    min-width: 0;
    padding: 28px 28px 36px;
}}

.app-title {{
    font-size: 28px;
    font-weight: 600;
    color: #17233b;
    margin-bottom: 16px;
}}

.status-banner {{
    min-height: 104px;
    background: #fff;
    border: 1px solid #d9e1eb;
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(30,50,80,.06);
    display: flex;
    align-items: center;
    padding: 18px 22px;
    margin-bottom: 18px;
}}

.status-logo {{
    width: 72px;
    height: 72px;
    flex: 0 0 72px;
    margin-right: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
}}

.status-logo img {{
    width: 72px;
    height: 72px;
    object-fit: contain;
    display: block;
}}

.status-text {{
    flex: 1;
}}

.status-text .status-running,
.status-text .status-restarting,
.status-text .status-stopped {{
    font-size: 23px;
}}

.status-subtitle {{
    color: #627089;
    margin-top: 5px;
    font-size: 14px;
}}

.light-button {{
    background: #eaf3ff !important;
    color: #1a3f70 !important;
    border-color: #d4e5f8 !important;
}}

.dashboard-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
}}

.dashboard-card {{
    background: #fff;
    border: 1px solid #d9e1eb;
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(30,50,80,.06);
    padding: 20px 22px;
    min-width: 0;
}}

.dashboard-card-title {{
    display: flex;
    align-items: center;
    gap: 11px;
    font-size: 19px;
    font-weight: 600;
    color: #17233b;
    padding-bottom: 13px;
    border-bottom: 1px solid #e2e8f0;
    margin-bottom: 4px;
}}

.metric-icon {{
    width: 34px;
    height: 34px;
    border-radius: 7px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: #edf3fa;
    color: #354b68;
    font-size: 11px;
    font-weight: 700;
}}

.metric-table td {{
    padding: 9px 2px;
    border-bottom: 1px solid #e4e9ef;
}}

.metric-table td:first-child {{
    width: 46%;
    font-weight: 400;
    color: #40536f;
}}

.metric-table td:last-child {{
    color: #1d2e49;
}}

.download {{
    color: #20a04b;
}}

.settings-layout {{
    max-width: 980px;
}}

.settings-card {{
    background: #fff;
    border: 1px solid #d9e1eb;
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(30,50,80,.06);
    margin-bottom: 16px;
    overflow: hidden;
}}

.settings-card.disabled {{
    opacity: 0.55;
}}

.settings-card-title {{
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 15px 18px;
    border-bottom: 1px solid #e2e8f0;
    font-size: 18px;
    font-weight: 600;
    color: #17233b;
}}

.settings-card-body {{
    padding: 18px 20px;
}}

.settings-card .form-row {{
    display: grid;
    grid-template-columns: 190px minmax(0, 1fr);
    align-items: center;
    gap: 18px;
    margin-bottom: 14px;
}}

.settings-card .form-row label {{
    margin: 0;
    color: #30415e;
}}

.settings-card input[type=text],
.settings-card input[type=password],
.settings-card input[type=number],
.settings-card select {{
    max-width: 620px;
}}

.settings-card .checkbox-row {{
    margin: 0 0 14px;
}}

.settings-card .checkbox-row label {{
    color: #30415e;
}}

.settings-card .help {{
    margin: 4px 0 0;
    color: #64748b;
}}

.settings-card .actions {{
    margin: 0;
    padding: 16px 20px;
    background: #f8fafc;
}}

.logs-card {{
    background: #fff;
    border: 1px solid #d9e1eb;
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(30,50,80,.06);
    padding: 18px;
}}

.logs-toolbar {{
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 14px;
}}

.logs-toolbar select {{
    width: 220px;
}}

.logs-toolbar button,
.logs-toolbar .button {{
    min-height: 36px;
}}

.logs-output {{
    min-height: 520px;
    max-height: calc(100vh - 250px);
    overflow: auto;
    background: #101820;
    border: 1px solid #0b1117;
    color: #d8dee7;
    padding: 16px;
    border-radius: 5px;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    line-height: 1.42;
}}

@media (max-width: 800px) {{
    .container {{
        padding: 10px;
    
    
    }}

    .app-shell {{
        margin: -10px -10px -30px;
    
    
    }}

    .app-sidebar {{
        width: 170px;
        flex-basis: 170px;
    
    
    }}

    .app-content {{
        padding: 18px 14px 28px;
    
    
    }}

    .dashboard-grid {{
        grid-template-columns: 1fr;
    
    
    }}

    .settings-card .form-row {{
        grid-template-columns: 1fr;
        gap: 6px;
    
    
    }}

    .settings-card input[type=text],
.settings-card input[type=password],
.settings-card input[type=number],
.settings-card select {{
        max-width: 100%;
    
    
    }}

    .logs-toolbar {{
        align-items: stretch;
        flex-direction: column;
    
    
    }}

    .logs-toolbar select {{
        width: 100%;
    
    
    }}
}}

.web-ui-actions {{
    display: flex;
    gap: 10px;
    align-items: flex-start;
}}

.web-ui-action {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 5px;
}}

.web-ui-address {{
    font-size: 11px;
    color: #64748b;
    white-space: nowrap;
}}

.button.disabled {{
    opacity: 0.42;
    cursor: default;
    pointer-events: none;
}}

.web-ui-address.disabled {{
    color: #94a3b8;
}}

.info-card {{
    position: relative;
    grid-column: 1 / -1;
}}

.info-maintainer-top {{
    position: absolute;
    top: 18px;
    right: 20px;
    font-size: 11px;
    color: #64748b;
    white-space: nowrap;
}}

.info-layout {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    padding-top: 12px;
}}

.info-text {{
    min-width: 180px;
}}

.info-title {{
    font-size: 18px;
    font-weight: 600;
    color: #1d2e49;
}}

.info-subtitle {{
    margin-top: 5px;
    color: #64748b;
    font-size: 14px;
}}

.info-links {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: flex-end;
}}

.info-link {{
    display: inline-flex;
    align-items: center;
    min-height: 34px;
    padding: 0 12px;
    border: 1px solid #d6e0ec;
    border-radius: 6px;
    background: #f7f9fc;
    color: #294766;
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
}}

.info-link:hover {{
    background: #edf3fa;
    border-color: #c7d5e5;
}}

.info-donate {{
    background: #fff5e9;
    border-color: #f3d5b1;
    color: #a85c13;
}}

.info-donate:hover {{
    background: #ffecd6;
    border-color: #e8bf8e;
}}

@media (max-width: 800px) {{
    .info-layout {{
        flex-direction: column;
        align-items: stretch;
    
    
    }}

    .info-links {{
        justify-content: flex-start;
    
    
    }}

    .info-maintainer-top {{
        position: static;
        margin-bottom: 8px;
    
    
    }}
}}
</style>
</head>
<body>
<div class="container">
""".format(html.escape(title))


def page_footer():
    return """
</div>
</body>
</html>
"""


def app_sidebar(active):
    items = [
        ("./", "▥", "Status", "status"),
        ("./settings", "⚙", "Settings", "settings"),
        ("./logs", "▤", "Logs", "logs"),
    ]

    parts = ['<div class="app-sidebar">']
    for href, icon, label, key in items:
        cls = "side-item active" if key == active else "side-item"
        parts.append(
            '<a class="{}" href="{}"><span class="side-icon">{}</span><span>{}</span></a>'.format(
                cls, href, icon, label
            )
        )
    parts.append("</div>")
    return "".join(parts)


def get_status_logo_data_uri():
    try:
        with open(STATUS_LOGO_FILE, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return "data:image/png;base64,{}".format(encoded)
    except Exception:
        return ""

RESTART_WATCH_SCRIPT = """
<script>
(function () {
    var started = Date.now();

    function poll() {
        if (Date.now() - started > 100000) {
            window.location.reload();
            return;
        }

        fetch('./restart-status', {cache: 'no-store', credentials: 'same-origin'})
            .then(function (response) { return response.text(); })
            .then(function (text) {
                if (text.trim() === 'ready') {
                    window.location.reload();
                } else {
                    setTimeout(poll, 1000);
                }
            })
            .catch(function () { setTimeout(poll, 1500); });
    }

    setTimeout(poll, 1500);
})();
</script>
"""


def main_page(host):
    status = get_status()

    configured_port = get_port()
    configured_https = get_https_enabled()
    auth = get_auth_enabled()

    running = get_running_ports()
    running_http_port = running["http"]
    running_https_port = running["https"]
    running_ssl = running["ssl"]
    running_force_https = running["force_https"]

    http_action = ""
    if running_http_port is not None and not running_force_https:
        http_action = '''        <div class="web-ui-action">
            <a class="button light-button"
               href="http://{0}:{1}/"
               target="_blank">
                🌐 Open HTTP ↗
            </a>
            <div class="web-ui-address">http://{0}:{1}</div>
        </div>
'''.format(host, running_http_port)

    https_action = ""
    if running_ssl and running_https_port is not None:
        https_action = '''        <div class="web-ui-action">
            <a class="button light-button"
               href="https://{0}:{1}/"
               target="_blank">
                🔒 Open HTTPS ↗
            </a>
            <div class="web-ui-address">https://{0}:{1}</div>
        </div>
'''.format(host, running_https_port)

    web_ui_actions = http_action + https_action

    restarting = restart_in_progress()

    if restarting:
        status = "Restarting"
        status_class = "status-restarting"
        status_text = "TorrServer is restarting. This page will update automatically."
    else:
        status_class = (
            "status-running"
            if status == "Running"
            else "status-stopped"
        )
        if status == "Running":
            status_text = "TorrServer is running normally."
        else:
            status_text = "TorrServer is not running."

    status_logo = get_status_logo_data_uri()

    body = page_header("TorrServer")

    body += """
<div class="app-shell">

<div class="app-sidebar">
    <a class="side-item active" href="./">
        <span class="side-icon">▥</span>
        <span>Status</span>
    </a>

    <a class="side-item" href="./settings">
        <span class="side-icon">⚙</span>
        <span>Settings</span>
    </a>

    <a class="side-item" href="./logs">
        <span class="side-icon">▤</span>
        <span>Logs</span>
    </a>
</div>

<div class="app-content">

<div class="app-title">Status</div>

<div class="status-banner">
    <div class="status-logo">
        <img src="{16}" alt="TorrServer">
    </div>
    <div class="status-text">
        <div class="{0}">{1}</div>
        <div class="status-subtitle">{14}</div>
        {stopped_info}
    </div>
    {15}
</div>

<div class="dashboard-grid">

<div class="dashboard-card">
    <div class="dashboard-card-title">
        <span class="metric-icon server-icon">TS</span>
        <span>TorrServer</span>
    </div>

    <table class="metric-table">
        <tr>
            <td>Version</td>
            <td>{4}</td>
        </tr>
        <tr>
            <td>Web port</td>
            <td>{5}</td>
        </tr>
        <tr>
            <td>HTTPS</td>
            <td>{6}</td>
        </tr>
        <tr>
            <td>Authentication</td>
            <td>{7}</td>
        </tr>
        <tr>
            <td>Uptime</td>
            <td>{8}</td>
        </tr>
    </table>
</div>

<div class="dashboard-card">
    <div class="dashboard-card-title">
        <span class="metric-icon system-icon">▣</span>
        <span>System</span>
    </div>

    <table class="metric-table">
        <tr>
            <td>DSM</td>
            <td>{9}</td>
        </tr>
        <tr>
            <td>NAS model</td>
            <td>{10}</td>
        </tr>
        <tr>
            <td>CPU</td>
            <td>{11}</td>
        </tr>
        <tr>
            <td>Cores</td>
            <td>{12}</td>
        </tr>
        <tr>
            <td>Architecture</td>
            <td>{13}</td>
        </tr>
    </table>
</div>


<div class="dashboard-card info-card">
    <div class="info-maintainer-top">Synology SPK package maintained by vladlenas</div>

    <div class="dashboard-card-title">
        <span class="metric-icon">i</span>
        <span>Information</span>
    </div>

    <div class="info-layout">
        <div class="info-text">
            <div class="info-title">TorrServer DSM</div>
            <div class="info-subtitle">TorrServer project, Synology SPK package and support</div>
        </div>

        <div class="info-links">
            <a class="info-link" href="https://github.com/YouROK/TorrServer" target="_blank" rel="noopener noreferrer">TorrServer Project ↗</a>
            <a class="info-link" href="https://github.com/vladlenas/TorrServer-DSM" target="_blank" rel="noopener noreferrer">SPK Project ↗</a>
            <a class="info-link info-donate" href="https://github.com/YouROK/TorrServer#donate" target="_blank" rel="noopener noreferrer">♥ Support TorrServer project ↗</a>
        </div>
    </div>
</div>


</div>
</div>
</div>
""".format(
        status_class,
        html.escape(status),
        html.escape(host),
        configured_port,
        html.escape(get_torrserver_version()),
        configured_port,
        "Enabled" if configured_https else "Disabled",
        "Enabled" if auth else "Disabled",
        html.escape(get_torrserver_uptime()),
        html.escape(get_dsm_version()),
        html.escape(get_nas_model()),
        html.escape(get_cpu_model()),
        get_cpu_cores(),
        html.escape(get_architecture()),
        status_text,
        '<div class="web-ui-actions">{}</div>'.format(web_ui_actions) if web_ui_actions else '',
        status_logo,
        stopped_info=stopped_info_html() if (status == "Stopped" and not restarting) else "",
    )

    if restarting:
        body += RESTART_WATCH_SCRIPT

    body += page_footer()
    return localize_html(body)


AUTH_CARD = """
<div class="settings-card">
    <div class="settings-card-title">
        <span class="metric-icon">●</span>
        <span>Authentication</span>
    </div>
    <div class="settings-card-body">

        <div class="checkbox-row">
            <label>
                <input type="checkbox" name="auth" value="1" {} onchange="toggleAuth()">
                Enable authentication
            </label>
        </div>

        <div id="authFields">
            <div class="form-row">
                <label for="username">Username</label>
                <input id="username" type="text" name="username" value="{}" {}>
            </div>

            <div class="form-row">
                <label for="password">Password</label>
                <input id="password" type="password" name="password" value="{}" data-password-placeholder="{}" {}>
            </div>
        </div>

    </div>
</div>
"""


def permissions_block(privileged):
    """The DSM permissions help inside the certificate card (no extra window)."""
    if privileged:
        return """
        <div class="status-running">Extended DSM permissions are configured.</div>
"""

    return """
        <div class="help">
            Extended DSM permissions are not configured. TorrServer itself continues to work, but DSM and manual certificates cannot be synchronized.
        </div>

        <details class="permission-help">
            <summary>DSM permissions</summary>

            <div class="help">
                TorrServer works without additional privileges. Root access is only required for DSM and manual certificate synchronization and for folders that TorrServer cannot write to.
            </div>

            <div class="notice">
                <strong>One-time setup</strong><br>
                Open Task Scheduler in DSM and create a User-defined script task. Select root as the user and run the following command once:
            </div>

            <pre>/var/packages/TorrServer/scripts/setup-permissions</pre>

            <div class="help">
                After the task finishes, return to TorrServer Settings and click Check permissions.
            </div>

            <div class="help">
                Instead, DSM can provide HTTPS with its own certificate through a reverse proxy rule for the TorrServer port. TorrServer HTTPS can then stay off.
            </div>
        </details>

        <div style="margin-top:10px">
            <button type="button" class="secondary" onclick="window.location.reload()">Check permissions</button>
        </div>
"""


def settings_page(message="", torrserver_dir_override=""):
    fuse = read_file(FUSE_FILE, "0") == "1"
    torrserver_dir = torrserver_dir_override or get_torrserver_dir()
    port = get_port()
    auth = get_auth_enabled()
    https = get_https_enabled()
    https_port = get_https_port()
    force_https = get_force_https()
    ssl_mode = get_ssl_mode()
    ssl_cert, ssl_key = get_ssl_paths()
    saved_username, saved_password = get_saved_account()
    privileged = has_privileged_access()
    dsm_certs = get_dsm_certificates() if privileged else []

    body = page_header("TorrServer Settings")

    body += """
<div class="app-shell">

{sidebar}

<div class="app-content">

<div class="app-title">Settings</div>

{language_selector}

<div class="notice">
<strong>After changing settings:</strong> first click <b>Save</b>.
<br>
{}
</div>
""".format(
        "Some changes require a restart of the TorrServer service to take effect.",
        sidebar=app_sidebar("settings"),
        language_selector=language_selector(),
    )

    if message:
        body += '<div class="notice">{}</div>'.format(html.escape(message))

    body += """
<div class="settings-layout">

<form method="post" action="./settings">
<fieldset {} style="border:0;padding:0;margin:0;min-width:0;">

<div class="settings-card">
    <div class="settings-card-title">
        <span class="metric-icon">TS</span>
        <span>TorrServer</span>
    </div>
    <div class="settings-card-body">

        <div class="form-row">
            <label for="webPort">Web port (HTTP)</label>
            <input id="webPort" type="number" name="port" min="1024" max="65535" value="{}">
        </div>

        <div class="form-row">
            <label for="torrserverDir">TorrServer directory</label>
            <div style="display:flex;gap:8px;max-width:620px;">
                <input id="torrserverDir" type="text" name="torrserver_dir" value="{}" placeholder="/volume1/...">
                <button type="button" class="secondary" onclick="openTorrServerBrowser()">Browse</button>
            </div>
            <div class="help" style="grid-column:2">
                Optional. The Cache and FUSE folders are created here: Cache for the TorrServer disk cache and FUSE for the virtual file system. The disk cache is switched on in the TorrServer web interface.
            </div>
        </div>

        <div class="checkbox-row">
            <label>
                <input type="checkbox" name="fuse" value="1" {} {} onchange="toggleFuseHint()">
                Enable FUSE filesystem
            </label>
        </div>

        <div id="fuseHint" class="media-hint">
            <details>
                <summary>Using FUSE with Plex or Emby</summary>

                <div class="media-grid">
                    <div>
                        <strong>Plex</strong>
                        <ul>
                            <li>FUSE can be used as a media library source for Plex.</li>
                            <li>Keep <b>“Show only active torrents”</b> disabled so Plex can see the library without starting torrents.</li>
                            <li>To avoid unnecessary reads of large virtual files, disable <b>“Perform extensive file analysis during maintenance”</b> and <b>“Video preview thumbnails”</b> in Plex.</li>
                        </ul>
                    </div>
                    <div>
                        <strong>Emby</strong>
                        <ul>
                            <li>FUSE can be used as a media library source for Emby.</li>
                            <li>Background task behavior with TorrServer FUSE has not been tested in this version.</li>
                        </ul>
                    </div>
                </div>

                <div class="help">
                    Normal library scanning can remain enabled. Background analysis and thumbnail generation may read large virtual files and cause significant CPU, network and storage load.
                </div>
            </details>
        </div>

    </div>
</div>

{auth_card}

<div class="settings-card">
    <div class="settings-card-title">
        <span class="metric-icon">🔒</span>
        <span>HTTPS</span>
    </div>
    <div class="settings-card-body">

        <div class="checkbox-row">
            <label>
                <input type="checkbox" name="https" value="1" {} {} onchange="toggleHttps()">
                Enable HTTPS
            </label>
        </div>

        <div class="form-row">
            <label for="httpsPort">HTTPS port</label>
            <input id="httpsPort" type="number" name="https_port" min="1024" max="65535" value="{}">
        </div>

        <div class="checkbox-row">
            <label>
                <input type="checkbox" name="force_https" value="1" {} {}>
                Force HTTPS (redirect HTTP to HTTPS)
            </label>
        </div>

    </div>
</div>

<div class="settings-card">
    <div class="settings-card-title">
        <span class="metric-icon">▣</span>
        <span>SSL Certificate</span>
    </div>
    <div class="settings-card-body">

        <div class="form-row">
            <label for="sslMode">Certificate source</label>
            <select name="ssl_mode" id="sslMode" onchange="toggleSslMode()">
                <option value="self" {}>TorrServer self-signed</option>
                <option value="dsm" {} {}>DSM certificate</option>
                <option value="manual" {} {}>Manual paths</option>
            </select>
        </div>

        <div id="dsmCertificateFields" class="form-row">
            <label for="sslDsm">DSM certificate</label>
            <select id="sslDsm">
                {}
            </select>
        </div>

        <div id="manualCertificateFields">
            <div class="form-row">
                <label for="sslCert">SSL Certificate path</label>
                <input id="sslCert" type="text" name="ssl_cert" value="{}" placeholder="/volume1/.../fullchain.pem">
            </div>

            <div class="form-row">
                <label for="sslKey">SSL Key path</label>
                <input id="sslKey" type="text" name="ssl_key" value="{}" placeholder="/volume1/.../privkey.pem">
            </div>
        </div>

        <div class="help">
            The selected source will be synchronized to TorrServer server.pem/server.key.
        </div>

        {permissions}

    </div>
</div>

<div class="settings-card">
    <div class="actions">
        <button type="submit">Save</button>
        <button type="submit" formaction="./restart" class="danger">Restart</button>
    </div>
</div>

</fieldset>
</form>
</div>

<script>
function toggleHttps() {{
    var enabled = document.querySelector('input[name="https"]').checked;
    document.getElementById('httpsPort').disabled = !enabled;
    document.getElementById('sslMode').disabled = !enabled;
    document.getElementById('sslDsm').disabled = !enabled;
    document.getElementById('sslCert').disabled = !enabled;
    document.getElementById('sslKey').disabled = !enabled;
}}

function toggleSslMode() {{
    var mode = document.getElementById('sslMode').value;
    document.getElementById('dsmCertificateFields').style.display =
        mode === 'dsm' ? 'grid' : 'none';
    document.getElementById('manualCertificateFields').style.display =
        mode === 'manual' ? 'block' : 'none';
}}

function syncDsmCertificate() {{
    var selected = document.getElementById('sslDsm');
    if (!selected || !selected.value) return;

    var value = selected.value.split('|');
    if (value.length === 2) {{
        document.querySelector('input[name="ssl_cert"]').value = value[0];
        document.querySelector('input[name="ssl_key"]').value = value[1];
    }}
}}

document.getElementById('sslDsm').addEventListener('change', syncDsmCertificate);

if (document.getElementById('sslDsm').value) {{
    syncDsmCertificate();
}}

function toggleAuth() {{
    var checkbox = document.querySelector('input[name="auth"]');
    var fields = document.getElementById('authFields');
    var inputs = fields.querySelectorAll('input');

    for (var i = 0; i < inputs.length; i++) {{
        inputs[i].disabled = !checkbox.checked;
    }}
}}

function toggleFuseHint() {{
    var checkbox = document.querySelector('input[name="fuse"]');
    document.getElementById('fuseHint').style.display = checkbox.checked ? 'block' : 'none';
}}

function openTorrServerBrowser() {{
    var field = document.querySelector('input[name="torrserver_dir"]');
    var path = field.value.trim();
    if (!path) path = '/';
    window.location.href = './browse?path=' + encodeURIComponent(path) + '&target=torrserver';
}}

var passwordField = document.getElementById('password');
if (passwordField) {{
    var passwordPlaceholder = passwordField.getAttribute('data-password-placeholder') || '';

    passwordField.addEventListener('focus', function() {{
        if (this.value === passwordPlaceholder) {{
            this.value = '';
        }}
    }});

    passwordField.addEventListener('blur', function() {{
        if (!this.value && passwordPlaceholder) {{
            this.value = passwordPlaceholder;
        }}
    }});
}}

toggleHttps();
toggleSslMode();
toggleAuth();
toggleFuseHint();
</script>
""".format(
        "",
        port,
        html.escape(torrserver_dir, quote=True),
        "checked" if fuse else "",
        "",
        "checked" if https else "",
        "",
        https_port,
        "checked" if force_https else "",
        "" if https else "disabled",
        "selected" if ssl_mode == SSL_CERT_MODE_SELF else "",
        "selected" if ssl_mode == SSL_CERT_MODE_DSM else "",
        "disabled" if not privileged else "",
        "selected" if ssl_mode == SSL_CERT_MODE_MANUAL else "",
        "disabled" if not privileged else "",
        "".join(
            '<option value="{}|{}" {}>{}</option>'.format(
                html.escape(item["cert"], quote=True),
                html.escape(item["key"], quote=True),
                "selected" if (item["cert"], item["key"]) == (ssl_cert, ssl_key) else "",
                html.escape(item["label"])
            )
            for item in dsm_certs
        ),
        html.escape(ssl_cert, quote=True),
        html.escape(ssl_key, quote=True),
        permissions=permissions_block(privileged),
        auth_card=AUTH_CARD.format(
            "checked" if auth else "",
            html.escape(saved_username, quote=True),
            "" if auth else "disabled",
            html.escape(PASSWORD_PLACEHOLDER if saved_password else "", quote=True),
            html.escape(PASSWORD_PLACEHOLDER if saved_password else "", quote=True),
            "" if auth else "disabled",
        ),
    )

    body += page_footer()
    return localize_html(body)

def cache_browser_path(path):
    """Return a safe cache-browser path under /volume* only."""
    if not path:
        return "/"

    path = os.path.abspath(path)

    if path == "/":
        return "/"

    if not re.match(r"^/volume[0-9]+(?:/.*)?$", path):
        return "/"

    real = os.path.realpath(path)
    if not re.match(r"^/volume[0-9]+(?:/.*)?$", real):
        return "/"

    if not os.path.isdir(real):
        return "/"

    return real


def cache_browser_page(path, target="cache"):
    path = cache_browser_path(path)

    if path == "/":
        # At the top level show only DSM volumes.
        try:
            names = sorted(
                name for name in os.listdir("/")
                if re.match(r"^volume[0-9]+$", name)
                and os.path.isdir(os.path.join("/", name))
            )
        except OSError:
            names = []
        parent = None
    else:
        try:
            names = sorted(
                name for name in os.listdir(path)
                if os.path.isdir(os.path.join(path, name))
                and not name.startswith(".")
                and not name.startswith("@")
            )
        except OSError:
            names = []
        parent = os.path.dirname(path.rstrip("/")) or "/"

    rows = []
    for name in names:
        child = os.path.join(path, name) if path != "/" else os.path.join("/", name)
        label = html.escape(name)
        rows.append(
            '<div style="margin:6px 0;">'
            '<a class="button secondary" style="width:100%;box-sizing:border-box;text-align:left;" '
            'href="./browse?path={}&target={}">{}/</a>'
            '</div>'.format(
                quote(child, safe=""),
                quote(target, safe=""),
                label
            )
        )

    if not rows:
        rows.append('<p>No accessible directories.</p>')

    parent_html = ""
    if parent is not None:
        parent_html = '<a class="button secondary" href="./browse?path={}&target={}">..</a>'.format(
            quote(parent, safe=""),
            quote(target, safe="")
        )

    if target == "torrserver":
        select_href = "./settings?torrserver_dir={}".format(quote(path, safe=""))
        page_title = "Select TorrServer directory"
    elif target == "fuse":
        select_href = "./settings?fuse_path={}".format(quote(path, safe=""))
        page_title = "Select FUSE directory"
    else:
        select_href = "./settings?cache_path={}".format(quote(path, safe=""))
        page_title = "Select cache directory"

    body = page_header(page_title)
    body += """
<div class="card">
<h1>{}</h1>
<p><b>Current:</b> <code>{}</code></p>
<div style="margin-bottom:15px;">
{}
</div>
<div style="margin-bottom:15px;">
<a class="button" href="{}">Select this directory</a>
</div>
<div>
{}
</div>
</div>
""".format(
        page_title,
        html.escape(path),
        parent_html,
        select_href,
        "".join(rows),
    )

    body += page_footer()
    return localize_html(body)

def logs_page():
    body = page_header("TorrServer Logs")

    body += """
<div class="app-shell">

{sidebar}

<div class="app-content">

<div class="app-title">Logs</div>

<div class="logs-card">

<div class="logs-toolbar">
    <select id="logSelect">
        <option value="TorrServer.log">TorrServer.log</option>
        <option value="TorrServer.log.1">TorrServer.log.1</option>
    </select>

    <button type="button" onclick="openLog()">↻ Refresh</button>

    <a class="button secondary"
       id="downloadButton"
       href="./download-log?name=TorrServer.log">
        ↓ Download
    </a>
</div>

<pre id="logContent" class="logs-output">Loading TorrServer.log...</pre>

</div>
</div>
</div>

<script>
function openLog() {{
    var name = document.getElementById("logSelect").value;
    document.getElementById("logContent").textContent = "Loading " + name + "...";

    var basePath = window.location.pathname.substring(
        0,
        window.location.pathname.lastIndexOf("/") + 1
    );

    fetch(basePath + "read-log?name=" + encodeURIComponent(name))
        .then(function(response) {{
            if (!response.ok) {{
                throw new Error("HTTP " + response.status);
            }}
            return response.text();
        }})
        .then(function(data) {{
            document.getElementById("logContent").textContent = data;
            document.getElementById("downloadButton").href =
                basePath + "download-log?name=" + encodeURIComponent(name);
        }})
        .catch(function(error) {{
            document.getElementById("logContent").textContent =
                "Unable to read log: " + error;
        }});
}}

document.getElementById("logSelect").addEventListener("change", openLog);
window.addEventListener("load", openLog);
</script>
""".format(sidebar=app_sidebar("logs"))

    body += page_footer()
    return localize_html(body)


class Handler(BaseHTTPRequestHandler):

    def send_security_headers(self):
        # Pages hold settings: never cache them, and only DSM itself (same
        # origin) may frame them.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")

    def send_html(self, content, status=200):
        data = content.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_security_headers()
        self.end_headers()

        self.wfile.write(data)

    def send_text(self, content, status=200):
        data = content.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_security_headers()
        self.end_headers()

        self.wfile.write(data)

    def redirect(self, location, extra_headers=()):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.send_security_headers()
        self.end_headers()

    def client_ip(self):
        # nginx overwrites X-Real-IP with the real peer address.
        value = (self.headers.get("X-Real-IP", "") or "").strip()
        return value if re.match(r"^[0-9A-Fa-f:.]{3,45}$", value) else self.client_address[0]

    def cookie_value(self, name):
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def send_message_page(self, title, text, status):
        """A tiny page with a title and one sentence, translated like the rest."""
        page = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>{title}</title></head>"
            "<body style=\"font-family:Arial,sans-serif;margin:40px\">"
            "<h2>{title}</h2><p>{text}</p></body></html>"
        ).format(title=html.escape(title), text=html.escape(text))
        self.send_html(localize_html(page), status)

    def send_denied(self):
        self.send_message_page(
            "Access denied",
            "Sign in to DSM as an administrator and open the TorrServer DSM application.",
            403,
        )

    def authorize(self):
        """Only DSM administrators may use the helper. Returns True if allowed;
        otherwise the response has already been sent."""
        parsed = urlparse(self.path)
        query_token = parse_qs(parsed.query).get("SynoToken", [""])[0].strip()
        cookie_token = self.cookie_value(TOKEN_COOKIE)
        header_token = (self.headers.get("X-SYNO-TOKEN", "") or "").strip()

        token = header_token or query_token or cookie_token
        if token and not TOKEN_PATTERN.match(token):
            token = ""

        user, reason = check_dsm_session(
            self.headers.get("Cookie", "") or "",
            token,
            self.client_ip(),
            self.request_host(),
        )

        if not user:
            auth_log("denied {} {} from {}: {}".format(
                self.command, parsed.path, self.client_ip(), reason))
            self.send_denied()
            return False

        # The DSM desktop hands the token over in the URL once; keep it in a
        # cookie and redirect to the clean URL so it does not stay in the
        # address bar or in later links.
        if query_token and query_token == token and query_token != cookie_token \
                and self.command == "GET":
            flags = "; Path={}; HttpOnly; SameSite=Strict".format(TOKEN_COOKIE_PATH)
            if (self.headers.get("X-Forwarded-Proto", "") or "").lower() == "https":
                flags += "; Secure"
            self.redirect(
                "./" + parsed.path.lstrip("/"),
                extra_headers=(("Set-Cookie", "{}={}{}".format(TOKEN_COOKIE, query_token, flags)),),
            )
            return False

        return True

    def request_host(self):
        """Host name from the Host header, restricted to hostname characters
        so it is safe to embed in HTML."""
        raw = (self.headers.get("Host", "") or "").strip()

        if raw.startswith("["):
            name = raw.split("]", 1)[0] + "]"
        else:
            name = raw.split(":", 1)[0]

        if re.match(r"^[A-Za-z0-9._\-\[\]:]{1,255}$", name):
            return name

        return "localhost"

    def is_same_origin(self):
        """CSRF guard: a POST must come from a page served by this host.

        Browsers always send Origin (or at least Referer) on cross-site form
        posts, so a missing or foreign value is rejected.
        """
        expected = self.request_host().strip("[]").lower()

        for header in ("Origin", "Referer"):
            value = self.headers.get(header)
            if not value:
                continue

            try:
                actual = urlparse(value).hostname
            except ValueError:
                return False

            return bool(actual) and actual.lower() == expected

        return False

    def read_post_params(self):
        """Return parsed form parameters, or None after sending an error."""
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self.send_text("Invalid Content-Length", 400)
            return None

        if length < 0:
            self.send_text("Invalid Content-Length", 400)
            return None

        if length > MAX_POST_BYTES:
            self.send_text("Request too large", 413)
            return None

        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return parse_qs(body)

    def do_GET(self):
        if not self.authorize():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.send_html(main_page(self.request_host()))
            return

        if path == "/restart-status":
            self.send_text("wait" if restart_in_progress() else "ready")
            return

        if path == "/settings":
            query = parse_qs(parsed.query)
            torrserver_dir = query.get("torrserver_dir", [""])[0]
            self.send_html(
                settings_page(
                    torrserver_dir_override=torrserver_dir
                )
            )
            return

        if path == "/browse":
            query = parse_qs(parsed.query)
            selected_path = query.get("path", ["/"])[0]
            target = query.get("target", ["cache"])[0]
            self.send_html(cache_browser_page(selected_path, target))
            return

        if path == "/logs":
            self.send_html(logs_page())
            return

        if path == "/read-log":
            query = parse_qs(parsed.query)
            name = query.get("name", [""])[0]
            log_path = get_log_path(name)

            if not log_path:
                self.send_text("Invalid log file", 400)
                return

            self.send_text(get_log_by_name(name))
            return

        if path == "/download-log":
            query = parse_qs(parsed.query)
            name = query.get("name", [""])[0]
            log_path = get_log_path(name)

            if not log_path:
                self.send_text("Invalid log file", 400)
                return

            try:
                with open(log_path, "rb") as f:
                    data = f.read()

                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/octet-stream"
                )
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="{}"'.format(name)
                )
                self.send_header(
                    "Content-Length",
                    str(len(data))
                )
                self.end_headers()

                self.wfile.write(data)

            except FileNotFoundError:
                self.send_text("Log file not found: {}".format(name), 404)
            except Exception as e:
                self.send_text(
                    "Unable to download log: {}".format(str(e)),
                    500,
                )

            return

        self.send_html("Not Found", 404)

    def do_POST(self):
        if not self.authorize():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if not self.is_same_origin():
            self.send_message_page(
                "Forbidden",
                "Cross-site request rejected. Reload the page and try again.",
                403,
            )
            return

        params = self.read_post_params()
        if params is None:
            return

        if path == "/language":
            language = params.get("language", ["en"])[0]
            if set_language(language):
                self.redirect("./settings")
            else:
                self.send_html(settings_page("Invalid language"), 400)
            return

        if path == "/settings":
            ok, message = save_settings(params)

            if ok:
                self.redirect("./settings")
            else:
                self.send_html(settings_page(message), 400)

            return

        if path == "/restart":
            ok, message = restart_torrserver()

            if ok:
                self.redirect("./")
            else:
                self.send_html(
                    localize_html(
                        page_header("Restart Error")
                        + """
<div class="card">
<h1>Restart failed</h1>
<p>{}</p>
</div>
""".format(html.escape(message))
                        + page_footer()
                    ),
                    500,
                )

            return

        self.send_html("Not Found", 404)

    def log_message(self, format_string, *args):
        return


PENDING_POLL_SECONDS = 5


def pending_cache_loop():
    while True:
        try:
            if not apply_pending_cache_path():
                time.sleep(PENDING_POLL_SECONDS)
                continue
        except Exception:
            pass

        time.sleep(PENDING_POLL_SECONDS * 2)


def log_rotation_loop():
    while True:
        rotate_log_if_needed()
        time.sleep(10)


def run():
    # Plain HTTP on loopback only. DSM nginx terminates TLS and proxies
    # /webman/3rdparty/TorrServer/helper/ here, so the browser never talks to
    # this port directly (no mixed content, no unauthenticated LAN exposure).
    server = ThreadingHTTPServer((HOST, HELPER_PORT), Handler)
    server.daemon_threads = True

    rotation_thread = threading.Thread(
        target=log_rotation_loop,
        daemon=True,
    )
    rotation_thread.start()

    pending_thread = threading.Thread(
        target=pending_cache_loop,
        daemon=True,
    )
    pending_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
