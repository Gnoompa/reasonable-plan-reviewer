"""Checks a review against its plan, and adds the findings code can prove.

Shared by the review command, the plan endpoint and the analyze endpoint, so
all three agree on the verdict and on what is wrong with a plan.
"""

from __future__ import annotations

import re

from .schemas import Review, graph_problems
from .scheduler import schedule

SEVERITY_ORDER = {"blocks_date": 0, "at_risk": 1, "note": 2}

# Share of the finish day one person must carry before it is called out.
BOTTLENECK_SHARE = 0.7

# Finding kinds computed by code. A model finding may not claim one of these.
STRUCTURAL_KINDS = {"cycle", "deadline", "late", "owner", "bottleneck"}


def normalize(text: str) -> str:
    """Collapse every run of whitespace to one space, as the contract asks."""
    return re.sub(r"\s+", " ", text).strip()


def in_plan(quote: str, plan_text: str) -> bool:
    """True when the quote is non-empty and word for word in the normalized plan."""
    quote = normalize(quote)
    return bool(quote) and quote in plan_text


def tidy_quote(quote: str, plan_text: str) -> str:
    """Drop markdown table pipes and other cells from a quoted table row.

    A model sometimes quotes the whole row, pipes included. That is valid but
    unreadable for the events officer, so the first cell that is still verbatim
    is used instead. A quote that is already clean is returned unchanged.
    """
    text = normalize(quote)
    if not text.startswith("|"):
        return quote
    for cell in text.strip("|").split("|"):
        cell = normalize(cell)
        if cell in plan_text:
            return cell
    return quote


def check_review(review: Review, plan: str) -> tuple[Review, list[str]]:
    """Return the review with unverifiable extras removed, and the contract errors.

    Contract fields that fail are errors, so the model is asked again.
    Dependency quotes are an extra field: a bad one is dropped, not retried,
    so it never costs an attempt.
    """
    text = normalize(plan)
    errors: list[str] = []

    if not review.tasks:
        errors.append("no tasks were extracted; the plan lists work that has to be scheduled")
    errors += graph_problems(review.tasks)

    tasks = []
    for task in review.tasks:
        if not in_plan(task.quote, text):
            errors.append(
                f"task {task.id} quote is not word for word in the plan: {task.quote!r}. "
                "Copy an exact sentence or the cell text from a table row."
            )
        kept = {
            dep: tidy_quote(quote, text)
            for dep, quote in task.dependency_quotes.items()
            if dep in task.depends_on and in_plan(quote, text)
        }
        tasks.append(task.model_copy(update={"quote": tidy_quote(task.quote, text), "dependency_quotes": kept}))

    for index, finding in enumerate(review.findings, start=1):
        if finding.quote is not None and not in_plan(finding.quote, text):
            errors.append(
                f"finding {index} ({finding.title!r}) quote is not word for word in the plan: "
                f"{finding.quote!r}. Copy an exact sentence, or use null."
            )

    tidied = [
        finding.model_copy(
            update={"quote": None if finding.quote is None else tidy_quote(finding.quote, text)}
        )
        for finding in review.findings
    ]
    return review.model_copy(update={"tasks": tasks, "findings": tidied}), errors


def structural_findings(
    tasks: list[dict],
    deadline_day: int | None,
    result: dict,
    quotes: dict[str, str] | None = None,
) -> list[dict]:
    """Findings that follow from the task list and its schedule alone."""
    findings: list[dict] = []
    quotes = quotes or {}

    if result["status"] == "cycle":
        findings.append(
            _finding(
                "cycle",
                "blocks_date",
                "Tasks wait on each other in a circle",
                f"{' → '.join(result['cycle'])} each wait for the next, so none of them can start. "
                "The plan cannot be scheduled until one of those waits is removed.",
                quotes.get(result["cycle"][0]),
            )
        )
    elif deadline_day is None:
        findings.append(
            _finding(
                "deadline",
                "blocks_date",
                "No clear deadline",
                f"The plan does not give one day to work to. The earliest it can finish is day {result['end_day']}.",
            )
        )
    elif not result["meets_deadline"]:
        late = result["days_late"]
        findings.append(
            _finding(
                "late",
                "blocks_date",
                f"{late} {'day' if late == 1 else 'days'} too late",
                f"The earliest the work can finish is day {result['end_day']}, and the plan is due on day {deadline_day}.",
            )
        )

    unowned = [task for task in tasks if task["owner"] is None]
    if unowned:
        names = ", ".join(task["name"] for task in unowned[:3])
        more = f" and {len(unowned) - 3} more" if len(unowned) > 3 else ""
        findings.append(
            _finding(
                "owner",
                "at_risk",
                "Work with nobody named" if len(unowned) > 1 else "A task with nobody named",
                f"No one is responsible for {names}{more}, so it may not get done.",
                quotes.get(unowned[0]["id"]),
            )
        )

    if result["status"] == "scheduled" and result["end_day"]:
        busiest = _busiest_owner(tasks)
        if busiest and busiest[1] >= result["end_day"] * BOTTLENECK_SHARE:
            owner, days = busiest
            findings.append(
                _finding(
                    "bottleneck",
                    "at_risk",
                    f"{owner} carries most of the plan",
                    f"{owner} has {days} days of work in a plan that takes {result['end_day']} days. "
                    "If they are ill or away, the date slips.",
                )
            )

    return findings


def _finding(kind: str, severity: str, title: str, detail: str, quote: str | None = None) -> dict:
    return {"title": title, "detail": detail, "quote": quote, "severity": severity, "kind": kind}


def _busiest_owner(tasks: list[dict]) -> tuple[str, int] | None:
    totals: dict[str, int] = {}
    for task in tasks:
        if task["owner"] is not None:
            totals[task["owner"]] = totals.get(task["owner"], 0) + task["duration_days"]
    if not totals:
        return None
    return max(sorted(totals.items()), key=lambda item: item[1])


def _ranked(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda finding: SEVERITY_ORDER.get(finding["severity"], 3))


def analyse(tasks: list[dict], deadline_day: int | None, quotes: dict[str, str] | None = None) -> dict:
    """Schedule and structural findings for a task list, computed once."""
    result = schedule(tasks, deadline_day)
    return {"schedule": result, "findings": _ranked(structural_findings(tasks, deadline_day, result, quotes))}


def _merge_model_findings(findings: list[dict]) -> list[dict]:
    """Fold model findings that make the same point into one.

    The model sometimes splits one point across two quotes. Both quotes are
    kept in the detail, since only the quote field is checked word for word.
    """
    merged: dict[tuple[str, str], dict] = {}
    for finding in findings:
        key = (finding["kind"], finding["title"].strip().lower())
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(finding)
            continue
        quote = finding.get("quote")
        if quote and quote != existing.get("quote") and quote not in existing["detail"]:
            existing["detail"] = f"{existing['detail']} The plan also says: \u201c{quote}\u201d"
    return list(merged.values())


def review_with_findings(review: Review) -> tuple[dict, dict]:
    """The review with code findings merged in, and its schedule.

    Code owns the structural kinds, so stored or model findings that claim one
    are replaced by the current computation rather than shown twice.
    """
    tasks = [
        task.model_dump(include={"id", "name", "owner", "duration_days", "depends_on"})
        for task in review.tasks
    ]
    quotes = {task.id: task.quote for task in review.tasks}
    result = analyse(tasks, review.deadline_day, quotes)

    model = [finding.model_dump() for finding in review.findings if finding.kind not in STRUCTURAL_KINDS]
    payload = review.model_dump()
    payload["findings"] = _ranked(result["findings"] + _merge_model_findings(model))
    return payload, result["schedule"]
