#!/usr/bin/env python3
"""Build a native, non-notarized WeSwitch app without installing dependencies."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=VERSION, help="Release version, with or without a leading v")
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="Bundle and archive output directory")
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        parser.error("Use a stable semantic version such as v0.1.0.")
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required.")
    if sys.platform != "darwin":
        parser.error("Build on macOS; cross-platform app packaging is not supported.")
    architecture = platform.machine()
    if architecture not in {"arm64", "x86_64"}:
        parser.error(f"Unsupported Python architecture: {architecture}")
    if importlib.util.find_spec("PyInstaller") is None:
        parser.error("Install requirements-dev.txt in a dedicated virtual environment first.")
    for name in ("launch_desktop.py", "server.py", "index.html", "i18n.js"):
        if not (ROOT / name).is_file():
            parser.error(f"Required source file is missing: {name}")

    output = args.output.expanduser().resolve()
    work = ROOT / "build"
    if output == ROOT or output == work or output.is_relative_to(work):
        parser.error("Choose a distribution directory separate from the source root and build directory.")
    # Only these generated PyInstaller locations may be replaced by --noconfirm.
    for path in (work, output / "WeSwitch", output / "WeSwitch.app"):
        if path.is_symlink():
            parser.error(f"Refusing a symlink at a generated build location: {path.name}")
    if any((output / name).exists() for name in ("WeSwitch", "WeSwitch.app")):
        parser.error("An app already exists in the output directory. Choose a fresh --output directory to avoid replacing it.")
    archive = output / f"WeSwitch-v{version}-macos-{architecture}.zip"
    checksum = archive.with_suffix(".zip.sha256")
    partial = archive.with_suffix(".zip.partial")
    if any(path.exists() or path.is_symlink() for path in (archive, checksum, partial)):
        parser.error("This version's archive/checksum already exists. Use a fresh --output directory; existing release archives are not overwritten.")
    for directory in (work, output):
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--windowed", "--onedir", "--noconfirm",
        "--name", "WeSwitch",
        "--osx-bundle-identifier", "com.susunola.weswitch",
        "--target-architecture", architecture,
        "--add-data", f"{ROOT / 'index.html'}:.",
        "--add-data", f"{ROOT / 'i18n.js'}:.",
        "--copy-metadata", "tomlkit",
        "--copy-metadata", "certifi",
        "--paths", str(ROOT),
        "--distpath", str(output),
        "--workpath", str(work),
        "--specpath", str(work),
    ]
    icon = ROOT / "assets" / "WeSwitch.icns"
    if icon.is_file():
        command.extend(["--icon", str(icon)])
    command.append(str(ROOT / "launch_desktop.py"))
    subprocess.run(command, cwd=ROOT, check=True)

    bundle = output / "WeSwitch.app"
    executable = bundle / "Contents" / "MacOS" / "WeSwitch"
    actual = subprocess.check_output(["/usr/bin/lipo", "-archs", str(executable)], text=True).strip().split()
    if actual != [architecture]:
        raise SystemExit(f"Architecture mismatch: Python is {architecture}, bundle contains {actual}.")
    subprocess.run([
        "/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
        str(bundle), str(partial),
    ], check=True)
    os.replace(partial, archive)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Created {archive.name} and {checksum.name} in {output}")
    print("No Developer ID signing or notarization was performed. PyInstaller may apply an ad-hoc signature; Gatekeeper can still warn or block launch.")


if __name__ == "__main__":
    main()
