[MiniVM docs](README.md) › Extra examples

# Extra examples (q0009–q0012)

The 8 bundled examples (`examples/programs/`) were analyzed first; these 4
(`examples/programs-extra/`) were added afterward, each for a specific
reason: either to exercise an opcode or pattern none of the 8 touch (so the
optimizer and its test suite have more than 8 data points to stand on), or
to make a specific optimization pass visibly fire on a case the bundled
examples don't happen to trigger. All run against the same
`examples/schema.json` / `examples/data.json` as the bundled set, and are
wired into `evaluate`'s own regression + fuzz layers alongside the original
8 (see `evaluate`'s `bundled_programs` list).

Ground truth for each (`bin/minivm` on the unmodified program) is saved
under `examples/expected-extra/`, exactly like the bundled
`examples/expected/`.

## q0009 — net line amount, average and remainder

**Intent:** average net amount per line (`price × qty × (1 − disc)`,
truncated to whole currency units) across lines of non-"open" orders,
restricted to products priced at least 0.01 — a data-quality guard that
predates `products.p_price` getting its own `NOT NULL` + `CHECK
[0.01, 9999.99]` constraint, now entirely redundant.

**What it adds opcode-wise:** `IDIV`, `MOD`, `TRUNC`, `SUB`/`MUL` together
— none of the 8 bundled examples use `IDIV` or `MOD` at all, and only one
(`q0007`) uses `TRUNC`, as dead code. It also has one genuinely dead
duplicate `LOAD` (`unused_disc`), and a second instance of the
always-true-filter pattern q0001 demonstrates, but keyed on a *different*
table and column (`products.p_price`, not `lines.l_qty`) — proving the
constraint-folding pass is a general mechanism, not special-cased to q0001.

```
$ bin/minivm examples/programs-extra/q0009.mini examples/data.json
{"ncols":3,"rows":[[2862,40,67]],"cycles":4532}

$ ./optimize examples/programs-extra/q0009.mini examples/schema.json > /tmp/q0009.opt.mini
$ bin/minivm /tmp/q0009.opt.mini examples/data.json
{"ncols":3,"rows":[[2862,40,67]],"cycles":4382}
```

Same rows, **3.3% fewer cycles** (4532 → 4382). The optimized program drops
`unused_disc`'s `LOAD` and the entire `price_ok`/`IDX`/two-`GATHER`
pre-filter chain — the `HJOIN` reads straight from `p_id` instead of the
filtered `p_id2`, because `GE(p_price, 0.01)` is proven true at every row
from `p_price`'s own schema constraints.

## q0010 — top 3 customers by total order amount

**Intent:** the 3 customers with the largest total order amount (ties by
customer id), name and total.

**What it adds:** `TOPK` used directly as its own opcode. The bundled
examples reach an equivalent "top N" effect in q0007 via `IOTA` + `SLICE`
instead, so `TOPK`'s own cost model (comparison against a bounded insertion
buffer, per `docs/cycles.md`) was otherwise untested. Also carries one dead
`LOAD` (`o_id_unused`) for DCE to remove.

```
$ bin/minivm examples/programs-extra/q0010.mini examples/data.json
{"ncols":2,"rows":[["Eve","2638.80"],["Ivy","1807.68"],["Bob","1614.73"]],"cycles":652}

$ bin/minivm /tmp/q0010.opt.mini examples/data.json
{"ncols":2,"rows":[["Eve","2638.80"],["Ivy","1807.68"],["Bob","1614.73"]],"cycles":618}
```

Same rows, **5.2% fewer cycles** (652 → 618) — exactly `o_id_unused`'s
`LOAD` (1 cycle per value × 30 order rows = 30, plus its own base cost 4 =
34 cycles) gone, nothing else, since there's no other redundancy in this
one.

## q0011 — region cross-check, small-table join algorithm choice

**Intent:** for each of the 4 regions, how many *other* regions exist (a
trivial cross-check some legacy report apparently wanted) — region code,
name, and count.

**What it adds:** a join over a genuinely tiny table (`regions`, 4 rows),
written with `HJOIN` the way the legacy author wrote every join in the
bundled examples, regardless of table size. `FILL` is used to build a
constant join key (so the join is a true cross join, not a self-match —
joining on `r_code` directly would only ever match each row to itself,
since region codes are unique; first draft of this example got that wrong
and shipped an empty result until it was caught during development).

```
$ bin/minivm examples/programs-extra/q0011.mini examples/data.json
{"ncols":3,"rows":[["east","East Coast",3],["north","Northern",3],["south","Southern",3],["west","West Coast",3]],"cycles":487}

$ bin/minivm /tmp/q0011.opt.mini examples/data.json
{"ncols":3,"rows":[["east","East Coast",3],["north","Northern",3],["south","Southern",3],["west","West Coast",3]],"cycles":455}
```

Same rows, **6.6% fewer cycles** (487 → 455). This is the one example in
the whole set (bundled or extra) small enough to trigger the
`HJOIN`↔`NLJOIN` algorithm-choice pass: with 4 rows on both sides of the
join, the estimated cost of `NLJOIN` (per-pair: `4 × 4 = 16`) beats
`HJOIN`'s per-row hashing overhead (`6 × (4 + 4) = 48`), so the optimized
program's line 7 reads `NLJOIN li, ri, k, k` where the original had
`HJOIN`. None of the 8 bundled examples' joins are on tables small enough
for this to ever fire — every join there is against `lines` (86 rows) or
bigger, where `HJOIN`'s linear-per-row cost beats `NLJOIN`'s quadratic one
by a wide margin, so the pass correctly leaves them as `HJOIN`.

## q0012 — payment lag and date arithmetic

**Intent:** for each order with at least one payment: days from the order
date to its latest payment, whether that's over 14 days, the order's
weekday, and a 14-day-from-order due date — by order id.

**What it adds:** `DATEDIFF` and `DATEADD`, used nowhere in the 8 bundled
examples at all (`q0007`'s one `DOW` call is itself dead code, its result
never read downstream).

```
$ bin/minivm examples/programs-extra/q0012.mini examples/data.json
{"ncols":5,"rows":[[1,18,true,1,"2023-01-16"],[2,14,false,1,"2024-03-25"], ...],"cycles":1594}

$ bin/minivm /tmp/q0012.opt.mini examples/data.json
{"ncols":5,"rows":[[1,18,true,1,"2023-01-16"],[2,14,false,1,"2024-03-25"], ...],"cycles":1594}
```

Same rows, **0% fewer cycles** — deliberately. This one exists purely for
opcode/correctness coverage (no dead code, no foldable filter, no
undersized join was written into it), so an unchanged cycle count here is
the expected, correct result, not a missed optimization. It's the control
case alongside q0009–q0011's positive ones: proof the optimizer doesn't
*remove* anything when there's genuinely nothing safe to remove.

## Summary

| Program | New opcodes/patterns exercised | Cycles before → after | Reduction |
|---|---|---|---|
| q0009 | `IDIV`, `MOD`, dead dup `LOAD`, constraint-fold on a 2nd table | 4532 → 4382 | 3.3% |
| q0010 | `TOPK` directly, dead `LOAD` | 652 → 618 | 5.2% |
| q0011 | `FILL`-based cross join, `HJOIN`↔`NLJOIN` swap | 487 → 455 | 6.6% |
| q0012 | `DATEDIFF`, `DATEADD` | 1594 → 1594 | 0% (control) |

All 4 are asserted byte-identical-in-rows and no-more-cycles by
`tests/test_optimize_examples.py`, and are included in `evaluate`'s own
fuzzing corpus (12 programs total, up from the bundled 8).
