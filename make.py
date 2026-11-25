"""Release helper for Shape Draw. Plain Python 3, no extra packages.

    python make.py test      smoke test (no Blender) + headless test in Blender
    python make.py check     version sync + Blender's manifest validator
    python make.py build     check + test, then build dist/shape_draw-<version>.zip
    python make.py install   build, then install the zip into Blender (user_default repo)
    python make.py lint      ruff, if it's installed (the Blender reviewers run it)

Blender is found from $BLENDER, then PATH, then the usual install folders.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")


def find_blender():
    env = os.environ.get("BLENDER")
    if env and os.path.isfile(env):
        return env
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    patterns = [
        r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
        "/Applications/Blender.app/Contents/MacOS/Blender",
        "/usr/bin/blender",
        "/snap/bin/blender",
    ]
    found = sorted(p for pat in patterns for p in glob.glob(pat))
    if found:
        return found[-1]  # newest version folder sorts last
    sys.exit("Blender not found: set the BLENDER environment variable to blender's path.")


def manifest_version():
    text = open(os.path.join(HERE, "blender_manifest.toml"), encoding="utf-8").read()
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def run(cmd, expect=None):
    print(">", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    res = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    out = res.stdout + res.stderr
    if res.returncode != 0 or (expect and expect not in out) or "Traceback" in out:
        print(out)
        sys.exit(f"FAILED: {' '.join(cmd[:3])}")
    return out


def check_versions():
    version = manifest_version()
    src = open(os.path.join(HERE, "shape_draw.py"), encoding="utf-8").read()
    tup = re.search(r'"version":\s*\(([^)]*)\)', src).group(1)
    bl_info = ".".join(p.strip() for p in tup.split(","))
    if bl_info != version:
        sys.exit(f"Version mismatch: manifest {version}, bl_info {bl_info}")
    changelog = open(os.path.join(HERE, "CHANGELOG.md"), encoding="utf-8").read()
    if f"## {version}" not in changelog:
        sys.exit(f"CHANGELOG.md has no '## {version}' entry")
    print(f"version {version} in sync")
    return version


def cmd_check():
    check_versions()
    run([find_blender(), "--command", "extension", "validate"], expect="Success")
    print("manifest valid")


def cmd_test():
    run([sys.executable, os.path.join("tests", "smoke_test.py")], expect="SMOKE_OK")
    print("smoke test ok")
    run([find_blender(), "--background", "--factory-startup",
         "--python", os.path.join("tests", "headless_test.py")], expect="ALL_OK")
    print("headless test ok")


def cmd_build():
    cmd_check()
    cmd_test()
    os.makedirs(DIST, exist_ok=True)
    zpath = os.path.join(DIST, f"shape_draw-{manifest_version()}.zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    run([find_blender(), "--command", "extension", "build", "--output-dir", DIST])
    names = zipfile.ZipFile(zpath).namelist()
    expected = {"__init__.py", "shape_draw.py", "blender_manifest.toml", "LICENSE"}
    if set(names) != expected:
        sys.exit(f"Unexpected zip contents: {sorted(names)}")
    print(f"built {os.path.relpath(zpath, HERE)} ({os.path.getsize(zpath) // 1024} KB): {', '.join(sorted(names))}")
    return zpath


def cmd_install():
    zpath = cmd_build()
    run([find_blender(), "--command", "extension", "install-file", "-r", "user_default", "-e", zpath])
    print("installed: restart Blender, or reinstall from Preferences > Get Extensions if it's open")


def cmd_lint():
    ruff = shutil.which("ruff")
    if not ruff:
        sys.exit("ruff isn't installed (pip install ruff)")
    subprocess.run([ruff, "check", "."], cwd=HERE, check=True)


COMMANDS = {"test": cmd_test, "check": cmd_check, "build": cmd_build,
            "install": cmd_install, "lint": cmd_lint}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]]()
