"""Portable launcher for the exact native comparison environment."""
from pathlib import Path
import sys

if sys.version_info[:3] != (3, 14, 2):
    raise SystemExit("The pinned Cutadapt comparison environment requires Python 3.14.2")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().with_name("cutadapt-packages")))
from cutadapt.cli import main_cli

main_cli()
