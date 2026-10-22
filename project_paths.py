"""Stable paths independent of cwd and Python package layout; no I/O on import."""
from pathlib import Path
PROJECT = Path(__file__).resolve().parent
FRONTEND = PROJECT / "frontend"
MACOS = PROJECT / "macos"
SWIFT_CACHE = PROJECT / ".swift-build"
