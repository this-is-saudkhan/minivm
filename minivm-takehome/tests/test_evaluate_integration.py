"""Integration tests for evaluate itself: it must accept the real optimizer
and every *correct* optimizer (the passthrough included -- doing nothing is
a valid, if poor, optimization), and must reject every deliberately broken
one in tests/fixtures/broken_optimizers/. These are slow (each run fuzzes
dozens of random datasets against 12 programs within evaluate's own ~95s
budget), so they're marked `slow` -- run `pytest -m "not slow"` to skip them
for a quick check of everything else.
"""
import os

import pytest

from conftest import ROOT, run_evaluate

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "broken_optimizers")


@pytest.mark.slow
def test_evaluate_accepts_the_real_optimizer(minivm_available):
    result = run_evaluate(os.path.join(ROOT, "optimize"))
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_evaluate_accepts_passthrough(minivm_available):
    """Doing nothing is a correct (if pointless) optimizer; evaluate must
    not flag it just because it's the original program wearing a mustache."""
    result = run_evaluate(os.path.join(FIXTURES, "passthrough.py"))
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_evaluate_catches_dropped_output_column(minivm_available):
    result = run_evaluate(os.path.join(FIXTURES, "drop_last_out_column.py"))
    assert result.returncode != 0
    assert "ncols differ" in result.stderr


@pytest.mark.slow
def test_evaluate_catches_data_dependent_threshold_bug(minivm_available):
    """This is the interesting one: the bug only manifests for orders with
    o_amt in a specific 10-unit band, which the fixed examples/data.json
    never happens to hit. Catching it requires evaluate's random-data
    fuzzing layer, not just its regression check against the bundled data.
    """
    result = run_evaluate(os.path.join(FIXTURES, "corrupt_q0001_threshold.py"))
    assert result.returncode != 0
    assert "q0001" in result.stderr
