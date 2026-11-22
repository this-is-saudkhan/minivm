[MiniVM docs](README.md) › Approach

# Approach: design, parameters, testing, and tool choices

This is the engineering reference for how `optimize` and `evaluate` work
and why they're built the way they are — the companion to `PLAN.md` (which
is the forward-looking "what I'd do with more time" write-up required by
the assignment) and `docs/EXAMPLES_EXTRA.md` (the 4 added example programs,
with run output and analysis).

## 1. Problem framing

MiniVM is a column-at-a-time execution engine for already-planned query
work — the thing a query optimizer like Snowflake's produces, not the SQL
itself. That reframes the task: `optimize` isn't rewriting a declarative
query where many physical plans could answer it; it's rewriting a *fixed*
physical plan into a cheaper, *provably equivalent* one. The two concerns
that follow directly from that:

- **Correctness is schema-relative, not data-relative.** The requirement
  is "same results on any data with the tables and constraints in
  schema.json" — not just the one dataset in `examples/data.json`. Every
  rewrite in `optimize` has to be justified from the schema's declared
  `key`/`not_null`/`refs`/`check`/`sorted` constraints (which hold for any
  conforming data), never from `stats` (which describe only the one
  dataset present when `--schema` was run). `stats` is only ever used
  where using it can't affect correctness — the `HJOIN`/`NLJOIN` choice,
  since the two are documented to always produce the same result.
- **A checker needs to be at least as rigorous as the thing it checks.**
  `evaluate` isn't a convenience test runner for my own `optimize`; the
  interface explicitly takes any `OPTIMIZER`. That shaped two decisions:
  it's a completely standalone script (doesn't import `optimize.py` or
  share its parser), and a meaningful fraction of its own development time
  went into testing *it* — see §4 and §5.

## 2. `optimize`: design and parameters

Pipeline: parse → one forward dataflow sweep (CSE + constraint-based filter
elision, interleaved) → backward-liveness DCE → a local `HJOIN`/`NLJOIN`
choice → re-serialize. See the file's own module docstring for the same
summary; this section is the "why," including every tunable constant.

### 2.1 Parsing and operand classification

A line splits into an opcode and comma-separated operand chunks (a small
hand-written scanner handles `'...'`-quoted strings so a comma or `;`
inside a STR literal doesn't get mis-split or treated as a comment start).
The key design choice: **whether a chunk is a register reference is
determined purely by its surface syntax**, not by which opcode or operand
position it's in — an identifier that isn't a keyword (`NULL`, `TRUE`,
`FALSE`, `ASC`, `DESC`, `NF`, `NL`, `NULLEQ`) and isn't a literal/`table.
column` *is* a register, full stop, because the grammar never lets the
same token mean two different things depending on position. That collapses
what would otherwise be a large per-opcode operand-arity table into: how
many of a line's leading chunks are **destinations** (`0` for `OUT`, `2`
for `NLJOIN`/`HJOIN`, `1` otherwise), plus one extra regex for the one
genuinely different shape in the grammar — a sort-key chunk
(`reg ASC|DESC NF|NL`), detected by `^([A-Za-z_]\w*)(\s+(?:ASC|DESC)\s+
(?:NF|NL))$` so its embedded register can be rewritten (for CSE aliasing)
while its direction/null-placement suffix is carried through verbatim.

**Decision point:** re-serialize kept lines by substituting *only* register
tokens into otherwise-untouched chunk text, rather than re-deriving a
canonical textual form for every operand. This means a NUM literal like
`750.00` is never reparsed-and-reprinted — it's copied byte-for-byte from
the input — so there's no risk of an optimization pass silently
reformatting a literal and losing exact precision (the spec requires
numbers to agree within 1e-6 relative, but there's no reason to introduce
even that much drift when zero drift is free).

### 2.2 CSE / value-numbering

A single forward pass keyed on `(op, tuple of resolved operand text)`:
identical computations are recognized and the later one is dropped, with
its destination register(s) aliased to the earlier one's. "Resolved" means
every register operand is first rewritten through the current alias map,
so two LOADs of the same column that happen to flow through differently-
named-but-equal-valued registers downstream still collapse. This applies
uniformly to every opcode — joins, sorts, groups, comparisons — since every
MiniVM instruction is a pure function of its inputs (no randomness, no
hidden state); the only thing opcode-specific is how many destinations an
instruction has (1, 2 for joins, 0 for `OUT`).

**Decision point:** CSE keys ignore the literal register *names* involved,
but not their declared *order* for multi-key instructions (`GROUP`,
`SORT`). `GROUP g, a, b` and `GROUP g, b, a` are provably identical in
result (row partitioning and group-numbering by first appearance don't
depend on which order the key columns are listed), but recognizing that
needs an extra canonicalization step for no measured benefit on the
available examples, so it's left as a known, documented missed dedup
rather than implemented speculatively.

### 2.3 Union-find "length class"

Many of the later passes need to know, for two registers, whether MiniVM's
own rules *guarantee* they hold the same number of rows — not whether they
happen to on some dataset, but whether it's true by construction. A small
union-find tracks this: two `LOAD`s from the **same table** start in the
same class (a table's row count is schema-wide, not data-specific, albeit
each run's actual number). Every element-wise instruction unions its
register operands with its result (the VM itself requires them to already
match). `GATHER d, s, i` sets `d`'s class to `i`'s class — not `s`'s —
since `GATHER`'s output length is literally "however many positions `i`
has," independent of `s`. `IOTA`, `FILL`, and `SORT` similarly propagate
from their defining operand. Everything else (`IDX`, `SLICE`, `TOPK`,
joins, `GROUP` and its aggregates) gets a fresh, unrelated class — these
genuinely can change row count in a way nothing upstream pins down
statically, so no claim is made.

### 2.4 Constraint-based fact propagation and the always-true filter fold

For every `LOAD` of a column, start with that column's schema-declared
facts: a numeric lower/upper bound from `check` (only for `INT`/`NUM`
columns — `DATE` `check` bounds aren't modeled, since no bundled schema
uses one) and whether it's `NOT NULL` (from an explicit `not_null` entry
*or* membership in `key`, since key columns are never NULL per the schema
spec). These facts propagate unchanged through `GATHER` and `SLICE` — both
only ever copy existing values from their source, so every value a
subsetted/reordered column can hold is a value the source could hold, and
if the source is never NULL, a subset of it never is either.

A comparison (`GT`/`GE`/`LT`/`LE`/`EQ`) between two operands with known
facts is folded to "provably TRUE at every row" when their bounds force it
**and both sides are proven NOT NULL** — the NOT NULL requirement matters
because a real NULL makes the comparison NULL, not TRUE, so without it the
"always true" conclusion would be wrong. (The dual, "always FALSE," isn't
implemented: a filter that's always false already costs ~0 at runtime once
`IDX` finds nothing to keep, so there's no measurable win to chase there —
see `PLAN.md` for the reasoning in full.)

When `IDX d, c` sees `c` proven always-TRUE, every later `GATHER x, s, d`
is recognized as an identity (aliased to `s`, not re-emitted) **provided
`s` and `d` are in the same length class** — i.e. `s` is a genuine sibling
of the filtered condition, not some unrelated column that happens to share
a name pattern. This is the mechanism behind q0001's and q0009's biggest
wins: a "sanity check" filter that's actually implied by the schema's own
`check`+`not_null` constraints turns an entire downstream fan-out of
`GATHER`s (through however many join stages) into free aliases, which the
DCE pass below then lets cascade all the way back to the now-unused
original comparison and its `LOAD` chain.

### 2.5 Backward-liveness DCE

Standard: seed the live set from `OUT`'s operands, walk instructions in
reverse, keep an instruction if any of its destinations are live (treating
`OUT` itself as always-live, since it's the one instruction with zero
destinations), and fold its own operands into the live set when kept. This
runs *after* the CSE/fold pass specifically so it also cleans up whatever
that pass's aliasing orphaned — the always-true comparison and its `LOAD`
chain in the q0001/q0009 case, a duplicate `LOAD` that only the dropped
duplicate instruction referenced, etc. — without either pass needing to
know about the other's internals.

### 2.6 `HJOIN`/`NLJOIN` algorithm choice

A final, independent pass: trace each join's key operands back to an
originating table (through `LOAD`/`GATHER`/`SLICE`/`FILL`/`IOTA` and the
element-wise opcodes, the same propagation shape as §2.4's facts, just for
"which table did this come from" instead of "what are its bounds"), look
up `schema[...].stats.rows` for each side, and pick whichever instruction
the documented cost formulas say is cheaper:
`NLJOIN ≈ rows_L × rows_R` vs. `HJOIN ≈ 6 × (rows_L + rows_R)` (both
ignoring the identical `+2 × matches` term and string-key-byte costs,
which are the same order on both sides and don't change which one wins
except at the margin). **Decision point:** this is the one place `stats`
(data-specific) rather than `check`/`not_null`/`key` (schema-wide) feeds a
rewrite — safe specifically because `HJOIN` and `NLJOIN` are documented to
"give the same result... only the cost differs," so an estimate that turns
out wrong on a different dataset costs cycles, never correctness. q0011
(4-row `regions` table) is the one example in the whole set where this
actually fires (estimated `16` vs `48`); every bundled example's joins are
against tables large enough that `HJOIN`'s linear cost wins by a wide
enough margin that no estimate error could flip the decision.

## 3. `evaluate`: design and parameters

`evaluate OPTIMIZER` has one job: find a defect, or run out of its ~2-
minute budget without finding one. Two layers, run in that order:

### 3.1 Regression layer

Every bundled program (12, after the 4 additions) is run through
`OPTIMIZER` once against `examples/schema.json`, and the result against
`examples/data.json` compared to the original's. Cheap, deterministic, and
catches anything a fixed, known-good dataset would reveal — including the
`ncols`-dropping defect in the `tests/fixtures/broken_optimizers/
drop_last_out_column.py` test fixture.

### 3.2 Fuzzing layer

The regression layer can only ever exercise the one dataset that ships
with the bundle. `evaluate` additionally generates fresh random table sets
per program and replays the comparison against each.

**The random generator (`generate_tableset`) is constraint-respecting by
construction**, not by rejection sampling: it processes tables in
dependency order (a topological sort of `refs` edges — a table referencing
another must be generated after it, so its foreign keys have something
real to point at), enforces `key` uniqueness during generation, draws
`refs` values only from the referenced table's already-generated key pool,
clips `check`-bounded values into range, and sorts a table's rows at the
end if it declares `sorted`. This matters because a dataset `bin/minivm`
itself would reject on load defeats the entire trial silently (both the
original and optimized runs error out identically, correctly treated as
"anything goes" by the `if the input program errors, anything goes` rule —
but that means a badly-behaved generator just wastes time, not that it
produces false results). `tests/test_evaluate_units.py` checks this
property directly, parametrized over 10+ seeds, rather than trusting it.

**Decision point — literal biasing.** Early development used purely
uniform random values within each column's declared/default range. That
missed a deliberately-introduced test bug (a corrupted filter threshold,
`750.00` → `740.00` in q0001) for a very mundane reason: `orders.o_amt` has
no schema `check` bound, so values were drawn uniformly from a wide
default range (±1000), and the 10-unit band where the corrupted and
correct thresholds disagree was landed in by chance roughly 1 in 300
samples. The fix: `harvest_literals` pulls every `INT`/`NUM`/`DATE` literal
out of the program under test (comment-stripped, so a number mentioned only
in a human-readable comment doesn't pollute the pool), and generation
biases toward them — the literal itself and its immediate neighbors
(`±0.01` for `NUM`, `±1` for `INT`, `±1 day` for `DATE`) — with probability
`0.35` per value, chosen so pool-biased values show up often without
crowding out the plainly-random coverage that still matters for
less-targeted bugs. A further wrinkle: `YEAR(col) = 2024`-style filters
compare a bare `INT` literal (not a `DATE` literal) against a date's year
component, so plausible-year integers (`1 ≤ v ≤ 9999`) are *also* offered
to `DATE` generation as candidate years, or the year-filter branch of a
program would almost never be reached by chance either (`2024` is 1 of
~40 years in the default range).

**Decision point — FK anchor chaining.** Even with literal biasing,
independent per-row random foreign-key selection makes a *multi-hop* join
chain (order → line → product → category, say) an improbable coincidence:
each hop needs its own random FK pick to land on the specific
already-interesting row from the previous hop. Rather than scale up the
number of generated datasets to compensate (expensive, and still
probabilistic), each table additionally gets exactly one **anchor row**
per dataset: generated with every non-FK value forced toward the literal
pool (not just probabilistically), and every FK column explicitly set to
the *previous* table's anchor key rather than a random pool pick. This
threads one fully-connected, literal-biased chain through the entire
reference graph per dataset — turning "will four independent dice rolls
agree" into "does one deliberately-constructed path exist," which is what
actually made the q0001-threshold test fixture detectable in practice (see
`docs/EXAMPLES_EXTRA.md`-adjacent history: it needed this fix, not just
more datasets, to show up reliably).

**Time/row-count parameters**, and why: `TIME_BUDGET_SECONDS = 95` (not
110+) leaves real headroom under the hard 2-minute limit for a slower
grading machine, since the loop's own deadline checks are the main safety
net but individual `bin/minivm`/optimizer subprocess calls still carry
their own timeouts (`MINIVM_TIMEOUT = 5s`, `OPTIMIZER_TIMEOUT = 15s`) that
could each eat into the margin once per call. Each program gets `50`
freshly-generated datasets of up to `30` rows per table (`20`/`20` for the
4 small synthetic programs) — small enough that generation and two
`bin/minivm` invocations per dataset comfortably fit the time budget across
12 bundled programs, large enough that the anchor-chaining mechanism above
reliably reaches multi-join and multi-filter combinations within that
budget. A measured run against the real `optimize`: **~20–30 seconds**
typical, well inside the 95s budget.

### 3.3 Order sensitivity and value comparison

"Order doesn't matter unless the input program specifies it" is
implemented as: a register is **order-carrying** if it's a direct
`SORT`/`TOPK` output, or a `GATHER` indexed by an order-carrying register;
the comparison is positional only if *every* `OUT` operand is
order-carrying, and falls back to a tolerant multiset comparison otherwise.
This is a documented, deliberate **under-approximation** — it doesn't
recognize that `HJOIN`/`NLJOIN` preserve a sorted left side's order when
iterating `(i, j)` pairs in position order, or that `IDX`/`SLICE` preserve
the relative order of whatever they're drawn from, so q0001 and q0007 (both
of which *do* have a real final order established by an early `SORT`
threading through a join and a filter) get the more lenient multiset
comparison rather than the stricter positional one. **The direction of the
error matters**: it can only make `evaluate` too lenient (accept a
correct-but-reordered output), never too strict (falsely reject a correct
optimizer for preserving order it wasn't actually required to), which was
judged the safer failure mode to ship given the time available — see
`PLAN.md` §6 for what a complete fix needs (a three-state per-register
model, not a one-bit flag).

Value comparison treats anything that parses as a number (`INT`, `NUM`, or
a big-integer-as-string per the output spec) with `1e-6` relative
tolerance, and everything else (`STR`, `DATE`, `BOOL`, `NULL`) exactly —
`BOOL` is checked for `is`-identity specifically so Python's
`bool`-is-an-`int` subclassing can't make `true` and `1` compare equal by
accident. Unordered-row comparison is a greedy bipartite match (pair each
original row with an unclaimed, value-equal optimized row) rather than a
provably-optimal one (Hopcroft–Karp) — adequate at the row counts this
fuzzer generates, and documented as a known (believed low-risk, unproven)
simplification in `PLAN.md`.

## 4. Testing strategy

Two layers, deliberately kept separate (see `tests/conftest.py`'s own
docstring for the same split):

- **Integration tests** (`tests/test_optimize_examples.py`,
  `tests/test_evaluate_integration.py`) invoke `optimize`/`evaluate` as
  actual subprocesses — exactly the CLI contract a grader exercises — and
  run results through the real `bin/minivm` binary. These are the ones
  that can't be fooled by an internal refactor that breaks the interface
  while every unit test still passes.
- **Unit tests** (`tests/test_optimize_units.py`,
  `tests/test_evaluate_units.py`) import `optimize`/`evaluate` directly as
  Python modules (`conftest.py`'s `import_script`, via
  `importlib.machinery.SourceFileLoader` — needed because neither file has
  a `.py` extension) to pin down specific logic in isolation: operand
  classification, the union-find, the `always_true` predicate rule and its
  `NOT NULL` requirement specifically (the exact thing that would silently
  break the q0001/q0009 optimization if it regressed), the value
  comparator's tolerance behavior, `order_matters`'s documented
  conservatism, and — parametrized over 10+ random seeds —
  that `generate_tableset`'s own output always satisfies the constraints it
  declares, since a fuzzer whose data `bin/minivm` itself would reject
  can't fuzz anything.

**Framework:** `pytest` (not unittest/nose — table-stakes choice for a
Python project; `pytest.ini` registers one custom marker, `slow`, for the
4 tests that each run `evaluate`'s full ~20–30s fuzzing pass).

**How to run** (from `minivm-takehome/`):

```sh
pip install pytest        # not in the stdlib; nothing else is needed
python3 -m pytest                    # everything (~85s on this machine)
python3 -m pytest -m "not slow"      # the 51 fast ones only (~2.5s)
python3 -m pytest -m slow            # just the 4 full-evaluate runs (~85s)
python3 -m pytest tests/test_optimize_units.py -v   # one file, verbose
```

55 tests total (51 fast, 4 slow), all passing as of this writeup.

**What existed before the pytest suite, during development:**
`scripts/check_examples.py` — a small standalone script (not pytest) that
runs every bundled example through both `optimize` and the real
`bin/minivm`, printing a cycles-before/after table; it's what was used
turn-by-turn while building each optimization pass, before the formal
pytest suite below consolidated those same checks into assertions. It's
kept in the repo (`scripts/`) since it's a convenient one-shot sanity
check (`python3 scripts/check_examples.py`) that doesn't need pytest
installed.

## 5. Extra example programs

See `docs/EXAMPLES_EXTRA.md` for the 4 added programs (q0009–q0012), each
with its reason for existing, its actual `bin/minivm` run output before and
after `optimize`, and an analysis of exactly which optimization (or,
for q0012, deliberately none) fired and why. Summary: `IDIV`/`MOD`/`TRUNC`
and a second instance of the constraint-fold pattern on a different
table/column (q0009), `TOPK` used directly (q0010), the one case in the
whole set small enough to trigger the `HJOIN`↔`NLJOIN` swap (q0011), and
`DATEDIFF`/`DATEADD` coverage with an intentional 0%-change control case
(q0012).

## 6. Tool and model choices

**Python 3, standard library only, no dependencies, 3.6-compatible.**
Decided from the start and held to throughout (verified by actually
developing against a 3.6.6 interpreter present on this machine, not just
writing code that looks 3.6-compatible): a take-home grader asked to "ask
us for a build if `bin/minivm` doesn't run" clearly expects to run
*something* themselves, and the fewer assumptions made about their Python
version or installed packages, the less likely that "something" fails for
a reason unrelated to the actual optimizer logic. Concretely this ruled
out f-string `=` debug specifiers, `subprocess.run(capture_output=...)`
(3.7+), `dict` union operators, the walrus operator, and
`datetime.date.fromisoformat` (3.7+) — each caught by actually hitting a
`TypeError`/`AttributeError` against the 3.6.6 interpreter during
development, not by manual auditing.

**Two standalone scripts, not a shared library.** `evaluate` does not
import anything from `optimize.py`, and re-implements its own (smaller)
program parser rather than reusing `optimize`'s. This was a deliberate
constraint, not an oversight: `evaluate`'s job is to test *any* optimizer
satisfying the CLI contract, and importing from `optimize.py` would make
it quietly dependent on that specific implementation's internals,
undermining the independence that makes it a meaningful check in the first
place.

**`bin/minivm`'s own dispatch logic is reimplemented directly in Python**
inside `evaluate` (`minivm_path()`), rather than shelling out to the
`#!/bin/sh` dispatcher script directly. This was forced by developing on
Windows: native Windows Python cannot exec a shebang file via
`subprocess` (no POSIX exec semantics), which surfaced as a `WinError 193`
during testing. Rather than special-case only the dispatcher, the fix also
covers the `OPTIMIZER` argument itself (`shebang_prefix`), gated entirely
behind `os.name == 'nt'` — a no-op, zero-risk change on whatever POSIX
system actually grades this, and a direct improvement in robustness
either way (one fewer indirection through a shell to go wrong).

**AI model: Claude Sonnet 5** (`claude-sonnet-5`), via Claude Code, the
only model used for this submission — no other models, no subagents
spawned. See `PLAN.md`'s "AI model usage" section for the token/spend
disclosure (filled in from Claude Code's `/cost` output) and a short note
on how the session actually went (iterative: write a pass, run it against
the real `bin/minivm` binary, hand-trace a wrong or merely-unconvincing
result back to its root cause, fix, re-verify — not written once and
assumed correct). Two explicit human decision points shaped scope before
any of the optimizer code was written:

1. **Language for `optimize`/`evaluate`**: Python 3, stdlib only — chosen
   over TypeScript/Node (extra build/runtime dependency for no compiled-
   performance benefit at this scale) or Go (a single static binary is
   nice, but adds a build step a grader running from source wouldn't
   necessarily expect) specifically because it has zero setup cost for
   whoever runs it next.
2. **Implementation ambition for the first pass**: offered three options —
   (a) "safe core only" (DCE, CSE, constraint-based filter elision,
   `HJOIN`/`NLJOIN` choice — all provably correctness-preserving, no join
   reordering), (b) safe core plus one additional pattern rewrite
   (collapsing a self-join-then-`GROUP` into a direct `GROUP`+aggregate
   when the self-join's output is otherwise unused), or (c) attempt join
   reordering under selectivity estimates too, accepting materially higher
   implementation/correctness risk within the session. **(a) was chosen**,
   explicitly trading a larger cycle-count win for lower risk of a subtle
   correctness bug in a tool whose entire job is "guaranteed-same-results,
   faster" — join reordering and the self-join rewrite are both documented
   as the top items in `PLAN.md`'s forward-looking plan instead of being
   rushed into this submission.
