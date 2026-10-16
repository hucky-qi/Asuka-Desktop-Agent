"""Compile native helpers with project-local caches and a driver crash fallback."""

import os
import subprocess
from pathlib import Path


def compile_helper(source, binary):
    directory = Path(source).resolve().parent / ".swift-build"
    directory.mkdir(exist_ok=True)
    environment = dict(os.environ, TMPDIR=str(directory) + "/")
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    command = ["swiftc", "-sdk", sdk, "-module-cache-path", str(directory / "modules"),
               str(source), "-o", str(binary)]
    try:
        subprocess.run(command, env=environment, capture_output=True, text=True,
                       timeout=180, check=True)
    except subprocess.CalledProcessError as exc:
        if exc.returncode not in (-5, 133):
            raise
        # Some Command Line Tools installations crash in the new Swift driver.
        # The same compiler's legacy driver still builds the native helpers.
        environment["SWIFT_USE_OLD_DRIVER"] = "1"
        subprocess.run(command, env=environment, capture_output=True, text=True,
                       timeout=180, check=True)
