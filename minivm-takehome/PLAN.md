# MiniVM optimizer/evaluator: design, limitations, and a plan for more time

## What's here

- `optimize` — Python 3 (stdlib only). Parses a program, then in one forward
  pass does CSE/value-numbering, tracks a union-find of registers guaranteed
  (by the instruction set's own length rules) to hold the same number of
  rows, and propagates numeric bounds/`not_null` facts through `LOAD` /
  `GATHER` / `SLICE` using only *schema constraints* (`check`, `not_null`,
  `key` — never the data-specific `stats`, so conclusions hold for any table
  set conforming to the schema). A comparison provably `TRUE` at every row
  (e.g. q0001's `GE l_qty, 1`, already implied by `l_qty`'s own `check`+
  `not_null`) makes every `GATHER` keyed by its `IDX` a no-op alias for its
  source column. A backward-liveness DCE pass cleans up whatever that
  orphaned, and a final pass swaps `HJOIN`/`NLJOIN` using `stats.rows` as a
  cost estimate — safe regardless of estimate quality, since the two are
  documented to always agree on results. Falls back to the unmodified input
  on any internal error.
- `evaluate` — standalone (doesn't import `optimize.py`, since it has to
  test *any* optimizer). Regresses against the 8 bundled examples exactly,
  then fuzzes: generates random table sets that satisfy every declared
  constraint, biases values toward the literal constants a program's own
  comparisons use, and deliberately chains one row per table end-to-end
  through the whole FK graph per dataset (independent per-row random FK
  picks make a multi-table join chain an improbable coincidence to hit
  otherwise). Compares with 1e-6 relative tolerance on numbers, exactly
  elsewhere, order-sensitive only when every `OUT` operand is traceably
  carried by a `SORT`/`TOPK`.
- Verified: all 8 examples produce identical rows to the originals on
  `examples/data.json`, cycles down 2.5%–26.4% (`scripts/check_examples.py`).
  `evaluate` passes against the real `optimize` and catches three
  deliberately-broken test optimizers (dropped output column, an
  unmodified-passthrough correctly accepted, and a corrupted filter
  threshold that only shows up on specific data).

## Known limitations (deliberate scope cuts, not oversights)

**`optimize` doesn't reorder joins or push filters through them.** All 8
examples load-join-join-join-then-filter; the biggest remaining cost in
every one of them is joining at full table width before a late filter
narrows things. Pushing a filter below a join it doesn't depend on, or
reordering joins by estimated selectivity, is real relational-algebra work
(need a logical plan, not just instruction-level pattern matching) and is
the single highest-value item deferred below.

**Only the "provably TRUE" side of constraint folding is implemented.**
An always-FALSE filter (data permitting) produces no measurable saving
anyway — `IDX`/`GATHER` on an empty index already cost ~0 at runtime — so
implementing it was pure risk for no benefit.

**No rank-via-self-join rewrite (q0002/q0003).** Those compute "count of
rows ranked higher within group" via a self-`HJOIN` + `GROUP` + `GSUM`.
MiniVM has no window-function or running-sum primitive, so there's no
cheaper *exact* replacement with the current instruction set. There is a
real, generalizable improvement left on the table, though: scope the
self-join to each group separately (`GROUP` first, self-join only within
each group) turns the cost from O(n²) over all rows into O(Σ group_size²) —
much cheaper whenever groups are small relative to the table. Scoped out of
this pass because it requires a multi-instruction restructuring (rewriting
one `HJOIN` into `GROUP` + a per-group join loop), not a local rewrite.

**`evaluate`'s order-sensitivity check is a conservative
under-approximation.** It marks a register order-carrying only if it's a
direct `SORT`/`TOPK` output or a `GATHER` indexed by one. It does not
recognize that `HJOIN`/`NLJOIN` preserve the left side's existing order
when iterating `(i, j)` pairs in position order, nor that `IDX`/`SLICE`
preserve the relative order of whatever they're drawn from. In q0001 and
q0007, the real output order *is* pinned down by an early `SORT` threading
through a join and an `IDX`, but the checker reports `order_matters=False`
for them and falls back to multiset comparison. This is a one-directional
risk: it can only make `evaluate` *too lenient* (accept a row permutation
that a strict reading of the input program disallows), never too strict
(never falsely fail a correct optimizer for reordering when the input
didn't actually require an order) — given the choice, that seemed like the
safer thing to ship under time pressure, but it's a real gap. A full fix
needs a three-state model per register (`SORT-DETERMINED` /
`IDENTITY-ASCENDING` / `UNKNOWN`) with `GATHER`'s propagation rule
depending on which applies to its index operand specifically.

**Row-multiset comparison uses greedy matching, not true bipartite
matching.** Exact on every field but the tolerance-based NUM comparison, so
a false negative would need two near-equal-but-distinct NUM values to be
pathologically ordered against the greedy search — believed safe in
practice for the table sizes `evaluate` generates, but not proven.

**Fuzzing is data-only, against a hand-picked handful of programs** (the 8
bundled ones, plus 4 small synthetic ones covering `DIV`/`IDIV`/`MOD`,
`TOPK`, `NLJOIN`+`NULLEQ`, and date arithmetic). It does not generate random
*programs*, so opcode combinations outside those 12 shapes are untested.

## With a few days and unlimited compute/tokens

**1. Build a real logical plan, not just instruction-level analysis.**
Lift the instruction stream into a small relational-algebra tree (scan /
filter / project / join / group-by / sort / limit) by recognizing the
`LOAD`→filter→`IDX`→`GATHER` and join→`GATHER`-projection idioms the
examples already follow. Rewrites (predicate pushdown, join reordering,
projection pushdown) become tree transformations with textbook correctness
arguments, instead of ad hoc instruction-pattern matching — and they
compose, so a filter can be pushed through several joins at once rather
than needing one bespoke check per join.

**2. Cost-based join reordering and filter pushdown**, using the schema's
`histogram`/`common`/`distinct` stats for real selectivity estimates (not
just `rows` for the HJOIN/NLJOIN choice as now): estimate each filter's
selectivity from its column's histogram, push filters below any join whose
other side they don't depend on, and order the remaining joins
smallest-estimated-intermediate-result-first — the standard dynamic-
programming join-order search, tractable at this instruction-count scale.
This is where the bulk of the remaining cycles in every one of the 8
examples lives.

**3. Scoped self-joins.** Generalize the pattern in limitation #3 above:
detect a self-`HJOIN`-on-key immediately followed by a `GROUP` on the same
key, and rewrite the pair into a direct per-group computation wherever the
self-join's only use is feeding that `GROUP` (already implemented for the
`GMAX`/`GMIN`/`GSUM`-only case in q0006's shape); extend it to the
rank-within-group case by scoping the self-join to run per-group instead of
globally.

**4. Program fuzzing, not just data fuzzing, for `evaluate`.** Generate
random *well-typed* MiniVM programs over a schema (random chains of loads,
filters, joins, group-bys, sorts, respecting each opcode's type rules), not
just random data for a fixed dozen programs. This is the highest-leverage
change to `evaluate` specifically: it would exercise opcode combinations
(`WRAP`, `NULLIF`, multi-key `GROUP`, chained `TOPK`, mixed INT/NUM
arithmetic near the NUM-range boundary) that no hand-written test touches,
and it's exactly the kind of test a grader's held-out programs are likely
to resemble more than my 12 examples do.

**5. A constraint-directed (SMT) data generator.** Uniform random data with
literal-biasing and FK anchor-chaining (what's shipped here) still only
*probabilistically* reaches deep multi-join/filter combinations — that's
why the corrupted-threshold bug in q0001 needed ~20 of 50 generated
datasets to show up empirically during development, not 1. A solver (Z3 or
similar) could take a program's branch conditions and schema constraints
and synthesize a dataset *provably* satisfying a target combination (e.g.
"an order with year=2024, amount in the corrupted/original disagreement
band, with a matching line and product and category") directly, rather
than sampling and hoping.

**6. Full order-provenance tracking in `evaluate`** per limitation #4
above, and a proper bipartite matcher (Hopcroft–Karp) for the unordered-row
comparison, removing both known theoretical gaps.

**7. An independent reference interpreter.** `evaluate` currently uses
`bin/minivm` as the oracle for *both* the original and optimized runs,
which is sufficient for catching optimizer bugs but can't catch a
`bin/minivm` bug shared by both runs. A from-scratch Python (or Rust)
interpreter against the same spec, cross-checked against `bin/minivm` on
the 8 examples, would make `evaluate` useful for validating the VM itself
too, and would make property-based testing (e.g. via Hypothesis) of the
*interpreter* possible as a separate concern from the optimizer.

**8. Performance of the tools themselves.** At 20,000 instructions and
10,000 rows per table (the documented limits), a pure-Python `optimize`
doing per-instruction dict operations should still be well within the 1s
budget, but I haven't measured it at that scale — I'd build a stress-test
generator at the documented limits and profile before trusting it there.
`evaluate`'s fuzzing is currently single-threaded and generates/tests
sequentially; trivially parallelizable across datasets (each is
independent), which would let a much larger fuzzing budget fit in the same
2-minute wall-clock window.

**9. Golden-file performance regression tracking.** Check the cycle count
for each bundled example into version control and fail CI if a change to
`optimize` regresses it — cheap insurance against an optimization pass
accidentally interacting badly with another one introduced later.

## AI model usage

Built interactively in a single Claude Code session using **Claude Sonnet
5** (`claude-sonnet-5`), the only model used. No subagents were spawned —
all design, implementation, debugging, and this writeup were done directly
in the main conversation. Token/spend figures for this session are
available via Claude Code's `/cost` command (not something this document
can report on its own behalf); the user should fill in the actual number
before sending this. As a point of reference for the shape of the work:
one long session, iterative — write a pass, run it against the real
`bin/minivm` binary, hand-trace a wrong result back to its cause, fix, and
re-verify. Three real bugs were found and fixed this way during
development (documented in the `evaluate` commit message): a missing
fact-propagation case, a fuzzer that couldn't reach deep join chains by
chance, and a Windows-only subprocess limitation unrelated to the actual
grading environment.
