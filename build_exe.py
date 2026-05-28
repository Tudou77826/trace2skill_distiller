"""Build trace2skill as a standalone Windows .exe using PyInstaller."""

import subprocess
import sys
from pathlib import Path


def main():
    project_root = Path(__file__).resolve().parent
    spec_file = project_root / "trace2skill.spec"

    if not spec_file.exists():
        print(f"Error: {spec_file} not found")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]

    print("Building trace2skill.exe ...")
    print(f"  Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(project_root))

    if result.returncode != 0:
        print(f"\nBuild failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    exe_path = project_root / "dist" / "trace2skill" / "trace2skill.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\nBuild succeeded!")
        print(f"  Output: {exe_path}")
        print(f"  Size:   {size_mb:.1f} MB")
    else:
        print(f"\nWarning: expected output not found at {exe_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
