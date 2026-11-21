"""Prompts for the review command.

Kept as constants rather than a markdown file so the text ships inside the
`backend` package, needs no path handling relative to the working directory,
and can be asserted in tests. The plan is passed inside a delimiter instead of
being interpolated, so braces or instructions in a plan cannot break the prompt.
"""

SYSTEM = """You read a volunteer committee's event plan and return the tasks it describes.

Reply with one JSON object and nothing else. No prose, no markdown fence.

Shape:
{
  "deadline_day": integer or null,
  "tasks": [
    {"id": "T1", "name": "...", "owner": "..." or null, "duration_days": 1,
     "depends_on": [],
     "quote": "...",
     "dependency_quotes": {"<id of the task waited on>": "..."}}
  ],
  "findings": [
    {"title": "...", "detail": "...", "quote": "..." or null,
     "severity": "blocks_date" | "at_risk" | "note"}
  ]
}

Rules that are checked by code, so an answer that breaks them is rejected:

1. Every quote must be copied word for word from the plan, case sensitive.
   Pick the shortest span that states the fact, and do not change it. Do not
   paraphrase, do not fix spelling, do not join two separate places with
   extra words.
   For a table row, quote only the cell text, such as "Apply for the road
   closure". Leave out the | characters that separate the columns and leave
   out the other cells.
2. deadline_day counts days from kickoff. "21 days after our kickoff meeting"
   is 21. "five weeks" is 35. If the plan gives two different deadlines, give
   null and add a finding that quotes both sentences.
   If the plan gives no clear number, give null and quote the vague phrase.
3. duration_days is a whole number of days, at least 1. A week is 7. Convert
   "about two weeks" to 14, "ten days" to 10, "a few days" to your best guess
   and add a finding about it.
4. Task ids are unique and short: T1, T2, T3. Every id in depends_on must be
   a task in the same reply, never an id you did not create.
5. Read the whole plan. A task, an owner, a duration or a dependency can be
   stated in a table or in a sentence, and both count. A sentence that says
   "once both bands are booked Ruth will arrange the lighting" is a dependency
   on the booking task. depends_on lists the tasks that must finish before
   this one can start.
6. owner is the one person who does the task. Write each person's name the
   same way every time, so "Ruth" and "Ruth Marsh" are not counted as two
   people. If nobody is named, or the plan says tbc, give null. Do not guess.
7. Never drop a dependency to tidy the graph. If two tasks wait on each other,
   keep both edges; code reports the cycle.
8. dependency_quotes holds, for each id in depends_on, the quote that shows
   the wait. It must also be word for word from the plan.

Findings are for what only a careful reader would notice: two parts of the
plan that disagree, a wording too vague to schedule, a piece of work the plan
implies but never lists. Keep them few and specific. Every finding needs a
quote, or null when the point is about the plan as a whole.

Do not write findings about these; code computes them from your task list:
tasks with no owner, tasks waiting on each other in a circle, whether the plan
finishes by its deadline, and one person carrying most of the work.
"""

USER = """Extract the tasks and findings from the plan between the delimiters.

<plan>
{plan}
</plan>"""

FEEDBACK = """
Your previous reply was rejected. Fix these problems and reply again with the
full corrected JSON object. Keep everything that was already correct.

{errors}
"""


def build(plan: str, errors: list[str] | None = None) -> str:
    """Return the user message: the plan, plus any errors to correct."""
    message = USER.format(plan=plan)
    if errors:
        items = "\n".join(f"- {error}" for error in errors)
        message += FEEDBACK.format(errors=items)
    return message
