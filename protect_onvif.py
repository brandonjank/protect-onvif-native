#!/usr/bin/env python3
"""Build and install version-checked UniFi Protect ONVIF extensions."""

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import adapter_7_3


PROJECT = Path(__file__).resolve().parent
PACKAGE = "unifi-protect"
BASELINE = (7, 3, 74)
SERVICE_MEMBER = "usr/share/unifi-protect/app/service.js"
SERVICE = Path("/") / SERVICE_MEMBER
BACKUPS = Path("/var/lib/protect-onvif-native/stock")
ARCHIVE_MEMBERS = ("control", "data")
MANIFEST_KEYS = {"version", "architecture", "stock_sha256", "patched_sha256", "package_sha256"}
DIGEST = re.compile(r"[a-f0-9]{64}")
# Exact 7.3.74 outputs from the earlier port, Axis fix, and first Native
# release. Keyed by the corresponding stock bundle hash.
LEGACY_BUNDLES = {
    "1b54ec68f73bfd0ab6b4d81748b9051a52c70156e2128a59f251bfa3ed461dea": (
        "2ad0ef4ba73560974840c002f642ca4d526153691fe30cf322e6d3571da27065",
        "87049b1477abeda25245430a379318dfa3dc2a64f5d7cdf31cd6ce1d41ff384a",
        "54eeacff350f286226d591292cd64f32ceab39659bab3b64e5f58b3b36d00654",
    ),
    "5e843ae59c7ad921666364e693a288694ed5deb2077b06bc5508874c7484c9d0": (
        "974dfbf9ac72a95fcf59a3dc0a37944ff63e8a6198c98a766ce6398ffbf92995",
        "31fb8f929fc2a5d4e3d035bc13377c7acaa2175f1123547a5caf4e749acf14f4",
        "efc98545e2306dabe36769612e9a63950dc08c2dd5fb12ee40167298fcdebe5d",
    ),
}
RELEASE_FILES = (
    "LICENSE.md", "README.md", "adapter_7_3.py", "onvif_helper.html", "protect_onvif.py",
    "systemd/50-protect-onvif.conf", "systemd/protect-onvif-watch.service",
    "systemd/protect-onvif-watch.timer", "tests/protect_7_3.cjs",
)


class PatchError(Exception):
    pass


def version_tuple(value):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+~][\w.+~-]+)?", value)
    if not match:
        raise PatchError(f"Unsupported Protect version format: {value}")
    return tuple(map(int, match.groups()))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with path.open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def unpack_members(package, directory):
    """Copy only the Debian package members needed for local verification."""
    members = {}
    with package.open("rb") as handle:
        if handle.read(8) != b"!<arch>\n":
            raise PatchError("Expected a Debian package")
        while header := handle.read(60):
            if len(header) != 60 or header[58:] != b"`\n":
                raise PatchError("Invalid Debian package archive")
            name = header[:16].decode("ascii").strip().rstrip("/")
            size = int(header[48:58])
            start = handle.tell()
            for kind in ARCHIVE_MEMBERS:
                if name in (f"{kind}.tar", f"{kind}.tar.xz", f"{kind}.tar.gz"):
                    if kind in members:
                        raise PatchError(f"Package contains more than one {kind} archive")
                    target = directory / name
                    with target.open("wb") as out:
                        left = size
                        while left:
                            chunk = handle.read(min(left, 1024 * 1024))
                            if not chunk:
                                raise PatchError("Truncated Debian package")
                            out.write(chunk)
                            left -= len(chunk)
                    members[kind] = target
            handle.seek(start + size + size % 2)
    if members.keys() != set(ARCHIVE_MEMBERS):
        raise PatchError("Expected control and data archives in the Debian package")
    return members


def read_tar_member(archive, path):
    with tarfile.open(archive) as handle:
        matches = [member for member in handle if member.name.removeprefix("./") == path]
        if len(matches) != 1 or not matches[0].isfile():
            raise PatchError(f"Expected one regular {path} in {archive.name}")
        with handle.extractfile(matches[0]) as content:
            return content.read()


def read_package(package):
    with tempfile.TemporaryDirectory(prefix="protect-deb-") as temp:
        members = unpack_members(package, Path(temp))
        control = read_tar_member(members["control"], "control").decode("utf-8")
        fields = dict(line.split(": ", 1) for line in control.splitlines()
                      if line and not line[0].isspace() and ": " in line)
        if fields.get("Package") != PACKAGE or fields.get("Architecture") != "arm64":
            raise PatchError("Expected an ARM64 unifi-protect package")
        version = fields.get("Version", "")
        if version_tuple(version) < BASELINE:
            raise PatchError(f"Protect {version} predates the 7.3.74 baseline")
        return version, fields["Architecture"], read_tar_member(members["data"], SERVICE_MEMBER)


def patch_bundle(stock):
    source = stock.decode("utf-8")
    for label, before, after in adapter_7_3.edits():
        count = source.count(before)
        if count != 1:
            raise PatchError(f"{label}: expected one match, found {count}")
        source = source.replace(before, after, 1)
    return source.encode("utf-8")


def run_checks(node, patched, *, test_modules):
    with tempfile.TemporaryDirectory(prefix="protect-check-") as temp:
        bundle = Path(temp) / "service.js"
        bundle.write_bytes(patched)
        subprocess.run([node, "--check", str(bundle)], check=True)
        if test_modules:
            subprocess.run([node, str(PROJECT / "tests/protect_7_3.cjs"), str(bundle)], check=True)


def write_atomic(target, content, reference=None):
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        temp = Path(temporary.name)
        try:
            temporary.write(content)
            if reference and os.name == "posix":
                original = reference.stat()
                os.fchown(temporary.fileno(), original.st_uid, original.st_gid)
                os.fchmod(temporary.fileno(), stat.S_IMODE(original.st_mode))
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary.close()
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)


def build(package, staging, node, *, expected_version=None):
    version, arch, stock = read_package(package)
    if expected_version and expected_version != version:
        raise PatchError(f"Expected the APT candidate {expected_version}, received {version}")
    patched = patch_bundle(stock)
    run_checks(node, patched, test_modules=True)
    manifest = {
        "version": version,
        "architecture": arch,
        "package_sha256": file_digest(package),
        "stock_sha256": sha256(stock),
        "patched_sha256": sha256(patched),
    }
    staging.mkdir(parents=True, exist_ok=True)
    name = f"{version}-{arch}-{manifest['stock_sha256'][:12]}"
    metadata = (json.dumps(manifest, indent=2) + "\n").encode()
    release = io.BytesIO()
    with zipfile.ZipFile(release, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in RELEASE_FILES:
            archive.write(PROJECT / file, file)
        archive.writestr(f"approved/{name}.json", metadata)
    target = staging / f"protect-onvif-{name}.zip"
    write_atomic(target, release.getvalue())
    write_atomic(staging / f"{name}.json", metadata)
    print(f"Verified Protect {version}. Release candidate staged at {target}")


def approved_manifests(directory):
    for file in sorted(directory.glob("*.json")):
        manifest = json.loads(file.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.keys() != MANIFEST_KEYS
                or not all(isinstance(value, str) for value in manifest.values())
                or any(not DIGEST.fullmatch(manifest[key])
                       for key in MANIFEST_KEYS if key.endswith("_sha256"))):
            raise PatchError(f"Invalid release manifest {file}")
        yield file, manifest


def installed_build():
    query = ["dpkg-query", "-W", "-f=${Version}\t${Architecture}\t${db:Status-Status}", PACKAGE]
    version, arch, status = subprocess.check_output(query, text=True).strip().split("\t")
    # Protect's postinst restarts the service before dpkg marks it installed.
    # The exact bundle hash is verified for both states.
    if status not in ("installed", "half-configured"):
        raise PatchError("Protect installation is incomplete")
    return version, arch


def save_backup(directory, content, digest):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = directory / f"{digest}.js"
    if not backup.exists():
        write_atomic(backup, content)
    if file_digest(backup) != digest:
        raise PatchError(f"Backup failed its checksum: {backup}")
    return backup


def migration_source(args, version, arch):
    if args.stock:
        return args.stock.read_bytes()
    package_version, package_arch, source = read_package(args.package)
    if (package_version, package_arch) != (version, arch):
        raise PatchError("The stock package version and architecture must match the installation")
    return source


def reconcile(args):
    if args.version:
        if not args.architecture:
            raise PatchError("--version requires --architecture")
        version, arch = args.version, args.architecture
    else:
        version, arch = installed_build()
    if args.service.is_symlink() or not args.service.is_file():
        raise PatchError(f"Expected a regular service bundle at {args.service}")
    current = args.service.read_bytes()
    digest = sha256(current)
    matching = [
        item for _, item in approved_manifests(args.approved)
        if item["version"] == version and item["architecture"] == arch
        and (digest in (item["stock_sha256"], item["patched_sha256"])
             or digest in LEGACY_BUNDLES.get(item["stock_sha256"], ()))
    ]
    if not matching:
        message = f"Protect {version} {arch} has no approved match for {digest}"
        if args.command in ("migrate", "restore"):
            raise PatchError(message)
        print(message)
        return
    if len(matching) != 1:
        raise PatchError("Multiple approved manifests match the same installed bundle")
    manifest = matching[0]
    stock_digest = manifest["stock_sha256"]
    legacy_digests = LEGACY_BUNDLES.get(stock_digest, ())
    is_legacy = digest in legacy_digests
    is_patched = digest == manifest["patched_sha256"]
    state = "patched" if is_patched else "legacy patch" if is_legacy else "approved stock"
    print(f"Protect {version} {arch}: {state}")
    if args.command == "status" or (args.command == "apply" and is_legacy):
        return

    if args.command == "restore":
        if not is_patched:
            return
        expected = args.legacy_sha256 or stock_digest
        if args.legacy_sha256 and expected not in legacy_digests:
            raise PatchError("The requested legacy hash does not match this Protect build")
        backup = args.backups / f"{expected}.js"
        replacement = backup.read_bytes()
        if sha256(replacement) != expected:
            raise PatchError(f"Backup failed its checksum: {backup}")
        action = "Restored legacy patch" if args.legacy_sha256 else "Restored stock"
    else:
        if is_patched:
            return
        if args.command == "migrate":
            if not is_legacy:
                raise PatchError("Use apply for an approved stock bundle")
            stock = migration_source(args, version, arch)
            if sha256(stock) != stock_digest:
                raise PatchError("The stock source does not match this legacy build")
        else:
            stock = current
        replacement = patch_bundle(stock)
        if sha256(replacement) != manifest["patched_sha256"]:
            raise PatchError("Generated patch did not match the approved release")
        run_checks(args.node, replacement, test_modules=False)
        save_backup(args.backups, stock, stock_digest)
        if is_legacy:
            backup = save_backup(args.backups, current, digest)
            print(f"Legacy backup saved at {backup}")
            print(f"Legacy rollback option: restore --legacy-sha256 {digest}")
        action = "Migrated the legacy patch" if is_legacy else "Applied approved patch"
    if file_digest(args.service) != digest:
        raise PatchError("The Protect bundle changed during preparation")
    write_atomic(args.service, replacement, reference=args.service)
    print(f"{action} for Protect {version}")


def watch(staging, node):
    """Track the newest candidate offered by the configured APT repositories."""
    result = subprocess.check_output(["apt-cache", "policy", PACKAGE], text=True,
                                     env={**os.environ, "LC_ALL": "C"})
    match = re.search(r"^\s*Candidate: (\S+)$", result, re.MULTILINE)
    if not match or match[1] == "(none)":
        raise PatchError("No Protect update candidate in the configured APT sources")
    version = match[1]
    if version_tuple(version) < BASELINE:
        print(f"APT offers Protect {version}, which predates the baseline")
        return
    if any(manifest["version"] == version and (staging / f"protect-onvif-{file.stem}.zip").is_file()
           for file, manifest in approved_manifests(staging)):
        print(f"Protect {version} is already staged in {staging}")
        return
    with tempfile.TemporaryDirectory(prefix="protect-apt-") as temp:
        subprocess.run(["apt-get", "download", f"{PACKAGE}={version}"], cwd=temp, check=True)
        packages = list(Path(temp).glob("unifi-protect_*.deb"))
        if len(packages) != 1:
            raise PatchError("APT download did not produce one Protect package")
        build(packages[0], staging, node, expected_version=version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "watch"):
        sub = commands.add_parser(command)
        if command == "build":
            sub.add_argument("package", type=Path)
        sub.add_argument("--staging", type=Path, required=True)
        sub.add_argument("--node", default="node")
    for command in ("status", "apply", "migrate", "restore"):
        sub = commands.add_parser(command)
        sub.add_argument("--service", type=Path, default=SERVICE)
        sub.add_argument("--approved", type=Path, default=PROJECT / "approved")
        sub.add_argument("--backups", type=Path, default=BACKUPS)
        sub.add_argument("--version", help="Offline testing override for the installed package version")
        sub.add_argument("--architecture", help="Offline testing override for the package architecture")
        sub.add_argument("--node", default="/usr/bin/node24")
        if command == "migrate":
            source = sub.add_mutually_exclusive_group(required=True)
            source.add_argument("--stock", type=Path, help="Pristine service.js from a legacy installer backup")
            source.add_argument("--package", type=Path, help="Matching official Protect Debian package")
        elif command == "restore":
            sub.add_argument("--legacy-sha256", help="Restore a recognized legacy backup by its SHA-256")
    args = parser.parse_args()
    try:
        if args.command == "build":
            build(args.package, args.staging, args.node)
        elif args.command == "watch":
            watch(args.staging, args.node)
        else:
            reconcile(args)
        return 0
    except (PatchError, OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"Protect ONVIF: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
