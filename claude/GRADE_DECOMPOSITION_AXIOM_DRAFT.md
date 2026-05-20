# Grade decomposition — draft axiom

A proposal for how per-grade variables relate to crude across the network.
For review before any further propagator work.

---

## Starting point

The asset graph is currently built around **crude** as the unit of flow:
- Every physical node has crude variables (P, C, S, B, F_in per upstream, F_out per downstream)
- Mass balance holds per node: `P + ΣF_in = C + ΔS + ΣF_out + B`
- Where we have data, variables are bound to TS or to formulas; where we
  don't, they're `latent()`

We now want to introduce **grades** (children of crude in the commodity
hierarchy: wti_midland, bakken_light, mars, etc.) without disturbing this.

---

## Two axioms — proposed

### Axiom: Mass balance per commodity

Mass balance applies independently for each commodity at each node:

```
P_g(n, t) + Σ F_in_g(n, *, t)
  = C_g(n, t) + ΔS_g(n, t) + Σ F_out_g(n, *, t) + B_g(n, t)
```

This is the same as the existing crude mass balance, just instantiated per
grade. Crude is one such commodity; every grade is another.

### Axiom: Hierarchy closure

For every (variable_type, node, t), the parent-commodity value equals the
sum over child-commodity values:

```
X_crude(n, t) = Σ_{g ∈ children(crude)} X_g(n, t)
```

Applied to every X ∈ {P, C, S, B} and to every per-edge F_in_g(n, X) and
F_out_g(n, X).

Together these two axioms are sufficient: they say "the network behaves the
same way for grades as for crude, and the grades aggregate back to crude."

---

## Where the binding happens (the "what's a formula here?" question)

The two axioms are CONSTRAINTS that must hold. They don't tell us what
formula to write. **Per node-type defaults — proposal:**

| Node type | P_g | C_g | S_g | B_g | F_out_g(n, X) | F_in_g(n, X) |
|---|---|---|---|---|---|---|
| **Producer** (basin) | `share_g × P_crude` | 0 | 0 | latent | proportional: `(F_out_crude(n,X) / P_crude) × P_g` | n/a (basin = source) |
| **Pass-through, single output** (pipe, gathering with 1 outflow) | 0 | 0 | 0 | 0 | `Σ F_in_g(n, *)` (sum-conservation) | `F_out_g(upstream → n)` (mirror) |
| **Pass-through, multi-output** (gathering with N>1 outflows, hub) | 0 | 0 | 0 | 0 | proportional: `(F_out_crude(n,X) / Σ F_in_crude(n)) × Σ F_in_g(n)` | mirror as above |
| **Storage** (hub, SPR) | 0 | 0 | `S_g(t-1) + Σ F_in_g(t) - Σ F_out_g(t)` (inventory recursion) | latent | proportional-to-stock-mix: `(S_g(t-1) / S_crude(t-1)) × F_out_crude(n, X)(t)` | mirror |
| **Refinery** | 0 | `slate_g × C_crude` OR `Σ F_in_g` (mass balance) | 0 | latent | n/a (refinery = sink) | mirror |
| **Boundary** (foreign supply, export sink) | latent | latent | 0 | latent | latent | latent |

`share_g(basin)`, `slate_g(refinery)`, and the per-edge proportional ratios
all become **variables themselves** — they can be:
- bound to a constant (e.g. `share_wti_midland_permian_tx = 0.70`)
- bound to a TS (e.g. Argus assay series, monthly-varying refinery slate)
- left latent (the LP allocator solves them)

This is the same pattern the framework already uses for crude.

---

## Precedence (when both data and formula apply)

For any (scenario, grade, node, variable_type):

1. **Explicit TS binding** wins (rare today; will be available once we have
   grade-level data sources).
2. **Explicit formula binding** in `variable_assignments` wins next (e.g. a
   manually-curated slate for a specific refinery overrides the default).
3. **Per-node-type default** (the table above) applies as fallback.
4. **`latent()`** is the terminal default — the LP allocator picks it up
   later.

Same three-level precedence as the existing crude framework, just
generalised to per-commodity.

---

## What this gives us (and what it doesn't)

**Naturally satisfied:**

- Hierarchy closure at producers: `P_crude(basin) = Σ share_g × P_crude(basin) = P_crude(basin)` ✓
- Per-grade mass balance at pass-through nodes (sum-conservation rule
  enforces `Σ F_in_g = Σ F_out_g` when there's one outflow; proportional
  rule when there are many)
- Where we have crude flow data, grades follow proportionally
- Where crude is latent, grades are latent — no spurious values

**Still requires data or rule decisions:**

- Refinery slate per (refinery, grade): no rule unless we have data; latent
  by default
- Multi-output crude allocation: still needs the LP (the proportional rule
  is a *fallback* when crude isn't observed at the edge level either)
- Storage inventory dynamics need a seed at t=0 (the existing 0-seed
  convention from the resolver handles this — S_g(0) = 0, no grade content
  at start of scenario)

**A consequence worth flagging:**

If the crude flow is latent on an edge AND we apply the per-grade
proportional rule, the grade is latent too (because the rule references
the latent crude flow). This means **grades only carry values where crude
carries values**. That's a feature, not a bug — it preserves the
"we don't make up data" invariant.

---

## Decision points for your review

1. **Is the per-node-type defaults table the right granularity?** Or do
   you want finer (e.g. per-refinery-type rules)?
2. **Storage outflow rule:** is "proportional to previous-period stock mix"
   what you want as the default? Other options: (a) FIFO (oldest stock
   leaves first, requires per-injection-date tracking — heavy); (b) blend
   model with a holding ratio (some grades preferred for blending — needs
   per-grade preference table).
3. **Multi-output gathering allocation:** the proportional rule assumes
   "perfect mixing at the gathering, all outflows carry the same grade mix
   as the inputs." Is that defensible for your producers, or do specific
   gatherings have known per-grade routing (e.g. condensate splitters that
   take only the lightest grade)?
4. **Refinery slate:** do you want `slate_g × C_crude` (with `slate_g`
   per-refinery as a variable) or `Σ F_in_g` (mass balance: refinery
   consumes whatever arrived)? Different implications for LP later.
5. **Naming:** should this be **Axiom 7** in the thesis (since the current
   set goes to Axiom 6), or should it be folded into the existing
   **Axiom 3 (Universal mass balance)** as an extension?

---

## Implementation note

The current propagator (`code/migrations/propagate_grades.py`) implements
parts of this — producer share, per-edge proportional at producer
outflows, structural per-grade variables on the reachable subgraph. The
single-output sum-conservation rule was attempted but pulled back to wait
for this axiom review.

Once the axiom is approved:

- The propagator can be re-written to walk the rules table above
  systematically per (node-type, variable-type, commodity).
- Each rule becomes a formula generator: given crude variable names and
  share/slate variable names, emit the right formula.
- All grade variables become bound consistently across the network.
- The two axioms (mass balance per commodity + hierarchy closure) become
  checkable invariants in `v_aggregation_consistency`-style audit views.
