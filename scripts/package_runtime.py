#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--skip-dependencies", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temporary:
        package = Path(temporary) / "package"
        package.mkdir()
        if not args.skip_dependencies:
            environment = Path(temporary) / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            pip = environment / ("Scripts/pip.exe" if sys.platform == "win32"
                                 else "bin/pip")
            subprocess.run([
                str(pip), "install",
                "--quiet",
                "--disable-pip-version-check",
                "--no-compile",
                "--only-binary=:all:",
                "--platform=manylinux2014_aarch64",
                "--implementation=cp",
                "--python-version=3.13",
                "--abi=cp313",
                "--target", str(package),
                "-r", str(ROOT / "requirements.txt"),
            ], check=True)
        for directory in ("temporal_agent", "aws_lambda", "fixtures"):
            shutil.copytree(
                ROOT / directory,
                package / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                dirs_exist_ok=True,
            )
        for filename in ("runtime_agent.py", "runtime_tools.py"):
            shutil.copy2(ROOT / filename, package / filename)

        with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package.rglob("*")):
                if (
                    path.is_file()
                    and "__pycache__" not in path.parts
                    and path.suffix != ".pyc"
                ):
                    archive.write(path, path.relative_to(package))

    print(args.output)


if __name__ == "__main__":
    main()
