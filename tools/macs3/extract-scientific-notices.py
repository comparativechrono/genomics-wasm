#!/usr/bin/env python3
"""Regenerate scientific-package notices from checksum-pinned original archives."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/macs3"
DEST = TOOL / "scientific-notices"
LICENSE_NAME = re.compile(r"^(?:licen[cs]e|copying|copyright|notice)(?:s)?(?:[._-].*)?$", re.I)
EXCLUDED_SUFFIXES = {".py", ".rst", ".html", ".yml", ".yaml", ".rtf"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def legal_file(name):
    path = PurePosixPath(name)
    named = (bool(LICENSE_NAME.match(path.name))
             or bool(re.search(r"(?:^|[_-])(?:licen[cs]e|copying|copyright|notice)(?:$|[_.-])", path.name, re.I))
             or any(part.lower() in {"licenses", "licences"} for part in path.parts))
    return (named and path.suffix.lower() not in EXCLUDED_SUFFIXES
            and not any(part in {"tests", "test", "doc", "docs", "examples", "vendored-meson", ".spin"}
                        for part in path.parts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra-records", type=Path, help="Additional pinned source records during lock authoring")
    args = parser.parse_args()
    lock = json.loads((TOOL / "sources.lock.json").read_text())
    dependencies = dict(lock["dependencies"])
    if args.extra_records:
        dependencies.update(json.loads(args.extra_records.read_text()))
    records = {"macs3": lock}
    for name, record in dependencies.items():
        if (name in lock["runtimePackages"] or name in {"pybind11", "runtime-openblas", "runtime-libf2c",
                                                       "runtime-boost", "runtime-hdf5", "runtime-scientific-zlib",
                                                       "runtime-scientific-recipes"}):
            records[name] = record
    manifest = []

    def save(component, archive, member, contents, kind):
        parts = PurePosixPath(member).parts
        if any(part in ("", ".", "..") for part in parts):
            raise RuntimeError(f"Unsafe archive member: {member}")
        filename = "__".join(parts[1:]) if len(parts) > 1 else parts[0]
        if kind == "embedded-comment":
            filename += ".notice"
        destination = DEST / component / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents)
        manifest.append({"component": component, "archive": archive["archive"],
                         "archiveSha256": archive["sha256"], "member": member,
                         "kind": kind, "destination": destination.relative_to(ROOT).as_posix(),
                         "sha256": sha256(contents)})

    for component, record in sorted(records.items()):
        path = ROOT / "build/downloads" / record["archive"]
        data = path.read_bytes()
        if sha256(data) != record["sha256"]:
            raise RuntimeError(f"Source checksum mismatch: {path}")
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                if component == "runtime-scientific-recipes" and len(PurePosixPath(member.name).parts) != 2:
                    continue
                if legal_file(member.name):
                    save(component, record, member.name, archive.extractfile(member).read(), "complete-file")
                elif component == "joblib" and member.name.endswith("/externals/cloudpickle/cloudpickle.py"):
                    contents = archive.extractfile(member).read()
                    block = re.match(rb'""".*?"""', contents, flags=re.S)
                    if not block or b"Redistribution and use" not in block[0]:
                        raise RuntimeError("Missing vendored CloudPickle license docstring")
                    save(component, record, member.name, block[0] + b"\n", "embedded-comment")
                elif component == "macs3" and ("/fermi-lite/" in member.name or "/Signal/swalign." in member.name):
                    contents = archive.extractfile(member).read()
                    blocks = re.findall(rb"/\*.*?\*/", contents[:8192], flags=re.S)
                    for block in blocks:
                        if (b"Permission is hereby granted" in block or b"SPDX-License-Identifier" in block
                                or b"Redistribution and use" in block):
                            save(component, record, member.name, block + b"\n", "embedded-comment")
                            break
        wheel_record = record.get("wheel")
        if wheel_record:
            path = ROOT / "build/downloads" / wheel_record["archive"]
            data = path.read_bytes()
            if sha256(data) != wheel_record["sha256"]:
                raise RuntimeError(f"Wheel checksum mismatch: {path}")
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    if not member.endswith("/") and legal_file(member):
                        save(component + "-wheel", wheel_record, member, archive.read(member), "complete-file")
    manifest.sort(key=lambda record: record["destination"])
    paths = {record["destination"] for record in manifest}
    if len(paths) != len(manifest):
        raise RuntimeError("Notice extraction produced duplicate output paths")
    for path in DEST.rglob("*"):
        if path.is_file() and path.name not in {"README.md", "manifest.json"} and path.relative_to(ROOT).as_posix() not in paths:
            raise RuntimeError(f"Stale notice file needs review: {path}")
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Extracted {len(manifest)} exact notice files from {len(records)} source packages")


if __name__ == "__main__":
    main()
