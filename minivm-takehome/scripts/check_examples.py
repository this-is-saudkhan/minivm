"""Quick regression check: run every bundled example through optimize and
the real bin/minivm, printing a cycles-before/after table. Not a pytest
replacement -- see tests/ for the formal suite -- but a convenient one-shot
sanity check that needs nothing installed. Run from minivm-takehome/:

    python3 scripts/check_examples.py
"""
import glob
import json
import os
import platform
import subprocess


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def minivm_path():
    # An absolute path, not a bare "bin/...": native Windows subprocess
    # can't resolve a relative executable path without a leading "./" or
    # drive -- os.path.exists() is happy with it, CreateProcess isn't.
    system = platform.system().lower()
    machine = platform.machine().lower()
    os_name = {"darwin": "macos", "windows": "windows"}.get(system, system)
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    ext = ".exe" if os_name == "windows" else ""
    specific = os.path.join(ROOT, "bin", "minivm-%s-%s%s" % (os_name, arch, ext))
    return specific if os.path.exists(specific) else os.path.join(ROOT, "bin", "minivm")


MINIVM = minivm_path()
os.makedirs("_scratch", exist_ok=True)

programs = sorted(glob.glob("examples/programs/q*.mini"))
programs += sorted(glob.glob("examples/programs-extra/q*.mini"))

for q in programs:
    base = os.path.splitext(os.path.basename(q))[0]
    opt_path = "_scratch/%s.opt.mini" % base
    with open(opt_path, "w") as f:
        subprocess.run(["python3", "optimize", q, "examples/schema.json"], stdout=f)
    orig = subprocess.run(
        [MINIVM, q, "examples/data.json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    opt = subprocess.run(
        [MINIVM, opt_path, "examples/data.json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    print("=== %s ===" % base)
    if orig.returncode != 0:
        print("  orig ERROR:", orig.stderr.strip())
        continue
    if opt.returncode != 0:
        print("  OPT ERROR (defect!):", opt.stderr.strip())
        continue
    a = json.loads(orig.stdout)
    b = json.loads(opt.stdout)
    same_rows = a["rows"] == b["rows"]
    same_ncols = a["ncols"] == b["ncols"]
    pct = 100.0 * (1 - b["cycles"] / a["cycles"])
    print("  cycles: %d -> %d  (%.1f%% fewer)" % (a["cycles"], b["cycles"], pct))
    print("  rows match: %s  ncols match: %s" % (same_rows, same_ncols))
    if not same_rows:
        print("  ORIG rows:", a["rows"][:3])
        print("  OPT  rows:", b["rows"][:3])
