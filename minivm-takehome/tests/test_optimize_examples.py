"""Integration tests: for every bundled example program (the 8 given ones
plus the 4 added in examples/programs-extra), optimize's output must run
through the real bin/minivm and produce the same rows as the original, in
fewer (or equal) cycles."""
import json
import os

import pytest

from conftest import (
    DATA_PATH, SCHEMA_PATH, example_programs, run_minivm, run_optimize,
)


@pytest.mark.parametrize("program_path", example_programs(), ids=lambda p: os.path.basename(p))
def test_optimized_matches_original_and_is_not_slower(minivm_available, tmp_path, program_path):
    orig = run_minivm(program_path, DATA_PATH)
    assert orig.returncode == 0, "bundled example should run cleanly: " + orig.stderr
    orig_result = json.loads(orig.stdout)

    opt = run_optimize(program_path, SCHEMA_PATH)
    assert opt.returncode == 0, "optimize should never fail on a valid program: " + opt.stderr
    opt_path = tmp_path / "optimized.mini"
    opt_path.write_text(opt.stdout, encoding="utf-8")

    opt_run = run_minivm(str(opt_path), DATA_PATH)
    assert opt_run.returncode == 0, (
        "optimized program errored where the original succeeded:\n" + opt_run.stderr
    )
    opt_result = json.loads(opt_run.stdout)

    assert opt_result["ncols"] == orig_result["ncols"]
    assert opt_result["rows"] == orig_result["rows"], (
        "optimize changed the result on %s" % program_path
    )
    assert opt_result["cycles"] <= orig_result["cycles"], (
        "optimize made %s slower (%d -> %d cycles)"
        % (program_path, orig_result["cycles"], opt_result["cycles"])
    )


def test_optimize_is_idempotent(minivm_available, tmp_path):
    """Optimizing an already-optimized program shouldn't change its
    behavior (it's fine if it's a no-op; it must not be a defect)."""
    program_path = example_programs()[0]
    first = run_optimize(program_path, SCHEMA_PATH)
    assert first.returncode == 0
    once_path = tmp_path / "once.mini"
    once_path.write_text(first.stdout, encoding="utf-8")

    second = run_optimize(str(once_path), SCHEMA_PATH)
    assert second.returncode == 0
    twice_path = tmp_path / "twice.mini"
    twice_path.write_text(second.stdout, encoding="utf-8")

    r1 = run_minivm(str(once_path), DATA_PATH)
    r2 = run_minivm(str(twice_path), DATA_PATH)
    assert r1.returncode == 0 and r2.returncode == 0
    assert json.loads(r1.stdout)["rows"] == json.loads(r2.stdout)["rows"]


def test_malformed_input_falls_back_unchanged(tmp_path):
    bad = tmp_path / "bad.mini"
    bad.write_text("THIS IS NOT VALID\nGARBAGE r1, r2\n", encoding="utf-8")
    result = run_optimize(str(bad), SCHEMA_PATH)
    assert result.returncode == 0
    assert result.stdout == bad.read_text(encoding="utf-8")


def test_missing_out_falls_back_unchanged(tmp_path):
    bad = tmp_path / "bad.mini"
    bad.write_text("LOAD a, orders.o_id\n", encoding="utf-8")
    result = run_optimize(str(bad), SCHEMA_PATH)
    assert result.returncode == 0
    assert result.stdout == bad.read_text(encoding="utf-8")
