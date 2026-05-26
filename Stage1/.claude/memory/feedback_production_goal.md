---
name: feedback-production-goal
description: "Pedro's primary goal is deploying this framework at an asset manager at scale; the thesis is the route, not the destination. Architectural decisions must be tested against the production-deployment lens, particularly the provenance inference layer that comes after the framework"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3845f5c4-9e59-4e99-8977-621d99497cb6
---

When making architectural / schema decisions on the oil_network project, the
primary test is: **does this choice support the provenance inference layer
that comes after the thesis?** The thesis is the academic deliverable; the
real goal is deploying at an asset manager and running this at scale on real
trading data.

The specific architectural property Pedro is preserving: once the schema
captures (a) origin tagging at the source (basin / import terminal / foreign
production), (b) grade/quality as a per-flow attribute, and (c) per-refinery
observed slate, then provenance — and therefore route inference — becomes
recoverable even past commingling hubs, because mass balance + grade
compatibility + capacity constraints jointly constrain the latent splits.
Commingling alone doesn't destroy reconstructability if grade fingerprints
are preserved.

**Why:** Pedro works in oil markets and is joining an asset manager with
collaborators to deploy this. Trading decisions depend on knowing where
crude is physically and where it's headed. The framework's job is to be
the consistent substrate that a downstream inference layer (LP solver,
attention-based GNN, etc.) can ride on.

**How to apply:**

1. Don't dismiss the latent allocations at hubs as "fundamentally lost
   information" — they are *recoverable in principle* once grades + slate
   data land. Frame the framework as constraint-providing, not
   provenance-discarding.
2. When choosing between modelling options, prefer the one that leaves a
   natural home for per-grade decomposition (aggregate nodes are good for
   this — e.g. [[project_imports_agg_pattern]] / padd_imports_agg layers
   can later carry per-grade variables without restructuring).
3. The grade/quality dimension is NOT in the schema yet (current scenario
   is single-commodity `crude`). It's the next major axis. Mention this
   honestly when discussing limits; don't paper over it.
4. Coverage contracts (Principle 2.9) are a production feature, not an
   academic one — they let a deployment scope which subsystems are
   authoritative without rewriting the asset graph. Preserve their
   primacy.
5. The thesis IS important — defend it, finish it well — but if a
   choice serves the thesis but harms production deployment, flag it
   and propose an alternative.

Related: [[project_thesis]], [[project_oil_network_schema]],
[[feedback_autonomy]].
