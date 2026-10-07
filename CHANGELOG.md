# Changelog

The top entry is used as the GitHub release notes; keep its version equal to
`PKG_VERSION` in `Makefile`.

## 2.2.145.2 (2026-10-06)

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

### Fixed

- Settings left over from older versions are cleaned up on upgrade (obsolete
  files, stale certificates). A **Reset package settings** option was added to
  the upgrade wizard.
- Translations could change form values, paths, JavaScript names and the logo
  image; only visible text is translated now.
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
