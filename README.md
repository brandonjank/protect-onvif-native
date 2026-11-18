# Protect ONVIF Native

Use third-party ONVIF cameras through Protect's own adoption, live view, and
recording paths. Cameras stream directly to Protect. The patch can expose each
lens of a multi-sensor camera as a separate Protect device with its own HQ and LQ
streams and snapshot.

## Compatibility

| Protect package | Stock SHA-256 prefix | Verification |
| --- | --- | --- |
| 7.3.74 ARM64, Debian 13 | `5e843ae59c7a` | Live on a UDM Pro Max with an AXIS P3715-PLVE |
| 7.3.74 ARM64, Debian 11 | `1b54ec68f73b` | Offline package, regression, and rollback tests |

These are the approved builds in the first release. Newer Protect packages need a
separate build and verification. On the live system, both lenses kept their device
IDs through migration and resumed HQ and LQ recording after a restart. Both showed
a measured bitrate in Recording Manager.

For a third-party camera with a blank HQ bitrate, Recording Manager uses the
average from completed HQ segments that started in the last six hours. The patch
divides total recorded bits by total recorded duration. The field stays blank
until a usable segment exists. Reload Protect to recalculate the value.

## Install

Install on a compatible UniFi console with root access, Python 3.9 or later,
`/usr/bin/node24`, and systemd. Plan for a brief interruption to recording while
Protect restarts.

1. Run `sha256sum /usr/share/unifi-protect/app/service.js` on the console. For a
   stock install, pick the release ZIP whose filename ends in the matching hash
   prefix. If Protect is already patched, use the matching stock backup's hash
   and follow the migration section below.
2. Download the ZIP and `SHA256SUMS` from the same GitHub release. Verify the
   download with `sha256sum --check --ignore-missing SHA256SUMS`.
3. On a fresh installation, extract the ZIP as root. Substitute the ZIP filename
   for `RELEASE.zip`.

   ```sh
   sudo install -d -o root -g root -m 0755 /opt/protect-onvif-native
   sudo python3 -m zipfile -e RELEASE.zip /opt/protect-onvif-native
   sudo python3 /opt/protect-onvif-native/protect_onvif.py status
   ```

4. If status shows `approved stock`, install the startup hook and restart Protect.
   For `legacy patch`, run the migration first. If status reports no approved
   match, check the package version and stock checksum before continuing.

   ```sh
   sudo install -D -m 0644 /opt/protect-onvif-native/systemd/50-protect-onvif.conf \
     /etc/systemd/system/unifi-protect.service.d/50-protect-onvif.conf
   sudo systemctl daemon-reload
   sudo systemctl restart unifi-protect.service
   sudo python3 /opt/protect-onvif-native/protect_onvif.py status
   ```

5. Once status shows `patched`, open the helper in your signed-in Protect
   session at `/proxy/protect/api/third-party-cameras/onvif-helper`. Check each
   lens in live view and play back footage recorded after the restart.

## Protect updates and rollback

On startup the hook verifies the version and bundle hash, saves the original,
checks JavaScript syntax and applies a matching release. An unrecognized stock
build starts normally with Protect's own camera behavior. For continued access
to the patched features, install an approved release for the new build before
updating Protect. Console OS updates that reset `/opt` or systemd configuration
may require reinstalling the project and hook.

To restore the stock bundle, remove the project's startup hook, reload systemd,
then restore and restart Protect.

```sh
sudo rm /etc/systemd/system/unifi-protect.service.d/50-protect-onvif.conf
sudo systemctl daemon-reload
sudo python3 /opt/protect-onvif-native/protect_onvif.py restore
sudo systemctl restart unifi-protect.service
```

## Migrate an earlier patch

Pause any existing job that runs the old `apply.sh` so this project can take over
future starts. If upgrading an earlier Native release, save a copy of its
`/opt/protect-onvif-native` directory before extracting the new ZIP. During a
maintenance window, use the matching stock backup from the old installer, a stock
backup in `/var/lib/protect-onvif-native/stock`, or the matching official package.

```sh
sudo python3 /opt/protect-onvif-native/protect_onvif.py migrate --stock /path/to/service.js.bak-onvifmod
# Or use the matching official Protect package
sudo python3 /opt/protect-onvif-native/protect_onvif.py migrate --package /path/to/unifi-protect.deb
```

The command checks the installed legacy hash, verifies the matching stock source,
builds and syntax-checks a fresh `service.js`, saves both bundles by hash, and
replaces the installed bundle atomically. Return to the startup hook and restart
steps under [Install](#install).

To roll back to the previous bundle, run `restore --legacy-sha256` with the hash
printed during migration, restore the previous startup setup, and restart
Protect. For an unrecognized custom patch, start from a verified stock bundle.
Upgrade Protect versions older than 7.3.74 before installing this project.

## Build a release

Run the builder with Python 3.9 or newer and Node 24 against an official Protect
package for the target console.

```sh
python3 protect_onvif.py build ./unifi-protect_7.3.74_arm64.deb --staging ./staged
```

The builder patches a temporary copy, checks JavaScript syntax, and runs the
ONVIF regression suite. The ZIP contains the project source and an approved-build
manifest so the recorder can generate the replacement from its stock Protect
bundle.

On a Debian system with a trusted Protect APT source, the watcher can check the
available candidate and build a release ZIP. Use one staging directory per
platform or package source. The watcher reads the local APT lists, so its results
depend on when those lists were refreshed.

```sh
python3 protect_onvif.py watch --node /usr/bin/node24 --staging ./staged
```

The optional systemd watcher and timer stage candidates for review. When Protect
changes its internals, adapt the patch and test the package before adding its
manifest to `approved/` and publishing the ZIP.

### GitHub Actions

The **Check for Protect releases** workflow checks Ubiquiti's stable Debian 11
and Debian 13 ARM64 feeds daily. It downloads new packages from Ubiquiti,
verifies their advertised SHA-256 and size, and runs the existing build and
regression tests. Each platform publishes independently after its tests pass.

An automated release includes the patch ZIP, checksums, and release notes.
Its tag identifies the Protect version, platform, and package hash. A build
that fails the patch or tests appears as a failed workflow run. If upstream
changes break the adapter, update the adapter and rerun the workflow.

Use **Run workflow** with `dry_run` enabled to test both current packages and
preserve build artifacts without publishing. Scheduled runs skip packages
already released, including the two original 7.3.74 packages.

## Report a compatibility issue

Include the Protect version, console model, camera model, and the output of
`protect_onvif.py status`. Redact credentials, addresses, and stream URLs from
any logs you share.

## License and attribution

GPL-3.0. The 7.3 adapter and helper UI are derived from
[CALTechNet/unifi-protect-multicamera-onvif](https://github.com/CALTechNet/unifi-protect-multicamera-onvif)
and Brandon Jank's November 18, 2026 changes for Protect 7.3.74 and AXIS multi-sensor
support. Protect ONVIF Native adds a version-checked installer, upgrade hook,
legacy migration, and measured bitrate display. This project is independent of
Ubiquiti.
