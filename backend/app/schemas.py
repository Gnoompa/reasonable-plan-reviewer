"""Shapes for the two contracts in docs/contracts.md."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

MAX_TASKS = 60
NO_OWNER = {"", "tbc", "tbd", "none", "nobody", "unknown", "-", "?", "n/a"}


def graph_problems(tasks: list[Task]) -> list[str]:
    """Duplicate ids and dependencies on ids that are not in the list."""
    problems = []
    ids = [task.id for task in tasks]
    duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    if duplicates:
        problems.append(f"duplicate task ids: {', '.join(duplicates)}")
    known = set(ids)
    for task in tasks:
        for dep in task.depends_on:
            if dep not in known:
                problems.append(f"task {task.id} depends on {dep}, which is not a task in the list")
    return problems


class Task(BaseModel):
    """A schedule request task. Strict, because the contract says integers."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    owner: str | None
    duration_days: StrictInt = Field(ge=1)
    depends_on: list[str]


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tasks: list[Task] = Field(max_length=MAX_TASKS)
    deadline_day: StrictInt

    @model_validator(mode="after")
    def _graph_is_valid(self):
        problems = graph_problems(self.tasks)
        if problems:
            raise ValueError("; ".join(problems))
        return self


class AnalyseRequest(BaseModel):
    """An edited task list. The deadline may be unknown."""

    model_config = ConfigDict(extra="ignore")

    tasks: list[Task] = Field(max_length=MAX_TASKS)
    deadline_day: StrictInt | None = None

    @model_validator(mode="after")
    def _graph_is_valid(self):
        problems = graph_problems(self.tasks)
        if problems:
            raise ValueError("; ".join(problems))
        return self


class CorrectionRequest(BaseModel):
    """The edited task list the events officer promotes to a snapshot."""

    model_config = ConfigDict(extra="ignore")

    tasks: list[Task] = Field(max_length=MAX_TASKS)
    deadline_day: StrictInt | None = None
    dismissed: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _graph_is_valid(self):
        problems = graph_problems(self.tasks)
        if problems:
            raise ValueError("; ".join(problems))
        return self


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
