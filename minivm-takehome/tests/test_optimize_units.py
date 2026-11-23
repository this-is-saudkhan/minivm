"""White-box unit tests of optimize.py's internal pieces, independent of
the end-to-end CLI behavior covered by test_optimize_examples.py."""
from decimal import Decimal


def test_split_operands_respects_quoted_strings(optimize_mod):
    m = optimize_mod
    assert m.split_operands("a, b, 'x, y''z', c") == ["a", "b", "'x, y''z'", "c"]


def test_strip_comment_ignores_semicolon_inside_string(optimize_mod):
    m = optimize_mod
    assert m.strip_comment("CAT d, a, '; not a comment'  ; real comment") == (
        "CAT d, a, '; not a comment'  "
    )


def test_is_register_rejects_keywords(optimize_mod):
    m = optimize_mod
    assert m.is_register("r1")
    assert m.is_register("_tmp")
    for kw in ("NULL", "TRUE", "FALSE", "ASC", "DESC", "NF", "NL", "NULLEQ", "null"):
        assert not m.is_register(kw)
    assert not m.is_register("orders.o_id")  # table.column is not a register


def test_chunk_classifies_sort_key_vs_register_vs_literal(optimize_mod):
    m = optimize_mod
    reg = m.Chunk("amt")
    assert reg.kind == "reg" and reg.reg == "amt"
    key = m.Chunk("credit DESC NL")
    assert key.kind == "sortkey" and key.reg == "credit" and key.suffix == " DESC NL"
    lit = m.Chunk("100.00")
    assert lit.kind == "other"
    tcol = m.Chunk("orders.o_amt")
    assert tcol.kind == "other"


def test_chunk_render_only_rewrites_the_register_part(optimize_mod):
    m = optimize_mod
    key = m.Chunk("credit DESC NL")
    assert key.render(lambda r: "canon_" + r) == "canon_credit DESC NL"
    lit = m.Chunk("100.00")
    assert lit.render(lambda r: "SHOULD NOT BE CALLED") == "100.00"


def test_parse_requires_single_trailing_out(optimize_mod):
    m = optimize_mod
    import pytest

    with pytest.raises(m.ParseError):
        m.parse("LOAD a, orders.o_id\n")  # no OUT at all
    with pytest.raises(m.ParseError):
        m.parse("LOAD a, orders.o_id\nOUT a\nOUT a\n")  # OUT not last/only
    instrs = m.parse("LOAD a, orders.o_id\nOUT a\n")
    assert [i.op for i in instrs] == ["LOAD", "OUT"]


def test_parse_ignores_comments_and_blank_lines(optimize_mod):
    m = optimize_mod
    instrs = m.parse("; a comment\n\nLOAD a, orders.o_id ; trailing\nOUT a\n")
    assert len(instrs) == 2


def test_union_find_basic(optimize_mod):
    m = optimize_mod
    uf = m.UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    assert uf.find("a") == uf.find("c")
    assert uf.find("a") != uf.find("d")
    assert uf.find(None) is None


def test_literal_numeric(optimize_mod):
    m = optimize_mod
    assert m.literal_numeric("42") == Decimal("42")
    assert m.literal_numeric("-7") == Decimal("-7")
    assert m.literal_numeric("12.50") == Decimal("12.50")
    assert m.literal_numeric("'abc'") is None
    assert m.literal_numeric("NULL") is None


def test_always_true_requires_not_null_on_both_sides(optimize_mod):
    m = optimize_mod
    bounded_not_null = m.Facts(Decimal(1), Decimal(500), True)
    bounded_nullable = m.Facts(Decimal(1), Decimal(500), False)
    literal_one = m.Facts(Decimal(1), Decimal(1), True)

    # l_qty (NOT NULL, CHECK [1,500]) >= 1 is always true.
    assert m.always_true("GE", bounded_not_null, literal_one)
    # Same bounds, but nullable -- a real NULL would make GE NULL, not TRUE.
    assert not m.always_true("GE", bounded_nullable, literal_one)
    # Unrelated threshold: not provable either way from these bounds.
    assert not m.always_true("GE", bounded_not_null, m.Facts(Decimal(10), Decimal(10), True))


def test_always_true_handles_all_five_comparison_ops(optimize_mod):
    m = optimize_mod
    small = m.Facts(Decimal(1), Decimal(5), True)
    big = m.Facts(Decimal(100), Decimal(200), True)
    assert m.always_true("LT", small, big)
    assert m.always_true("LE", small, big)
    assert m.always_true("GT", big, small)
    assert m.always_true("GE", big, small)
    assert not m.always_true("EQ", small, big)
    singleton = m.Facts(Decimal(7), Decimal(7), True)
    assert m.always_true("EQ", singleton, singleton)


def test_build_column_facts_combines_check_not_null_and_key(optimize_mod, schema):
    m = optimize_mod
    facts = m.build_column_facts(schema)
    l_qty = facts[("lines", "l_qty")]
    assert l_qty.not_null and l_qty.lo == Decimal("1") and l_qty.hi == Decimal("500")
    # o_id is in `key`, which implies NOT NULL even without its own entry.
    o_id = facts[("orders", "o_id")]
    assert o_id.not_null
    # o_amt has neither a CHECK nor a NOT NULL/key declaration.
    o_amt = facts[("orders", "o_amt")]
    assert not o_amt.not_null and o_amt.lo is None and o_amt.hi is None
