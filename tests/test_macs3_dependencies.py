"""Use the real pinned packaging parser to reject incomplete browser wheel sets."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools/macs3/check-wheel-dependencies.py"


class BrowserDependencyClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lock = json.loads((ROOT / "tools/macs3/sources.lock.json").read_text())
        record = lock["dependencies"]["packaging"]["wheel"]
        cls.parser_sha = record["sha256"]
        cls.parser_wheel = next((path for path in (
            ROOT / "build/downloads" / record["archive"],
            ROOT / "dist/macs3" / record["filename"],
            ROOT / "dist/macs3/sources" / record["archive"],
        ) if path.is_file()), None)
        if cls.parser_wheel is None:
            raise unittest.SkipTest("Fetch the pinned packaging wheel before running its parser tests")
        if hashlib.sha256(cls.parser_wheel.read_bytes()).hexdigest() != cls.parser_sha:
            raise ValueError("Pinned packaging parser wheel checksum mismatch")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def wheel(self, name, version="1.0", *, requirements=(), extras=(), python=">=3.12"):
        path = self.directory / f"{name}-{version}-py3-none-any.whl"
        headers = ["Metadata-Version: 2.3", f"Name: {name}", f"Version: {version}", f"Requires-Python: {python}"]
        headers += [f"Requires-Dist: {value}" for value in requirements]
        headers += [f"Provides-Extra: {value}" for value in extras]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{name}-{version}.dist-info/METADATA", "\n".join(headers) + "\n")
        return path

    def run_helper(self, *wheels, sha=None):
        return subprocess.run([sys.executable, "-I", "-S", str(HELPER),
                               "--packaging", str(self.parser_wheel),
                               "--packaging-sha256", sha or self.parser_sha,
                               "--python", "3.14.2", *map(str, wheels)], capture_output=True, text=True)

    def test_emscripten_marker_requires_timezone_data(self):
        pandas = self.wheel("pandas", requirements=['tzdata>=2026.1; sys_platform == "emscripten"'])
        missing = self.run_helper(pandas)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Missing browser dependency", missing.stderr)
        complete = self.run_helper(pandas, self.wheel("tzdata", "2026.1"))
        self.assertEqual(complete.returncode, 0, complete.stderr)
        self.assertEqual(json.loads(complete.stdout)["requirementsChecked"], 1)

    def test_wrong_versions_python_and_parser_checksum_are_rejected(self):
        package = self.wheel("subject", requirements=["numpy>=2.0,<3"])
        old_numpy = self.wheel("numpy", "1.26")
        self.assertIn("Incompatible browser dependency", self.run_helper(package, old_numpy).stderr)
        self.assertIn("requires Python", self.run_helper(self.wheel("newpython", python=">=3.15")).stderr)
        self.assertIn("checksum mismatch", self.run_helper(package, sha="0" * 64).stderr)

    def test_compound_markers_extras_and_name_normalisation(self):
        a = self.wheel("subject", requirements=[
            'unused; python_version < "3.12" or sys_platform == "win32"',
            'array_api_compat[special]>=1; implementation_name == "cpython" and python_version >= "3.14"',
        ])
        b = self.wheel("array-api-compat", extras=["special"], requirements=['six; extra == "special"'])
        self.assertIn("requires six", self.run_helper(a, b).stderr)
        result = self.run_helper(a, b, self.wheel("six"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_distributions_and_direct_urls_are_rejected(self):
        same = self.wheel("same")
        self.assertIn("duplicate wheel", self.run_helper(same, same).stderr)
        direct = self.wheel("direct", requirements=["same @ https://example.invalid/same.whl"])
        self.assertIn("direct-URL", self.run_helper(direct, same).stderr)


if __name__ == "__main__":
    unittest.main()
