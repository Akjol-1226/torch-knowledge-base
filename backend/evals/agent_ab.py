"""Small local coordinator for one frozen two-policy experiment.

Every primary slot runs once in a fresh interpreter using the copied Python
bundle. Local hashes detect changes relative to the supplied contract; they do
not authenticate a wholly replaced experiment or approve semantic judgments.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt

from evals.agent_artifacts import (
    EvaluationPlan,
    RunRecord,
    file_sha256,
    load_dataset,
    load_json,
    load_jsonl,
    parse_jsonl,
    read_bytes,
    strict_json,
    validate_run_set,
    write_json,
)
from evals.agent_experiment import (
    ExperimentBudget,
    ExperimentState,
    FrozenExperimentContract,
    advance_state,
    build_schedule,
    freeze_experiment,
    initial_state,
    paired_statistics,
    primary_run_id,
)
from evals.agent_schemas import (
    AgentDataset,
    NonBlankText,
    Sha256,
    StrictModel,
    canonical_json,
    canonical_sha256,
    sha256_text,
)
from evals.agent_snapshot import PROFILE, audit_release, load_release
from evals.agent_trace import audit_trace
from evals.scoring.agent_quality import BoundReview, CaseGrade, score_case

_INPUTS = ("dataset.json", "plan.json", "contract.json", "config.json", "schedule.json")
_PACKAGES = ("pydantic", "langchain-core", "langchain-openai", "openai", "bm25s", "jieba", "numpy")
_PROMPT = "app/modules/chat/prompts/system.md"
# Frozen corpus verification and receipt publication are outside capture time.
# Keep the same fixed setup allowance for both arms, without extending model time.
WORKER_GRACE_SECONDS = 120


class SharedConfig(StrictModel):
    """Only nonsecret, shared experiment inputs; credentials stay in child env."""

    model_route: NonBlankText
    endpoint_sha256: Sha256
    system_prompt: NonBlankText
    retrieval_top_n: StrictInt = Field(ge=1)
    warmup: Literal["none"] = "none"
    model_route_version_kind: Literal["rolling_alias"] = "rolling_alias"


class FrozenConfig(SharedConfig):
    source_bundle_sha256: Sha256


def _software() -> dict[str, str]:
    return {"python": sys.version, **{name: version(name) for name in _PACKAGES}}


def _decoded(raw: bytes) -> Any:
    return strict_json(raw.decode("utf-8"))


def _git_state(backend: Path) -> dict:
    executable = shutil.which("git")
    if executable is None:
        return {"available": False, "head": None, "dirty": None, "diff_sha256": None}

    def invoke(arguments: list[str]) -> bytes:
        result = subprocess.run(  # noqa: S603 - fixed read-only git arguments, no shell
            [executable, *arguments],
            cwd=backend,
            capture_output=True,
            check=True,
        )
        return result.stdout

    try:
        head = invoke(["rev-parse", "HEAD"]).decode("ascii").strip()
        status = invoke(["status", "--porcelain", "--untracked-files=normal"])
        diff = invoke(["diff", "--binary", "--", "app", "evals"])
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {"available": False, "head": None, "dirty": None, "diff_sha256": None}
    return {
        "available": True,
        "head": head,
        "dirty": bool(status),
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked_content_binding": "full_source_bundle_inventory",
    }


def _regular(path: Path) -> None:
    for item in (path, *path.parents):
        if item.exists():
            stat = item.lstat()
            if item.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                raise ValueError("links and reparse points are not allowed")


def _bundle_inventory(bundle: Path) -> dict[str, str]:
    _regular(bundle)
    result = {}
    for path in sorted(bundle.rglob("*")):
        _regular(path)
        if not path.is_file():
            continue
        name = path.relative_to(bundle).as_posix()
        allowed = (path.suffix == ".py" and name.startswith(("app/", "evals/"))) or name == _PROMPT
        if not allowed:
            raise ValueError("unexpected source bundle file")
        result[name] = file_sha256(path)
    if "evals/agent_ab.py" not in result or _PROMPT not in result:
        raise ValueError("source bundle is incomplete")
    return result


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(canonical_json(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _atomic_runs(path: Path, runs: list[RunRecord]) -> None:
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("x", encoding="utf-8") as stream:
        for run in runs:
            stream.write(canonical_json(run) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _slots(contract: FrozenExperimentContract) -> list[dict[str, Any]]:
    policies = {arm.arm_id: arm.grounding_repair_policy for arm in contract.arms}
    return [
        {
            "index": index,
            "case_id": pair.case_id,
            "repeat_index": pair.repeat_index,
            "arm_id": arm,
            "run_id": primary_run_id(contract, arm, pair.case_id, pair.repeat_index),
            "policy": policies[arm],
        }
        for index, (pair, arm) in enumerate(
            (pair, arm) for pair in build_schedule(contract) for arm in pair.arm_order
        )
    ]


def prepare_experiment(
    dataset_path: Path,
    release_root: Path,
    output: Path,
    *,
    shared_config: SharedConfig,
    budget: ExperimentBudget,
    experiment_id: str,
    seed: int = 0,
    repeats: int = 1,
    source_backend: Path | None = None,
) -> dict[str, Any]:
    """Copy one new private experiment; never copy env files, tests or documents as code."""
    source_backend = source_backend or Path(__file__).resolve().parents[1]
    for path in (source_backend, release_root, output):
        _regular(path)
    source_backend = source_backend.resolve(strict=True)
    release_root = release_root.resolve(strict=True)
    output = output.resolve()
    if (
        output == release_root
        or output.is_relative_to(release_root)
        or any(output.is_relative_to(source_backend / area) for area in ("app", "evals"))
    ):
        raise ValueError("experiment output cannot overlap frozen source inputs")
    shared_config = SharedConfig.model_validate(shared_config.model_dump(mode="python"))
    dataset = load_dataset(dataset_path)
    release = audit_release(release_root)
    if release["retrieval_top_n"] != shared_config.retrieval_top_n:
        raise ValueError("release and shared retrieval settings differ")
    _regular(output)
    if output.exists():
        raise ValueError("experiment output must be new")
    private = output / "private"
    private.mkdir(parents=True, exist_ok=False)
    bundle = private / "source_bundle"
    for area in ("app", "evals"):
        for path in sorted((source_backend / area).rglob("*.py")):
            _regular(path)
            relative = path.relative_to(source_backend)
            destination = bundle / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
    prompt = bundle / _PROMPT
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(shared_config.system_prompt, encoding="utf-8")
    code_files = _bundle_inventory(bundle)
    config = FrozenConfig(
        **shared_config.model_dump(mode="python"),
        source_bundle_sha256=canonical_sha256(code_files),
    )
    shutil.copytree(release_root, private / "release")
    copied_release = audit_release(private / "release")
    if copied_release != release:
        raise ValueError("release changed during preparation")
    plan = EvaluationPlan(
        experiment_id=experiment_id,
        dataset_sha256=canonical_sha256(dataset),
        arm_ids=["baseline", "candidate"],
        case_ids=[case.id for case in dataset.cases],
        repeats=repeats,
    )
    contract = freeze_experiment(
        dataset,
        plan,
        snapshot_sha256=release["release_id"],
        shared_config_sha256=canonical_sha256(config),
        budget=budget,
        seed=seed,
    )
    for name, value in (
        ("dataset.json", dataset.model_dump(mode="json")),
        ("plan.json", plan.model_dump(mode="json")),
        ("contract.json", contract.model_dump(mode="json")),
        ("config.json", config.model_dump(mode="json")),
        ("schedule.json", [pair.model_dump(mode="json") for pair in build_schedule(contract)]),
    ):
        write_json(private / name, value)
    manifest = {
        "experiment_bundle_version": 1,
        "retrieval_profile": PROFILE,
        "immutable_files": {name: file_sha256(private / name) for name in _INPUTS},
        "source_bundle_files": code_files,
        "software": _software(),
        "source_bundle_is_authentication": False,
        "worker_grace_seconds": WORKER_GRACE_SECONDS,
        "git_state_at_freeze": _git_state(source_backend),
    }
    write_json(private / "manifest.json", manifest)
    _atomic_json(private / "state.json", initial_state(contract))
    _atomic_runs(private / "runs.jsonl", [])
    _load_experiment(output)
    return manifest


def _load_experiment(
    root: Path,
) -> tuple[AgentDataset, EvaluationPlan, FrozenExperimentContract, FrozenConfig]:
    _regular(root)
    private = root / "private"
    _regular(private / "manifest.json")
    manifest = _decoded(read_bytes(private / "manifest.json"))
    if (
        type(manifest.get("experiment_bundle_version")) is not int
        or manifest["experiment_bundle_version"] != 1
        or manifest.get("retrieval_profile") != PROFILE
        or manifest.get("software") != _software()
        or manifest.get("source_bundle_is_authentication") is not False
        or type(manifest.get("worker_grace_seconds")) is not int
        or manifest["worker_grace_seconds"] != WORKER_GRACE_SECONDS
        or set(manifest.get("immutable_files", {})) != set(_INPUTS)
    ):
        raise ValueError("invalid frozen experiment manifest")
    buffers = {}
    for name in _INPUTS:
        _regular(private / name)
        buffers[name] = read_bytes(private / name)
        if hashlib.sha256(buffers[name]).hexdigest() != manifest["immutable_files"][name]:
            raise ValueError("frozen experiment input changed")
    inventory = _bundle_inventory(private / "source_bundle")
    if inventory != manifest.get("source_bundle_files"):
        raise ValueError("source bundle changed")
    live_backend = Path(__file__).resolve().parents[1]
    for name, expected_hash in inventory.items():
        if name.endswith(".py") and file_sha256(live_backend / name) != expected_hash:
            raise ValueError("running coordinator/auditor code differs from frozen bundle")
    dataset = AgentDataset.model_validate(_decoded(buffers["dataset.json"]))
    plan = EvaluationPlan.model_validate(_decoded(buffers["plan.json"]))
    contract = FrozenExperimentContract.model_validate(_decoded(buffers["contract.json"]))
    config = FrozenConfig.model_validate(_decoded(buffers["config.json"]))
    validate_run_set(dataset, plan, [])
    rebuilt = freeze_experiment(
        dataset,
        plan,
        snapshot_sha256=contract.snapshot_sha256,
        shared_config_sha256=canonical_sha256(config),
        budget=contract.budget,
        seed=contract.seed,
        bootstrap_replicates=contract.bootstrap_replicates,
    )
    if rebuilt != contract or config.source_bundle_sha256 != canonical_sha256(inventory):
        raise ValueError("experiment contract binding differs")
    if canonical_json(_decoded(buffers["schedule.json"])) != canonical_json(
        [pair.model_dump(mode="json") for pair in build_schedule(contract)]
    ):
        raise ValueError("frozen schedule differs")
    release = audit_release(private / "release")
    if (
        release["release_id"] != contract.snapshot_sha256
        or release["retrieval_top_n"] != config.retrieval_top_n
    ):
        raise ValueError("frozen release differs")
    return dataset, plan, contract, config


def _environment(env_file: Path, config: FrozenConfig, bundle: Path) -> dict[str, str]:
    from app.core.config import Settings

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    endpoint = settings.litellm_base_url
    key = settings.litellm_api_key.get_secret_value()
    if (
        not key
        or sha256_text(endpoint) != config.endpoint_sha256
        or (settings.chat_model != config.model_route)
    ):
        raise ValueError("runtime credentials or route differ from frozen configuration")
    allowed = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
    )
    result = {name: os.environ[name] for name in allowed if name in os.environ}
    result.update(
        {
            "PYTHONPATH": str(bundle),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
            "LITELLM_BASE_URL": endpoint,
            "LITELLM_API_KEY": key,
            "CHAT_MODEL": config.model_route,
        }
    )
    return result


@contextmanager
def _coordinator_lock(private: Path):
    lock = private / "coordinator.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def _capture_path(root: Path, index: int) -> Path:
    if type(index) is not int or index < 0:
        raise ValueError("invalid slot index")
    return root / "private" / "captures" / f"{index:06d}"


def _state(root: Path, contract: FrozenExperimentContract) -> ExperimentState:
    path = root / "private/state.json"
    _regular(path)
    state = ExperimentState.model_validate(_decoded(read_bytes(path)))
    planned = {slot["run_id"] for slot in _slots(contract)}
    if (
        state.contract_sha256 != canonical_sha256(contract)
        or not set(state.started_run_ids) <= planned
        or len(state.started_run_ids) > contract.budget.max_started_runs
        or (state.experiment_status == "complete" and set(state.terminal_run_ids) != planned)
    ):
        raise ValueError("state differs from the frozen schedule or budget")
    return state


def _capture(
    root: Path,
    slot: dict[str, Any],
    dataset: AgentDataset,
    plan: EvaluationPlan,
    contract: FrozenExperimentContract,
    config: FrozenConfig,
) -> tuple[RunRecord, dict]:
    directory = _capture_path(root, slot["index"])
    private = directory / "private"
    _regular(directory)
    _regular(directory / "worker-receipt.json")
    receipt = _decoded(read_bytes(directory / "worker-receipt.json"))
    expected = ("dataset.json", "plan.json", "runs.jsonl", "trace.json")
    if (
        receipt.get("run_id") != slot["run_id"]
        or receipt.get("input_manifest_sha256") != (file_sha256(root / "private/manifest.json"))
        or receipt.get("verified_before_and_after") is not True
    ):
        raise ValueError("worker did not bind its frozen inputs")
    if set(receipt.get("files", {})) != set(expected):
        raise ValueError("invalid worker artifact inventory")
    buffers = {}
    for name in expected:
        _regular(private / name)
        buffers[name] = read_bytes(private / name)
        if hashlib.sha256(buffers[name]).hexdigest() != receipt["files"][name]:
            raise ValueError("capture artifact changed")
    if AgentDataset.model_validate(_decoded(buffers["dataset.json"])) != dataset or (
        EvaluationPlan.model_validate(_decoded(buffers["plan.json"])) != plan
    ):
        raise ValueError("capture input differs from experiment")
    rows = parse_jsonl(buffers["runs.jsonl"])
    if len(rows) != 1:
        raise ValueError("primary capture must contain exactly one run")
    run = RunRecord.model_validate(rows[0])
    validate_run_set(dataset, plan, [run])
    if (
        run.run_id != slot["run_id"]
        or run.slot() != (slot["arm_id"], slot["case_id"], slot["repeat_index"])
        or run.execution_status == "not_started"
    ):
        raise ValueError("capture identity differs from scheduled primary")
    trace = _decoded(buffers["trace.json"])
    if trace.get("run_id") != run.run_id:
        raise ValueError("trace belongs to another run")
    expected_runtime = {
        "max_agent_rounds": contract.budget.max_agent_rounds,
        "max_tool_concurrency": contract.budget.max_tool_concurrency,
        "model_route": config.model_route,
        "configured_max_retries": contract.budget.sdk_max_retries,
        "configured_max_output_tokens": contract.budget.max_output_tokens,
        "configured_timeout_seconds": contract.budget.model_timeout_seconds,
    }
    for event in trace.get("events", []):
        if event.get("kind") != "runtime_config":
            continue
        data = event["data"]
        for key, expected_value in expected_runtime.items():
            if key not in data and run.artifact_status != "complete":
                continue
            value = data.get(key)
            if isinstance(expected_value, float):
                matches = type(value) in (int, float) and value == expected_value
            else:
                matches = type(value) is type(expected_value) and value == expected_value
            if not matches:
                raise ValueError("observed runtime differs from frozen budget/config")
        if type(data.get("temperature")) not in (int, float) or data["temperature"] != 0:
            raise ValueError("observed temperature differs from frozen zero setting")
        snapshots = trace.get("snapshots", {})
        if (run.artifact_status == "complete" or data.get("prompt_snapshot") in snapshots) and (
            snapshots.get(data.get("prompt_snapshot")) != config.system_prompt
        ):
            raise ValueError("observed prompt differs from frozen shared prompt")
        if run.artifact_status == "complete" or data.get("tool_schema_snapshot") in snapshots:
            from langchain_core.utils.function_calling import convert_to_openai_tool

            from app.modules.chat.tools import KB_TOOLS

            if canonical_json(snapshots.get(data.get("tool_schema_snapshot"))) != canonical_json(
                [convert_to_openai_tool(tool) for tool in KB_TOOLS]
            ):
                raise ValueError("observed tools differ from frozen runtime tool set")
    try:
        audited = audit_trace(trace)
    except ValueError:
        if run.artifact_status == "complete":
            raise
    else:
        if run.artifact_status == "complete" and (
            trace.get("artifact_status") != "complete"
            or audited["execution_status"] != run.execution_status
        ):
            raise ValueError("trace terminal differs from run")
    if run.execution_status == "completed" and run.artifact_status == "complete":
        finals = [
            event for event in trace.get("events", []) if event["kind"] == "turn_final_answer"
        ]
        if (
            len(finals) != 1
            or trace["snapshots"].get(finals[0]["data"]["answer_snapshot"], {}).get("text")
            != run.answer
        ):
            raise ValueError("answer differs from trace projection")
    events = trace.get("events") or []
    started = events[0] if events else {}
    if run.artifact_status == "complete" and started.get("kind") != "turn_started":
        raise ValueError("complete trace lacks its policy boundary")
    if (
        started.get("kind") == "turn_started"
        and started.get("data", {}).get("grounding_repair_policy") != slot["policy"]
    ):
        raise ValueError("trace repair policy differs")
    return run, trace


def _launch_worker(root: Path, index: int, env: dict[str, str], timeout: float) -> None:
    log = root / "private/logs" / f"{index:06d}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("xb") as stream:
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter/module; no shell or manifest command
            [
                sys.executable,
                "-P",
                "-m",
                "evals.agent_ab",
                "_worker",
                "--root",
                str(root),
                "--slot-index",
                str(index),
            ],
            cwd=root,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            raise


def run_experiment(
    root: Path, *, env_file: Path, max_wall_seconds: float | None = None
) -> ExperimentState:
    root = root.resolve(strict=True)
    dataset, plan, contract, config = _load_experiment(root)
    if max_wall_seconds is not None and (
        not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0
    ):
        raise ValueError("invalid early-stop wall time")
    private = root / "private"
    deadline = time.monotonic() + max_wall_seconds if max_wall_seconds is not None else math.inf
    env = _environment(env_file, config, private / "source_bundle")
    with _coordinator_lock(private):
        state = _state(root, contract)
        if state.experiment_status in {"complete", "interrupted", "invalid"}:
            return state
        runs = []
        stop_reason = None
        for slot in _slots(contract):
            identity = slot["run_id"]
            if identity in state.started_run_ids:
                # Never rerun a previously started primary, even after a crash.
                try:
                    run, _ = _capture(root, slot, dataset, plan, contract, config)
                except (OSError, ValueError):
                    continue
                runs.append(run)
                if identity not in state.terminal_run_ids:
                    state = advance_state(contract, state, terminal_run_ids=[identity])
                continue
            if len(state.started_run_ids) >= contract.budget.max_started_runs:
                stop_reason = "budget_exhausted"
                break
            if time.monotonic() >= deadline:
                stop_reason = "caller_interrupted"
                break
            if _capture_path(root, slot["index"]).exists():
                raise ValueError("an unstarted primary already has capture artifacts")
            state = advance_state(contract, state, started_run_ids=[identity])
            _atomic_json(private / "state.json", state)
            try:
                _launch_worker(
                    root,
                    slot["index"],
                    env,
                    min(
                        contract.budget.run_timeout_seconds + WORKER_GRACE_SECONDS,
                        deadline - time.monotonic(),
                    ),
                )
                run, _ = _capture(root, slot, dataset, plan, contract, config)
            except KeyboardInterrupt:
                stop_reason = "caller_interrupted"
                break
            except (OSError, ValueError, subprocess.SubprocessError):
                # A missing terminal remains missing; never synthesize completion.
                stop_reason = "worker_error"
                break
            runs.append(run)
            state = advance_state(contract, state, terminal_run_ids=[identity])
            _atomic_json(private / "state.json", state)
            _atomic_runs(private / "runs.jsonl", runs)
        state = advance_state(contract, state, finalize=True, stop_reason=stop_reason)
        _atomic_json(private / "state.json", state)
        _atomic_runs(private / "runs.jsonl", runs)
    return state


def _worker_inputs(root: Path, index: int):
    dataset, plan, contract, config = _load_experiment(root)
    expected_bundle = (root / "private/source_bundle").resolve(strict=True)
    if not Path(__file__).resolve().is_relative_to(expected_bundle):
        raise ValueError("worker is not executing the frozen source bundle")
    slots = _slots(contract)
    if type(index) is not int or not 0 <= index < len(slots):
        raise ValueError("worker slot is outside schedule")
    return dataset, plan, contract, config, expected_bundle, slots[index]


async def _worker(root: Path, index: int, *, verify_only: bool = False) -> None:
    dataset, plan, contract, config, expected_bundle, slot = await asyncio.to_thread(
        _worker_inputs, root, index
    )
    before_manifest = file_sha256(root / "private/manifest.json")
    from pydantic import SecretStr

    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    if sha256_text(settings.litellm_base_url) != config.endpoint_sha256 or (
        settings.chat_model != config.model_route
    ):
        raise ValueError("worker endpoint or model differs")
    settings.litellm_api_key = SecretStr(os.environ.get("LITELLM_API_KEY", ""))
    settings.data_dir = root / "private/release/data"
    settings.hybrid_enabled = False
    settings.retrieval_top_n = config.retrieval_top_n
    settings.chat_rate_limit_rps = 0
    settings.langsmith_tracing = False
    settings.chat_system_prompt_path = str(expected_bundle / _PROMPT)
    if verify_only:
        return
    state = await asyncio.to_thread(_state, root, contract)
    if slot["run_id"] not in state.started_run_ids or slot["run_id"] in state.terminal_run_ids:
        raise ValueError("worker slot was not exclusively started by its coordinator")
    from langsmith import tracing_context

    from app.modules.chat.model import build_chat_model
    from app.modules.chat.native_runtime import NativeReactRuntime
    from app.modules.chat.observation import TurnObservation
    from app.modules.chat.tools import KB_TOOLS
    from evals.agent_capture import capture_case

    view = load_release(root / "private/release")
    runtime = NativeReactRuntime(
        build_chat_model(
            timeout_seconds=contract.budget.model_timeout_seconds,
            max_retries=contract.budget.sdk_max_retries,
            max_output_tokens=contract.budget.max_output_tokens,
        ),
        KB_TOOLS,
        config.system_prompt,
        max_agent_rounds=contract.budget.max_agent_rounds,
        max_tool_concurrency=contract.budget.max_tool_concurrency,
    )
    output = _capture_path(root, index)
    with tracing_context(enabled=False):
        await capture_case(
            dataset,
            plan,
            case_id=slot["case_id"],
            arm_id=slot["arm_id"],
            repeat_index=slot["repeat_index"],
            runtime=runtime,
            output=output,
            timeout_seconds=contract.budget.run_timeout_seconds,
            config={"max_concurrency": contract.budget.max_tool_concurrency},
            store=view,
            observation=TurnObservation(slot["run_id"], max_bytes=8 * 1024 * 1024),
            grounding_repair_policy=slot["policy"],
        )
    await asyncio.to_thread(_load_experiment, root)
    if file_sha256(root / "private/manifest.json") != before_manifest:
        raise ValueError("experiment inputs changed during worker execution")
    capture_private = output / "private"
    write_json(
        output / "worker-receipt.json",
        {
            "run_id": slot["run_id"],
            "verified_before_and_after": True,
            "input_manifest_sha256": file_sha256(root / "private/manifest.json"),
            "files": {
                name: file_sha256(capture_private / name)
                for name in ("dataset.json", "plan.json", "runs.jsonl", "trace.json")
            },
        },
    )
    _capture(root, slot, dataset, plan, contract, config)


def _quantiles(values: list[float]) -> dict[str, float | int | None]:
    def percentile(q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * q
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return {"count": len(values), "p50": percentile(0.5), "p95": percentile(0.95)}


def _diagnostics(
    runs: list[RunRecord], traces: dict[str, dict], grades: list[CaseGrade], planned: int
) -> dict:
    complete_traces = [traces[r.run_id] for r in runs if r.artifact_status == "complete"]
    events = [event for trace in complete_traces for event in trace["events"]]
    by_kind = Counter(event["kind"] for event in events)
    durations = {
        kind: [
            event["data"]["invoke_wall_ms"]
            for event in events
            if event["kind"] == kind and type(event["data"].get("invoke_wall_ms")) in (int, float)
        ]
        for kind in ("model_finished", "tool_finished")
    }
    strict_ids = {grade.run_id for grade in grades if grade.strict_status == "pass"}
    usage = [event["data"].get("usage") for event in events if event["kind"] == "model_finished"]
    usage_keys = ("input_tokens", "output_tokens", "total_tokens")
    arm_ids = {run.run_id for run in runs}
    arm_grades = [grade for grade in grades if grade.run_id in arm_ids]
    decided = sum(grade.strict_status != "pending" for grade in arm_grades)
    valid_reviews = sum(grade.review_status == "valid" for grade in arm_grades)
    review_counts = Counter(grade.review_status for grade in arm_grades)
    repair_starts, repair_durations = {}, []
    for event in events:
        passage = event["data"].get("repair_pass")
        if event["kind"] == "grounding_repair_started":
            repair_starts[passage] = event["elapsed_ms"]
        elif event["kind"] == "grounding_repair_finished" and passage in repair_starts:
            repair_durations.append(event["elapsed_ms"] - repair_starts.pop(passage))
    return {
        "planned_runs": planned,
        "terminal_runs": len(runs),
        "missing_runs": planned - len(runs),
        "terminal_coverage": len(runs) / planned,
        "execution_status_counts": dict(Counter(run.execution_status for run in runs)),
        "complete_trace_count": len(complete_traces),
        "complete_trace_coverage": len(complete_traces) / planned,
        "terminal_observed_elapsed_ms": _quantiles(
            [r.duration_ms for r in runs if r.duration_ms is not None]
        ),
        "completed_execution_elapsed_ms": _quantiles(
            [
                r.duration_ms
                for r in runs
                if r.execution_status == "completed" and r.duration_ms is not None
            ]
        ),
        "strict_success_elapsed_ms": _quantiles(
            [r.duration_ms for r in runs if r.run_id in strict_ids and r.duration_ms is not None]
        ),
        "model_invoke_wall_ms": _quantiles(durations["model_finished"]),
        "tool_invoke_wall_ms": _quantiles(durations["tool_finished"]),
        "grounding_repair_preparation_wall_ms": _quantiles(repair_durations),
        "quality_review_valid_runs": valid_reviews,
        "review_status_counts": {
            name: review_counts[name] for name in ("valid", "missing", "invalid")
        },
        "quality_review_coverage": valid_reviews / planned,
        "strict_quality_decided_runs": decided,
        "strict_task_success_rate_all_planned": (
            sum(g.strict_status == "pass" for g in arm_grades) / planned
            if decided == planned
            else None
        ),
        "model_calls_in_complete_traces": by_kind["model_finished"],
        "tool_calls_in_complete_traces": by_kind["tool_finished"],
        "server_autofix_reads_in_complete_traces": by_kind["server_autofix_read"],
        "correction_input_reads_in_complete_traces": by_kind["correction_input_evidence"],
        "usage_observed_calls": sum(isinstance(u, dict) for u in usage),
        "usage_total_calls": len(usage),
        "usage_key_observed_counts": {
            key: sum(isinstance(u, dict) and type(u.get(key)) is int and u[key] >= 0 for u in usage)
            for key in usage_keys
        },
        "usage_observed_subtotals": {
            key: sum(
                u[key]
                for u in usage
                if isinstance(u, dict) and type(u.get(key)) is int and u[key] >= 0
            )
            if any(isinstance(u, dict) and type(u.get(key)) is int and u[key] >= 0 for u in usage)
            else None
            for key in usage_keys
        },
        "cost": None,
        "underlying_physical_work_stopped": None,
    }


def audit_experiment(root: Path, *, reviews_path: Path | None = None) -> dict[str, Any]:
    dataset, plan, contract, config = _load_experiment(root)
    state = _state(root, contract)
    reviews = {}
    if reviews_path is not None:
        for row in load_jsonl(reviews_path):
            review = BoundReview.model_validate(row)
            if review.run_id in reviews:
                raise ValueError("duplicate run review")
            reviews[review.run_id] = review
    cases = {case.id: case for case in dataset.cases}
    runs, grades, traces = [], [], {}
    for slot in _slots(contract):
        if not _capture_path(root, slot["index"]).exists():
            continue
        try:
            run, trace = _capture(root, slot, dataset, plan, contract, config)
        except (OSError, ValueError):
            receipt_path = _capture_path(root, slot["index"]) / "worker-receipt.json"
            if receipt_path.exists():
                _regular(receipt_path)
                raise
            if slot["run_id"] in state.terminal_run_ids:
                raise ValueError("recorded terminal lacks its sealed primary artifacts") from None
            # An interrupted worker may leave partial files, never a synthetic terminal.
            continue
        if run.run_id not in state.started_run_ids:
            raise ValueError("capture has no recorded start")
        runs.append(run)
        traces[run.run_id] = trace
        grades.append(
            score_case(
                cases[run.case_id],
                run.answer,
                dataset_sha256=canonical_sha256(dataset),
                run_id=run.run_id,
                review=reviews.pop(run.run_id, None),
                execution_status=run.execution_status,
                artifact_status=run.artifact_status,
            )
        )
    if reviews or not set(state.terminal_run_ids) <= {r.run_id for r in runs}:
        raise ValueError("review or terminal state lacks its scheduled capture")
    if state.experiment_status in {"complete", "interrupted"}:
        expected = [run.model_dump(mode="json") for run in runs]
        if canonical_json(load_jsonl(root / "private/runs.jsonl")) != canonical_json(expected):
            raise ValueError("aggregate JSONL differs from primary captures")
    result = paired_statistics(contract, runs, grades)
    result["state"] = state.model_dump(mode="json")
    result["diagnostics"] = {
        arm.role: _diagnostics(
            [run for run in runs if run.arm_id == arm.arm_id],
            traces,
            grades,
            len(contract.cases) * contract.repeats,
        )
        for arm in contract.arms
    }
    result["retrieval_profile"] = PROFILE
    result["cost"] = None
    result["automatic_production_promotion"] = False
    return result


def public_report(audit: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "planned_pair_count",
        "comparable_pair_count",
        "unresolved_pair_count",
        "pair_decision_coverage",
        "planned_case_count",
        "document_group_count",
        "primary_delta",
        "conservative_delta_bounds",
        "document_cluster_bootstrap_interval",
        "interval_unavailable_reason",
        "few_document_groups",
        "promotion_decision",
        "retrieval_profile",
        "cost",
        "automatic_production_promotion",
    )
    diagnostic_keys = (
        "planned_runs",
        "terminal_runs",
        "missing_runs",
        "terminal_coverage",
        "execution_status_counts",
        "complete_trace_count",
        "complete_trace_coverage",
        "terminal_observed_elapsed_ms",
        "completed_execution_elapsed_ms",
        "strict_success_elapsed_ms",
        "model_invoke_wall_ms",
        "tool_invoke_wall_ms",
        "grounding_repair_preparation_wall_ms",
        "quality_review_valid_runs",
        "quality_review_coverage",
        "strict_quality_decided_runs",
        "strict_task_success_rate_all_planned",
        "review_status_counts",
        "model_calls_in_complete_traces",
        "tool_calls_in_complete_traces",
        "server_autofix_reads_in_complete_traces",
        "correction_input_reads_in_complete_traces",
        "usage_observed_calls",
        "usage_total_calls",
        "usage_key_observed_counts",
        "usage_observed_subtotals",
        "cost",
        "underlying_physical_work_stopped",
    )
    diagnostics = {
        role: {key: audit["diagnostics"][role][key] for key in diagnostic_keys}
        for role in ("baseline", "candidate")
    }
    return {
        **{key: audit[key] for key in keys},
        "diagnostics": diagnostics,
        "experiment_status": audit["state"]["experiment_status"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    for name in ("dataset", "release", "config", "budget", "output"):
        prepare.add_argument("--" + name, type=Path, required=True)
    prepare.add_argument("--experiment-id", required=True)
    prepare.add_argument("--seed", type=int, default=0)
    prepare.add_argument("--repeats", type=int, default=1)
    run = sub.add_parser("run")
    run.add_argument("--root", type=Path, required=True)
    run.add_argument("--env-file", type=Path, required=True)
    run.add_argument("--max-wall-seconds", type=float)
    for name in ("audit", "report"):
        item = sub.add_parser(name)
        item.add_argument("--root", type=Path, required=True)
        item.add_argument("--reviews", type=Path)
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--root", type=Path, required=True)
    worker.add_argument("--slot-index", type=int, required=True)
    worker.add_argument("--verify-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare_experiment(
                args.dataset,
                args.release,
                args.output,
                shared_config=SharedConfig.model_validate(load_json(args.config)),
                budget=ExperimentBudget.model_validate(load_json(args.budget)),
                experiment_id=args.experiment_id,
                seed=args.seed,
                repeats=args.repeats,
            )
            print("prepared")
        elif args.command == "run":
            state = run_experiment(
                args.root, env_file=args.env_file, max_wall_seconds=args.max_wall_seconds
            )
            print(f"started={len(state.started_run_ids)} terminal={len(state.terminal_run_ids)}")
        elif args.command == "_worker":
            asyncio.run(_worker(args.root, args.slot_index, verify_only=args.verify_only))
            print("worker_finished")
        else:
            result = audit_experiment(args.root, reviews_path=args.reviews)
            if args.command == "audit":
                _atomic_json(args.root / "private/audit.json", result)
                print(
                    f"planned={result['planned_pair_count']} "
                    f"comparable={result['comparable_pair_count']}"
                )
            else:
                print(canonical_json(public_report(result)))
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("experiment interrupted", file=sys.stderr)
        return 130
    except Exception:
        print("experiment input, worker, or artifact validation failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
