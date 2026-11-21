# Plan and decision log

Decisions made by questioning the plan against [README](../README.md)

## Known defects in the starter

- [scheduler.py](../backend/app/scheduler.py) ignores the owner constraint (two tasks for one person both start on day 0).
- It recurses forever on a cycle, so the request fails with a 500. [harbour-run.json](../data/reviews/harbour-run.json) has a real cycle (T2 and T3), so that plan currently fails to open.
- No input validation. Duplicate ids, unknown dependencies, `duration_days < 1` and more than 60 tasks should return 4xx, not 500.
- `get_plan` builds a file path from `plan_id` without checking it, so it is open to path traversal.
- [review.py](../backend/app/review.py) runs `json.loads` on raw model output with no shape or quote check.
- Stored data breaks the quote rule: [lantern-festival.json](../data/reviews/lantern-festival.json) T15 is not word for word from the plan. The harbour-run deadline is 99, which appears nowhere in the plan.

## Decisions

### Q1: Contradictory or missing deadline
- harbour-run says both "30 days after kickoff" and "five weeks after kickoff".
- If the plan has no single clear deadline, `deadline_day` is `null`, and a finding quotes both sentences.
- When the deadline is null, the page shows the earliest finish day, says "no clear deadline", and lets Nora enter a deadline to get a verdict.

### Q2: Who writes findings
- Code computes the structural findings inside `./review`: cycle, unowned task, late by N days, bottleneck person, no clear deadline.
- The model adds judgement findings only, such as missing tasks or vague wording. Duplicates of the structural findings are removed.
- Each finding gets an extra `severity` field (blocks date, at risk, note). Contracts allow extra fields.
- The page ranks findings by severity, shows the top items first and collapses the rest.

### Q3: Table and prose together
- Dependencies are the union of table and prose. Never drop one to avoid a cycle.
- Durations use the stated number: a week is 7 days, "about two weeks" is 14. If the table and prose differ, use the longer value and add a finding.
- Each dependency stores a verified quote in an extra field, so Nora can see where each "waits for" came from.

### Q4: Model and provider
- Use `OPENROUTER_API_KEY` and OpenRouter provider instead of Anthropic.
- Generate the stored reviews with `nvidia/nemotron-3.5-super-vl:free`, so graders can reproduce them for free.
- Allow 3 attempts within a 270 s total budget. Each call gets `min(90 s, remaining)`, and validation errors are fed back on each retry. After the last failure, exit non-zero and write nothing (write to a temp file, then rename).
- Env vars for NOTES: `OPENROUTER_API_KEY` and `REVIEW_MODEL` (optional).

### Q5: Evidence for the finish day
- `/api/schedule` returns extra fields:
  - `optimal`: true when CP-SAT proves no shorter schedule exists.
  - `lower_bound`: the best bound the solver proved, used when it stops at the time limit.
  - Data for a timeline with one row per owner.
- The page shows a Gantt chart, one row per owner.

### Q6: Corrections
- Nora can edit durations, owners, dependencies and the deadline, add and remove tasks, and dismiss findings.
- The page shows what changed: verdict before and after, findings resolved or newly raised, and moved bars highlighted on the Gantt chart.
- Corrections are saved as a `{REVIEW_ID}_correction_{1,2,3,...}.json` as a layer on top of the review, with a "reset to review" button. `data/reviews/` is never edited.

### Q7: Recomputing after edits
- A shared `backend/app/analysis.py` is used by both `./review` and a new `POST /api/analyze`, which returns the schedule plus the structural findings.
- `/api/schedule` returns what the contract specifies, plus the Q5 evidence fields.
- Model findings stay as they are after an edit.
- The page does not call the model. It uses stored reviews only, for reliability and cost.

### Q8: Hardening for unseen plans
- Write a small eval set: 3–4 plans of our own, each with expected facts (task count, deadline, cycle), checked by a script.
- Not included: owner alias normalisation, table-row coverage check, temperature 0.
- Known risk: "Ruth" and "Ruth Marsh" would count as two people, the schedule would come out too early, and no warning would show. Include an alias case in the eval set to find out whether the model merges the names.

## Status

Implemented end to end.

- Backend: `schemas.py`, `scheduler.py`, `prompts.py`, `provider.py`, `analysis.py`, `review.py`, `corrections.py`, `main.py`.
- Frontend: verdict, findings with quotes and dismiss, Gantt by owner, editable task table, save and reset. Styled from BRAND.md.
- Corrections are written to `data/corrections/<plan>/<plan>_correction_<n>.json`; `data/reviews/` is never edited.
- 46 tests pass; both contract checks pass; `tsc --noEmit` and pyflakes are clean.
- The three stored reviews were regenerated with `./review` against the free model.

## Frontend notes

- Each finding carries a stable `id` (`<kind>:<title>`), so dismissing one does not hide the others.
- Structural findings are recomputed on the server after every edit; model findings stay as stored.
- The page never calls the model, so it stays fast and works without a key.

## Scheduler design (CP-SAT)

1. Validate the request with Pydantic; on failure return 4xx with a JSON body.
2. Run Kahn's algorithm. If any tasks are left, walk back through their dependencies until a task repeats, and return one cycle as `status: "cycle"`.
3. Model:
   - Each task is an interval variable: start, duration, end.
   - For every dependency, `start_j >= end_i`.
   - `AddNoOverlap` across each non-null owner's tasks. Tasks with a null owner are left out.
   - Minimise the latest end day.
   - Horizon is the sum of all durations.
4. Solver settings:
   - Time limit 3 s.
   - Workers: `min(8, cpu_count)`.
   - `random_seed = 0`.
   - Import OR-tools when the module loads, not per request.
5. If the solver returns no solution, use a greedy fallback: list scheduling by longest path to the end, placing each task after its dependencies and when its owner is free.
6. Before returning, check that every dependency and owner constraint holds in the result.
