#!/usr/bin/env python3
"""Fetch the pinned webR/DESeq2 assets and assemble an entirely local bundle.

locfit is compiled separately by scripts/build-deseq2-locfit.sh. All other Wasm
packages are existing upstream binaries, not ports created by this project.
"""
import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCK = ROOT / "tools/deseq2/sources.lock.json"
BUILD = ROOT / "build/deseq2"
DIST = ROOT / "dist/deseq2"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_archive(asset, output):
    """Export only the pinned linked-runtime sources, not the whole compiler."""
    revision = asset["revision"]
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise RuntimeError("Git source revisions must be full commit hashes")
    paths = asset["paths"]
    prefix = asset["archive_prefix"]
    for name in [*paths, prefix.rstrip("/")]:
        if (not name or "\\" in name or pathlib.PurePosixPath(name).is_absolute()
                or any(part in {"", ".", ".."} for part in name.split("/"))):
            raise RuntimeError("Unsafe path in Git source export")
    if not prefix.endswith("/"):
        raise RuntimeError("Git archive prefix must end in a slash")
    with tempfile.TemporaryDirectory(prefix="source-export-", dir=output.parent) as temporary:
        checkout = pathlib.Path(temporary) / "checkout"
        subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)

        def git(*arguments, **kwargs):
            return subprocess.run(["git", "-C", str(checkout), *arguments],
                                  check=True, **kwargs)

        git("remote", "add", "origin", asset["repository"])
        git("config", "remote.origin.promisor", "true")
        git("config", "remote.origin.partialclonefilter", "blob:none")
        git("fetch", "--filter=blob:none", "--depth", "1", "origin", revision)
        # Exact anchored patterns include the selected subtrees and individual
        # notices without fetching the rest of the compiler's source trees.
        git("sparse-checkout", "set", "--no-cone", "--stdin",
            input="".join("/" + name + "\n" for name in paths), text=True)
        git("checkout", "--detach", revision)
        resolved = git("rev-parse", "HEAD", capture_output=True, text=True).stdout.strip()
        if resolved != revision:
            raise RuntimeError("Git source checkout did not resolve to the pinned commit")
        with output.open("wb") as stream:
            git("-c", "tar.umask=0002", "archive", "--format=tar",
                "--prefix=" + prefix, revision, "--", *paths, stdout=stream)


def fetch(asset, directory):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / asset["filename"]
    if target.exists() and sha256(target) == asset["sha256"]:
        return target
    bundled = DIST / "packages" / asset["filename"]
    if directory == BUILD / "downloads" and bundled.is_file() and sha256(bundled) == asset["sha256"]:
        shutil.copyfile(bundled, target)
        return target
    temporary = target.with_name(target.name + ".download")
    if asset.get("kind") == "git-archive":
        git_archive(asset, temporary)
    else:
        subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error",
                        "--retry", "3", asset["url"], "--output", str(temporary)], check=True)
    if (sha256(temporary) != asset["sha256"]
            or temporary.stat().st_size != asset["size"]):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Checksum mismatch for {asset['filename']}")
    temporary.replace(target)
    return target


def sources(lock):
    yield lock["webr"]["source"]
    yield lock["r"]["source"]
    for package in lock["packages"] + lock["build_dependencies"]:
        yield package["source"]
    for extra in lock.get("runtime_sources", []):
        yield extra.get("source", extra)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources-only", action="store_true", help="Only fetch corresponding sources")
    parser.add_argument("--locfit", type=pathlib.Path,
                        default=BUILD / "locfit/locfit_1.5-9.12.tgz")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text())
    tasks = [(asset, BUILD / "sources") for asset in sources(lock)]
    if not args.sources_only:
        tasks += [(lock["webr"]["binary"], BUILD / "downloads")]
        tasks += [(p["binary"], BUILD / "downloads") for p in lock["packages"] if "url" in p["binary"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for path in pool.map(lambda task: fetch(*task), tasks):
            print(f"Verified {path.name}", flush=True)
    if args.sources_only:
        return
    if not args.locfit.is_file():
        raise SystemExit(f"Missing compiled locfit: {args.locfit}. Run scripts/build-deseq2-locfit.sh first.")
    with tarfile.open(args.locfit) as archive:
        description = archive.extractfile("locfit/DESCRIPTION").read().decode()
        if "Version: 1.5-9.12\n" not in description or "emscripten" not in description:
            raise SystemExit("locfit is not the expected version compiled for Emscripten")
    DIST.mkdir(parents=True, exist_ok=True)
    webr = DIST / "webr"
    if webr.exists():
        shutil.rmtree(webr)
    webr.mkdir()
    with tarfile.open(BUILD / "downloads" / lock["webr"]["binary"]["filename"]) as archive:
        for member in archive.getmembers():
            if member.name.startswith("package/dist/"):
                relative = pathlib.PurePosixPath(member.name).relative_to("package/dist")
                if not relative.parts or relative.parts[0] in {"tests", "repl"}:
                    continue
                if member.isfile():
                    path = webr / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, path.open("wb") as target:
                        shutil.copyfileobj(source, target)
            elif member.name in {"package/LICENSE.md", "package/LICENCE.md", "package/package.json"}:
                (webr / pathlib.PurePosixPath(member.name).name).write_bytes(archive.extractfile(member).read())
    packages = DIST / "packages"
    if packages.exists():
        shutil.rmtree(packages)
    packages.mkdir()
    manifest = {key: lock[key] for key in ("schemaVersion", "name", "version", "webrVersion", "rVersion")}
    manifest["packages"] = []
    for package in lock["packages"]:
        binary = package["binary"]
        source = BUILD / "downloads" / binary["filename"] if "url" in binary else args.locfit
        target = packages / binary["filename"]
        shutil.copyfile(source, target)
        manifest["packages"].append({"name": package["name"], "version": package["version"],
                                      "filename": target.name, "sha256": sha256(target),
                                      "size": target.stat().st_size})
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    checksums = [f"{sha256(path)}  {path.relative_to(DIST).as_posix()}"
                 for path in sorted(DIST.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"]
    (DIST / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    print(f"Assembled {DIST}: webR {lock['webrVersion']}, R {lock['rVersion']}, {len(manifest['packages'])} R packages")


if __name__ == "__main__":
    main()
