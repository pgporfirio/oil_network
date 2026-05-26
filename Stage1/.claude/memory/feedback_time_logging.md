---
name: feedback-time-logging
description: "At the end of each thesis-project session, append an hours-worked row to clean/claude/time_log.md"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e9ada12e-a99a-4dcc-95f4-cae8de8abde7
---

At the end of each working session on the thesis project, append a row to `Thesis/clean/claude/time_log.md` with the session date and an estimated hours figure (rounded to the nearest 0.5h), plus a brief note linking to the relevant HANDOVER pass(es).

**Why:** Pedro wants a rough running total of time spent on the thesis — order-of-magnitude, not precision. The HANDOVER tracks passes (semantic units of work) but not clock time, so the running hours total is lost across sessions. Started 2026-05-14 with a retroactive estimate of ~50–60h covering passes 1–20.

**How to apply:** At session wrap-up (after the user signals done, before ending), edit `time_log.md` to add the row. Estimate hours from session length if known; otherwise ask Pedro for a rough number. Don't over-engineer this — one row per session is enough.

**Idle-gap rule (added 2026-05-14):** when Pedro takes >5 minutes to reply to a question I asked, treat the gap as not-working and subtract it from the session length — he's probably doing something else. Apply this when estimating hours, not just when he tells me a number.

Relates to [[project_thesis]] and [[project_oil_network_schema]].
