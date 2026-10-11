#!/usr/bin/env python3
"""Package the local DESeq2/webR bundle, real data and pinned source archives.

This verifies build inputs and archive integrity. Numerical validation is a
separate gate and is never inferred from a successful packaging operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "tools/deseq2/sources.lock.json"
WASM_HEADER = b"\0asm\x01\0\0\0"


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def safe_name(name):
    if (not isinstance(name, str) or not name or "\\" in name
            or "\0" in name or any(ord(char) < 32 for char in name)
            or PurePosixPath(name).is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise RuntimeError(f"Unsafe archive path: {name!r}")
    return name


def required(path):
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f"Required regular release input is missing or unsafe: {path}")
    return path


def checked_asset(asset, directory):
    filename = safe_name(asset["filename"])
    if "/" in filename:
        raise RuntimeError("Asset filenames must not contain directories")
    path = required(directory / filename)
    if (path.stat().st_size != asset["size"]
            or not re.fullmatch(r"[a-f0-9]{64}", asset["sha256"])
            or digest(path) != asset["sha256"]):
        raise RuntimeError(f"Pinned asset checksum or size mismatch: {path.relative_to(ROOT)}")
    return path


def package_metadata(path, package, *, binary):
    """Reject mislabeled native libraries or different package versions."""
    with tarfile.open(path) as archive:
        member = archive.getmember(f"{package['name']}/DESCRIPTION")
        description = archive.extractfile(member).read().decode("utf-8")
        fields = dict(re.findall(r"^([A-Za-z][A-Za-z0-9_./-]*): (.*)$", description, re.M))
        if fields.get("Package") != package["name"] or fields.get("Version") != package["version"]:
            raise RuntimeError(f"Wrong package name or version in {path.name}")
        if not fields.get("License"):
            raise RuntimeError(f"Missing package license declaration in {path.name}")
        if binary:
            for entry in archive.getmembers():
                safe_name(entry.name.rstrip("/"))
                if entry.issym() or entry.islnk():
                    raise RuntimeError(f"Unexpected link in R package: {path.name}:{entry.name}")
                if entry.isfile() and entry.name.endswith(".so"):
                    if archive.extractfile(entry).read(8) != WASM_HEADER:
                        raise RuntimeError(f"Non-WebAssembly shared library in {path.name}")
        return fields


def collect():
    lock = json.loads(required(LOCK).read_text())
    distribution = ROOT / "dist/deseq2"
    manifest_path = required(distribution / "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    for key in ("name", "version", "rVersion", "webrVersion"):
        if manifest[key] != lock[key]:
            raise RuntimeError(f"Build manifest and source lock disagree on {key}")
    if not lock.get("runtime_sources"):
        raise RuntimeError("The source lock must include webR's linked runtime sources")
    if lock.get("runtime_source_gaps"):
        raise RuntimeError("Source coverage is incomplete: " + "; ".join(lock["runtime_source_gaps"]))
    required(ROOT / "tools/deseq2/README.md")
    entries = {}
    source_hashes = {}

    def add(path, *, source=False, name=None):
        required(path)
        name = safe_name(name or path.relative_to(ROOT).as_posix())
        if name in entries and entries[name] != path:
            raise RuntimeError(f"Duplicate package entry: {name}")
        entries[name] = path
        if source:
            source_hashes[name] = digest(path)

    def add_tree(directory, *, source=False):
        if not directory.is_dir() or directory.is_symlink():
            raise RuntimeError(f"Required release directory is missing: {directory}")
        for path in sorted(directory.rglob("*")):
            if any(part in {".git", "__pycache__"} for part in path.relative_to(directory).parts):
                continue
            if path.is_symlink():
                raise RuntimeError(f"Release directories must not contain symlinks: {path}")
            if path.is_file():
                add(path, source=source)

    for name in ("LICENSE", "THIRD_PARTY.md", "runtime/deseq2.mjs", "runtime/DESEQ2.md",
                 "scripts/package-deseq2.py", "scripts/build-deseq2.py",
                 "scripts/build-deseq2-locfit.sh", "scripts/fetch-deseq2-data.py",
                 "scripts/setup-deseq2-native.py",
                 "scripts/validate-deseq2.mjs", "scripts/test-deseq2-demo.mjs",
                 "datasets/deseq2-manifest.json", "tests/test_deseq2_compare.py"):
        add(ROOT / name, source=True)
    for directory in ("tools/deseq2", "examples/deseq2", "validation/deseq2",
                      "datasets/deseq2-airway"):
        add_tree(ROOT / directory, source=True)
    add(required(ROOT / "build/deseq2/locfit/locfit-build-environment.txt"))

    # Verify the committed real-data hashes before including them in the release.
    subprocess.run([sys.executable, str(ROOT / "scripts/fetch-deseq2-data.py"), "--verify"],
                   cwd=ROOT, check=True)

    npm = checked_asset(lock["webr"]["binary"], ROOT / "build/deseq2/downloads")
    add(npm)
    expected_webr = {}
    with tarfile.open(npm) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            if member.name.startswith("package/dist/"):
                relative = member.name.removeprefix("package/dist/")
                if relative.split("/")[0] in {"tests", "repl"}:
                    continue
            elif member.name in {"package/LICENSE.md", "package/LICENCE.md", "package/package.json"}:
                relative = member.name.removeprefix("package/")
            else:
                continue
            safe_name(relative)
            expected_webr[relative] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    actual_webr = {path.relative_to(distribution / "webr").as_posix(): digest(required(path))
                   for path in (distribution / "webr").rglob("*") if path.is_file()}
    if expected_webr != actual_webr:
        raise RuntimeError("The webR distribution differs from its checksum-pinned npm archive")
    if (distribution / "webr/R.wasm").open("rb").read(8) != WASM_HEADER:
        raise RuntimeError("The R interpreter is not a WebAssembly binary")
    if not {"LICENSE.md", "LICENCE.md"} & set(actual_webr):
        raise RuntimeError("The webR distribution license is missing")
    provenance = json.loads(required(ROOT / "tools/deseq2/webr-build-provenance.json").read_text())
    wasm_bytes = (distribution / "webr/R.wasm").read_bytes()
    if (provenance["wasmSha256"] != hashlib.sha256(wasm_bytes).hexdigest()
            or provenance["npmArchiveSha256"] != lock["webr"]["binary"]["sha256"]):
        raise RuntimeError("webR compiler provenance does not identify the bundled binary")
    for item in provenance["embeddedCompilerStrings"]:
        encoded = item["text"].encode("utf-8")
        if wasm_bytes[item["offset"]:item["offset"] + len(encoded)] != encoded:
            raise RuntimeError("The recorded compiler string is absent from the webR binary")

    expected_packages = {package["name"]: package for package in lock["packages"]}
    built_packages = {package["name"]: package for package in manifest["packages"]}
    if (set(expected_packages) != set(built_packages)
            or len(expected_packages) != len(lock["packages"])
            or len(built_packages) != len(manifest["packages"])):
        raise RuntimeError("Missing, extra or duplicated R packages in the distribution")
    for name, package in expected_packages.items():
        built = built_packages[name]
        if built["version"] != package["version"] or built["filename"] != package["binary"]["filename"]:
            raise RuntimeError(f"Unexpected binary version or filename for {name}")
        path = checked_asset(built, distribution / "packages")
        if "sha256" in package["binary"]:
            checked_asset(package["binary"], distribution / "packages")
        package_metadata(path, package, binary=True)
        if "url" not in package["binary"]:
            add(path, name=f"build/deseq2/locfit/{path.name}")
        else:
            # Store one runtime copy; the assembler can reuse it as a cache.
            # The binaries are also listed explicitly in release.json.
            pass
    if {path.name for path in (distribution / "packages").iterdir()} != {
            package["filename"] for package in built_packages.values()}:
        raise RuntimeError("Unexpected files in the R package directory")
    add_tree(distribution)

    source_assets = [lock["webr"]["source"], lock["r"]["source"]]
    for package in lock["packages"] + lock["build_dependencies"]:
        source = checked_asset(package["source"], ROOT / "build/deseq2/sources")
        package_metadata(source, package, binary=False)
        source_assets.append(package["source"])
    source_assets += [entry.get("source", entry) for entry in lock["runtime_sources"]]
    for asset in source_assets:
        add(checked_asset(asset, ROOT / "build/deseq2/sources"), source=True)
    locfit_source = next(package["source"] for package in lock["packages"] if package["name"] == "locfit")
    add(checked_asset(locfit_source, ROOT / "build/deseq2/sources"), source=True,
        name="build/downloads/" + locfit_source["filename"])
    # The original data-package archive permits offline fixture regeneration.
    data_manifest = json.loads((ROOT / "datasets/deseq2-manifest.json").read_text())
    for filename, asset in data_manifest["sources"].items():
        add(checked_asset({**asset, "filename": filename, "size": asset["bytes"]},
                          ROOT / "build/deseq2-data"), source=True)

    playwright_version = json.loads(required(ROOT / "package.json").read_text())["devDependencies"]["playwright"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", playwright_version):
        raise RuntimeError("Browser validation requires an exact Playwright version")
    entries["README.md"] = package_readme(lock).encode()
    entries["package.json"] = json_bytes({
        "name": "deseq2-webr-standalone", "private": True, "type": "module",
        "scripts": {"validate": "node scripts/validate-deseq2.mjs",
                    "test": "python3 -m unittest discover -s tests -p 'test_*.py'"},
        "devDependencies": {"playwright": playwright_version}, "engines": {"node": ">=20"},
    })
    entries["source-manifest.json"] = json_bytes({
        "schemaVersion": 1, "upstreamLockSha256": digest(LOCK),
        "files": dict(sorted(source_hashes.items())),
        "notes": "Sources, original notices and build recipes are included. The webR and most R-package binaries are unmodified upstream builds. Only locfit is compiled by this project's build script.",
    })
    entries["release.json"] = json_bytes({
        "schemaVersion": 1, "name": "DESeq2", "version": lock["version"],
        "rVersion": lock["rVersion"], "webrVersion": lock["webrVersion"],
        "sourceLockSha256": digest(LOCK), "runtimeManifestSha256": digest(manifest_path),
        "packages": manifest["packages"],
        "binaryOrigin": "Upstream webR, r-wasm and Bioconductor R-universe binaries; locally compiled locfit dependency. DESeq2 analytical code is unchanged.",
        "validation": "Packaging checks build identity and completeness only. Consult the separate native-versus-webR validation reports for numerical agreement and tested scope.",
    })
    hashes = {name: digest(value) if isinstance(value, Path) else hashlib.sha256(value).hexdigest()
              for name, value in sorted(entries.items())}
    entries["SHA256SUMS"] = "".join(f"{value}  {name}\n" for name, value in hashes.items()).encode()
    return entries, lock


def package_readme(lock):
    return f'''# DESeq2 {lock["version"]} with webR

This standalone package runs the actual DESeq2 R package in a browser or Node.js.
It includes webR {lock["webrVersion"]}, R {lock["rVersion"]}, its pinned R-package
dependencies, a JavaScript interface, an editable browser example, real airway
count data, source archives, build recipes and original licence notices.

Serve this directory over HTTP:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/examples/deseq2/`. Everything needed for analysis is
served locally; no R server or external package repository is used at runtime.
Keep the directory layout intact. The full airway example contains 63,677 genes
and eight real samples. Browser memory and analysis time limit larger inputs.

See `tools/deseq2/README.md` for the JavaScript API, available functions, package
versions, build instructions and limitations. `examples/deseq2/README.md`
describes input tables and the paired airway analysis. `validation/deseq2/`
contains the script and comparison policy used for native R validation.

Node.js 20 or newer can use `runtime/deseq2.mjs` without npm dependencies.
To run the optional browser validation scripts, install their pinned test
dependency with `npm install`, then install browsers using
`npx playwright install`. The standalone archive supplies `package.json` and
does not require an npm lockfile to use the browser example or the runtime.

The webR interpreter and most package binaries come from their upstream
distributors without changes. This project compiles the missing locfit
dependency and assembles the bundle; it does not claim to have created the
upstream DESeq2 WebAssembly build. Only the dependency and analysis cases
explicitly documented in the validation reports have been tested.

The checksum-pinned corresponding sources are under `build/deseq2/sources/`.
The original airway archive is under `build/deseq2-data/`. The compiler toolchain
is not included. `scripts/build-deseq2-locfit.sh` uses the pinned development
container recorded in `tools/deseq2/locfit.lock.json`; this container is for
building the dependency and is not needed to use the browser package.

Verify packaged bytes with `sha256sum -c SHA256SUMS`. `release.json` records the
build identities; packaging success is not a numerical-validation result.

`LICENSE` covers this repository's original infrastructure. It does not replace
the upstream licences: the webR binary distribution is GPL-3.0, DESeq2 is
LGPL-3.0-or-later, and the bundled dependencies retain their own licences. Keep
the original notices, source archives and build recipes when redistributing.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "releases")
    args = parser.parse_args()
    entries, lock = collect()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = f"deseq2-webr-{lock['version']}"
    target = output / f"{stem}.zip"
    temporary = target.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as archive:
            for name, content in sorted(entries.items()):
                safe_name(name)
                info = zipfile.ZipInfo(f"{stem}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                mode = 0o755 if isinstance(content, Path) and content.stat().st_mode & 0o111 else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with archive.open(info, "w", force_zip64=True) as target_stream:
                    if isinstance(content, Path):
                        with content.open("rb") as source:
                            shutil.copyfileobj(source, target_stream, 1024 * 1024)
                    else:
                        target_stream.write(content)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("Release ZIP integrity check failed")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    (output / f"{stem}.zip.sha256").write_text(f"{digest(target)}  {target.name}\n")
    print(f"Created {target}: {len(entries)} files, {target.stat().st_size:,} bytes")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, KeyError, ValueError, tarfile.TarError,
            zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(f"Packaging failed: {error}", file=sys.stderr)
        raise SystemExit(1)
