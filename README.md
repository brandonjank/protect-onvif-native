# Protect ONVIF Native

Use third-party ONVIF cameras through Protect's own adoption, live view, and recording
paths. All camera traffic goes directly to Protect. The starting point is the
multi-sensor patch we tested with an AXIS P3715-PLVE on Protect 7.3.74.

## Compatibility

| Protect package | Patch and module tests | Console testing |
| --- | --- | --- |
| 7.3.74 ARM64 on Debian 13 | Passed | The original adapter was tested with both Axis lenses recording |
| 7.3.74 ARM64 on Debian 11 | Passed | Pending |
| Newer builds | Build and test each package before approval | Pending |

The bundled adapter keeps per-lens HQ and LQ profiles, snapshots, and adopted device
identities. The test suite exercises both ONVIF profile formats, adoption permissions,
periodic sync, and camera IP changes. For an approved build, open the helper from your
signed-in Protect console at
`/proxy/protect/api/third-party-cameras/onvif-helper`.

Future work includes exposing capabilities that each camera provides, starting with
stream metadata and playback verification. Motion events, PTZ, and two-way audio
each need their own device and Protect compatibility tests. Protect's AI Port remains
the detection path for cameras paired to it.

## Prepare a release

Run the builder with Python 3.9 or newer and Node 24 against an official Protect
package downloaded for the target console platform.

```sh
python3 protect_onvif.py build ./unifi-protect_7.3.74_arm64.deb --staging ./staged
```

The builder reads the service bundle and package metadata, applies the adapter on a
temporary copy, checks JavaScript syntax, and runs the ONVIF regression suite. The
output is a release ZIP containing the project source and a manifest keyed by
version, architecture, and the exact stock and patched bundle hashes. The manifest
lets the console build a patch locally from its own copy of Protect.

On a Debian system with the console's trusted APT sources, run the following to look
up the latest available candidate using the local APT package lists. Use a staging
directory per platform or APT source.

```sh
python3 protect_onvif.py watch --node /usr/bin/node24 --staging ./staged
```

The included systemd watcher and timer can run on the console. They stage a manifest
and release ZIP when the build and tests pass. Keep the APT package lists current through the
console's regular package update mechanism. When Protect changes its internals, adapt
the patch and run the suite for that version before moving the manifest into
`approved/`. Public release publication is a separate step.

## Apply on console startup

Place the project at `/opt/protect-onvif-native` under root ownership. The startup
hook runs as root to update the package-owned bundle. Check the installed bundle first.

```sh
sudo python3 /opt/protect-onvif-native/protect_onvif.py status
```

The installer recognizes the exact approved stock and patched hashes. For a console
with a previous community patch, plan the initial migration from a verified stock
bundle matching the installed package.

Install the drop-in and reload systemd during a maintenance window.

```sh
sudo install -D -m 0644 /opt/protect-onvif-native/systemd/50-protect-onvif.conf \
  /etc/systemd/system/unifi-protect.service.d/50-protect-onvif.conf
sudo systemctl daemon-reload
```

On the next start of Protect, the hook checks the installed package and bundle.
For an approved stock build, it backs up the original by hash, checks the replacement
and swaps it in atomically. Subsequent starts recognize the patched hash. A build
without an approved manifest starts with its stock bundle. Separate lens playback
and recording behavior on a new stock build should be checked after upgrades.

To revert, remove the drop-in, reload the systemd configuration, and run
`sudo python3 /opt/protect-onvif-native/protect_onvif.py restore`. The restore command
checks the backup hash before restoring the stock bundle. Restart Protect during
the maintenance window to load it. Console upgrades that reset `/opt` or systemd
configuration may require reinstalling the project and drop-in.

## License and attribution

GPL-3.0. The 7.3 adapter and helper UI are derived from
[CALTechNet/unifi-protect-multicamera-onvif](https://github.com/CALTechNet/unifi-protect-multicamera-onvif)
and Brandon Jank's 7.3.74 and Axis compatibility changes at commits `961997f` and
`db5da6b`. This project is independent of Ubiquiti.
