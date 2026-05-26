# Resolver redesign — fixed-point iteration

Brief for replacing `resolve_scenario.py`'s topological-sort + mirror-post-pass design with a single fixed-point loop. Self-contained: read this in VS Code alongside the current file and the new code can be written from here.

---

## 1. Current state (as of v27 thesis / current `resolve_scenario.py`)

The resolver evaluates every `(scenario, variable, date)` and writes one row to `oil_network.scenario_resolved_values`. It works in three phases:

1. **Classify** each assignment into one of 5 kinds via `classify()`:
   - `observed` — TS-bound (`timeseries_id IS NOT NULL`)
   - `zero` — `formula = '0'` (pass-through node defaults)
   - `latent` — `formula = 'latent()'` (declared unknown)
   - `sum` — `formula = 'sum'` (sugar for `+1 ·` every `formula_input`)
   - `arithmetic` — signed combination of variable_ids (also covers single-term aliases)
   - anything else → `unknown` → `source = 'unresolved'`

2. **Topological sort** via `graphlib.TopologicalSorter` over a dependency dict built by `build_deps()`. Each formula-bound variable depends on the variables named in its `formula_inputs`. TS-bound and structural-zero variables have no deps.

3. **Single-pass evaluation** in topo order. For each variable, call the matching `eval_*` function. The evaluators are pure: given inputs already-resolved (via the `get(vid, d)` closure over `resolved`), they emit a `{date: Cell}` map.

4. **Mirror post-pass** via `promote_mirrors()`. After the main loop, sweep latent relational variables and check if their paired direction (`inflow A→B` ↔ `outflow B→A`) has a resolved value at each date. If yes, borrow it and re-label as `source = 'derived'`, `formula_used = 'mirror(pvid)'`. Updates `stats` to reclassify the promoted rows from `latent` to `reverse_mirror`.

5. **Persist + audit + refresh L4 views**. Writes to `scenario_resolved_values` with a `run_id` linking back to `scenario_resolver_runs`. Calls `refresh_analytic()` at the end.

### What's load-bearing

- The `Cell = (value, source, formula_used, timeseries_id)` tuple — downstream views key off `source` and `formula_used`.
- The five `source` values written to the table: `observed`, `zero`, `latent`, `derived`, `partial`, `unresolved`. The CHECK constraint on `scenario_resolved_values.source` will reject anything else.
- LOCF semantics in `eval_observed()` (last observation carried forward, with `formula_used = 'locf(YYYY-MM-DD)'` for carried rows).
- Mirror's audit-trail label: `mirror(paired_variable_id)` in `formula_used`.
- The dispatch stats dict reported in `scenario_resolver_runs.dispatch_stats` (JSONB). Keys currently: `observed`, `zero`, `latent`, `sum`, `arithmetic`, `reverse_mirror`, `unresolved`.

### Why it's more complex than it needs to be

- `build_deps()` exists only to feed `TopologicalSorter`. The deps are entirely recoverable from `formula_inputs` — building a separate dict is incidental complexity.
- The mirror post-pass duplicates the structure of the main loop (iterate variables, check inputs, emit cells) just for one case.
- Mirror requires a parallel `paired_variable_id()` construction that lives outside the formula vocabulary. The structural identity "inflow(A→B) equals outflow(B→A)" is not represented in `formula_inputs`; it's reconstructed on the fly.

---

## 2. Proposed change: fixed-point iteration

Replace the topo sort + mirror post-pass with a single loop that sweeps the unresolved set until nothing new gets resolved.

```
known := {}
unresolved := set(all variables)

# Pass 0: ground in observations and structural zeros
for v in unresolved:
    if classify(v) in (observed, zero):
        known[v] := evaluate(v)
        remove v from unresolved

# Fixed-point sweep
loop:
    progress := false
    for v in unresolved:
        if all inputs(v) are in known:
            known[v] := evaluate(v)
            remove v from unresolved
            progress := true
    if not progress: break

# Anything still unresolved is latent or genuinely under-determined
for v in unresolved:
    known[v] := (NULL, 'latent', ..., ...)
```

### Equivalence

For any DAG-resolvable assignment set, fixed-point converges to the same `known` as topo sort, in at most `depth(DAG)` passes. Starter graph depth ≈ 5–8, so ~5–8 sweeps × ~1830 variables = ~10–15k formula evaluations per resolver run. Topo sort is one sweep over the same variables. Performance is the same order of magnitude — clarity is the win.

### Why mirror falls out for free

Define mirror as a first-class formula kind. A relational variable whose value should track its paired direction gets:

```
formula = 'mirror'
formula_inputs = [paired_variable_id]
```

instead of `formula = 'latent()'` with no inputs. Now mirror is just another formula kind:

- `classify()` returns `KIND_MIRROR` when `formula = 'mirror'`.
- `eval_mirror()` looks up the single input via `get(...)` and emits `(value, 'derived', f'mirror({input_id})', None)`.
- In the fixed-point loop, mirror is evaluable when its input is in `known`. Pass N resolves `F_in`; pass N+1 sees `F_in` is known and evaluates `F_out = mirror(F_in)`.

No post-pass. No `paired_variable_id()` helper. No cycle gates. No re-labelling of stats.

### Latent stays as a terminal kind

`formula = 'latent()'` keeps its current meaning: "declared unknown, no resolution path, value=NULL by design." After the fixed-point loop converges, any remaining unresolved variable gets the `latent` source. A non-zero count of variables ending up `unresolved` because their formula didn't match any known kind is still a bug, not a normal state.

---

## 3. What to change in `resolve_scenario.py`

### Remove

- `build_deps()` — no longer needed.
- `paired_variable_id()` — no longer needed if mirror becomes a formula kind. (Keep it temporarily if migrating in two steps; see §5.)
- `promote_mirrors()` — no longer needed.
- The `from graphlib import TopologicalSorter` import and the `sorter = TopologicalSorter()` block in `resolve()`.

### Add

- `KIND_MIRROR = "mirror"` constant.
- `eval_mirror(a, dates, get)` evaluator. Reads the single entry in `formula_inputs`, looks it up, emits a `derived`/`partial` cell. Treat it identically to a single-term arithmetic for partial handling.
- A `classify()` branch returning `KIND_MIRROR` when `formula == 'mirror'` and exactly one input is named.
- A fixed-point loop replacing the topo-sort block in `resolve()`. See sketch below.

### Modify

- `classify()` — add the mirror branch. Order: `observed → zero → latent → mirror → sum → arithmetic → unknown`. Mirror must be checked before arithmetic, because a single-input mirror would otherwise classify as a single-term arithmetic. (Or: check the literal formula string `'mirror'` first.)
- Stats keys reported in `dispatch_stats`: keep the existing keys (`observed`, `zero`, `latent`, `sum`, `arithmetic`, `reverse_mirror`, `unresolved`). Map `KIND_MIRROR` → `reverse_mirror` in the stats dict so the historical audit-row schema doesn't change.
- Comments/docstring at top of file: drop the "Corollary D mirror promotion" paragraph; replace with one sentence on fixed-point convergence.

### Fixed-point loop sketch

```python
known: dict[str, dict[_date, Cell]] = {}
unresolved = set(by_id.keys())

# Pass 0: TS-bound and structural zeros (no deps)
for vid in list(unresolved):
    a = by_id[vid]
    kind = classify(a, by_id)
    if kind == KIND_OBSERVED:
        known[vid] = eval_observed(a, dates, ts_data)
        unresolved.discard(vid)
        stats['observed'] += 1
    elif kind == KIND_ZERO:
        known[vid] = eval_zero(a, dates)
        unresolved.discard(vid)
        stats['zero'] += 1

# Fixed-point sweep
def all_inputs_known(a):
    return all(inp in known for inp in (a['formula_inputs'] or []))

while True:
    progress = False
    for vid in list(unresolved):
        a = by_id[vid]
        kind = classify(a, by_id)
        if kind == KIND_LATENT:
            continue  # handled in final sweep below
        if kind == KIND_UNKNOWN:
            continue  # ditto
        if not all_inputs_known(a):
            continue
        if kind == KIND_SUM:
            known[vid] = eval_sum(a, dates, get)
            stats['sum'] += 1
        elif kind == KIND_ARITHMETIC:
            known[vid] = eval_arithmetic(a, dates, get)
            stats['arithmetic'] += 1
        elif kind == KIND_MIRROR:
            known[vid] = eval_mirror(a, dates, get)
            stats['reverse_mirror'] += 1
        unresolved.discard(vid)
        progress = True
    if not progress:
        break

# Final sweep: everything still unresolved is latent or genuinely unknown
for vid in unresolved:
    a = by_id[vid]
    kind = classify(a, by_id)
    if kind == KIND_LATENT:
        known[vid] = eval_latent(a, dates)
        stats['latent'] += 1
    else:
        known[vid] = eval_unknown(a, dates)
        stats['unresolved'] += 1

resolved = known  # rename for downstream persist/audit code
```

The `get(vid, d)` closure stays unchanged — it reads from `known` (renamed `resolved` for the persistence block).

### Partial-value handling

`eval_sum` and `eval_arithmetic` already handle missing inputs by emitting `source = 'partial'`. The fixed-point loop should only enter their evaluators when `all_inputs_known(a)` — i.e., every input has at least entered `known`, even if some cells in its date map are NULL. The current evaluators then handle the per-date NULL cases themselves. This keeps the existing `partial` semantics.

Subtle point: `all_inputs_known(a)` checks whether the input *variable* has been resolved (its date-map exists in `known`), not whether every date in that map has a non-NULL value. That's the right level — once the input has been visited, we know what we know.

---

## 4. What stays exactly the same

- `Cell` tuple shape and meaning.
- All five `eval_*` functions for `observed`, `zero`, `latent`, `sum`, `arithmetic`. They're pure; the loop calls them differently but their bodies don't change.
- LOCF logic and audit trail.
- DDL for `scenario_resolved_values` and `scenario_resolver_runs`.
- `dispatch_stats` JSONB schema (same keys reported).
- Persistence block: DELETE existing rows, batch INSERT via `execute_values`, update audit row with `completed_at`, `duration_ms`, `n_assignments`, `n_rows_written`, `dispatch_stats`.
- L4 view refresh at the end.
- CLI surface (`--scenario`, `--dry-run`, `--quiet`, `--notes`).

---

## 5. Migration path

Two options:

**A. One-shot rewrite.** Replace topo+mirror with fixed-point in one commit. Requires data migration: every relational `latent()` assignment that today gets mirror-promoted needs its formula switched from `latent()` to `mirror` with `formula_inputs = [paired_variable_id]`. About 15 rows in the starter scenario (matches the historical `reverse_mirror` count of 15).

**B. Two-step.** First, swap the main loop to fixed-point but keep `promote_mirrors()` as a post-pass exactly as today. No data migration needed; the resolver semantics are unchanged. Second, in a separate commit, migrate the latent-with-paired-direction rows to `formula = 'mirror'`, add `eval_mirror()`, and delete `promote_mirrors()`. Each step is independently testable.

Option B is safer for verifying that the fixed-point loop is equivalent before changing data.

### Verification

Before/after diff on `scenario_resolved_values` should be empty (or comprise only `formula_used` cosmetic changes — e.g., the order of operations recorded for sums). The dispatch counts in `scenario_resolver_runs.dispatch_stats` should match within ±0 for `observed`, `zero`, `sum`, `arithmetic`, `reverse_mirror`, `unresolved`; `latent` may differ if any latents were silently absorbing what should be mirror cases.

Concrete test:
1. Run current resolver, snapshot `scenario_resolved_values` to a temp table.
2. Run fixed-point resolver.
3. `SELECT … EXCEPT …` both ways — should return zero rows on `(scenario_id, variable_id, observation_date, value, source)`.

---

## 6. Notes for the rewrite

- Keep the file under 600 lines; current is 539. The rewrite should net out shorter (drop `build_deps`, drop `promote_mirrors`, drop the topo-sort block; add a 20-line fixed-point loop and a 10-line `eval_mirror`).
- Don't remove `paired_variable_id()` until after the data migration in Option B. If choosing Option A, delete it in the same commit.
- The `classify()` function should remain pure — no side effects, takes assignment + by_id, returns a kind constant.
- Convergence guard: cap the outer `while True` at, say, 50 iterations as a safety net against an unforeseen non-convergent state. Log a warning if hit; this should never fire on the starter scenario.
- Tests: add a `test_fixed_point.py` with two scenarios — a trivial chain (`A=TS, B=A, C=B`) confirming three-pass convergence, and a mirror case (`F_in=TS, F_out=mirror(F_in)`) confirming two-pass convergence.
