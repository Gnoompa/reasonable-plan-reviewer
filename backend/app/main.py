"""FastAPI app: the contract endpoints, the analyze endpoint and the page."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .analysis import analyse, review_with_findings
from .schemas import AnalyseRequest, Review, ScheduleRequest, graph_problems
from .scheduler import schedule

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DIST = ROOT / "frontend" / "dist"

# A plan id is a file name, so it is restricted to the shape the data uses.
PLAN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

app = FastAPI()


def _plan_file(plan_id: str) -> Path:
    if not PLAN_ID.match(plan_id):
        raise HTTPException(status_code=404, detail=f"unknown plan {plan_id!r}")
    path = DATA / "plans" / f"{plan_id}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown plan {plan_id!r}")
    return path


def _review_file(plan_id: str) -> Path:
    path = DATA / "reviews" / f"{plan_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no review for {plan_id!r}")
    return path


@app.get("/api/plans")
def list_plans():
    plans = []
    for path in sorted((DATA / "plans").glob("*.md"), reverse=True):
        title = next(
            (
                line.lstrip("# ").strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("#")
            ),
            path.stem,
        )
        plans.append({"id": path.stem, "title": title})
    return plans


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    text = _plan_file(plan_id).read_text(encoding="utf-8")
    try:
        review = Review.model_validate(json.loads(_review_file(plan_id).read_text(encoding="utf-8")))
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=500, detail=f"stored review for {plan_id!r} is unreadable: {error}")
    problems = graph_problems(review.tasks)
    if problems:
        raise HTTPException(status_code=500, detail=f"stored review for {plan_id!r} is invalid: {'; '.join(problems)}")

    payload, result = review_with_findings(review)
    return {"id": plan_id, "text": text, "review": payload, "schedule": result}


@app.post("/api/schedule")
def post_schedule(request: ScheduleRequest):
    """The contract endpoint. Invalid input is rejected by the request model."""
    return schedule([task.model_dump() for task in request.tasks], request.deadline_day)


@app.post("/api/analyze")
def post_analyze(request: AnalyseRequest):
    """Recompute the schedule and the structural findings after an edit."""
    return analyse([task.model_dump() for task in request.tasks], request.deadline_day)


if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="page")
