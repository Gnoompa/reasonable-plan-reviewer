"""Correction snapshots: the events officer's edits, kept next to the data.

A correction is a layer on top of a stored review. `data/reviews/` is never
written to, so the review a plan was handed in with always stays available.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

CORRECTION_ID = re.compile(r"^\d{1,6}$")


class CorrectionError(RuntimeError):
    """The snapshot could not be read or written."""


def _directory(base: Path, plan_id: str) -> Path:
    path = base / "corrections" / plan_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _files(base: Path, plan_id: str) -> list[tuple[int, Path]]:
    directory = base / "corrections" / plan_id
    if not directory.is_dir():
        return []
    found = []
    for path in directory.glob(f"{plan_id}_correction_*.json"):
        match = re.fullmatch(rf"{re.escape(plan_id)}_correction_(\d+)\.json", path.name)
        if match:
            found.append((int(match.group(1)), path))
    return sorted(found)


def save(
    base: Path,
    plan_id: str,
    tasks: list[dict],
    deadline_day: int | None,
    dismissed: list[str],
    quotes: dict[str, str] | None = None,
) -> dict:
    """Write the next numbered snapshot and return its summary.

    Each task keeps the quote it had in the stored review, so a saved
    correction can still point back at the plan wording.
    """
    quotes = quotes or {}
    tasks = [{**task, "quote": quotes.get(task["id"], "")} for task in tasks]
    files = _files(base, plan_id)
    number = files[-1][0] + 1 if files else 1
    path = _directory(base, plan_id) / f"{plan_id}_correction_{number}.json"
    payload = {
        "id": number,
        "plan_id": plan_id,
        "saved_at": time.strftime("%Y-%m-%d %H:%M"),
        "deadline_day": deadline_day,
        "tasks": tasks,
        "dismissed": dismissed,
    }
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        raise CorrectionError(f"could not save the correction: {error}") from error
    return {"id": number, "saved_at": payload["saved_at"]}


def listing(base: Path, plan_id: str) -> list[dict]:
    """Newest first, so the page can offer the most recent snapshot."""
    summaries = []
    for number, path in reversed(_files(base, plan_id)):
        try:
            saved_at = json.loads(path.read_text(encoding="utf-8")).get("saved_at", "")
        except (OSError, ValueError):
            saved_at = ""
        summaries.append({"id": number, "saved_at": saved_at})
    return summaries


def load(base: Path, plan_id: str, correction_id: str) -> dict:
    if not CORRECTION_ID.match(correction_id):
        raise CorrectionError("no such correction")
    path = base / "corrections" / plan_id / f"{plan_id}_correction_{int(correction_id)}.json"
    if not path.is_file():
        raise CorrectionError("no such correction")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CorrectionError(f"could not read the correction: {error}") from error
