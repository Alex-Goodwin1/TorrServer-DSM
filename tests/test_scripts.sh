#!/bin/sh
# Tests for the package shell scripts. No DSM needed.
# Run:  sh tests/test_scripts.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="${ROOT}/src/scripts"
FAILED=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; FAILED=$((FAILED + 1)); }
check() { # check <description> <command...>
    desc="$1"; shift
    if "$@"; then pass "$desc"; else fail "$desc"; fi
}
has()  { [ -e "$VAR/$1" ]; }
hasnt(){ [ ! -e "$VAR/$1" ]; }

setup_var() { # setup_var <ssl mode>
    rm -rf "$TMP/var" "$TMP/target"
    VAR="$TMP/var"; mkdir -p "$VAR" "$TMP/target/ui"
    for f in torrserver.fuse.path server.pem server.key b.tmp c.stage TorrServer.pid accs.db; do echo x > "$VAR/$f"; done
    echo 9999 > "$VAR/torrserver.port"
    echo "$1" > "$VAR/torrserver.ssl.mode"
}

run_setup() { # run_setup <shell snippet>   (extra env passed through)
    env SYNOPKG_PKGNAME=TorrServer SYNOPKG_DSM_VERSION_MAJOR=7 \
        SYNOPKG_PKGVAR="$VAR" SYNOPKG_PKGDEST="$TMP/target" \
        sh -c ". '${SCRIPTS}/service-setup'; $1"
}

echo "== service-setup: upgrade migration"
setup_var self
run_setup migrate_config >/dev/null
check "settings kept"                       has torrserver.port
check "port still 9999"                     [ "$(cat "$VAR/torrserver.port")" = 9999 ]
check "accounts kept"                       has accs.db
check "obsolete torrserver.fuse.path gone"  hasnt torrserver.fuse.path
check "leftover *.tmp/*.stage gone"         sh -c "[ ! -e '$VAR/b.tmp' ] && [ ! -e '$VAR/c.stage' ]"
check "stale server.pem/key gone (self)"    sh -c "[ ! -e '$VAR/server.pem' ] && [ ! -e '$VAR/server.key' ]"
check "stale pid file gone"                 hasnt TorrServer.pid
SCHEMA="$(run_setup 'echo "$CONFIG_SCHEMA"')"
check "schema recorded"                     [ "$(cat "$VAR/config.schema")" = "$SCHEMA" ]

setup_var dsm
run_setup migrate_config >/dev/null
check "server.pem kept in dsm mode"         has server.pem

echo "== second upgrade is a no-op for certificates"
run_setup migrate_config >/dev/null
check "server.pem still kept"               has server.pem

echo "== upgrade wizard: reset requested"
setup_var dsm
echo 1 > "$VAR/cache.pending"
run_setup "wizard_reset_settings=true; migrate_config" >/dev/null
check "port setting removed"                hasnt torrserver.port
check "accounts removed"                    hasnt accs.db
check "pending cache removed"               hasnt cache.pending
check "certificates removed"                sh -c "[ ! -e '$VAR/server.pem' ]"
check "schema recorded after reset"         has config.schema

echo "== DSM upgrade sequence: the real postinst, then the real postupgrade"
mkdir -p "$TMP/stubbin"; printf '#!/bin/sh\nexit 0\n' > "$TMP/stubbin/chown"; chmod +x "$TMP/stubbin/chown"

dsm_prepare() {   # state left by the PREVIOUS release (it has no config.schema)
    D="$TMP/dsm"; rm -rf "$D"; mkdir -p "$D/var" "$D/target" "$D/tmpup" "$D/home"; VAR="$D/var"
}
dsm_old_state() { # dsm_old_state <ssl mode>
    echo 9999 > "$VAR/torrserver.port"; echo '{"u":"p"}' > "$VAR/accs.db"; echo x > "$VAR/torrserver.fuse.path"; echo /volume1/x > "$VAR/cache.path"
    echo "$1" > "$VAR/torrserver.ssl.mode"; echo OLD-DSM-CERT > "$VAR/server.pem"; echo OLD-DSM-KEY > "$VAR/server.key"
    echo x > "$VAR/leftover.tmp"
}
dsm_steps() {     # dsm_steps [reset] <step>...    (DSM calls postinst first, then postupgrade)
    reset=""; [ "$1" = reset ] && { reset=1; shift; }
    for step in "$@"; do
        ( cd "$D" && env PATH="$TMP/stubbin:$PATH" SYNOPKG_PKGNAME=TorrServer SYNOPKG_DSM_VERSION_MAJOR=7 \
            SYNOPKG_PKGVAR="$VAR" SYNOPKG_PKGDEST="$D/target" SYNOPKG_TEMP_UPGRADE_FOLDER="$D/tmpup" \
            SYNOPKG_TEMP_LOGFILE="$D/log.txt" SYNOPKG_PKGHOME="$D/home" ${reset:+wizard_reset_settings=true} \
            sh "${SCRIPTS}/$step" >/dev/null 2>&1 )
    done
}
EXPECTED="$(sh -c ". '${SCRIPTS}/service-setup' 2>/dev/null; echo \$CONFIG_SCHEMA" 2>/dev/null || true)"

dsm_prepare; dsm_old_state self
echo "old service output" > "$VAR/service.log"; echo "old cert log" > "$VAR/Helper.log"; echo "older" > "$VAR/Helper.log.1"
echo "TorrServer history" > "$VAR/TorrServer.log"
dsm_steps postinst postupgrade
check "upgrade: the separate service.log / Helper.log are removed" sh -c "[ ! -e '$VAR/service.log' ] && [ ! -e '$VAR/Helper.log' ] && [ ! -e '$VAR/Helper.log.1' ]"
check "upgrade: TorrServer.log and its history are kept"    sh -c "[ \"\$(cat '$VAR/TorrServer.log')\" = 'TorrServer history' ]"
check "upgrade: obsolete torrserver.fuse.path removed"      hasnt torrserver.fuse.path
check "upgrade: obsolete cache.path removed"                hasnt cache.path
check "upgrade: stale DSM certificate removed (self mode)"  sh -c "[ ! -e '$VAR/server.pem' ] && [ ! -e '$VAR/server.key' ]"
check "upgrade: leftover *.tmp removed"                     hasnt leftover.tmp
check "upgrade: settings are kept"                          sh -c "[ \"\$(cat '$VAR/torrserver.port')\" = 9999 ] && [ -e '$VAR/accs.db' ]"
check "upgrade: default files created by postinst exist"    sh -c "[ -e '$VAR/torrserver.fuse' ] && [ -e '$VAR/torrserver.dir' ]"
check "upgrade: schema recorded at the end"                 [ "$(cat "$VAR/config.schema")" = "$SCHEMA" ]

dsm_prepare; dsm_old_state dsm; dsm_steps postinst postupgrade
check "upgrade (dsm cert mode): the certificate is kept"    sh -c "[ -e '$VAR/server.pem' ] && [ -e '$VAR/server.key' ]"
check "upgrade (dsm cert mode): obsolete file still removed" hasnt torrserver.fuse.path

dsm_prepare; dsm_old_state self; dsm_steps reset postinst postupgrade
check "upgrade + reset: package settings removed"           sh -c "[ ! -e '$VAR/torrserver.port' ] && [ ! -e '$VAR/accs.db' ] && [ ! -e '$VAR/torrserver.dir' ]"
check "upgrade + reset: obsolete file removed too"          hasnt torrserver.fuse.path
check "upgrade + reset: schema recorded"                    [ "$(cat "$VAR/config.schema")" = "$SCHEMA" ]

dsm_prepare; dsm_old_state self; echo 2 > "$VAR/config.schema"      # the broken earlier build marked it 2 without cleaning
dsm_steps postinst postupgrade
check "repair: an installation marked by the broken build is cleaned" sh -c "[ ! -e '$VAR/torrserver.fuse.path' ] && [ ! -e '$VAR/server.pem' ]"

dsm_prepare; dsm_old_state self; dsm_steps postinst postupgrade
echo "TORRSERVER-OWN-CERT" > "$VAR/server.pem"; echo "TORRSERVER-OWN-KEY" > "$VAR/server.key"    # TorrServer generated its own
dsm_steps postinst postupgrade                                                                  # the NEXT upgrade
check "next upgrade: TorrServer's own certificate is NOT deleted" sh -c "[ \"\$(cat '$VAR/server.pem')\" = TORRSERVER-OWN-CERT ] && [ -e '$VAR/server.key' ]"

dsm_prepare; dsm_steps postinst            # fresh install: empty var, postinst only
check "fresh install: schema recorded"                      [ "$(cat "$VAR/config.schema")" = "$SCHEMA" ]
check "fresh install: default files created"                sh -c "[ -e '$VAR/torrserver.fuse' ] && [ -e '$VAR/torrserver.dir' ]"
listing="$(LC_ALL=C ls "$VAR" | tr '\n' ' ')"
check "fresh install: the installer does not create the shared log (root would own it)" hasnt TorrServer.log
check "fresh install: only the expected files appear"       [ "$listing" = "config.schema torrserver.dir torrserver.fuse " ]

echo "== service lifecycle: the real start-stop-status (run with bash, which is what DSM's sh is)"
if [ -x /bin/python3 ] && command -v bash >/dev/null 2>&1; then
    S="$TMP/svc"; rm -rf "$S"; mkdir -p "$S/scripts" "$S/var" "$S/target/bin" "$S/target/helper" "$S/target/ui"
    cp "${SCRIPTS}/start-stop-status" "${SCRIPTS}/service-setup" "${SCRIPTS}/restart-torrserver" "$S/scripts/"
    # Stand-ins: a "TorrServer" that records its arguments, and a helper that only waits.
    cat > "$S/target/bin/TorrServer" <<'FAKE'
#!/bin/sh
printf '%s\n' "$@" > "$FAKE_ARGS_FILE"
while true; do sleep 1; done
FAKE
    printf 'import time\ntime.sleep(300)\n' > "$S/target/helper/helper.py"
    chmod +x "$S/target/bin/TorrServer"
    ssc() { # ssc <action>
        env FAKE_ARGS_FILE="$S/args.txt" SYNOPKG_PKGNAME=TorrServer SYNOPKG_DSM_VERSION_MAJOR=7 \
            SYNOPKG_PKGDEST="$S/target" SYNOPKG_PKGVAR="$S/var" bash "$S/scripts/start-stop-status" "$1"
    }
    # A killed process whose parent has not reaped it yet is a zombie: kill -0
    # still succeeds on it, so look at the process state as well.
    alive() { kill -0 "$1" 2>/dev/null && [ "$(ps -o stat= -p "$1" 2>/dev/null | cut -c1)" != "Z" ]; }
    gone() { # gone <pid>  (allow a few seconds for the process table to settle)
        n=0; while alive "$1" && [ "$n" -lt 8 ]; do sleep 1; n=$((n + 1)); done; ! alive "$1"; }
    all_alive() { for p in "$@"; do alive "$p" || return 1; done; }
    all_gone() { for p in "$@"; do gone "$p" || return 1; done; }
    cleanup_svc() { for p in $(cat "$S/var/TorrServer.pid" 2>/dev/null); do kill -9 "$p" 2>/dev/null; done; }
    trap 'cleanup_svc; rm -rf "$TMP"' EXIT

    ssc status >/dev/null; rc=$?
    check "not started yet: status exits 3"                  [ "$rc" -eq 3 ]

    echo "history from the previous run" > "$S/var/TorrServer.log"
    ssc start; rc=$?
    check "start exits 0"                                    [ "$rc" -eq 0 ]
    check "...and does not wipe the earlier log"             grep -q "history from the previous run" "$S/var/TorrServer.log"
    PIDS="$(cat "$S/var/TorrServer.pid" 2>/dev/null)"
    set -- $PIDS; HELPER_PID="$1"; TS_PID="$2"
    check "pid file lists exactly two processes"             [ "$#" -eq 2 ]
    check "both processes are running"                       all_alive "$HELPER_PID" "$TS_PID"
    sleep 1
    check "TorrServer got -d <var directory>"                grep -qx -- "$S/var" "$S/args.txt"
    check "TorrServer got the default port 8090"             sh -c "grep -A1 -x -- '-p' '$S/args.txt' | grep -qx 8090"
    check "TorrServer got its own log file (-l)"             sh -c "grep -A1 -x -- '-l' '$S/args.txt' | grep -qx '$S/var/TorrServer.log'"
    check "the one log gets a start line in TorrServer's format" grep -Eq "^[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9:]{8} UTC0 service: Starting TorrServer" "$S/var/TorrServer.log"
    check "no separate service.log is written"              sh -c "[ ! -e '$S/var/service.log' ]"
    check "service_prestart ran: firewall file written"      grep -q 'dst.ports="8090/tcp"' "$S/target/ui/TorrServer.sc"

    out="$(ssc status)"; rc=$?
    check "status exits 0 while running"                     [ "$rc" -eq 0 ]
    check "status says it is running"                        sh -c "echo '$out' | grep -q 'is running'"

    ssc start; rc=$?
    check "a second start is harmless (exit 0)"              [ "$rc" -eq 0 ]
    check "...and does not change the pids"                  [ "$(cat "$S/var/TorrServer.pid")" = "$PIDS" ]
    check "...it only says that it is already running"       grep -q "already running" "$S/var/TorrServer.log"

    ssc stop; rc=$?
    check "stop exits 0"                                     [ "$rc" -eq 0 ]
    check "both processes are gone"                          all_gone "$HELPER_PID" "$TS_PID"
    check "pid file removed"                                 sh -c "[ ! -e '$S/var/TorrServer.pid' ]"
    ssc status >/dev/null; rc=$?
    check "after stop: status exits 3"                       [ "$rc" -eq 3 ]
    ssc stop; rc=$?
    check "stopping a stopped service is harmless (exit 0)"  [ "$rc" -eq 0 ]

    echo 999999 > "$S/var/TorrServer.pid"                    # a pid that does not exist
    ssc status >/dev/null; rc=$?
    check "a stale pid file means not running (exit 3)"      [ "$rc" -eq 3 ]
    check "...and the stale file is removed"                 sh -c "[ ! -e '$S/var/TorrServer.pid' ]"

    # TorrServer stopped on its own (its web page, a crash): the package still
    # counts as running while the helper is alive, so DSM keeps the desktop icon.
    ssc start >/dev/null; sleep 1
    set -- $(cat "$S/var/TorrServer.pid"); HELPER_PID="$1"; TS_PID="$2"
    kill -TERM "$TS_PID"; gone "$TS_PID"
    ssc status >/dev/null; rc=$?
    check "TorrServer gone, helper alive: status stays 0 (icon stays)" [ "$rc" -eq 0 ]
    check "...the pid file still holds the helper"           sh -c "grep -q '^$HELPER_PID' '$S/var/TorrServer.pid'"
    ssc start; rc=$?
    set -- $(cat "$S/var/TorrServer.pid" 2>/dev/null)
    check "start next to a living helper exits 0"            [ "$rc" -eq 0 ]
    check "...brings TorrServer back"                        alive "$2"
    check "...without touching the helper"                   [ "$1" = "$HELPER_PID" ] && alive "$HELPER_PID"
    kill -TERM "$2"; gone "$2"
    ssc stop; rc=$?
    check "stop with only the helper alive exits 0"          [ "$rc" -eq 0 ]
    check "...and stops the helper"                          gone "$HELPER_PID"
    ssc status >/dev/null; rc=$?
    check "helper gone: status exits 3"                      [ "$rc" -eq 3 ]

    # Stopping also removes a FUSE mount that TorrServer did not release.
    printf '#!/bin/sh\necho "$*" >> "$FAKE_UMOUNT_LOG"\n' > "$S/fake-fusermount"; chmod +x "$S/fake-fusermount"
    echo "torrserver-fuse /volume1/left/FUSE fuse.torrserver rw 0 0" > "$S/mounts"
    : > "$S/umount.log"
    export MOUNTS_FILE="$S/mounts" FUSERMOUNT_CMD="$S/fake-fusermount" FAKE_UMOUNT_LOG="$S/umount.log"
    ssc start >/dev/null; sleep 1
    check "start unmounts a leftover FUSE mount first"       grep -qx -- "-u -z -- /volume1/left/FUSE" "$S/umount.log"
    : > "$S/umount.log"
    ssc stop >/dev/null; sleep 1
    check "stop removes a leftover FUSE mount as well"       grep -qx -- "-u -z -- /volume1/left/FUSE" "$S/umount.log"
    unset MOUNTS_FILE FUSERMOUNT_CMD FAKE_UMOUNT_LOG

    echo 9999 > "$S/var/torrserver.port"
    ssc start >/dev/null; sleep 1
    check "a saved port is used on the next start"           sh -c "grep -A1 -x -- '-p' '$S/args.txt' | grep -qx 9999"
    check "...and appears in the firewall file"              grep -q 'dst.ports="9999/tcp"' "$S/target/ui/TorrServer.sc"
    ssc stop >/dev/null; sleep 1

    echo "== restart-torrserver: no root, helper keeps running"
    rst() { # rst <helper pid> [extra env...]
        h="$1"; shift
        env "$@" TORRSERVER_HELPER_PID="$h" FAKE_ARGS_FILE="$S/args.txt" SYNOPKG_PKGNAME=TorrServer SYNOPKG_DSM_VERSION_MAJOR=7 \
            SYNOPKG_PKGDEST="$S/target" SYNOPKG_PKGVAR="$S/var" RESTART_STOP_TIMEOUT=3 sh "$S/scripts/restart-torrserver"
    }
    rm -f "$S/var/torrserver.port"
    ssc start >/dev/null; sleep 1
    set -- $(cat "$S/var/TorrServer.pid"); HELPER_PID="$1"; OLD_TS="$2"
    echo 9100 > "$S/var/torrserver.port"
    rst "$HELPER_PID"; rc=$?
    set -- $(cat "$S/var/TorrServer.pid" 2>/dev/null); NEW_HELPER="$1"; NEW_TS="$2"
    check "restart exits 0"                                  [ "$rc" -eq 0 ]
    check "the old TorrServer is gone"                       gone "$OLD_TS"
    check "a new TorrServer runs"                            alive "$NEW_TS"
    check "...with a different pid"                          [ "$NEW_TS" != "$OLD_TS" ]
    check "the helper was not touched"                       alive "$HELPER_PID"
    check "pid file keeps the helper pid first"              [ "$NEW_HELPER" = "$HELPER_PID" ]
    check "pid file lists exactly two processes"             [ "$(cat "$S/var/TorrServer.pid" | wc -w)" -eq 2 ]
    sleep 1
    check "the new settings are used (port 9100)"            sh -c "grep -A1 -x -- '-p' '$S/args.txt' | grep -qx 9100"
    check "the restart lock is released"                     sh -c "[ ! -e '$S/var/restart.lock' ]"
    ssc status >/dev/null; rc=$?
    check "DSM still sees the package as running"            [ "$rc" -eq 0 ]

    rst "$HELPER_PID"; rc=$?                                 # a second restart in a row
    set -- $(cat "$S/var/TorrServer.pid"); SECOND_TS="$2"
    check "a second restart works"                           sh -c "[ $rc -eq 0 ] && [ '$SECOND_TS' != '$NEW_TS' ]"
    check "...and the previous one was stopped"              gone "$NEW_TS"

    echo 999999 > "$S/var/TorrServer.pid"                    # stale pid file (TorrServer crashed)
    rst "$HELPER_PID"; rc=$?
    set -- $(cat "$S/var/TorrServer.pid" 2>/dev/null)
    check "restart works with a stale pid file"              sh -c "[ $rc -eq 0 ] && [ '$1' = '$HELPER_PID' ] && kill -0 $2"
    check "...and the stale TorrServer was stopped first"    gone "$SECOND_TS"

    mkdir "$S/var/restart.lock"; echo "$HELPER_PID" > "$S/var/restart.lock/pid"     # a live owner
    before="$(cat "$S/var/TorrServer.pid")"
    rst "$HELPER_PID"; rc=$?
    check "a restart in progress blocks a second one"        sh -c "[ $rc -eq 0 ] && [ \"\$(cat '$S/var/TorrServer.pid')\" = '$before' ]"
    echo 999999 > "$S/var/restart.lock/pid"                  # a dead owner
    rst "$HELPER_PID"; rc=$?
    check "a stale lock does not block the restart"          sh -c "[ $rc -eq 0 ] && [ \"\$(cat '$S/var/TorrServer.pid')\" != '$before' ]"

    rm -f "$S/var/TorrServer.pid"
    env -u TORRSERVER_HELPER_PID SYNOPKG_PKGNAME=TorrServer SYNOPKG_DSM_VERSION_MAJOR=7 SYNOPKG_PKGDEST="$S/target" SYNOPKG_PKGVAR="$S/var" \
        sh "$S/scripts/restart-torrserver"; rc=$?
    check "without a helper pid it refuses (exit 1)"         [ "$rc" -eq 1 ]
    check "...and the log says why"                          grep -q "helper pid is unknown" "$S/var/TorrServer.log"

    for p in $(ps -eo pid=,args= | grep "$S/target/bin/TorrServer" | grep -v grep | awk '{print $1}'); do kill -9 "$p" 2>/dev/null; done
    kill -9 "$HELPER_PID" 2>/dev/null; rm -f "$S/var/TorrServer.pid" "$S/var/torrserver.port"

    echo "== restart-torrserver: a short DSM timeout does not cut the shutdown short"
    cat > "$S/target/bin/TorrServer" <<'SLOW'
#!/bin/sh
# needs 3 seconds to shut down after SIGTERM
trap 'sleep 3; exit 0' TERM
while true; do sleep 1; done
SLOW
    ssc start >/dev/null; sleep 1
    set -- $(cat "$S/var/TorrServer.pid"); H="$1"; SLOW_TS="$2"
    : > "$S/var/TorrServer.log"
    env SVC_WAIT_TIMEOUT=1 TORRSERVER_HELPER_PID="$H" FAKE_ARGS_FILE="$S/args.txt" SYNOPKG_PKGNAME=TorrServer SYNOPKG_DSM_VERSION_MAJOR=7 \
        SYNOPKG_PKGDEST="$S/target" SYNOPKG_PKGVAR="$S/var" sh "$S/scripts/restart-torrserver"; rc=$?
    check "restart succeeds although DSM says SVC_WAIT_TIMEOUT=1" [ "$rc" -eq 0 ]
    check "...the slow TorrServer was stopped gracefully"        sh -c "! grep -q 'ignored SIGTERM' '$S/var/TorrServer.log'"
    for p in $(ps -eo pid=,args= | grep "$S/target/bin/TorrServer" | grep -v grep | awk '{print $1}'); do kill -9 "$p" 2>/dev/null; done
    kill -9 "$H" 2>/dev/null; rm -f "$S/var/TorrServer.pid"

    # A service that ignores SIGTERM must still be stopped (SIGKILL after the timeout).
    cat > "$S/target/bin/TorrServer" <<'STUBBORN'
#!/bin/sh
trap '' TERM
while true; do sleep 1; done
STUBBORN
    ssc start >/dev/null; sleep 1
    set -- $(cat "$S/var/TorrServer.pid"); STUBBORN_PID="$2"
    check "the stubborn service is running"                  alive "$STUBBORN_PID"
    env SVC_WAIT_TIMEOUT=2 FAKE_ARGS_FILE="$S/args.txt" SYNOPKG_PKGNAME=TorrServer SYNOPKG_DSM_VERSION_MAJOR=7 \
        SYNOPKG_PKGDEST="$S/target" SYNOPKG_PKGVAR="$S/var" bash "$S/scripts/start-stop-status" stop; rc=$?
    sleep 1
    check "stop exits 0 even when SIGTERM is ignored"        [ "$rc" -eq 0 ]
    check "...the service was killed"                        gone "$STUBBORN_PID"
    check "...and the pid file is gone"                      sh -c "[ ! -e '$S/var/TorrServer.pid' ]"
    check "...the log says a KILL was needed"                sh -c "grep -q 'Stopping TorrServer service' '$S/var/TorrServer.log'"

    ssc bogus >/dev/null 2>&1; rc=$?
    check "an unknown action exits 1"                        [ "$rc" -eq 1 ]
    cleanup_svc
else
    echo "SKIP (needs /bin/python3 and bash)"
fi

echo "== service-setup: sourcing has no side effects (status polls)"
setup_var self; rm -f "$VAR/config.schema"
run_setup ":" >/dev/null 2>&1
check "no .sc written when merely sourced"  sh -c "[ -z \"\$(ls '$TMP/target/ui')\" ]"
check "no schema written when sourced"      hasnt config.schema

echo "== service-setup: command line"
setup_var self; echo 1 > "$VAR/torrserver.auth"; echo '{"u":"p"}' > "$VAR/accs.db"
out="$(run_setup 'echo "$SERVICE_COMMAND"')"
check "auth flag passed"                    sh -c "echo '$out' | grep -q ' -a '"
check "one log for everything"              run_setup '[ "$LOG_FILE" = "$TORRSERVER_LOG" ] && [ "${LOG_FILE##*/}" = TorrServer.log ]'

echo "== service-setup: which Python runs the Helper"
setup_var self; mkdir -p "$TMP/py/Python3.9/target/usr/bin"
printf '#!/bin/sh\n' > "$TMP/py/system-python3"; chmod +x "$TMP/py/system-python3"
cp "$TMP/py/system-python3" "$TMP/py/Python3.9/target/usr/bin/python3"
out="$(PYTHON_CANDIDATES="$TMP/py/none $TMP/py/system-python3 $TMP/py/Python3.9/target/usr/bin/python3" run_setup 'echo "$HELPER_COMMAND"')"
check "the system Python is used when it exists"        sh -c "echo '$out' | grep -q '^$TMP/py/system-python3 '"
out="$(PYTHON_CANDIDATES="$TMP/py/none $TMP/py/Python3*/target/usr/bin/python3" run_setup 'echo "$HELPER_COMMAND"')"
check "...else the Python 3 package from Package Center" sh -c "echo '$out' | grep -q '^$TMP/py/Python3.9/target/usr/bin/python3 '"
out="$(PYTHON_CANDIDATES="$TMP/py/none" run_setup 'echo "$HELPER_COMMAND"')"
check "...and with none the old path stays (the log says why)" sh -c "echo '$out' | grep -q '^/bin/python3 '"
PYTHON_CANDIDATES="$TMP/py/none" run_setup 'service_prestart' >/dev/null 2>&1
check "...no Python: the log tells to install Python 3"  grep -q "Python 3 was not found" "$VAR/TorrServer.log"

echo "== a FUSE mount left behind is removed before TorrServer starts"
setup_var self; rm -f "$VAR/TorrServer.log"
cat > "$TMP/fake-fusermount" <<'FAKE'
#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_UMOUNT_LOG"
case "$*" in *broken*) exit 1 ;; esac
exit 0
FAKE
chmod +x "$TMP/fake-fusermount"
cat > "$TMP/mounts" <<'MOUNTS'
fusectl /sys/fs/fuse/connections fusectl rw,nosuid,nodev,noexec,relatime 0 0
torrserver-fuse /volume1/docker/PlexTorr/FUSE fuse.torrserver rw,nosuid,nodev,relatime,user_id=179006,group_id=179006,allow_other 0 0
torrserver-fuse /volume1/my\040films/FUSE fuse.torrserver rw,nosuid,nodev,relatime,user_id=179006,group_id=179006,allow_other 0 0
torrserver-fuse /volume1/broken/FUSE fuse.torrserver rw,nosuid,nodev,relatime,user_id=179006,group_id=179006,allow_other 0 0
other /volume1/other fuse.sshfs rw 0 0
MOUNTS
: > "$TMP/umount.log"
MOUNTS_FILE="$TMP/mounts" FUSERMOUNT_CMD="$TMP/fake-fusermount" FAKE_UMOUNT_LOG="$TMP/umount.log" \
    run_setup 'cleanup_stale_fuse_mounts' >/dev/null 2>&1
check "every leftover fuse.torrserver mount is unmounted (lazily)" grep -qx -- "-u -z -- /volume1/docker/PlexTorr/FUSE" "$TMP/umount.log"
check "...a path with a space is decoded"                          grep -qx -- "-u -z -- /volume1/my films/FUSE" "$TMP/umount.log"
check "...other mounts are not touched"                            sh -c "! grep -q -e fusectl -e sshfs -e /volume1/other '$TMP/umount.log'"
check "...it is written to the log"                                grep -q "Unmounted a leftover FUSE mount at /volume1/docker/PlexTorr/FUSE" "$VAR/TorrServer.log"
check "...one that cannot be unmounted says how to do it by hand"  grep -q "umount -l '/volume1/broken/FUSE'" "$VAR/TorrServer.log"

# A running TorrServer owns its mount: nothing is unmounted then.
mkdir -p "$TMP/target/bin"
printf '#!/bin/sh\nwhile true; do sleep 1; done\n' > "$TMP/target/bin/TorrServer"; chmod +x "$TMP/target/bin/TorrServer"
"$TMP/target/bin/TorrServer" & RUNNING=$!
sleep 1; : > "$TMP/umount.log"
MOUNTS_FILE="$TMP/mounts" FUSERMOUNT_CMD="$TMP/fake-fusermount" FAKE_UMOUNT_LOG="$TMP/umount.log" \
    run_setup 'cleanup_stale_fuse_mounts' >/dev/null 2>&1
kill "$RUNNING" 2>/dev/null; wait "$RUNNING" 2>/dev/null
check "a running TorrServer: nothing is unmounted"                 sh -c "[ ! -s '$TMP/umount.log' ]"
rm -rf "$TMP/target/bin"

echo "== preinst: the install stops with a message when there is no Python 3"
pre() { # pre <PYTHON_CANDIDATES> : run the real preinst the way DSM does
    PD="$TMP/pre"; rm -rf "$PD"; mkdir -p "$PD/var" "$PD/target"
    ( cd "$PD" && env PYTHON_CANDIDATES="$1" SYNOPKG_PKGNAME=TorrServer SYNOPKG_DSM_VERSION_MAJOR=7 \
        SYNOPKG_PKGVAR="$PD/var" SYNOPKG_PKGDEST="$PD/target" SYNOPKG_TEMP_LOGFILE="$PD/log.txt" \
        sh "${SCRIPTS}/preinst" >/dev/null 2>&1 )
}
pre "$TMP/py/system-python3"; rc=$?
check "with Python 3 the install goes on (exit 0)"      [ "$rc" -eq 0 ]
pre "$TMP/py/none"; rc=$?
check "without it the install fails (exit 1)"           [ "$rc" -eq 1 ]
check "...and the dialog text says what to install"    grep -q "Install the Python 3 package from DSM Package Center" "$TMP/pre/log.txt"

echo "== service-setup: FUSE path with spaces is skipped"
setup_var self; mkdir -p "$TMP/My Data/FUSE"; echo 1 > "$VAR/torrserver.fuse"; echo "$TMP/My Data" > "$VAR/torrserver.dir"
check "no --fusepath"                       run_setup '[ -z "$FUSE_ARGS" ] && [ -n "$FUSE_SKIPPED_REASON" ]'
setup_var self; mkdir -p "$TMP/Data/FUSE"; echo 1 > "$VAR/torrserver.fuse"; echo "$TMP/Data" > "$VAR/torrserver.dir"
check "normal path gets --fusepath"         run_setup 'case "$FUSE_ARGS" in "--fusepath "*"/Data/FUSE") true;; *) false;; esac'

echo "== service-setup: firewall file"
setup_var self; echo 1 > "$VAR/torrserver.https"; echo 9443 > "$VAR/torrserver.https.port"
run_setup update_firewall_port >/dev/null 2>&1
check "web + https ports listed"            grep -q 'dst.ports="9999,9443/tcp"' "$TMP/target/ui/TorrServer.sc"
check "helper port NOT exposed"             sh -c "! grep -q 42777 '$TMP/target/ui/TorrServer.sc'"

echo "== certificate-helper: logging into the shared log (it runs as root)"
CH="$TMP/ch"; rm -rf "$CH"; mkdir -p "$CH"
chlog() { # chlog <log file> : run the real log_message() against that file
    fn="$(sed -n '/^log_message()/,/^}/p' "${SCRIPTS}/certificate-helper")"
    LOGF="$1" FN="$fn" sh -c 'LOG_FILE="$LOGF"; eval "$FN"; log_message hello'
}
check "certificate-helper uses TorrServer.log"            grep -q '^LOG_FILE="${CONFIG_DIR}/TorrServer.log"' "${SCRIPTS}/certificate-helper"
: > "$CH/TorrServer.log"; chlog "$CH/TorrServer.log"
check "...appends in TorrServer's format to an existing log" grep -Eq "^[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9:]{8} UTC0 certificate-helper: hello$" "$CH/TorrServer.log"
chlog "$CH/missing.log"
check "...never creates the log (root would own it)"       sh -c "[ ! -e '$CH/missing.log' ]"
echo keep > "$CH/target.txt"; ln -s "$CH/target.txt" "$CH/link.log"; chlog "$CH/link.log"
check "...never follows a symbolic link"                   sh -c "[ \"\$(cat '$CH/target.txt')\" = keep ]"

echo "== certificate-helper: path resolution"
mkdir -p "$TMP/syno/sub" "$TMP/volume1/share"
echo c > "$TMP/syno/sub/cert.pem"; echo c > "$TMP/volume1/share/ok.pem"
ln -s /etc/passwd "$TMP/syno/sub/evil.pem"; ln -s "$TMP/syno/sub/cert.pem" "$TMP/volume1/share/link.pem"
sed -n '/^resolve_path()/,/^}/p' "${SCRIPTS}/certificate-helper" \
  | sed "s|\${CERT_ROOT}|$TMP/syno|g; s|/volume\[0-9\]\*/\*|$TMP/volume[0-9]*/*|" > "$TMP/fn.sh"
allowed() { sh -c ". '$TMP/fn.sh'; resolve_path '$2' $1" >/dev/null 2>&1; }
check "dsm: normal file allowed"            allowed dsm "$TMP/syno/sub/cert.pem"
check "dsm: '..' rejected"                  sh -c "! sh -c \". '$TMP/fn.sh'; resolve_path '$TMP/syno/../../../etc/passwd' dsm\" >/dev/null 2>&1"
check "dsm: symlink out of root rejected"   sh -c "! sh -c \". '$TMP/fn.sh'; resolve_path '$TMP/syno/sub/evil.pem' dsm\" >/dev/null 2>&1"
check "manual: normal file allowed"         allowed manual "$TMP/volume1/share/ok.pem"
check "manual: symlink out of share rejected" sh -c "! sh -c \". '$TMP/fn.sh'; resolve_path '$TMP/volume1/share/link.pem' manual\" >/dev/null 2>&1"

echo "== prepare-directory: rejects bad input (needs root; rejections change nothing)"
if [ "$(id -u)" -eq 0 ]; then RUN=""; elif sudo -n true 2>/dev/null; then RUN="sudo -n"; else RUN="skip"; fi
if [ "$RUN" = skip ]; then
    echo "SKIP (not root and no passwordless sudo)"
else
    $RUN sh "${SCRIPTS}/prepare-directory" >/dev/null 2>&1; rc=$?
    check "no argument exits 64 (the Helper's permission probe relies on it)" [ "$rc" -eq 64 ]
    $RUN sh "${SCRIPTS}/prepare-directory" a b >/dev/null 2>&1; rc=$?
    check "wrong argument count also exits 64" [ "$rc" -eq 64 ]
    $RUN sh "${SCRIPTS}/prepare-directory" "/etc" >/dev/null 2>&1; rc=$?
    check "a rejected directory is NOT 64 (not mistaken for the probe)" [ "$rc" -eq 1 ]
    for bad in "/volume1/../etc/x" "/volume1" "/etc" "/volume1/a b" "/volume1/ok/.."; do
        check "rejects '$bad'"              sh -c "! $RUN sh '${SCRIPTS}/prepare-directory' '$bad' >/dev/null 2>&1"
    done
fi

echo "== release notes extraction (CI release job)"
NOTES="${ROOT}/.github/scripts/release-notes.sh"
cat > "$TMP/CHANGELOG.md" <<'CL'
# Changelog

Intro text that is not part of any release.

## 2.0.2 (2026-02-02)

New things.

### Fixed

- a fix

## 2.0.1 (2026-01-01)

Old things.

## 2.0.0 (2025-12-01)

## 1.9.9 (2025-11-01)

Empty heading above.
CL
top="$(sh "$NOTES" 2.0.2 "$TMP/CHANGELOG.md")"
check "top entry is returned"                    sh -c "echo '$top' | grep -q 'New things'"
check "its sub-sections are included"            sh -c "echo '$top' | grep -q '^- a fix'"
check "older entries are not included"           sh -c "! echo '$top' | grep -q 'Old things'"
check "the heading itself is not repeated"       sh -c "! echo '$top' | grep -q '^## 2.0.2'"
check "text before the first entry is skipped"   sh -c "! echo '$top' | grep -q 'Intro text'"
old="$(sh "$NOTES" 2.0.1 "$TMP/CHANGELOG.md")"
check "an older entry can be selected"           sh -c "[ \"$old\" = 'Old things.' ]"
sh "$NOTES" 2.0.2 "$TMP/CHANGELOG.md" > "$TMP/n.txt"
check "no leading blank line"                    sh -c "[ \"\$(head -n1 '$TMP/n.txt')\" = 'New things.' ]"
check "no trailing blank lines"                  sh -c "[ \"\$(tail -n1 '$TMP/n.txt')\" = '- a fix' ]"
check "unknown version -> nothing, exit 1"       sh -c "! sh '$NOTES' 9.9.9 '$TMP/CHANGELOG.md' >/dev/null"
check "a version prefix does not match"          sh -c "! sh '$NOTES' 2.0 '$TMP/CHANGELOG.md' >/dev/null"
check "an empty entry -> exit 1 (falls back)"    sh -c "! sh '$NOTES' 2.0.0 '$TMP/CHANGELOG.md' >/dev/null"
check "missing file -> exit 1"                   sh -c "! sh '$NOTES' 2.0.2 '$TMP/nope.md' >/dev/null"
check "missing version argument -> exit 1"       sh -c "! sh '$NOTES' '' '$TMP/CHANGELOG.md' >/dev/null"
check "the real CHANGELOG has an entry for itself" sh -c "v=\$(grep -m1 '^## ' '${ROOT}/CHANGELOG.md' | awk '{print \$2}'); sh '$NOTES' \"\$v\" '${ROOT}/CHANGELOG.md' >/dev/null"

echo "== release-check.sh (pre-flight check before a release)"
if command -v git >/dev/null 2>&1; then
    CHECK="${ROOT}/.github/scripts/release-check.sh"
    newrepo() { # a throw-away repository that is ready to release version 2.3.145.2, with an origin
        R="$TMP/rel"; O="$TMP/origin.git"; rm -rf "$R" "$O"
        git init -q --bare "$O"
        mkdir -p "$R/.github/scripts"; cp "${ROOT}/.github/scripts/release-notes.sh" "$R/.github/scripts/"
        cp "${ROOT}/build-package.sh" "$R/"
        printf 'TORRSERVER_VERSION := MatriX.145.2\nPKG_VERSION := 2.3.145.2\n\nARCHES := amd64 arm64 arm7\n' > "$R/Makefile"
        printf '# Changelog\n\n## 2.3.145.2 (2026-10-07)\n\nFixes.\n\n## 2.2.145.2 (2026-10-06)\n\nOlder.\n' > "$R/CHANGELOG.md"
        : > "$R/checksums.sha256"
        ( cd "$R" && git init -q -b main && git config user.email t@t && git config user.name t && git add -A \
          && git commit -qm init && git tag v2.2.145.2 && git checkout -qb release && git remote add origin "$O" \
          && git push -q origin --all && git push -q origin --tags ) 2>/dev/null
    }
    pins() { # all six pins the build needs
        for k in TorrServer-MatriX.145.2-linux-amd64 TorrServer-MatriX.145.2-linux-arm64 TorrServer-MatriX.145.2-linux-arm7 \
                 ffprobe-6.1-linux-64.zip ffprobe-6.1-linux-arm-64.zip ffprobe-6.1-linux-armhf-32.zip; do
            printf '%064d  %s\n' 1 "$k"; done > "$R/checksums.sha256"
    }
    commit_all() { ( cd "$R" && git add -A && git commit -qm change ) >/dev/null 2>&1; }
    rc_check() { ( cd "$R" && RELEASE_CHECK_SKIP_TESTS=1 sh "$CHECK" ) > "$TMP/rc.out" 2>&1; echo $?; }

    newrepo; pins; commit_all
    check "a repository that is ready passes"               [ "$(rc_check)" = 0 ]
    check "...and says READY"                               grep -q "READY to release v2.3.145.2" "$TMP/rc.out"
    check "...with no warning when every download is pinned" grep -q "(1 warning(s))" "$TMP/rc.out"   # only 'tests skipped'

    newrepo; pins; commit_all; echo x > "$R/stray.txt"
    check "uncommitted changes are refused"                 [ "$(rc_check)" = 1 ]
    check "...and named"                                    grep -q "FAIL  uncommitted changes" "$TMP/rc.out"

    newrepo; pins; sed -i 's/^PKG_VERSION := .*/PKG_VERSION := 2.2.145.2/' "$R/Makefile"; sed -i 's/^## 2.3.145.2/## 2.2.145.2/' "$R/CHANGELOG.md"; commit_all
    check "a version that is already tagged is refused"     [ "$(rc_check)" = 1 ]
    check "...it says the tag exists"                       grep -q "tag v2.2.145.2 already exists" "$TMP/rc.out"
    check "...and does not call that version newer"         sh -c "! grep -q 'is newer than the latest release' '$TMP/rc.out'"

    newrepo; pins; sed -i 's/^PKG_VERSION := .*/PKG_VERSION := 2.1.145.2/' "$R/Makefile"; sed -i 's/^## 2.3.145.2/## 2.1.145.2/' "$R/CHANGELOG.md"; commit_all
    check "a version older than the latest release is refused" [ "$(rc_check)" = 1 ]
    check "...it says it is not newer"                      grep -q "is not newer than the latest release 2.2.145.2" "$TMP/rc.out"

    newrepo; pins; ( cd "$R" && git tag v2.3.145.2 && git push -q origin --tags ) 2>/dev/null; ( cd "$R" && git tag -d v2.3.145.2 ) >/dev/null 2>&1
    check "a tag that exists only on GitHub is refused"     [ "$(rc_check)" = 1 ]
    check "...it says the tag exists"                       grep -q "tag v2.3.145.2 already exists" "$TMP/rc.out"

    newrepo; pins; sed -i 's/^PKG_VERSION := .*/PKG_VERSION := 2.3.145.3/' "$R/Makefile"; sed -i 's/^## 2.3.145.2/## 2.3.145.3/' "$R/CHANGELOG.md"; commit_all
    check "a version that does not end with the TorrServer build is refused" [ "$(rc_check)" = 1 ]
    check "...it explains the format"                       grep -q "must be <your version>.145.2" "$TMP/rc.out"

    newrepo; pins; sed -i 's/^## 2.3.145.2/## 2.2.9.9/' "$R/CHANGELOG.md"; commit_all
    check "a CHANGELOG that does not start with this version is refused" [ "$(rc_check)" = 1 ]

    newrepo; pins; printf '# Changelog\n\n## 2.4.145.2 (2026-10-08)\n\nNewer text.\n\n## 2.3.145.2 (2026-10-07)\n\nFixes.\n' > "$R/CHANGELOG.md"; commit_all
    check "an entry for this version below a different top entry is refused" [ "$(rc_check)" = 1 ]
    check "...it names the entry on top"                    grep -q "top CHANGELOG.md entry is '2.4.145.2'" "$TMP/rc.out"

    newrepo; pins; printf '# Changelog\n\n## 2.3.145.2 (2026-10-07)\n\n## 2.2.145.2 (2026-10-06)\n\nOlder.\n' > "$R/CHANGELOG.md"; commit_all
    check "an empty release entry is refused"               [ "$(rc_check)" = 1 ]

    newrepo; pins; grep -v "ffprobe-6.1-linux-64.zip" "$R/checksums.sha256" > "$R/c" && mv "$R/c" "$R/checksums.sha256"; commit_all
    check "a half-pinned checksums file is refused (CI would fail the build)" [ "$(rc_check)" = 1 ]
    check "...it names the missing download"                grep -q "missing: ffprobe-6.1-linux-64.zip" "$TMP/rc.out"

    newrepo; pins; grep -v "linux-arm7" "$R/checksums.sha256" > "$R/c" && mv "$R/c" "$R/checksums.sha256"; commit_all
    rc_check >/dev/null
    check "a missing TorrServer pin is refused too"         grep -q "missing: TorrServer-MatriX.145.2-linux-arm7" "$TMP/rc.out"

    newrepo; commit_all
    check "no pins at all is only a warning"                [ "$(rc_check)" = 0 ]
    check "...and the warning says what to run"             grep -q "make checksums" "$TMP/rc.out"

    newrepo; pins; commit_all; ( cd "$R" && git checkout -q main ) >/dev/null 2>&1
    rc_check >/dev/null
    check "being on the main branch is a warning, not an error" grep -q "warn  you are on main" "$TMP/rc.out"
    check "...and it does not make the check fail"          [ "$(rc_check)" = 0 ]
else
    echo "SKIP (git is not installed)"
fi

echo
if [ "$FAILED" -eq 0 ]; then echo "ALL PASSED"; else echo "FAILED: $FAILED"; exit 1; fi
