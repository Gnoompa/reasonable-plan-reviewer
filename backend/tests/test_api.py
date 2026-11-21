import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


def test_plans_are_listed():
    response = TestClient(app).get("/api/plans")
    assert response.status_code == 200
    assert {plan["id"] for plan in response.json()} == {"spring-market", "lantern-festival", "harbour-run"}


def client():
    return TestClient(app)


def test_unknown_plan_is_404():
    assert client().get("/api/plans/does-not-exist").status_code == 404


def test_plan_id_cannot_escape_the_data_directory():
    response = client().get("/api/plans/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 404


def test_plan_detail_has_a_schedule():
    response = client().get("/api/plans/spring-market")
    assert response.status_code == 200
    body = response.json()
    assert body["review"]["tasks"]
    assert body["schedule"]["status"] == "scheduled"


def test_schedule_endpoint_returns_a_contract_response():
    request = {
        "tasks": [
            {"id": "A", "name": "a", "owner": "Ann", "duration_days": 3, "depends_on": []},
            {"id": "B", "name": "b", "owner": "Ann", "duration_days": 2, "depends_on": []},
        ],
        "deadline_day": 10,
    }
    response = client().post("/api/schedule", json=request)
    assert response.status_code == 200
    body = response.json()
    assert body["end_day"] == 5
    assert body["meets_deadline"] is True


def test_duplicate_ids_are_rejected():
    request = {
        "tasks": [
            {"id": "A", "name": "a", "owner": None, "duration_days": 1, "depends_on": []},
            {"id": "A", "name": "b", "owner": None, "duration_days": 1, "depends_on": []},
        ],
        "deadline_day": 10,
    }
    assert client().post("/api/schedule", json=request).status_code == 422


def test_unknown_dependency_is_rejected():
    request = {
        "tasks": [{"id": "A", "name": "a", "owner": None, "duration_days": 1, "depends_on": ["Z"]}],
        "deadline_day": 10,
    }
    assert client().post("/api/schedule", json=request).status_code == 422


def test_zero_duration_is_rejected():
    request = {
        "tasks": [{"id": "A", "name": "a", "owner": None, "duration_days": 0, "depends_on": []}],
        "deadline_day": 10,
    }
    assert client().post("/api/schedule", json=request).status_code == 422


def test_more_than_sixty_tasks_is_rejected():
    tasks = [
        {"id": f"T{index}", "name": "t", "owner": None, "duration_days": 1, "depends_on": []}
        for index in range(61)
    ]
    assert client().post("/api/schedule", json={"tasks": tasks, "deadline_day": 10}).status_code == 422


def test_missing_body_is_rejected():
    assert client().post("/api/schedule", json={}).status_code == 422


def test_cycle_request_returns_200_with_cycle():
    request = {
        "tasks": [
            {"id": "A", "name": "a", "owner": None, "duration_days": 1, "depends_on": ["B"]},
            {"id": "B", "name": "b", "owner": None, "duration_days": 1, "depends_on": ["A"]},
        ],
        "deadline_day": 10,
    }
    response = client().post("/api/schedule", json=request)
    assert response.status_code == 200
    assert response.json()["status"] == "cycle"


def test_analyze_recomputes_after_an_edit():
    request = {
        "tasks": [
            {"id": "A", "name": "a", "owner": "Ann", "duration_days": 4, "depends_on": []},
            {"id": "B", "name": "b", "owner": "Ann", "duration_days": 4, "depends_on": []},
        ],
        "deadline_day": 5,
    }
    response = client().post("/api/analyze", json=request)
    assert response.status_code == 200
    body = response.json()
    assert body["schedule"]["end_day"] == 8
    assert body["schedule"]["meets_deadline"] is False
    assert any(finding["kind"] == "late" for finding in body["findings"])


def test_schedule_rejects_non_integer_durations():
    for value in ("3", True, 2.5):
        request = {
            "tasks": [{"id": "A", "name": "a", "owner": None, "duration_days": value, "depends_on": []}],
            "deadline_day": 10,
        }
        assert client().post("/api/schedule", json=request).status_code == 422, value


def test_stored_cycle_is_reported_with_a_finding():
    body = client().get("/api/plans/harbour-run").json()
    assert body["schedule"]["status"] == "cycle"
    assert any(finding["kind"] == "cycle" for finding in body["review"]["findings"])
