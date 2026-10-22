"""Capture one diagnostic turn through the real HTTP streaming execution path.

The command uses an explicit reader in a fresh CLI process. A reader override
and private content snapshots do not certify a consistent corpus/index release,
an unseen dataset or a controlled A/B experiment. No automatic grading occurs.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import tempfile
import uuid
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from time import perf_counter
from typing import Any

from app.modules.chat.execution import execute_turn
from app.modules.chat.observation import TurnObservation
from app.modules.chat.tools import current_kbs, use_read_view
from evals.agent_artifacts import (
    EvaluationPlan,
    RunRecord,
    file_sha256,
    load_dataset,
    load_json,
    validate_run_set,
    write_json,
    write_jsonl,
)
from evals.agent_schemas import (
    AgentDataset,
    ExecutionStatus,
    canonical_json,
    case_sha256,
    sha256_text,
)
from evals.agent_trace import audit_trace


def _prepare_output(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    private = output / "private"
    private.mkdir()
    return private


def _write_trace(path: Path, trace: dict[str, Any]) -> None:
    # Compact encoding follows the recorder's byte accounting; pretty-printing
    # large repeated histories can exceed the offline reader's size limit.
    path.write_bytes((canonical_json(trace) + "\n").encode("utf-8"))


async def capture_case(
    dataset: AgentDataset, plan: EvaluationPlan, *, case_id: str, arm_id: str,
    repeat_index: int, runtime: Any, output: Path, timeout_seconds: float = 120,
    config: Any = None, store: Any = None,
    observation: TurnObservation | None = None,
) -> RunRecord:
    """Persist the terminal even on cancellation, then propagate caller cancellation.

    Output must be new. All raw content remains under private/. Captures are
    diagnostic; formal comparisons require a separately frozen release contract.
    """
    validate_run_set(dataset, plan, [])
    if (arm_id, case_id, repeat_index) not in plan.slots():
        raise ValueError("capture is outside the experiment plan")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("invalid capture timeout")
    case = next(case for case in dataset.cases if case.id == case_id)
    private = await asyncio.to_thread(_prepare_output, output)
    recorder = observation if observation is not None else TurnObservation(
        uuid.uuid4().hex, max_bytes=8 * 1024 * 1024
    )
    status: ExecutionStatus = "error"
    answer = ""
    partial: list[str] = []
    cancelled = None
    started = perf_counter()
    token = current_kbs.set(case.input.kbs or None)
    try:
        with use_read_view(store) if store is not None else nullcontext():
            async with asyncio.timeout(timeout_seconds):
                async for event in execute_turn(
                    runtime, [message.model_dump() for message in case.input.messages],
                    config or {}, observation=recorder,
                ):
                    if event.get("type") == "chunk":
                        partial.append(event.get("text") or "")
                    elif event.get("type") == "answer":
                        answer = event.get("text") or ""
                        status = "completed"
    except TimeoutError:
        status = "timeout"
    except asyncio.CancelledError as exc:
        status = "cancelled"
        cancelled = exc
    except Exception:
        # The detailed exception may contain provider credentials or private text.
        status = "error"
    finally:
        current_kbs.reset(token)
    elapsed_ms = (perf_counter() - started) * 1000
    trace = recorder.bundle()
    artifact_status = "incomplete"
    try:
        audit_trace(trace)
        artifact_status = trace["artifact_status"]
    except ValueError:
        artifact_status = "incomplete"
    if status in {"timeout", "cancelled"}:
        # A cancelled await cannot prove that an underlying sync tool thread or
        # provider physical request has stopped. Keep that uncertainty visible.
        artifact_status = "incomplete"
    if status != "completed":
        answer = "".join(partial)
    run = RunRecord(
        experiment_id=plan.experiment_id, arm_id=arm_id, case_id=case_id,
        repeat_index=repeat_index, run_id=recorder.run_id,
        case_sha256=case_sha256(case), answer_sha256=sha256_text(answer), answer=answer,
        execution_status=status, artifact_status=artifact_status,
        output_kind="final" if status == "completed" else "partial", duration_ms=elapsed_ms,
    )
    write_json(private / "dataset.json", dataset.model_dump(mode="json"))
    write_json(private / "plan.json", plan.model_dump(mode="json"))
    write_jsonl(private / "runs.jsonl", [run.model_dump(mode="json")])
    _write_trace(private / "trace.json", trace)
    write_json(private / "manifest.json", {
        "capture_schema_version": 1,
        "scope": "diagnostic_capture_not_frozen_release_or_ab_certification",
        "reader_scope": "explicit_context_view" if store is not None else "configured_live_store",
        "observation_scope": "logical_invocation_boundaries_not_physical_attempts_or_tool_body",
        "persistence_included": False, "cost": None,
        "underlying_physical_work_stopped": None,
        "files": {name: file_sha256(private / name) for name in (
            "dataset.json", "plan.json", "runs.jsonl", "trace.json"
        )},
    })
    if cancelled is not None:
        raise cancelled
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "plan", "data-dir", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--arm-id", required=True)
    parser.add_argument("--repeat-index", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        # Model construction stays on the project's LiteLLM adapter. The new
        # process has no inherited global TreeStore cache from another arm.
        from app.core.config import get_settings
        from app.core.retrieval import TreeStore
        from app.modules.chat.runtime import build_chat_runtime

        dataset = load_dataset(args.dataset)
        plan = EvaluationPlan.model_validate(load_json(args.plan))
        settings = get_settings()
        settings.apply_litellm_env()
        # This CLI is a local diagnostic. Platform export belongs to a later,
        # separately configured integration; do not inherit tracing from .env.
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
        from langsmith import tracing_context

        with (
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as diagnostic_log,
            redirect_stdout(diagnostic_log), redirect_stderr(diagnostic_log),
            tracing_context(enabled=False),
        ):
            run = asyncio.run(capture_case(
                dataset, plan, case_id=args.case_id, arm_id=args.arm_id,
                repeat_index=args.repeat_index, runtime=build_chat_runtime(), output=args.output,
                timeout_seconds=args.timeout_seconds,
                config={"max_concurrency": settings.chat_tool_concurrency},
                store=TreeStore(args.data_dir),
            ))
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("capture cancelled", file=sys.stderr)
        return 130
    except Exception:
        print("capture input or artifact validation failed", file=sys.stderr)
        return 2
    print(f"execution_status={run.execution_status} artifact_status={run.artifact_status}")
    return 0 if run.execution_status == "completed" and run.artifact_status == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
