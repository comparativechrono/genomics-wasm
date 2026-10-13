"""Run the exact upstream MACS3 CLI in an isolated, pinned package tree."""
from pathlib import Path
import runpy
import sys

if sys.version_info[:3] != (3, 14, 2):
    raise SystemExit("The pinned MACS3 comparison environment requires Python 3.14.2")
sys.dont_write_bytecode = True

base = Path(__file__).resolve().parent
sys.path.insert(0, str(base / "macs3-packages"))
if __name__ == "__main__":
    # A real upstream process pool, without forkserver AF_UNIX requirements.
    import multiprocessing
    multiprocessing.set_start_method("spawn")
    sys.argv[0] = "macs3"
    runpy.run_path(str(base / "macs3-cli/macs3"), run_name="__main__")
