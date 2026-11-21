"""Shapes for the two contracts in docs/contracts.md."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

MAX_TASKS = 60
NO_OWNER = {"", "tbc", "tbd", "none", "nobody", "unknown", "-", "?", "n/a"}


def graph_problems(tasks: list[Task]) -> list[str]:
    """Duplicate ids and dependencies on ids that are not in the list."""
    problems = []
    counts = Counter(task.id for task in tasks)
    duplicates = sorted(task_id for task_id, count in counts.items() if count > 1)
    if duplicates:
        problems.append(f"duplicate task ids: {', '.join(duplicates)}")
    known = set(counts)
    for task in tasks:
        for dep in task.depends_on:
            if dep not in known:
                problems.append(f"task {task.id} depends on {dep}, which is not a task in the list")
    return problems


class Task(BaseModel):
    """A schedule request task. Strict, because the contract says integers.

    `quote` and `dependency_quotes` are optional provenance. The contract allows
    extra fields, and carrying them here lets a structural finding keep pointing
    at the plan wording after the officer edits the task list.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    owner: str | None
    duration_days: StrictInt = Field(ge=1)
    depends_on: list[str]
    quote: str = ""
    dependency_quotes: dict[str, str] = Field(default_factory=dict)


class _TaskList(BaseModel):
    """A request carrying a task list. The graph must be sound before scheduling."""

    model_config = ConfigDict(extra="ignore")

    tasks: list[Task] = Field(max_length=MAX_TASKS)
    deadline_day: StrictInt | None = None

    @model_validator(mode="after")
    def _graph_is_valid(self):
        problems = graph_problems(self.tasks)
        if problems:
            raise ValueError("; ".join(problems))
        return self


class ScheduleRequest(_TaskList):
    """The contract request. The deadline is required."""

    deadline_day: StrictInt


class AnalyseRequest(_TaskList):
    """An edited task list. The deadline may be unknown."""


class CorrectionRequest(_TaskList):
    """The edited task list the events officer promotes to a snapshot."""

    dismissed: list[str] = Field(default_factory=list)


class ReviewTask(BaseModel):
    """A task as the model writes it. Lenient where coercion is safe."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    owner: str | None = None
    duration_days: int = Field(ge=1)
    depends_on: list[str] = Field(default_factory=list)
    quote: str
    dependency_quotes: dict[str, str] = Field(default_factory=dict)

    @field_validator("owner", mode="before")
    @classmethod
    def _placeholder_owner_is_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.lower() in NO_OWNER:
                return None
        return value


Severity = Literal["blocks_date", "at_risk", "note"]


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    detail: str
    quote: str | None = None
    severity: Severity = "note"
    kind: str = "model"

    @field_validator("severity", mode="before")
    @classmethod
    def _unknown_severity_is_note(cls, value):
        return value if value in ("blocks_date", "at_risk", "note") else "note"


class Review(BaseModel):
    model_config = ConfigDict(extra="ignore")

    deadline_day: int | None = None
    tasks: list[ReviewTask] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
