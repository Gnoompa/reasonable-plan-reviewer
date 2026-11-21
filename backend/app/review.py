"""Review a plan with a model, check the answer, and write the review JSON.

Run as `./review <plan.md> <out.json>`. Writes nothing and exits non-zero when
no usable review could be produced, as docs/contracts.md requires.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from . import prompts, provider
from .analysis import check_review, review_with_findings
from .schemas import Review

TOTAL_BUDGET_SECONDS = 270.0
ATTEMPTS = 3
MAX_CALL_SECONDS = 90.0
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Read KEY=value lines into the environment. Variables already set win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value:
            os.environ.setdefault(key, value)


def review_plan(plan: str, complete=None) -> dict:
    """Ask the model for a review, retrying with the problems it must fix.

    A provider failure (timeout, rate limit) uses up an attempt but sends no
    feedback; a rejected answer sends back the exact problems found.
    """
    complete = complete or provider.complete
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    feedback: list[str] = []
    last_problem = "no attempt was made"

    for attempt in range(1, ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            last_problem = "ran out of time"
            break

        try:
            reply = complete(prompts.SYSTEM, prompts.build(plan, feedback), min(MAX_CALL_SECONDS, remaining))
            review = Review.model_validate(provider.parse_json(reply))
        except provider.ModelError as error:
            last_problem = str(error)
            _log(attempt, last_problem)
            continue
        except ValidationError as error:
            feedback = _describe(error)
            last_problem = "; ".join(feedback)
            _log(attempt, last_problem)
            continue

        review, problems = check_review(review, plan)
        if not problems:
            payload, _ = review_with_findings(review)
            return payload
        feedback = problems
        last_problem = "; ".join(problems[:4])
        _log(attempt, f"{len(problems)} problem(s): {last_problem}")

    raise provider.ModelError(f"no usable review after {ATTEMPTS} attempts. Last problem: {last_problem}")


def _describe(error: ValidationError) -> list[str]:
    problems = []
    for item in error.errors()[:8]:
        where = ".".join(str(step) for step in item["loc"]) or "reply"
        problems.append(f"{where}: {item['msg']}")
    return problems


def _log(attempt: int, message: str) -> None:
    print(f"attempt {attempt}/{ATTEMPTS} rejected: {message[:300]}", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: ./review <plan.md> <out.json>", file=sys.stderr)
        return 2

    load_env()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set. Put it in .env (see .env.example) or export it.", file=sys.stderr)
        return 1
    plan_path, out_path = Path(argv[1]), Path(argv[2])
    try:
        plan = plan_path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"cannot read plan: {error}", file=sys.stderr)
        return 1

    try:
        payload = review_plan(plan)
    except provider.ModelError as error:
        print(f"review failed: {error}", file=sys.stderr)
        return 1

    # Write next to the target and rename, so a crash never leaves half a file.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(out_path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(out_path)
    print(f"wrote {out_path}: {len(payload['tasks'])} tasks, {len(payload['findings'])} findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
