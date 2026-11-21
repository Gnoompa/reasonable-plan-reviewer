"""FastAPI app: the contract endpoints, corrections and the page."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .analysis import analyse, review_with_findings
from .corrections import CorrectionError, listing as list_corrections, load as load_correction, save as save_correction
from .schemas import AnalyseRequest, CorrectionRequest, Review, ScheduleRequest, graph_problems
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


def _stored_review(plan_id: str) -> Review:
    path = DATA / "reviews" / f"{plan_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no review for {plan_id!r}")
    try:
        review = Review.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=500, detail=f"stored review for {plan_id!r} is unreadable: {error}")
    problems = graph_problems(review.tasks)
    if problems:
        raise HTTPException(status_code=500, detail=f"stored review for {plan_id!r} is invalid: {'; '.join(problems)}")
    return review


def _with_correction(review: Review, request: CorrectionRequest) -> Review:
    """The stored review, with the officer's tasks and deadline in place of the original."""
    quotes = {task.id: task.quote for task in review.tasks}
    tasks = [
        task.model_copy(update={"quote": quotes.get(task.id, "")}) for task in request.tasks
    ]
    return review.model_copy(update={"tasks": tasks, "deadline_day": request.deadline_day})


def _correction_payload(plan_id: str, request: CorrectionRequest) -> tuple[dict, dict]:
    review = _with_correction(_stored_review(plan_id), request)
    return review_with_findings(review, request.dismissed)


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
    payload, result = review_with_findings(_stored_review(plan_id))
    return {"id": plan_id, "text": text, "review": payload, "schedule": result}


@app.post("/api/schedule")
def post_schedule(request: ScheduleRequest):
    """The contract endpoint. Invalid input is rejected by the request model."""
    return schedule([task.model_dump() for task in request.tasks], request.deadline_day)


@app.post("/api/analyze")
def post_analyze(request: AnalyseRequest):
    """Recompute the schedule and the structural findings after an edit."""
    return analyse([task.model_dump() for task in request.tasks], request.deadline_day)


@app.get("/api/plans/{plan_id}/corrections")
def get_corrections(plan_id: str):
    _plan_file(plan_id)
    return list_corrections(DATA, plan_id)


@app.post("/api/plans/{plan_id}/corrections")
def post_correction(plan_id: str, request: CorrectionRequest):
    """Save the officer's edits, then return the recomputed review and schedule."""
    _plan_file(plan_id)
    payload, result = _correction_payload(plan_id, request)
    quotes = {task.id: task.quote for task in _stored_review(plan_id).tasks}
    try:
        summary = save_correction(
            DATA,
            plan_id,
            [task.model_dump() for task in request.tasks],
            request.deadline_day,
            request.dismissed,
            quotes,
        )
    except CorrectionError as error:
        raise HTTPException(status_code=500, detail=str(error))
    return {"correction": summary, "review": payload, "schedule": result}


@app.get("/api/plans/{plan_id}/corrections/{correction_id}")
def get_correction(plan_id: str, correction_id: str):
    _plan_file(plan_id)
    try:
        correction = load_correction(DATA, plan_id, correction_id)
    except CorrectionError as error:
        raise HTTPException(status_code=404, detail=str(error))
    payload, result = _correction_payload(
        plan_id,
        CorrectionRequest(
            tasks=correction["tasks"],
            deadline_day=correction.get("deadline_day"),
            dismissed=correction.get("dismissed", []),
        ),
    )
    return {"correction": correction, "review": payload, "schedule": result}


if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="page")
