import json
import subprocess
import sys
import glob
import os

MINIVM = "./bin/minivm-windows-x86_64.exe"
os.makedirs("_scratch", exist_ok=True)

for q in sorted(glob.glob("examples/programs/q*.mini")):
    base = os.path.splitext(os.path.basename(q))[0]
    opt_path = f"_scratch/{base}.opt.mini"
    subprocess.run(["python3", "optimize", q, "examples/schema.json"], stdout=open(opt_path, "w"))
    orig = subprocess.run([MINIVM, q, "examples/data.json"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    opt = subprocess.run([MINIVM, opt_path, "examples/data.json"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    print(f"=== {base} ===")
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
    print(f"  cycles: {a['cycles']} -> {b['cycles']}  ({pct:.1f}% fewer)")
    print(f"  rows match: {same_rows}  ncols match: {same_ncols}")
    if not same_rows:
        print("  ORIG rows:", a["rows"][:3])
        print("  OPT  rows:", b["rows"][:3])
