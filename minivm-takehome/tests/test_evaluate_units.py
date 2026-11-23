"""White-box unit tests of evaluate.py's internal pieces: the value
comparator, the order-sensitivity heuristic, and the constraint-respecting
random data generator. These matter as much as optimize.py's own tests --
a bug in the checker is a bug that can hide a real defect."""
import random

import pytest


# ---------------------------------------------------------------------
# Value comparison: 1e-6 relative tolerance on numbers, exact elsewhere.
# ---------------------------------------------------------------------


def test_values_equal_exact_for_non_numeric(evaluate_mod):
    m = evaluate_mod
    assert m.values_equal(None, None)
    assert not m.values_equal(None, 0)
    assert m.values_equal(True, True)
    assert not m.values_equal(True, 1)  # BOOL and INT must not be conflated
    assert m.values_equal("ab", "ab")
    assert not m.values_equal("ab", "ab ")  # trailing space matters for STR
    assert m.values_equal("2024-01-31", "2024-01-31")


def test_values_equal_num_tolerance(evaluate_mod):
    m = evaluate_mod
    assert m.values_equal("100.000000", "100.0000001")  # well within 1e-6 relative
    assert not m.values_equal("100.00", "100.01")  # outside tolerance
    assert m.values_equal("0", "0")  # zero-vs-zero must not divide by zero
    assert not m.values_equal("0", "0.01")


def test_rows_equal_ordered_requires_positional_match(evaluate_mod):
    m = evaluate_mod
    a = [[1, "x"], [2, "y"]]
    assert m.rows_equal_ordered(a, [[1, "x"], [2, "y"]])
    assert not m.rows_equal_ordered(a, [[2, "y"], [1, "x"]])  # same multiset, wrong order


def test_rows_equal_unordered_allows_permutation_not_corruption(evaluate_mod):
    m = evaluate_mod
    a = [[1, "x"], [2, "y"]]
    assert m.rows_equal_unordered(a, [[2, "y"], [1, "x"]])
    assert not m.rows_equal_unordered(a, [[1, "x"], [3, "y"]])
    assert not m.rows_equal_unordered(a, [[1, "x"]])  # different row count


# ---------------------------------------------------------------------
# order_matters: conservative (documented in PLAN.md as a known
# under-approximation -- it must never report True for something that
# isn't order-carrying, only the reverse is allowed).
# ---------------------------------------------------------------------


def test_order_matters_true_for_direct_sort_then_out(evaluate_mod):
    m = evaluate_mod
    prog = "LOAD a, orders.o_id\nSORT s, a ASC NF\nGATHER b, a, s\nOUT b\n"
    assert m.order_matters(m.parse_program(prog)) is True


def test_order_matters_false_with_no_sort(evaluate_mod):
    m = evaluate_mod
    prog = "LOAD a, orders.o_id\nOUT a\n"
    assert m.order_matters(m.parse_program(prog)) is False


def test_order_matters_false_when_only_some_outputs_are_sorted(evaluate_mod):
    m = evaluate_mod
    prog = (
        "LOAD a, orders.o_id\nLOAD b, orders.o_amt\n"
        "SORT s, a ASC NF\nGATHER sa, a, s\nOUT sa, b\n"
    )
    assert m.order_matters(m.parse_program(prog)) is False


# ---------------------------------------------------------------------
# harvest_literals: pulls literal constants out of program source so the
# fuzzer can bias generated data toward them.
# ---------------------------------------------------------------------


def test_harvest_literals_ignores_comments(evaluate_mod):
    m = evaluate_mod
    prog = "; the real threshold is 999 not this comment's 750.00\nGT m, a, 750.00\nOUT m\n"
    pool = m.harvest_literals(prog)
    assert pool["NUM"] == [m.Decimal("750.00")]
    assert 999 not in pool["INT"]  # that number only appeared in a comment


def test_harvest_literals_separates_int_num_date(evaluate_mod):
    m = evaluate_mod
    prog = "EQ a, x, 2024\nGT b, y, 100.50\nGE c, d, #2024-01-01#\nOUT a\n"
    pool = m.harvest_literals(prog)
    assert 2024 in pool["INT"]
    assert m.Decimal("100.50") in pool["NUM"]
    assert pool["DATE"] == [m.parse_iso_date("2024-01-01")]
    assert 2024 in pool["YEAR"]  # plausible-year ints are offered to DATE generation too


# ---------------------------------------------------------------------
# generate_tableset: every output must itself satisfy the constraints it
# declares, for any seed -- otherwise bin/minivm would reject the fuzzer's
# own data, silently defeating the whole fuzzing layer.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(10))
def test_generated_data_respects_schema_constraints(evaluate_mod, schema, seed):
    m = evaluate_mod
    rng = random.Random(seed)
    ts = m.generate_tableset(schema, rng, max_rows=15)

    lines_cols = [c["name"] for c in ts["lines"]["columns"]]
    li, lni = lines_cols.index("l_order"), lines_cols.index("l_no")
    lqi = lines_cols.index("l_qty")
    seen_keys = set()
    for row in ts["lines"]["rows"]:
        key = (row[li], row[lni])
        assert key not in seen_keys, "lines.key (l_order, l_no) must be unique"
        seen_keys.add(key)
        assert row[lqi] is not None, "l_qty is NOT NULL"
        assert 1 <= row[lqi] <= 500, "l_qty CHECK [1, 500]"

    order_ids = {row[0] for row in ts["orders"]["rows"]}
    for row in ts["lines"]["rows"]:
        assert row[li] in order_ids, "lines.l_order must reference an existing orders.o_id"

    cat_cols = [c["name"] for c in ts["categories"]["columns"]]
    kii = cat_cols.index("k_id")
    cat_ids = {row[kii] for row in ts["categories"]["rows"]}
    kpi = cat_cols.index("k_parent")
    for row in ts["categories"]["rows"]:
        assert row[kpi] is None or row[kpi] in cat_ids, "k_parent must be NULL or a real k_id"


@pytest.mark.parametrize("seed", range(5))
def test_generated_lines_rows_are_stored_sorted(evaluate_mod, schema, seed):
    m = evaluate_mod
    rng = random.Random(seed)
    ts = m.generate_tableset(schema, rng, max_rows=20)
    cols = [c["name"] for c in ts["lines"]["columns"]]
    oi, ni = cols.index("l_order"), cols.index("l_no")
    keys = [(row[oi], row[ni]) for row in ts["lines"]["rows"]]
    assert keys == sorted(keys), "the `sorted` constraint requires ascending (l_order, l_no)"
