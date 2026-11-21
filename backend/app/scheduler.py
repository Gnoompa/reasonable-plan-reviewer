"""Schedules a task list. CP-SAT when available, list scheduling otherwise.

The response always satisfies docs/contracts.md: every task appears once, a
task starts at or after each dependency's end, and one person's tasks never
overlap. Callers pass contract-shaped dicts; validation lives in schemas.py.
"""

from __future__ import annotations

import math
import os

try:  # Imported once at module load: the first import costs about a second.
    from ortools.sat.python import cp_model
except ImportError:  # pragma: no cover - exercised only without ortools
    cp_model = None

SOLVER_TIME_LIMIT = 3.0


def find_cycle(tasks: list[dict]) -> list[str]:
    """Return the ids on one circular dependency in dependency order, or []."""
    known = {task["id"] for task in tasks}
    remaining = {task["id"]: set(task["depends_on"]) & known for task in tasks}
    while True:
        ready = {task_id for task_id, deps in remaining.items() if not deps}
        if not ready:
            break
        for task_id in ready:
            del remaining[task_id]
        for deps in remaining.values():
            deps -= ready
    if not remaining:
        return []

    # Every task left waits on another task left, so following dependencies
    # from any of them must revisit a task; the stretch from that task onwards
    # is one cycle. Sorting keeps the answer the same from run to run.
    walk: list[str] = []
    seen: dict[str, int] = {}
    current = min(remaining)
    while current not in seen:
        seen[current] = len(walk)
        walk.append(current)
        current = min(remaining[current])
    return walk[seen[current]:]


def empty_schedule(deadline_day: int | None) -> dict:
    return {
        "status": "scheduled",
        "schedule": [],
        "end_day": 0,
        "deadline_day": deadline_day,
        "meets_deadline": None if deadline_day is None else True,
        "days_late": None if deadline_day is None else 0,
        "cycle": [],
        "optimal": True,
        "lower_bound": 0,
        "timeline": [],
    }


def cycle_response(cycle: list[str], deadline_day: int | None) -> dict:
    return {
        "status": "cycle",
        "schedule": [],
        "end_day": None,
        "deadline_day": deadline_day,
        "meets_deadline": None,
        "days_late": None,
        "cycle": cycle,
        "optimal": None,
        "lower_bound": None,
        "timeline": [],
    }


def lower_bound(tasks: list[dict]) -> int:
    """max(longest dependency chain, busiest owner's total days)."""
    by_id = {task["id"]: task for task in tasks}
    memo: dict[str, int] = {}

    def chain(task_id: str) -> int:
        if task_id not in memo:
            task = by_id[task_id]
            memo[task_id] = task["duration_days"] + max(
                (chain(dep) for dep in task["depends_on"]), default=0
            )
        return memo[task_id]

    longest = max((chain(task["id"]) for task in tasks), default=0)
    per_owner: dict[str, int] = {}
    for task in tasks:
        if task["owner"] is not None:
            per_owner[task["owner"]] = per_owner.get(task["owner"], 0) + task["duration_days"]
    return max(longest, max(per_owner.values(), default=0))


def list_schedule(tasks: list[dict]) -> dict[str, dict]:
    """Serial list scheduling: the ready task with the longest tail goes first.

    Used only when CP-SAT is missing or finds nothing. Always valid, not
    always shortest.
    """
    by_id = {task["id"]: task for task in tasks}
    successors: dict[str, list[str]] = {task_id: [] for task_id in by_id}
    for task in tasks:
        for dep in task["depends_on"]:
            successors[dep].append(task["id"])

    tail: dict[str, int] = {}

    def tail_of(task_id: str) -> int:
        if task_id not in tail:
            tail[task_id] = by_id[task_id]["duration_days"] + max(
                (tail_of(nxt) for nxt in successors[task_id]), default=0
            )
        return tail[task_id]

    # Ties break on the original order, so the same input always gives the same plan.
    order = {task["id"]: index for index, task in enumerate(tasks)}
    done: dict[str, dict] = {}
    owner_free: dict[str, int] = {}
    while len(done) < len(tasks):
        ready = [
            task
            for task in tasks
            if task["id"] not in done and all(dep in done for dep in task["depends_on"])
        ]
        if not ready:
            raise ValueError("cycle in task list")
        task = max(ready, key=lambda item: (tail_of(item["id"]), -order[item["id"]]))
        start = max((done[dep]["end_day"] for dep in task["depends_on"]), default=0)
        if task["owner"] is not None:
            start = max(start, owner_free.get(task["owner"], 0))
        end = start + task["duration_days"]
        done[task["id"]] = {"id": task["id"], "start_day": start, "end_day": end}
        if task["owner"] is not None:
            owner_free[task["owner"]] = end
    return done


def solve_cpsat(tasks: list[dict]) -> tuple[dict[str, dict], bool, int] | None:
    """Return (items, proven optimal, bound), or None when no solution was found."""
    if cp_model is None:
        return None

    horizon = sum(task["duration_days"] for task in tasks)
    model = cp_model.CpModel()
    starts, ends, intervals = {}, {}, {}
    for task in tasks:
        starts[task["id"]] = model.NewIntVar(0, horizon, f"start_{task['id']}")
        ends[task["id"]] = model.NewIntVar(0, horizon, f"end_{task['id']}")
        intervals[task["id"]] = model.NewIntervalVar(
            starts[task["id"]], task["duration_days"], ends[task["id"]], f"task_{task['id']}"
        )

    for task in tasks:
        for dep in task["depends_on"]:
            model.Add(starts[task["id"]] >= ends[dep])

    by_owner: dict[str, list] = {}
    for task in tasks:
        if task["owner"] is not None:
            by_owner.setdefault(task["owner"], []).append(intervals[task["id"]])
    for spans in by_owner.values():
        model.AddNoOverlap(spans)

    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, list(ends.values()))
    model.Minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT
    solver.parameters.num_workers = min(8, os.cpu_count() or 1)
    solver.parameters.random_seed = 0
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    items = {
        task["id"]: {
            "id": task["id"],
            "start_day": solver.Value(starts[task["id"]]),
            "end_day": solver.Value(ends[task["id"]]),
        }
        for task in tasks
    }
    return items, status == cp_model.OPTIMAL, math.ceil(solver.BestObjectiveBound() - 1e-9)


def violations(tasks: list[dict], items: dict[str, dict]) -> list[str]:
    """Contract rules a schedule must satisfy before it is returned."""
    problems: list[str] = []
    by_id = {task["id"]: task for task in tasks}
    if set(items) != set(by_id):
        missing = sorted(set(by_id) - set(items))
        extra = sorted(set(items) - set(by_id))
        return [f"coverage: missing={missing} extra={extra}"]

    for task_id, item in items.items():
        task = by_id[task_id]
        if item["start_day"] < 0:
            problems.append(f"{task_id} starts before day 0")
        if item["end_day"] - item["start_day"] != task["duration_days"]:
            problems.append(f"{task_id} duration does not match its span")
        for dep in task["depends_on"]:
            if item["start_day"] < items[dep]["end_day"]:
                problems.append(f"{task_id} starts before {dep} ends")

    by_owner: dict[str, list[dict]] = {}
    for task_id, task in by_id.items():
        if task["owner"] is not None:
            by_owner.setdefault(task["owner"], []).append(items[task_id])
    for owner, spans in by_owner.items():
        spans.sort(key=lambda span: span["start_day"])
        for earlier, later in zip(spans, spans[1:]):
            if later["start_day"] < earlier["end_day"]:
                problems.append(f"{earlier['id']} overlaps {later['id']} for {owner}")
    return problems


def schedule(tasks: list[dict], deadline_day: int | None) -> dict:
    """Return a contract-shaped response plus the evidence fields from the plan."""
    if not tasks:
        return empty_schedule(deadline_day)

    cycle = find_cycle(tasks)
    if cycle:
        return cycle_response(cycle, deadline_day)

    bound = lower_bound(tasks)
    solution = solve_cpsat(tasks)
    if solution is not None and not violations(tasks, solution[0]):
        items, optimal, solver_bound = solution
        bound = max(bound, solver_bound)
    else:
        items = list_schedule(tasks)
        optimal = False

    ordered = [items[task["id"]] for task in tasks]
    end_day = max((item["end_day"] for item in ordered), default=0)
    optimal = optimal or end_day == bound
    names = {task["id"]: task["name"] for task in tasks}
    owners = {task["id"]: task["owner"] for task in tasks}

    return {
        "status": "scheduled",
        "schedule": ordered,
        "end_day": end_day,
        "deadline_day": deadline_day,
        "meets_deadline": None if deadline_day is None else end_day <= deadline_day,
        "days_late": None if deadline_day is None else max(0, end_day - deadline_day),
        "cycle": [],
        "optimal": optimal,
        "lower_bound": bound,
        "timeline": [
            {
                "id": item["id"],
                "name": names[item["id"]],
                "owner": owners[item["id"]],
                "start_day": item["start_day"],
                "end_day": item["end_day"],
            }
            for item in ordered
        ],
    }
