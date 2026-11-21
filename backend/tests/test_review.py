import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import provider  # noqa: E402
from app import review as review_module  # noqa: E402
from app.analysis import analyse, check_review, review_with_findings  # noqa: E402
from app.review import main, review_plan  # noqa: E402
from app.schemas import Review  # noqa: E402

PLAN = """# Spring market

The market runs on day 21. Everything must be finished by then.

| Task | Owner | Days | Waits for |
| --- | --- | --- | --- |
| Book the hall | Joan | 2 | |
| Print the posters | Joan | 3 | Book the hall |
"""

GOOD = {
    "deadline_day": 21,
    "tasks": [
        {
            "id": "T1",
            "name": "Book the hall",
            "owner": "Joan",
            "duration_days": 2,
            "depends_on": [],
            "quote": "| Book the hall | Joan | 2 | |",
        },
        {
            "id": "T2",
            "name": "Print the posters",
            "owner": "Joan",
            "duration_days": 3,
            "depends_on": ["T1"],
            "quote": "| Print the posters | Joan | 3 | Book the hall |",
        },
    ],
    "findings": [],
}


def reply(payload):
    return json.dumps(payload)


def test_a_valid_reply_is_accepted():
    payload = review_plan(PLAN, complete=lambda system, user, timeout: reply(GOOD))
    assert payload["deadline_day"] == 21
    assert len(payload["tasks"]) == 2
    assert "schedule" not in payload


def test_a_paraphrased_quote_is_rejected_and_retried():
    bad = json.loads(json.dumps(GOOD))
    bad["tasks"][0]["quote"] = "Book the hall for the market"
    calls = []

    def complete(system, user, timeout):
        calls.append(user)
        return reply(bad if len(calls) == 1 else GOOD)

    payload = review_plan(PLAN, complete=complete)
    assert len(calls) == 2
    assert "not word for word" in calls[1]
    # The row is reduced to its first cell, so the events officer sees the wording, not the pipes.
    assert payload["tasks"][0]["quote"] == "Book the hall"


def test_an_unusable_reply_raises_after_three_attempts():
    calls = []

    def complete(system, user, timeout):
        calls.append(user)
        return "not json at all"

    with pytest.raises(provider.ModelError):
        review_plan(PLAN, complete=complete)
    assert len(calls) == 3


def test_unknown_dependency_is_rejected():
    bad = json.loads(json.dumps(GOOD))
    bad["tasks"][1]["depends_on"] = ["T9"]
    calls = []

    def complete(system, user, timeout):
        calls.append(user)
        return reply(bad if len(calls) == 1 else GOOD)

    review_plan(PLAN, complete=complete)
    assert "T9, which is not a task" in calls[1]


def test_structural_findings_are_added_by_code():
    payload = review_plan(PLAN, complete=lambda system, user, timeout: reply(GOOD))
    kinds = {finding["kind"] for finding in payload["findings"]}
    assert "bottleneck" in kinds


def test_quote_errors_ignore_whitespace_differences():
    review = Review.model_validate(
        {
            "deadline_day": 21,
            "tasks": [
                {
                    "id": "T1",
                    "name": "Book the hall",
                    "owner": "Joan",
                    "duration_days": 2,
                    "depends_on": [],
                    "quote": "Book the hall   Joan",
                }
            ],
            "findings": [],
        }
    )
    _, problems = check_review(review, "Book the hall Joan")
    assert problems == []


def test_analyse_reports_a_cycle():
    tasks = [
        {"id": "A", "name": "a", "owner": None, "duration_days": 1, "depends_on": ["B"]},
        {"id": "B", "name": "b", "owner": None, "duration_days": 1, "depends_on": ["A"]},
    ]
    result = analyse(tasks, 10)
    assert result["schedule"]["status"] == "cycle"
    assert any(finding["kind"] == "cycle" for finding in result["findings"])


def test_analyse_flags_a_missing_deadline():
    tasks = [{"id": "A", "name": "a", "owner": "Ann", "duration_days": 1, "depends_on": []}]
    result = analyse(tasks, None)
    assert any(finding["kind"] == "deadline" for finding in result["findings"])


def copy(payload):
    return json.loads(json.dumps(payload))


def test_an_empty_quote_is_rejected():
    bad = copy(GOOD)
    bad["tasks"][0]["quote"] = "   "
    _, problems = check_review(Review.model_validate(bad), PLAN)
    assert any("T1 quote" in problem for problem in problems)


def test_a_provider_failure_uses_an_attempt_then_recovers():
    calls = []

    def complete(system, user, timeout):
        calls.append(user)
        if len(calls) == 1:
            raise provider.ModelError("provider returned HTTP 429")
        return reply(GOOD)

    payload = review_plan(PLAN, complete=complete)
    assert len(calls) == 2
    assert "rejected" not in calls[1]
    assert payload["tasks"]


def test_a_bad_dependency_quote_is_dropped_not_retried():
    noisy = copy(GOOD)
    noisy["tasks"][1]["dependency_quotes"] = {"T1": "invented words", "T9": "Book the hall"}
    calls = []

    def complete(system, user, timeout):
        calls.append(user)
        return reply(noisy)

    payload = review_plan(PLAN, complete=complete)
    assert len(calls) == 1
    assert payload["tasks"][1]["dependency_quotes"] == {}


def test_placeholder_owner_becomes_null():
    noisy = copy(GOOD)
    noisy["tasks"][0]["owner"] = "tbc"
    review = Review.model_validate(noisy)
    assert review.tasks[0].owner is None


def test_model_findings_cannot_claim_a_structural_kind():
    noisy = copy(GOOD)
    noisy["findings"] = [{"title": "Late", "detail": "x", "quote": None, "kind": "late"}]
    payload = review_plan(PLAN, complete=lambda system, user, timeout: reply(noisy))
    assert not any(finding["title"] == "Late" for finding in payload["findings"])


def test_command_writes_nothing_and_fails_when_no_review(tmp_path, monkeypatch):
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN)
    out = tmp_path / "out.json"
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(review_module.provider, "complete", lambda system, user, timeout: "nope")
    assert main(["review", str(plan), str(out)]) == 1
    assert not out.exists()


def test_command_writes_a_contract_shaped_review(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from check_contract import validate_review_shape

    plan = tmp_path / "plan.md"
    plan.write_text(PLAN)
    out = tmp_path / "out.json"
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(review_module.provider, "complete", lambda system, user, timeout: reply(GOOD))
    assert main(["review", str(plan), str(out)]) == 0
    assert validate_review_shape(json.loads(out.read_text())) == []


def test_env_file_fills_missing_variables_only(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nexport OPENROUTER_API_KEY='from-file'\nREVIEW_MODEL=\nOTHER=x\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OTHER", "from-shell")
    monkeypatch.delenv("REVIEW_MODEL", raising=False)
    review_module.load_env(env)
    import os
    assert os.environ["OPENROUTER_API_KEY"] == "from-file"
    assert os.environ["OTHER"] == "from-shell"
    assert "REVIEW_MODEL" not in os.environ


def test_a_quoted_table_row_is_reduced_to_its_cell():
    review = Review.model_validate(
        {
            "deadline_day": 21,
            "tasks": [
                {
                    "id": "T1",
                    "name": "Book the hall",
                    "owner": "Joan",
                    "duration_days": 2,
                    "depends_on": [],
                    "quote": "| Book the hall | Joan | 2 | |",
                }
            ],
            "findings": [],
        }
    )
    tidied, problems = check_review(review, "| Book the hall | Joan | 2 | |")
    assert problems == []
    assert tidied.tasks[0].quote == "Book the hall"


def test_two_model_findings_with_one_title_become_one():
    review = Review.model_validate(
        {
            "deadline_day": None,
            "tasks": [
                {
                    "id": "T1",
                    "name": "a",
                    "owner": "Ann",
                    "duration_days": 1,
                    "depends_on": [],
                    "quote": "a",
                }
            ],
            "findings": [
                {"title": "Conflicting dates", "detail": "Two dates.", "quote": "first", "severity": "blocks_date"},
                {"title": "Conflicting dates", "detail": "Two dates.", "quote": "second", "severity": "blocks_date"},
            ],
        }
    )
    payload, _ = review_with_findings(review)
    conflicts = [f for f in payload["findings"] if f["title"] == "Conflicting dates"]
    assert len(conflicts) == 1
    assert "second" in conflicts[0]["detail"]
