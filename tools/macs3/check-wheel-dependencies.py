#!/usr/bin/env python3
"""Check browser wheel requirements with the verified, bundled PEP 508 parser.

Run with python -I -S. No installation, network access or ambient site packages
are used: the pinned pure-Python packaging wheel supplies the standard parser.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def metadata(path):
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(members) != 1:
            raise ValueError(f"No unique wheel metadata: {path}")
        return BytesParser().parsebytes(archive.read(members[0]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaging", type=Path, required=True)
    parser.add_argument("--packaging-sha256", required=True)
    parser.add_argument("--python", required=True, help="Full target Python version")
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args()
    if not sys.flags.isolated or not sys.flags.no_site:
        parser.error("Invoke this helper with python -I -S")
    wheel = args.packaging.resolve(strict=True)
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != args.packaging_sha256:
        parser.error("PEP 508 parser wheel checksum mismatch")
    if metadata(wheel).get("Name", "").lower() != "packaging":
        parser.error("PEP 508 parser wheel is not packaging")
    sys.path.insert(0, str(wheel))
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name
    from packaging.version import Version

    target_python = Version(args.python)
    if len(target_python.release) != 3:
        parser.error("Target Python must have major, minor and patch versions")
    environment = {
        "implementation_name": "cpython", "implementation_version": args.python,
        "os_name": "posix", "platform_machine": "wasm32",
        "platform_python_implementation": "CPython", "platform_release": "",
        "platform_system": "Emscripten", "platform_version": "",
        "python_full_version": args.python,
        "python_version": ".".join(map(str, target_python.release[:2])),
        "sys_platform": "emscripten", "extra": "",
    }
    installed = {}
    for path in args.wheels:
        headers = metadata(path)
        name = canonicalize_name(headers.get("Name", ""))
        if not name or name in installed:
            raise ValueError(f"Missing or duplicate wheel distribution: {name}")
        version = Version(headers["Version"])
        python_requirement = headers.get("Requires-Python")
        if python_requirement and target_python not in SpecifierSet(python_requirement):
            raise ValueError(f"{name} requires Python {python_requirement}; target is {target_python}")
        installed[name] = {
            "version": version,
            "requirements": [Requirement(value) for value in headers.get_all("Requires-Dist", [])],
            "extras": {canonicalize_name(value) for value in headers.get_all("Provides-Extra", [])},
        }
    active = {name: {""} for name in installed}
    pending = [(name, "") for name in installed]
    checked = set()
    while pending:
        name, extra = pending.pop()
        for requirement in installed[name]["requirements"]:
            if requirement.marker and not requirement.marker.evaluate({**environment, "extra": extra}):
                continue
            dependency = canonicalize_name(requirement.name)
            if requirement.url:
                raise ValueError(f"Unverifiable direct-URL requirement in {name}: {requirement}")
            if dependency not in installed:
                raise ValueError(f"Missing browser dependency: {name} requires {requirement}")
            if requirement.specifier and installed[dependency]["version"] not in requirement.specifier:
                raise ValueError(f"Incompatible browser dependency: {name} requires {requirement}, "
                                 f"bundled {dependency} is {installed[dependency]['version']}")
            checked.add((name, str(requirement)))
            for requested in requirement.extras:
                requested = canonicalize_name(requested)
                if requested not in installed[dependency]["extras"]:
                    raise ValueError(f"Undeclared extra: {name} requires {dependency}[{requested}]")
                if requested not in active[dependency]:
                    active[dependency].add(requested)
                    pending.append((dependency, requested))
    print(json.dumps({"target": environment,
                      "packages": {name: str(item["version"]) for name, item in sorted(installed.items())},
                      "requirementsChecked": len(checked)}, sort_keys=True))


if __name__ == "__main__":
    main()
