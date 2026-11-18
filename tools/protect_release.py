#!/usr/bin/env python3
"""Discover, verify, and publish Protect compatibility releases in GitHub Actions."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from zipfile import BadZipFile, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protect_onvif as patch


FEED = "https://fw-update.ubnt.com/api/firmware-latest"
PLATFORMS = {
    "uos-deb11-arm64": "Debian 11 ARM64",
    "uos-deb13-arm64": "Debian 13 ARM64",
}
UPDATE_HOSTS = {"fw-update.ubnt.com", "fw-update.ui.com"}
DOWNLOAD_HOSTS = {"fw-download.ubnt.com", "fw-download.ui.com"}
MAX_PACKAGE_SIZE = 512 * 1024 * 1024


def trusted_url(url, hosts):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in hosts:
        raise ValueError(f"Unexpected Ubiquiti download origin: {parsed.netloc}")
    return parsed


class UbiquitiRedirects(HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Candidate:
    platform: str
    version: str
    digest: str
    size: int
    url: str

    def __post_init__(self):
        if self.platform not in PLATFORMS or not re.fullmatch(r"\d+\.\d+\.\d+", self.version):
            raise ValueError("Unexpected Protect platform or release version")
        if not patch.DIGEST.fullmatch(self.digest) or type(self.size) is not int or not 0 < self.size <= MAX_PACKAGE_SIZE:
            raise ValueError("Invalid Ubiquiti package checksum or size")
        path = trusted_url(self.url, DOWNLOAD_HOSTS).path
        if not path.startswith("/data/unifi-protect/") or not path.endswith(".deb"):
            raise ValueError("Expected an official Protect Debian package URL")

    @property
    def tag(self):
        return f"protect-{self.version}-{self.platform.removeprefix('uos-')}-{self.digest[:12]}"

    @property
    def title(self):
        return f"Protect {self.version} ({PLATFORMS[self.platform]})"


def latest(platform):
    query = urlencode([("filter", f"eq~~{key}~~{value}") for key, value in (
        ("product", patch.PACKAGE), ("channel", "release"), ("platform", platform),
    )])
    opener = build_opener(UbiquitiRedirects(UPDATE_HOSTS))
    with opener.open(f"{FEED}?{query}", timeout=30) as response:
        entries = json.load(response)["_embedded"]["firmware"]
    if len(entries) != 1:
        raise ValueError(f"Expected one stable Protect candidate for {platform}, received {len(entries)}")
    entry = entries[0]
    if (entry["product"], entry["channel"], entry["platform"]) != (patch.PACKAGE, "release", platform):
        raise ValueError("The Ubiquiti feed returned an unexpected product, channel or platform")
    return Candidate(platform, entry["version"].removeprefix("v"), entry["sha256_checksum"],
                     entry["file_size"], entry["_links"]["data"]["href"])


def gh(*args):
    return subprocess.check_output(["gh", *args], encoding="utf-8")


def find_release(repo, tag):
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"))
    return next((release for page in pages for release in page if release["tag_name"] == tag), None)


def plan(platform, repo, dry_run):
    candidate = latest(platform)
    print(f"Ubiquiti lists {candidate.title}", file=sys.stderr)
    if patch.version_tuple(candidate.version) < patch.BASELINE:
        return None
    if dry_run:
        return candidate
    if patch.version_tuple(candidate.version) == patch.BASELINE and any(
        item["package_sha256"] == candidate.digest for _, item in patch.approved_manifests(patch.PROJECT / "approved")
    ):
        print("This package is included in v0.1.0.", file=sys.stderr)
        return None
    release = find_release(repo, candidate.tag)
    if release and not release["draft"]:
        print(f"{candidate.tag} is already published.", file=sys.stderr)
        return None
    return candidate


def download(candidate, target):
    url = subprocess.check_output(
        ["curl", "--fail", "--silent", "--show-error", "--location",
         "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "3",
         "--connect-timeout", "15", "--max-time", "300", "--retry", "2",
         "--max-filesize", str(candidate.size), "--output", str(target),
         "--write-out", "%{url_effective}", candidate.url],
        encoding="utf-8",
    )
    trusted_url(url, DOWNLOAD_HOSTS)
    if target.stat().st_size != candidate.size or patch.file_digest(target) != candidate.digest:
        raise ValueError("The Protect package does not match the official size and SHA-256")


def prepare_assets(candidate, staging):
    [(manifest_path, manifest)] = patch.approved_manifests(staging)
    if (manifest["version"], manifest["architecture"], manifest["package_sha256"]) != (
        candidate.version, "arm64", candidate.digest,
    ):
        raise ValueError("The staged manifest does not match the tested package")
    stem = f"{candidate.version}-arm64-{manifest['stock_sha256'][:12]}"
    archive = staging / f"protect-onvif-{stem}.zip"
    expected = {file: (patch.PROJECT / file).read_bytes() for file in patch.RELEASE_FILES}
    expected[f"approved/{stem}.json"] = manifest_path.read_bytes()
    with ZipFile(archive) as release:
        if sorted(release.namelist()) != sorted(expected):
            raise ValueError("Unexpected release archive contents")
        for item in release.infolist():
            content = expected[item.filename]
            if item.file_size != len(content) or release.read(item) != content:
                raise ValueError(f"Unexpected content in release archive: {item.filename}")
    notes = staging / "RELEASE_NOTES.md"
    sums = staging / "SHA256SUMS"
    sums.write_text(f"{patch.file_digest(archive)}  {archive.name}\n", encoding="ascii")
    notes.write_text(
        f"Automated compatibility release for **{candidate.title}** from Ubiquiti's stable channel.\n\n"
        "The patch applied to the checksummed official package and passed the JavaScript "
        "syntax and ONVIF regression tests. Use the stock service bundle with "
        f"SHA-256 `{manifest['stock_sha256']}`.\n\n"
        "Verification ran offline. See the repository README for installation and rollback.\n\n"
        f"<!-- Protect package SHA-256: {candidate.digest} -->\n",
        encoding="utf-8",
    )
    return archive, sums, notes


def build(candidate, staging):
    with tempfile.TemporaryDirectory(prefix="protect-package-") as directory:
        package = Path(directory) / "unifi-protect.deb"
        download(candidate, package)
        patch.build(package, staging, "node", expected_version=candidate.version)
    prepare_assets(candidate, staging)


def missing_assets(release, assets):
    remaining = {asset.name: asset for asset in assets}
    for uploaded in release["assets"]:
        name = uploaded["name"]
        asset = remaining.pop(name, None)
        digest = uploaded.get("digest")
        if (not asset or uploaded["state"] != "uploaded" or asset.stat().st_size != uploaded["size"]
                or (digest and digest != f"sha256:{patch.file_digest(asset)}")):
            raise ValueError(f"Unexpected asset in draft release: {name}")
    return tuple(remaining.values())


def publish(candidate, staging, repo, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected the tested source commit")
    assets = prepare_assets(candidate, staging)
    release = find_release(repo, candidate.tag)
    if release and not release["draft"]:
        print(f"{candidate.tag} is already published.")
        return
    if release:
        marker = f"<!-- Protect package SHA-256: {candidate.digest} -->"
        if release["target_commitish"] != commit or marker not in release["body"]:
            raise ValueError("Existing draft does not match this package and tested source commit")
        missing = missing_assets(release, assets)
        if missing:
            print(gh("release", "upload", candidate.tag, *map(str, missing), "--repo", repo).strip())
    else:
        print(gh("release", "create", candidate.tag, *map(str, assets),
                 "--repo", repo, "--target", commit, "--title", candidate.title,
                 "--notes-file", str(assets[-1]), "--draft").strip())
    draft = find_release(repo, candidate.tag)
    if not draft or not draft["draft"] or missing_assets(draft, assets):
        raise ValueError("The complete draft release could not be verified")
    print(gh("release", "edit", candidate.tag, "--repo", repo, "--draft=false", "--latest=false").strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discovery = commands.add_parser("plan")
    discovery.add_argument("platform", choices=PLATFORMS)
    discovery.add_argument("--repo", required=True)
    discovery.add_argument("--dry-run", action="store_true")
    for command in ("build", "publish"):
        operation = commands.add_parser(command)
        operation.add_argument("staging", type=Path)
        if command == "publish":
            operation.add_argument("--repo", required=True)
            operation.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            candidate = plan(args.platform, args.repo, args.dry_run)
            if candidate:
                print(json.dumps(asdict(candidate), separators=(",", ":")))
        else:
            candidate = Candidate(**json.loads(os.environ["PROTECT_CANDIDATE"]))
            if args.command == "build":
                build(candidate, args.staging)
            else:
                publish(candidate, args.staging, args.repo, args.commit)
    except (patch.PatchError, ValueError, KeyError, TypeError, OSError, URLError,
            BadZipFile, subprocess.CalledProcessError) as error:
        print(f"Protect release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
