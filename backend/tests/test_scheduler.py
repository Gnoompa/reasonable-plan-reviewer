import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import scheduler  # noqa: E402
from app.scheduler import find_cycle, schedule  # noqa: E402


def task(id, duration_days, depends_on=()):
    return {"id": id, "name": id, "owner": None, "duration_days": duration_days, "depends_on": list(depends_on)}


def test_single_task_ends_after_its_duration():
    result = schedule([task("A", 3)], 10)
    assert result["schedule"] == [{"id": "A", "start_day": 0, "end_day": 3}]
    assert result["end_day"] == 3


def test_dependent_task_starts_after_its_dependency():
    result = schedule([task("A", 3), task("B", 2, ["A"])], 10)
    by_id = {item["id"]: item for item in result["schedule"]}
    assert by_id["B"]["start_day"] == 3
    assert result["end_day"] == 5


def owned(id, duration_days, owner, depends_on=()):
    return {
        "id": id,
        "name": id,
        "owner": owner,
        "duration_days": duration_days,
        "depends_on": list(depends_on),
    }


def test_one_person_never_does_two_tasks_at_once():
    # Ann has two independent 3-day tasks: they must run one after the other.
    result = schedule([owned("A", 3, "Ann"), owned("B", 3, "Ann")], 10)
    assert result["end_day"] == 6
    starts = {entry["id"]: entry["start_day"] for entry in result["schedule"]}
    assert sorted(starts.values()) == [0, 3]


def test_different_owners_work_in_parallel():
    result = schedule([owned("A", 3, "Ann"), owned("B", 3, "Bob")], 10)
    assert result["end_day"] == 3


def test_cycle_is_reported_not_raised():
    result = schedule([task("A", 1, ["B"]), task("B", 1, ["A"])], 10)
    assert result["status"] == "cycle"
    assert set(result["cycle"]) == {"A", "B"}
    assert result["schedule"] == []
    assert result["end_day"] is None


def test_self_dependency_is_a_cycle():
    result = schedule([task("A", 1, ["A"])], 10)
    assert result["status"] == "cycle"
    assert result["cycle"] == ["A"]


def test_empty_task_list_ends_on_day_zero():
    result = schedule([], 10)
    assert result["status"] == "scheduled"
    assert result["end_day"] == 0
    assert result["meets_deadline"] is True


def test_late_plan_reports_days_late():
    result = schedule([owned("A", 5, "Ann")], 3)
    assert result["meets_deadline"] is False
    assert result["days_late"] == 2


def test_schedule_satisfies_owner_and_dependency_rules():
    tasks = [
        owned("A", 2, "Ann"),
        owned("B", 3, "Ann", ["A"]),
        owned("C", 1, "Bob", ["A"]),
        owned("D", 2, "Ann", ["C"]),
    ]
    result = schedule(tasks, 20)
    by_id = {entry["id"]: entry for entry in result["schedule"]}
    for item in tasks:
        for dep in item["depends_on"]:
            assert by_id[item["id"]]["start_day"] >= by_id[dep]["end_day"]
    for owner in {"Ann", "Bob"}:
        spans = sorted(
            (by_id[item["id"]] for item in tasks if item["owner"] == owner),
            key=lambda entry: entry["start_day"],
        )
        for earlier, later in zip(spans, spans[1:]):
            assert earlier["end_day"] <= later["start_day"]


def test_cycle_answer_is_stable_and_follows_dependencies():
    tasks = [task("C", 1, ["A"]), task("A", 1, ["B"]), task("B", 1, ["C"]), task("D", 1, ["A"])]
    cycle = find_cycle(tasks)
    assert cycle == find_cycle(list(reversed(tasks)))
    assert set(cycle) == {"A", "B", "C"}


def test_fallback_without_cp_sat_is_valid(monkeypatch):
    monkeypatch.setattr(scheduler, "cp_model", None)
    tasks = [
        owned("A", 2, "Ann"),
        owned("B", 5, "Bob", ["A"]),
        owned("C", 1, "Ann"),
        owned("D", 3, "Ann", ["A"]),
    ]
    result = schedule(tasks, 20)
    assert result["optimal"] in (True, False)
    assert scheduler.violations(tasks, {item["id"]: item for item in result["schedule"]}) == []
    assert result["end_day"] == 7


def test_optimal_result_reports_matching_bound():
    result = schedule([owned("A", 3, "Ann"), owned("B", 4, "Ann")], 10)
    assert result["optimal"] is True
    assert result["lower_bound"] == result["end_day"] == 7


def test_fallback_is_deterministic(monkeypatch):
    """The same input must give the same plan, so a rerun cannot change the answer."""
    monkeypatch.setattr(scheduler, "cp_model", None)
    tasks = [
        owned("A", 2, "Ann"),
        owned("B", 2, "Ann"),
        owned("C", 2, "Bob"),
        owned("D", 1, "Bob", ["A"]),
    ]
    first = schedule(tasks, 20)
    second = schedule(tasks, 20)
    assert first["end_day"] == second["end_day"]
    assert first["schedule"] == second["schedule"]

    # Reversing the input may reorder the answer, but must not change the days.
    reversed_result = schedule(list(reversed(tasks)), 20)
    assert reversed_result["end_day"] == first["end_day"]
    by_id = {item["id"]: item for item in reversed_result["schedule"]}
    for item in first["schedule"]:
        assert by_id[item["id"]]["start_day"] == item["start_day"]


def test_a_wide_graph_stays_within_the_contract_time():
    """60 tasks is the contract maximum; the answer must come back quickly."""
    import time

    tasks = [owned(f"T{i}", 1 + i % 4, f"P{i % 5}", [f"T{i - 1}"] if i % 7 == 0 and i else []) for i in range(60)]
    started = time.perf_counter()
    result = schedule(tasks, 40)
    elapsed = time.perf_counter() - started
    assert result["status"] == "scheduled"
    assert elapsed < 10, f"took {elapsed:.2f}s"
