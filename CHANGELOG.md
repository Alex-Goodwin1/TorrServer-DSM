# Changelog

The top entry is used as the GitHub release notes; keep its version equal to
`PKG_VERSION` in `Makefile`.

## 2.10.146 (2026-10-10)

TorrServer stays at `MatriX.146`.

### Changed

- **The folder chooser warns about folders the TorrServer user cannot write
  to.** Such folders are marked "no write access" in the list, and choosing one
  shows the warning before Save, not after. The message now names the folder
  and the shared folder that needs the Read/Write permission (for example
  `/volume1/docker/PlexTorr` and `docker`), so it is clear what to fix in DSM.
- Folder names in the chooser are never translated (a folder called `Status`
  stays `Status`).
- The hint at the top of Settings is now one sentence: click Save, then
  Restart (all changes need a restart).
- Internal cleanup of the package scripts (a duplicate variable and an
  outdated comment removed); behaviour is unchanged.

## 2.9.146 (2026-10-10)

TorrServer stays at `MatriX.146`.

### Fixed

- **The Helper window said "Sorry, the page you are looking for is not found"
  after the first install** (seen on DSM 7.3.2 and 7.4.1). DSM links the
  package's nginx rule but, as its documentation says, does not reload nginx
  by itself. The package now declares `instuninst_restart_services =
  nginx.service` in its `INFO`, the way DSM provides for this, so DSM reloads
  nginx when the package is installed or removed. No root script is involved.
  If you already hit it, run `sudo synosystemctl reload nginx` once.

## 2.8.146 (2026-10-09)

TorrServer stays at `MatriX.146`.

### Changed

- **Nothing in the package runs as root any more.** The package works the way
  DSM intends: with the reduced rights of its own `TorrServer` user. The
  scripts `certificate-helper`, `prepare-directory` and `setup-permissions`
  and the sudo rule are gone, and so is everything in the Helper that depended
  on them (permission check, **Check permissions** button, root fallback for
  folders). A folder the service user cannot write to is fixed the standard DSM
  way (Control Panel, Shared Folder, Edit, Permissions, System internal user),
  as the Helper explains.
- **The SSL Certificate card is a manual.** The certificate source choice
  (self-signed, DSM certificate, manual paths) is removed: the certificate is
  set on TorrServer's own page (**Settings, Additional, HTTPS**). The card
  explains the manual upload and two ways to keep the certificate renewed:
  a DSM reverse proxy, or a Task Scheduler task with a ready script that copies
  the DSM certificate to a folder TorrServer can read. The README describes
  both in detail.

- **Package description.** Package Center now shows a short description of
  TorrServer's main features (English, Russian and Polish) instead of
  "TorrServer, torrent to http.".

### Upgrading

- A `server.pem` / `server.key` copied by an earlier version stays and keeps
  working, but nothing renews it any more. Move to a reverse proxy or the
  Task Scheduler script before it expires.
- The old sudo rule is left behind because the package cannot remove it
  without root. Delete it once: `sudo rm -f /etc/sudoers.d/TorrServer`.
- The settings files `torrserver.ssl.mode`, `torrserver.ssl.cert` and
  `torrserver.ssl.key` are removed on upgrade.

## 2.7.146 (2026-10-09)

TorrServer stays at `MatriX.146`.

### Fixed

- **A FUSE folder that could not be opened.** When TorrServer ended without
  releasing its FUSE mount (killed, crashed, or the folder was changed while
  Plex or Docker still used it), the mount was left behind with nothing serving
  it, and the folder could not be opened at all, not even in File Station.
  The package now removes such leftover mounts every time TorrServer starts,
  restarts or stops (never while TorrServer is running), and writes what it
  did to `TorrServer.log`. If one cannot be removed, the log gives the command
  to run by hand (`umount -l <mount point>`).

## 2.6.146 (2026-10-09)

TorrServer updated from `MatriX.145.2` to `MatriX.146`.

### Changed

- **One log file.** `TorrServer.log` now holds everything: TorrServer, the
  start / stop / restart scripts, the Helper and the certificate script, all in
  TorrServer's own line format. It is no longer wiped when the service starts,
  so what happened before a crash is still there, and it can be read or
  downloaded while TorrServer is stopped. The Logs tab lists the log and its
  rotated copies. `service.log` and `Helper.log` are removed on upgrade.
- **Status page:** separate **HTTP** and **HTTPS** rows with their ports or
  "Disabled" (with "HTTPS only" the stale HTTP port is gone), new **Directory**
  and **FUSE** rows, and the NAS memory (**Memory**).
- When TorrServer has stopped, the status page shows a **Start** button on the
  right of the status card (where the Open buttons are while it runs), with
  the last lines of the log below the status. The page notices a stop or
  start done elsewhere (TorrServer's own page, a crash) and updates by itself.
- **DSM keeps the package "Running" while the Helper runs**, even if TorrServer
  itself is stopped, so the desktop icon does not disappear and the Helper
  window stays reachable to start TorrServer again. "Start" in Package Center
  starts TorrServer next to the running Helper.
- Log lines and folder names are no longer run through the translation (a
  log line "Starting ..." could show up as "Запуститьing ..." in Russian).
- **Settings:** the Save / Restart bar stays at the bottom of the window, the
  language is one compact row, and the card titles and the side menu use one
  set of line icons instead of mixed symbols.
- **Certificate:** choosing "TorrServer's own (self-signed)" again after the
  DSM or own-files certificate now really switches back; the copied
  certificate used to stay in place and kept being served.
- **Certificate uploaded in TorrServer 146:** while a certificate uploaded on
  TorrServer's own web page is in use (it takes priority), the SSL card on the
  Settings page says so and the certificate source cannot be changed there.
  After TorrServer is switched back to its self-signed certificate, the choice
  (TorrServer, DSM, own paths) is available again.
- **"Manual paths" certificate source is gone** from the Settings page: since
  TorrServer 146 an own certificate is uploaded (or pointed to) on TorrServer's
  own page (Settings, Additional, HTTPS). An installation that already uses
  manual paths keeps working and keeps the option.
- **Python 3:** the Helper looks for Python in `/bin`, `/usr/bin` and the
  Python 3 package. If none is found the installation stops with a message
  that says what to install (and the log says so as well), instead of
  installing a package whose window cannot open.

## 2.5.145.2 (2026-10-08)

TorrServer itself is unchanged (`MatriX.145.2`).

### Changed

- **Settings are reorganized.** Authentication now comes before HTTPS and SSL.
  The DSM permission instructions moved into the SSL Certificate card instead
  of a separate window, and the media server recommendations window is gone:
  ticking **FUSE** shows a short inline hint for Plex and Emby.
- The TorrServer folder help now explains that the folder is optional and that
  the disk cache is switched on in the TorrServer web interface.
- When TorrServer is stopped, the status page shows the last lines of its log
  and a **Start** button.
- **Restart from the Helper no longer needs root.** The Restart button is
  always available. It stops and starts only the TorrServer process (the
  Helper keeps running) and reads the saved settings again, so a new port,
  HTTPS or password takes effect. TorrServer gets time to shut down cleanly
  before it is killed.
- While TorrServer restarts, the status page shows **Restarting** and updates
  itself when TorrServer is back.
- The TorrServer folder is optional: the port, password and HTTPS can be saved
  without choosing one. Only FUSE still needs a folder.
- The package no longer ships the root restart service
  (`pkg-TorrServer-restart.service`) or the `restart-package` script, and the
  optional sudo rule no longer lists it. An existing rule keeps working; run
  `setup-permissions` again to drop the old entry.
- The optional permission is now needed only for DSM / manual certificates and
  for folders the service user cannot write to; the texts in all five languages
  say so.
- The service log no longer repeats sudo's "a password is required" line when
  the optional permission is not configured.

## 2.4.145.2 (2026-10-08)

TorrServer itself is unchanged (`MatriX.145.2`).

### Changed

- **Restart from the Helper no longer needs root.** The Restart button is
  always available. It stops and starts only the TorrServer process (the
  Helper keeps running) and reads the saved settings again, so a new port,
  HTTPS or password takes effect. Concurrent restarts are serialized and a
  stale pid file is handled.
- The package no longer ships the root restart service
  (`pkg-TorrServer-restart.service`) or the `restart-package` script, and the
  optional sudo rule no longer lists it. An existing rule keeps working; run
  `setup-permissions` again to drop the old entry.
- The optional permission is now needed only for DSM / manual certificates and
  for folders the service user cannot write to; the texts in all five languages
  say so.
- While TorrServer restarts, the status page shows **Restarting** and updates
  itself when TorrServer is back (before, it jumped to a stale page and the new
  port only appeared after switching tabs).
- The TorrServer folder is optional: the port, password and HTTPS can be saved
  without choosing one. Only FUSE still needs a folder.
- The service log no longer repeats sudo's "a password is required" line when
  the optional permission is not configured.

## 2.3.145.2 (2026-10-07)

TorrServer itself is unchanged (`MatriX.145.2`).

### Security

- **The Helper now requires a DSM administrator session.** Before, the page was
  served to anyone who could reach the DSM port, because nginx does not check
  the DSM login for that path. Everyone else gets `403`; if the session cannot
  be verified, nobody gets in. Refusals are logged to `service.log`.
- The Helper listens on `127.0.0.1` only (it used to listen on all interfaces)
  and port 42777 is no longer opened in the firewall configuration.
- State-changing requests must come from the same origin (CSRF protection);
  request size is limited; responses are not cached and cannot be framed by
  other sites.
- The root helper scripts refuse paths containing `..` and symbolic links
  (they could be used to read or create files outside the intended folders).
- `accs.db` is written atomically with mode `0600`; other TorrServer accounts in
  it are no longer overwritten.
- The DSM system certificate is no longer copied over TorrServer's own
  `server.pem` / `server.key` in the self-signed mode; certificates are
  installed as a pair.

### Changed

- **No root needed for everyday use.** The settings form was entirely locked
  until a sudo rule was configured. Port, password, HTTPS with the built-in
  certificate and FUSE now work immediately, and the Helper creates the
  `Cache` / `FUSE` folders itself when the service user may write there.
  Only restart from the Helper, DSM / manual certificates and folders the
  service user cannot write to still need the optional permission.
- When the service user cannot write to the chosen folder, the Helper explains
  how to grant access in DSM instead of showing a raw `sudo` error.
- Saving the cache folder no longer resets TorrServer's other settings, and it
  is applied automatically when TorrServer is not running at the moment.
- The service output now goes to `service.log`; `TorrServer.log` is no longer
  truncated on every start. Both logs are rotated.
- Removed code and files left over from older versions that the package no
  longer needs (DSM 6 handling, an unused `cache.path` file, unused dashboard
  code). Obsolete files are cleaned up on upgrade.

### Fixed

- Settings left over from older versions are cleaned up on upgrade (obsolete
  files, stale certificates). A **Reset package settings** option was added to
  the upgrade wizard.
- Translations could change form values, paths, JavaScript names and the logo
  image; only visible text is translated now.
- Error messages were partly in English in the other languages (for example
  "Веб-порт 42777 is reserved ..."). All messages and the access-denied page
  are now translated completely, and the instructions name DSM's screens the
  way DSM itself does: Russian «Планировщик задач» instead of «Диспетчер
  задач», Polish „Harmonogram zadań"; for Ukrainian and Lithuanian, which DSM
  does not offer, DSM's English names are kept.
- Checking the HTTPS port no longer blocks saving when HTTPS is off; user names
  containing `:` are rejected; folders containing spaces are rejected (they
  broke the FUSE option).
- The Helper no longer fails with a blank window or a missing session token
  inside the DSM desktop.

### Build and CI

- Downloaded binaries are cached per TorrServer version, written atomically,
  and can be pinned with SHA-256 sums (`make checksums`).
- Unit and script tests run for every pull request and before every release.

### Upgrade notes

- Reload DSM (**Ctrl+Shift+R**) after upgrading.
- The optional sudo rule is not refreshed by an upgrade. If you enabled it with
  an older version and want the optional features, run `setup-permissions`
  again from DSM Task Scheduler.
- The Helper needs a DSM administrator account.
- If you installed 2.2.145.2: its upgrade did not clean up leftovers of older
  versions (old files stayed behind). Upgrading to this release repairs that
  automatically.
- **HTTPS in the self-signed mode:** an older version copied the DSM certificate
  over TorrServer's own `server.pem` / `server.key`. The upgrade removes that
  copy, so TorrServer generates its own self-signed certificate again and
  clients may show a certificate warning once. To keep using the DSM
  certificate, choose **DSM certificate** in the Helper (this needs the optional
  permissions).

## 2.2.145.2 (2026-10-06)

Replaced by the release above. The clean-up of leftovers during an upgrade did
not run in this release, so old files stayed behind. Upgrade to the newer
release; it repairs this automatically.
