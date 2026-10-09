# Changelog

The top entry is used as the GitHub release notes; keep its version equal to
`PKG_VERSION` in `Makefile`.

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
