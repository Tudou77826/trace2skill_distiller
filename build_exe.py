"""Build trace2skill as a standalone Windows .exe using PyInstaller."""

import argparse
import subprocess
import sys
from pathlib import Path


def _build(project_root: Path, spec_name: str, expected_output: Path) -> None:
    spec_file = project_root / spec_name
    if not spec_file.exists():
        print(f"Error: {spec_file} not found")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]

    print(f"Building {expected_output.name} ...")
    print(f"  Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(project_root))

    if result.returncode != 0:
        print(f"\nBuild failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    if expected_output.exists():
        size_mb = expected_output.stat().st_size / (1024 * 1024)
        print("\nBuild succeeded!")
        print(f"  Output: {expected_output}")
        print(f"  Size:   {size_mb:.1f} MB")
    else:
        print(f"\nWarning: expected output not found at {expected_output}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build standalone Trace2Skill executables.")
    parser.add_argument(
        "--target",
        choices=["gui", "cli", "all"],
        default="gui",
        help="Executable target to build. Defaults to the GUI one-file app.",
    )
    parser.add_argument("--gui", action="store_const", const="gui", dest="target", help="Build trace2skill-gui.exe.")
    parser.add_argument("--cli", action="store_const", const="cli", dest="target", help="Build the CLI onedir executable.")
    parser.add_argument("--all", action="store_const", const="all", dest="target", help="Build both GUI and CLI targets.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    if args.target in {"gui", "all"}:
        _build(project_root, "trace2skill-gui.spec", project_root / "dist" / "trace2skill-gui.exe")
    if args.target in {"cli", "all"}:
        _build(project_root, "trace2skill.spec", project_root / "dist" / "trace2skill" / "trace2skill.exe")


if __name__ == "__main__":
    main()
