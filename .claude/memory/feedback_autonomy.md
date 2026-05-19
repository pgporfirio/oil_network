---
name: Full autonomy on thesis project — do not ask, just proceed
description: On the thesis/oil-network project, Pedro wants full autonomy and no confirmation prompts — read files directly from the directory rather than running exploratory shell commands that trigger permission prompts
type: feedback
originSessionId: 77985a0a-6dc2-4425-beea-7ddad2ee3c79
---
On the crude-oil / thesis project, Pedro does not want to be asked anything. Execute the task end-to-end and only stop for a genuine blocker.

**Why:** He often kicks off a task and steps away (explicitly said "I need to leave and I might only be back tomorrow morning"). Permission prompts stall the work. He has twice said verbatim "full autonomy" when I asked or when exploratory bash calls triggered prompts.

**How to apply:**
- Prefer the Read tool on files in the working directory over shell `cat`/`python -c`/`psql` exploratory calls — those trigger permission prompts he has to click through.
- Don't run pre-flight "is X installed / is the db up" checks when a full deliverable (notebook, script) will exercise those paths anyway. Let the deliverable's own first cell be the smoke test.
- Don't confirm design choices mid-task. Make a reasonable call consistent with `CLAUDE.md` / `DESIGN_PRINCIPLES.md` and proceed. If it turns out wrong, fix it on the next turn.
- He does want to be told what was built at the end — keep the end-of-turn summary informative.
