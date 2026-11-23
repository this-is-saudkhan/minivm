"""Shared fixtures for the pytest suite.

Two testing styles are used side by side, deliberately:
  - Integration tests invoke `optimize`/`evaluate` as subprocesses, exactly
    as a grader would (`./optimize program.mini schema.json`), and run
    results through the real bin/minivm binary. These are the ones that
    actually matter for correctness: they can't be fooled by an internal
    refactor that breaks the CLI contract.
  - Unit tests import optimize.py/evaluate.py's functions directly (via
    import_script below, since neither file has a .py extension) to pin
    down specific pieces of logic -- the parser, the union-find, the
    constraint-folding predicate, the row comparator -- in isolation,
    independent of whatever the end-to-end behavior happens to be today.

Run from the repo root: `python3 -m pytest` (or `pytest` if it's on PATH).
"""
import importlib.util
import importlib.machinery
import json
import os
import platform
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def import_script(name):
    """Import a shebang'd, extensionless CLI script (optimize/evaluate) as
    an ordinary module, for white-box unit testing of its functions."""
    path = os.path.join(ROOT, name)
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def optimize_mod():
    return import_script("optimize")


@pytest.fixture(scope="session")
def evaluate_mod():
    return import_script("evaluate")


def _minivm_path():
    system = platform.system().lower()
    machine = platform.machine().lower()
    os_name = {"darwin": "macos", "windows": "windows"}.get(system, system)
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    ext = ".exe" if os_name == "windows" else ""
    specific = os.path.join(ROOT, "bin", "minivm-%s-%s%s" % (os_name, arch, ext))
    if os.path.exists(specific):
        return specific
    return os.path.join(ROOT, "bin", "minivm")


MINIVM = _minivm_path()


def _shebang_prefix(path):
    if os.name != "nt":
        return []
    with open(path, "rb") as f:
        first = f.readline()
    if not first.startswith(b"#!"):
        return []
    parts = first[2:].decode("utf-8", "replace").split()
    if parts and parts[0].endswith("env") and len(parts) > 1:
        return [parts[1]]
    return parts[:1] if parts else []


def run_minivm(program_path, tableset_path, timeout=10):
    p = subprocess.run(
        [MINIVM, program_path, tableset_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=timeout,
    )
    return p


def run_optimize(program_path, schema_path, optimizer=None, timeout=15):
    optimizer = optimizer or os.path.join(ROOT, "optimize")
    cmd = _shebang_prefix(optimizer) + [optimizer, program_path, schema_path]
    p = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=timeout,
    )
    return p


def run_evaluate(optimizer, timeout=130):
    cmd = _shebang_prefix(os.path.join(ROOT, "evaluate")) + [
        os.path.join(ROOT, "evaluate"), optimizer,
    ]
    p = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=timeout,
    )
    return p


@pytest.fixture(scope="session")
def minivm_available():
    if not os.path.exists(MINIVM):
        pytest.skip("no bin/minivm build for this platform (ask for a build)")
    return MINIVM


def example_programs():
    progs = []
    for sub in ("programs", "programs-extra"):
        d = os.path.join(ROOT, "examples", sub)
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".mini"):
                    progs.append(os.path.join(d, fn))
    return progs


SCHEMA_PATH = os.path.join(ROOT, "examples", "schema.json")
DATA_PATH = os.path.join(ROOT, "examples", "data.json")


@pytest.fixture(scope="session")
def schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)
