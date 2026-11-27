"""Shared logic for the factory scripts.

Everything deterministic lives here: job state, legal stage moves, parsing
agent files, and the path rules (protected paths, scope, auto-merge
allowlist). Standard library only, so it runs anywhere Python 3.10+ does.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import fnmatch
import json
import math
import os
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

# --------------------------------------------------------------------------
# Stages and the only moves allowed between them.
# --------------------------------------------------------------------------

TRANSITIONS: dict[str, set[str]] = {
    "intake": {"spec", "needs-info", "rejected"},
    "needs-info": {"intake", "rejected"},
    "spec": {"ready", "needs-input", "needs-human"},
    "ready": {"build", "spec", "rejected"},
    "needs-input": {"spec", "rejected"},
    "build": {"gate", "needs-human", "needs-input"},
    "gate": {"review", "build", "recovery"},
    "review": {"approve", "build", "recovery"},
    "recovery": {"spec", "build", "needs-human", "rejected"},
    "approve": {"merging", "needs-human"},
    "merging": {"merged", "needs-human", "build"},
    "needs-human": {"build", "merged", "rejected"},
    "merged": set(),
    "rejected": set(),
}
TERMINAL = {"merged", "rejected"}
CLARIFY_STAGES = {"intake", "needs-info", "spec", "needs-input"}
WAITING_STAGES = {"needs-info", "needs-input", "needs-human"}


def lane(stage: str) -> str:
    """Which independent lane owns a stage: clarify (no code), build, or done."""
    if stage in TERMINAL:
        return "done"
    return "clarify" if stage in CLARIFY_STAGES else "build"


class FactoryError(Exception):
    """A refused action. The message is shown to the orchestrator as is."""


class FactoryBusy(FactoryError):
    """A concurrent command holds a required lock."""


# --------------------------------------------------------------------------
# Paths and config
# --------------------------------------------------------------------------


def repo_root(start: Path | None = None) -> Path:
    """The main checkout's root (never a worktree's)."""
    cwd = str(start or Path.cwd())
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=cwd, capture_output=True, text=True,
    )
    if common.returncode != 0:
        raise FactoryError("not inside a git repository")
    return Path(common.stdout.strip()).parent


def load_config(root: Path) -> dict:
    path = root / "factory" / "config.json"
    if not path.exists():
        raise FactoryError(f"missing {path}")
    return json.loads(path.read_text())


def jobs_dir(root: Path) -> Path:
    return root / "factory" / "jobs"


def phases_dir(root: Path) -> Path:
    """Per-phase files the orchestrator keeps (coverage.md). Not committed, like jobs/."""
    return root / "factory" / "phases"


def job_dir(root: Path, job_id: str) -> Path:
    if not valid_reporting_job_id(job_id):
        raise FactoryError(f"invalid job ID {job_id!r}")
    d = jobs_dir(root) / job_id
    if any(path.is_symlink() for path in (d.parent.parent, d.parent, d)):
        raise FactoryError("job directory must be a real directory")
    if not (d / "job.json").exists():
        raise FactoryError(f"no job {job_id}")
    return d


def reporting_job_dir(root: Path, job_id: str) -> Path:
    """Refuse aliases and non-job paths for observation commands."""
    if not valid_reporting_job_id(job_id):
        raise FactoryError("invalid job ID")
    folder = jobs_dir(root) / job_id
    state = folder / "job.json"
    if any(path.is_symlink() for path in (folder.parent.parent, folder.parent, folder, state)):
        raise FactoryError(f"job {job_id} must use a real job directory and state file")
    if not state.is_file():
        raise FactoryError(f"no job {job_id}")
    return folder


def valid_reporting_job_id(job_id: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", job_id) is not None


def slugify(text: str, words: int = 4) -> str:
    parts = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(parts[:words]) or "job"


def make_job_id(issue: int, title: str) -> str:
    return f"{issue:03d}-{slugify(title)}"


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Job state. job.json is written only through these functions.
# --------------------------------------------------------------------------


GATE_PENDING = ".gate-publication.pending"
_held_mutex = ContextVar("factory_job_mutex", default=None)


@contextmanager
def _directory_lock(d: Path, busy: str):
    # Lock the directory inode itself so a replaced lock file cannot split ownership.
    try:
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise FactoryError(f"cannot open directory {d.name}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FactoryBusy(busy) from exc
        yield fd
    finally:
        os.close(fd)


@contextmanager
def job_mutex(d: Path):
    owner = (os.getpid(), d)
    if _held_mutex.get() == owner:
        yield
        return
    if any(path.is_symlink() for path in (d.parent.parent, d.parent, d)):
        raise FactoryError("job directory must be a real directory")
    with _directory_lock(d, f"job {d.name} is busy; retry"):
        token = _held_mutex.set(owner)
        try:
            yield
        finally:
            _held_mutex.reset(token)


def gate_worktree_lock(wt: Path):
    return _directory_lock(wt, "another gate is running on this worktree; retry after it exits")


def require_admission(d: Path) -> None:
    if os.path.lexists(d / GATE_PENDING):
        raise FactoryError("gate publication interrupted; reconcile the receipt and job before retrying")


def _sync_directory(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".factory-tmp-", delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(tmp, path)
            _sync_directory(path.parent)
        finally:
            tmp.unlink(missing_ok=True)


def remove_marker(path: Path) -> None:
    path.unlink()
    _sync_directory(path.parent)


def read_job(d: Path) -> dict:
    try:
        fd = os.open(d / "job.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise FactoryError("job.json must be a regular file")
            job = json.load(stream)
        if not isinstance(job, dict):
            raise FactoryError("job.json must contain an object")
        revision = job.get("revision", 0)
        if type(revision) is not int or revision < 0:
            raise FactoryError("invalid job revision")
        return job
    except (OSError, ValueError) as exc:
        raise FactoryError("invalid or unreadable job.json") from exc


def validate_reporting_job(job, job_id: str) -> dict:
    """Validate the stored fields used by reporting and tolerate missing legacy dates."""
    if not isinstance(job, dict) or job.get("id") != job_id:
        raise FactoryError(f"invalid or unreadable job state for {job_id}")
    history = job.get("history")
    if history is None:
        history = []
    if not isinstance(history, list) or any(
        not isinstance(step, dict) or any(step.get(key) is not None and not isinstance(step[key], str)
                                          for key in ("stage", "note", "at"))
        for step in history
    ):
        raise FactoryError(f"job {job_id} has malformed history")
    surfaces, reviewers = job.get("surfaces"), job.get("reviewers")
    if surfaces is not None and (not isinstance(surfaces, list) or any(not isinstance(s, str) for s in surfaces)):
        raise FactoryError(f"job {job_id} has malformed surfaces")
    if reviewers is not None and (not isinstance(reviewers, dict)
                                  or any(not isinstance(model, str) for model in reviewers.values())):
        raise FactoryError(f"job {job_id} has malformed reviewers")
    for field in ("stage", "risk", "branch", "worktree", "stacked_on_branch", "factory_version", "origin"):
        value = job.get(field)
        if value is not None and not isinstance(value, str):
            raise FactoryError(f"job {job_id} has malformed {field}")
    if "round" in job and (type(job["round"]) is not int or job["round"] < 0):
        raise FactoryError(f"job {job_id} has malformed round")
    for field in ("owner_minutes", "cost_usd"):
        value = job.get(field)
        if value is not None:
            try:
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise FactoryError(f"job {job_id} has malformed {field}")
    for field in ("escaped", "owner_changes"):
        value = job.get(field)
        if value is not None and type(value) is not bool:
            raise FactoryError(f"job {job_id} has malformed {field}")
    normalized_history = [step if _ts(step.get("at")) is not None else {**step, "at": None} for step in history]
    return {**job, "history": normalized_history}


def write_job(d: Path, job: dict, *, publishing_gate: bool = False) -> None:
    # The gate alone writes state with a pending marker, while holding the
    # mutex continuously from admission through marker removal.
    if publishing_gate and _held_mutex.get() != (os.getpid(), d):
        raise FactoryError("gate publication requires the job mutex")
    with job_mutex(d):
        if not publishing_gate:
            require_admission(d)
        path = d / "job.json"
        revision = read_job(d).get("revision", 0) if os.path.lexists(path) else 0
        updated = {**job, "revision": revision + 1}
        atomic_write(path, (json.dumps(updated, indent=2) + "\n").encode())
        job["revision"] = updated["revision"]


def new_job(job_id: str, issue: int, title: str) -> dict:
    return {
        "id": job_id,
        "issue": issue,
        "title": title,
        "stage": "intake",
        "round": 0,
        "risk": None,
        "surfaces": [],
        "reviewers": {},
        "branch": f"factory/{job_id}",
        "worktree": f".worktrees/{job_id}",
        "pr": None,
        "history": [{"at": now(), "stage": "intake", "note": "created"}],
    }


def move(job: dict, to: str, note: str = "") -> dict:
    """Move a job to a new stage, refusing illegal moves."""
    frm = job["stage"]
    if to not in TRANSITIONS.get(frm, set()):
        raise FactoryError(f"illegal move {frm} -> {to} for job {job['id']}")
    job["stage"] = to
    job["history"].append({"at": now(), "stage": to, "note": note})
    return job


PRODUCT_MARK = "PRODUCT DECISION"
_SECTION_RE = re.compile(r"^##\s+(.*?)\s*$")
_OPEN_ITEM_RE = re.compile(r"^\s*[-*]\s*\[\s\]\s*(.+?)\s*$")


def _sections(text: str) -> dict[str, list[str]]:
    """Split a Markdown file into {heading: lines} by its level-2 headings."""
    out: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1).strip().lower()
            out.setdefault(current, [])
        else:
            out.setdefault(current, []).append(line)
    return out


def section_text(text: str, heading: str) -> str:
    """The body of one "## heading" section, stripped; "" if it isn't there."""
    return "\n".join(_sections(text).get(heading.strip().lower(), [])).strip()


def open_product_questions(spec_text: str) -> list[str]:
    """Every product question a spec still leaves open; [] means it may be built.

    Open means: an unticked "- [ ]" item under "## Product questions", or the
    words PRODUCT DECISION anywhere outside "## Owner decisions" (an assumption
    the spec-writer flagged but did not lift into a question).
    """
    found: list[str] = []
    for heading, lines in _sections(spec_text).items():
        if heading == "owner decisions":
            continue
        for line in lines:
            m = _OPEN_ITEM_RE.match(line) if heading == "product questions" else None
            if m:
                found.append(m.group(1))
            elif PRODUCT_MARK in line:
                found.append(line.strip().lstrip("-* ").strip())
    return found


def open_product_gaps(build_text: str) -> list[str]:
    """Unticked "- [ ]" items under build.md's "## Product gaps": product decisions the
    builder met mid-build that neither the spec nor the standing rules settle."""
    return [m.group(1) for line in _sections(build_text).get("product gaps", [])
            if (m := _OPEN_ITEM_RE.match(line))]


def question_ids(issue: int, questions: list[str]) -> list[str]:
    """Stable, unambiguous ids for one ask, e.g. "71-Q1", so an answer by id can't land
    on the wrong list. A leading "Q<n>:" the spec already gave is replaced by the id."""
    out = []
    for k, q in enumerate(questions, 1):
        text = re.sub(r"^\*{0,2}Q\d+(?:[:.]\*{0,2}|\*{0,2}[:.])\s*", "", q.strip())
        out.append(f"{issue}-Q{k}: {text}")
    return out


def after_gate(job: dict) -> bool:
    """True once a job has passed the clarify exit at least once (reached ready).
    A question asked after that is a phase 1 miss: the owner is interrupted mid-build."""
    return any(e["stage"] == "ready" for e in job.get("history", []))


def record_ask(job: dict, count: int, source: str) -> dict:
    """Log one round of questions to the owner, for the scorecard."""
    job.setdefault("asks", []).append(
        {"at": now(), "count": count, "source": source, "after_gate": after_gate(job)})
    return job


def phase_blockers(jobs: list[dict], phase: str) -> list[str]:
    """Jobs in the same phase still in their first clarify. While any is, nobody in the
    phase may start building: all of a phase's questions come before any of its builds.
    A job back in clarify after the gate (a spec refresh, or a gap found while building)
    doesn't hold the others: it stops only itself."""
    return [j["id"] for j in jobs
            if j.get("phase") == phase and lane(j["stage"]) == "clarify" and not after_gate(j)]


_MISSING_RE = re.compile(r"^\s*[-*]?\s*MISSING:\s*(.+?)\s*$")


def coverage_missing(coverage_text: str) -> list[str]:
    """Lines "MISSING: <area>" in a phase's coverage.md: source with no issue in the phase."""
    return [m.group(1) for line in coverage_text.splitlines() if (m := _MISSING_RE.match(line))]


def enter_build(job: dict, d: Path, note: str = "") -> dict:
    """Move a job into build by hand (`job advance <id> build`), always into a fresh round.

    The first build after the spec is cleared is round 1. Any later move into build (owner feedback
    from needs-human, a CI failure while merging, a retry from recovery) is the next
    round, so the earlier round's gate and review files are never overwritten or
    counted again. Owner-driven rounds are not capped by max_rounds. Refuses, changing
    nothing, if the move is illegal, the new round's folder already exists, or (from
    ready) the spec still has an open product question.
    """
    if "build" not in TRANSITIONS.get(job["stage"], set()):
        raise FactoryError(f"illegal move {job['stage']} -> build for job {job['id']}")
    if job["stage"] in ("spec", "ready"):
        spec = d / "spec.md"
        if not spec.exists():
            raise FactoryError(f"no spec.md for job {job['id']}; write the spec first")
        open_qs = open_product_questions(spec.read_text())
        if open_qs:
            raise FactoryError(f"spec for job {job['id']} has {len(open_qs)} open product "
                               "question(s); run `job clarify` and wait for the owner")
    new_round = job["round"] + 1
    if (d / f"round-{new_round}").exists():
        raise FactoryError(f"round-{new_round} already exists for job {job['id']}; "
                           "refusing to overwrite it")
    job["round"] = new_round
    return move(job, "build", note or f"round {new_round}")


def next_round_or_recovery(job: dict, max_rounds: int, note: str) -> dict:
    """After a failed gate or a CHANGES review: another build round, or recovery."""
    if job["round"] < max_rounds:
        job["round"] += 1
        return move(job, "build", note)
    return move(job, "recovery", note + f"; {max_rounds} rounds used")


# --------------------------------------------------------------------------
# Parsing agent files
# --------------------------------------------------------------------------

HEADER_RE = re.compile(r"^([A-Z][A-Z-]*):\s*(.*?)\s*$")


def parse_header(text: str) -> dict[str, str]:
    """Read the KEY: value lines at the top of an agent file."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if out:
                break
            continue
        m = HEADER_RE.match(line)
        if not m:
            break
        out[m.group(1)] = m.group(2)
    return out


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def body_after_header(text: str) -> str:
    """Everything after the header block, for posting as a comment."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and (HEADER_RE.match(lines[i]) or lines[i].strip() in {"APPROVE", "ESCALATE"}):
        i += 1
    return "\n".join(lines[i:]).strip()


def parse_surfaces(value: str) -> list[str]:
    items = [s.strip().lower() for s in value.split(",")]
    return [s for s in items if s and s != "none"]


def section(text: str, heading: str) -> str:
    """The body of a '## heading' section."""
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", re.M | re.S)
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def allowed_files(spec_text: str) -> list[str]:
    """Paths or globs listed under '## Files' in spec.md, one per bullet."""
    out = []
    for line in section(spec_text, "Files").splitlines():
        m = re.match(r"^\s*[-*]\s+`?([^`\s]+)`?", line)
        if m:
            out.append(m.group(1))
    return out


# --------------------------------------------------------------------------
# Path rules
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Scorecard: per-job metrics from job.json, grouped by factory version
# --------------------------------------------------------------------------

BASE_VERSION = "v1"


def _ts(at: str | None) -> _dt.datetime | None:
    if not isinstance(at, str):
        return None
    try:
        value = _dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
        return value.astimezone(_dt.timezone.utc) if value.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def job_metrics(job: dict) -> dict:
    """What one job's history says: timings, gate, escalation, merge path."""
    h = job.get("history") or []
    stages = [e.get("stage") for e in h]
    created = _ts(h[0].get("at")) if h else None
    ready = None
    for i, e in enumerate(h):
        if stages[i] == "needs-human" or (stages[i] == "merged" and i and stages[i - 1] == "merging"):
            ready = _ts(e.get("at"))
            break
    merged = next((_ts(e.get("at")) for e in h if e.get("stage") == "merged"), None)
    merge_recorded = "merged" in stages or job.get("stage") == "merged"
    escalated = None
    if any(e.get("note") == "approver escalated" for e in h):
        escalated = True
    elif "merging" in stages:
        escalated = False
    return {
        "merged": merge_recorded,
        "rejected": job.get("stage") == "rejected",
        "agent_minutes": round((ready - created).total_seconds() / 60, 2) if ready and created else None,
        "lead_hours": round((merged - created).total_seconds() / 3600, 2) if merged and created else None,
        "merged_at": merged,
        "first_pass_gate": None,
        "first_gate_attempted": "gate" in stages or job.get("stage") == "gate",
        "rounds": job.get("round", 0),
        "escalated": escalated,
        "auto_merged": merged is not None and "merging" in stages and stages[stages.index("merged") - 1] == "merging",
        "accepted_as_built": None,
        "escaped": job.get("escaped") if type(job.get("escaped")) is bool else None,
        "owner_changes": job.get("owner_changes") if type(job.get("owner_changes")) is bool else None,
        "owner_minutes": job.get("owner_minutes"),
        "cost_usd": job.get("cost_usd"),
        # v1.3: asks are logged from v1.3 on, so older jobs report None, not zero.
        "questions": sum(a["count"] for a in job["asks"]) if "asks" in job else None,
        "asked_after_gate": any(a["after_gate"] for a in job["asks"]) if "asks" in job else None,
        "owner_wait_hours": _waiting_hours(h),
    }


def _waiting_hours(history: list[dict]) -> float:
    """Hours the job sat waiting on a person for an answer (needs-input, needs-info),
    counting only waits that have ended."""
    total = 0.0
    for e, nxt in zip(history, history[1:]):
        if e["stage"] in ("needs-input", "needs-info"):
            total += (_ts(nxt["at"]) - _ts(e["at"])).total_seconds() / 3600
    return round(total, 2)


def _median(values: list):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else round((vals[mid - 1] + vals[mid]) / 2, 2)


def _mean(values: list):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _count(ms: list[dict], key: str) -> tuple[int, int]:
    known = [m[key] for m in ms if m[key] is not None]
    return sum(1 for v in known if v), len(known)


def scorecard(jobs: list[dict]) -> dict[str, dict]:
    """One card per factory version. Pairs are (count, out of)."""
    groups: dict[str, list[dict]] = {}
    for job in jobs:
        if job.get("origin") == "controlled":
            continue
        groups.setdefault(job.get("factory_version") or BASE_VERSION, []).append(job_metrics(job))
    cards = {}
    for version, ms in groups.items():
        merged = [m for m in ms if m["merged"]]
        stamped_merges = [m["merged_at"] for m in merged if m["merged_at"] is not None]
        weeks = 1.0
        if len(stamped_merges) > 1:
            span = max(stamped_merges) - min(stamped_merges)
            weeks = max(1.0, span.total_seconds() / (7 * 86400))
        first_gates = sum(m["first_gate_attempted"] for m in ms)
        cards[version] = {
            "version": version,
            "jobs": len(ms),
            "merged": len(merged),
            "rejected": sum(1 for m in ms if m["rejected"]),
            "merges_per_week": round(len(merged) / weeks, 2) if len(merged) == len(stamped_merges) else None,
            "agent_minutes_median": _median([m["agent_minutes"] for m in ms]),
            "lead_hours_median": _median([m["lead_hours"] for m in merged]),
            "first_pass_gate": None,
            "rounds_mean": round(sum(m["rounds"] for m in merged) / len(merged), 2) if merged else None,
            "escalated": _count(ms, "escalated"),
            "auto_merged": (sum(1 for m in merged if m["auto_merged"]), len(merged)),
            "accepted_as_built": None,
            "change_fail": None,
            "owner_minutes_median": _median([m["owner_minutes"] for m in merged]),
            "cost_usd_median": _median([m["cost_usd"] for m in merged]),
            "questions_mean": _mean([m["questions"] for m in ms]),
            "asked_after_gate": _count(ms, "asked_after_gate"),
            "owner_wait_hours_median": _median([m["owner_wait_hours"] for m in ms]),
            "coverage": {
                "first_pass_gate": {"eligible": first_gates, "observed": 0, "unknown": first_gates},
                "accepted_as_built": {"eligible": len(merged), "observed": 0, "unknown": len(merged)},
                "change_fail": {"eligible": len(merged), "observed": 0, "unknown": len(merged),
                                "legacy_affected": sum(m["escaped"] is True for m in merged)},
            },
        }
    return cards


SCORECARD_ROWS = [
    ("Jobs", "jobs"), ("Merged", "merged"), ("Merges per week", "merges_per_week"),
    ("Agent time to a merge-ready PR, median minutes", "agent_minutes_median"),
    ("Lead time, job created to merged, median hours", "lead_hours_median"),
    ("First-pass gate", "first_pass_gate"), ("Rounds per merged job", "rounds_mean"),
    ("Escalated by the approver", "escalated"), ("Auto-merged", "auto_merged"),
    ("Accepted as built", "accepted_as_built"), ("Change fail rate", "change_fail"),
    ("Owner minutes per merged PR, median", "owner_minutes_median"),
    ("Cost per merged PR, median USD", "cost_usd_median"),
    ("Questions to the owner per job, mean", "questions_mean"),
    ("Jobs that asked after the clarify gate", "asked_after_gate"),
    ("Hours a job waited on an answer, median", "owner_wait_hours_median"),
]
SMALL_SAMPLE = 10


def _cell(value) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, (list, tuple)):
        k, n = value
        return f"{k} of {n} ({round(100 * k / n)}%)" if n else "none yet"
    return str(value)


_MARKDOWN_CODE_TEXT = re.compile(r"://|www\.|@|#[0-9]|^(?:[-+]|[0-9]{1,9}[.)])(?: |$)", re.IGNORECASE)


def markdown_code(value) -> str:
    """Render a literal, single-line code span, including any embedded backticks."""
    text = " ".join(str(value).split())
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    delimiter = "`" * (longest + 1)
    if text.startswith("`") or text.endswith("`"):
        text = f" {text} "
    return f"{delimiter}{text}{delimiter}"


def markdown_text(value, *, table_cell: bool = False) -> str:
    """Render untrusted display text without Markdown markup or automatic links."""
    text = " ".join(str(value).split())
    if _MARKDOWN_CODE_TEXT.search(text):
        span = markdown_code(text)
        return span.replace("|", r"\|") if table_cell else span
    return re.sub(r"([\\`*_\[\]<>~|#&])", r"\\\1", text)


def render_scorecard(cards: dict[str, dict], baseline: dict | None) -> str:
    """Markdown: the frozen baseline, then every other version's card beside it."""
    base_version = (baseline or {}).get("version", BASE_VERSION)
    others = [v for v in sorted(cards) if v != base_version]
    head = ["Metric", f"{markdown_text(base_version, table_cell=True)} baseline (frozen)"]
    head += [f"{markdown_text(v, table_cell=True)} (n = {cards[v]['jobs']})" for v in others]
    lines = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    for label, key in SCORECARD_ROWS:
        row = [label, _cell((baseline or {}).get(key))]
        for version in others:
            card = cards[version]
            coverage = card.get("coverage", {}).get(key)
            value = _cell(card.get(key))
            if coverage and coverage["eligible"]:
                value += f" ({coverage['observed']}/{coverage['eligible']} observed, {coverage['unknown']} unknown)"
            row.append(value)
        lines.append("| " + " | ".join(row) + " |")
    small = [v for v in others if cards[v]["jobs"] < SMALL_SAMPLE]
    if small or (baseline and baseline.get("jobs", 0) < SMALL_SAMPLE):
        lines += ["", f"Fewer than {SMALL_SAMPLE} jobs in a column, so the comparison is directional only. "
                  "Report every rate with its count."]
    if baseline and baseline.get("notes"):
        lines.extend(["", "Historical baseline notes"])
        lines.extend(f"- {note}" for note in baseline["notes"].values())
        if base_version == BASE_VERSION and baseline["notes"].get("frozen", "").startswith("2026-11-18"):
            lines.append("- The owner-reported ‘1 day’ has unresolved unit and scope.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Text the factory posts on GitHub (after tstack's file-pr and
# leaving-pr-comments skills: problem first, one attribution note)
# --------------------------------------------------------------------------

AGENT_NAME = "Claude Code (software factory)"
_CRITERION_RE = re.compile(r"^\s*\d+\.\s")


def criteria_met(build_text: str) -> tuple[int, int]:
    """(met, total) from build.md's numbered acceptance-criteria lines."""
    lines = [ln for ln in section(build_text, "Acceptance criteria").splitlines() if _CRITERION_RE.match(ln)]
    met = sum(1 for ln in lines if re.search(r"\bmet\b", ln) and not re.search(r"\bnot met\b", ln))
    return met, len(lines)


def pr_body(job: dict, spec_text: str, build_text: str, base_branch: str = "main") -> str:
    """The PR description: the problem, then the fix, then how it was proved."""
    parts = []
    problem = section(spec_text, "Problem statement")
    if problem:
        parts.append(problem)
    fix = section(spec_text, "Summary")
    if fix:
        parts.append(fix)
    met, total = criteria_met(build_text)
    if total:
        proof = (f"**How it was proved:** the builder reports {met} of {total} acceptance criteria met, "
                 "with the test or command for each in `build.md`. The gate and reviews run next.")
    else:
        proof = "**How it was proved:** no build notes yet. The gate and reviews run next."
    parts.append(proof)
    parts.append(f"Closes #{job['issue']}")
    if job.get("stacked_on_pr"):
        parts.append(f"**Stacked on #{job['stacked_on_pr']}**, so this PR shows only its own changes. "
                     f"Merge #{job['stacked_on_pr']} first; GitHub then retargets this PR to `{base_branch}` "
                     "when that branch is deleted.")
    parts.append(f"Created by the software factory in Claude Code, job `{job['id']}`. The spec, build notes, "
                 "gate results and reviews are in the job folder.")
    return "\n\n".join(parts)


def agent_comment(body: str, owner: str) -> str:
    """A comment posted for the owner, opened with one attribution note."""
    return f"> [!NOTE]\n> 🤖 **{AGENT_NAME} responding on behalf of {owner}**\n\n{body.strip()}"


def matches(path: str, patterns: list[str]) -> bool:
    """fnmatch, where '*' also crosses folders, plus 'dir/' meaning everything under dir."""
    for p in patterns:
        if p.endswith("/") and path.startswith(p):
            return True
        if fnmatch.fnmatchcase(path, p):
            return True
    return False


def protected_violations(changed: list[str], protected: list[str]) -> list[str]:
    return [p for p in changed if matches(p, protected)]


def scope_violations(changed: list[str], allowed: list[str]) -> list[str]:
    return [p for p in changed if not matches(p, allowed)]


def automerge_ok(changed: list[str], allowlist: list[str], risk: str | None, surfaces: list[str]) -> tuple[bool, str]:
    """Blast radius, not confidence: low risk, no sensitive surface, every file on the allowlist."""
    if risk != "low":
        return False, f"risk is {risk}, only low-risk jobs can merge without a human"
    sensitive = [s for s in surfaces if s in {"security", "eval"}]
    if sensitive:
        return False, f"touches the {', '.join(sensitive)} surface"
    outside = [p for p in changed if not matches(p, allowlist)]
    if outside:
        return False, "files outside the auto-merge allowlist: " + ", ".join(outside[:10])
    if not changed:
        return False, "no changed files"
    return True, "every changed file is on the allowlist"


# --------------------------------------------------------------------------
# Small helpers around git and gh
# --------------------------------------------------------------------------


def run(cmd: list[str], cwd: Path | None = None, check: bool = True, stdin: str | None = None) -> str:
    res = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, input=stdin)
    if check and res.returncode != 0:
        raise FactoryError(f"{' '.join(cmd[:3])} failed: {(res.stderr or res.stdout).strip()[:500]}")
    return res.stdout


def fetch_base(cwd: Path, base: str) -> None:
    """Fetch the base into refs/remotes/origin/<base> with an explicit refspec.

    A bare `git fetch origin <base>` only updates FETCH_HEAD when the clone has
    no fetch refspec (clones of empty repos, some shallow or CI clones), which
    leaves origin/<base> missing or stale.
    """
    run(["git", "fetch", "--quiet", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"], cwd=cwd)


def start_ref(job: dict, base: str) -> str:
    """What the job's own changes are measured against: the base, or the job it is stacked on."""
    return job.get("stacked_on_branch") or f"origin/{base}"


def changed_files(worktree: Path, base: str, job: dict | None = None) -> list[str]:
    """Files this job changed. For a stacked job, the upstream job's changes don't count."""
    fetch_base(worktree, base)
    start = start_ref(job or {}, base)
    out = run(["git", "diff", "--name-only", f"{start}...HEAD"], cwd=worktree)
    return [line for line in out.splitlines() if line.strip()]


# --------------------------------------------------------------------------
# GitHub, through the REST API only (`gh api`). High-level `gh issue`/`gh pr`
# commands use GraphQL, which some sandboxes (including Claude Code cloud
# sessions) block, so the factory never depends on them.
# --------------------------------------------------------------------------


def repo_slug(root: Path, *, remote: str | None = None) -> str:
    url = remote if remote is not None else run(["git", "remote", "get-url", "origin"], cwd=root).strip()
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise FactoryError("origin is not a GitHub repo")
    return m.group(1)


def gh_api(root: Path, method: str, path: str, fields: dict | None = None, check: bool = True):
    """Call the GitHub REST API for this repo. `path` is relative to repos/<owner>/<repo>."""
    from job_links import PublicGitHubResponse, public_repo

    remote = run(["git", "remote", "get-url", "origin"], cwd=root).removesuffix("\n")
    repo = repo_slug(root, remote=remote)
    cmd = ["gh", "api", "-X", method, f"repos/{repo}/{path}" if path else f"repos/{repo}"]
    link_endpoint = (method == "GET" and re.fullmatch(r"issues/[1-9][0-9]*", path)
                     or method == "POST" and path == "pulls")
    capture = link_endpoint and os.environ.get("GH_HOST", "github.com") == "github.com" and public_repo(remote) == repo
    if capture:
        cmd += ["--hostname", "github.com"]
    for key, value in (fields or {}).items():
        if isinstance(value, list):
            for v in value:
                cmd += ["-f", f"{key}[]={v}"]
        elif isinstance(value, bool):
            cmd += ["-F", f"{key}={'true' if value else 'false'}"]
        else:
            cmd += ["-f", f"{key}={value}"]
    out = run(cmd, check=check)
    try:
        data = json.loads(out) if out.strip() else None
        return PublicGitHubResponse(data, repo) if capture and isinstance(data, dict) else data
    except json.JSONDecodeError:
        return None


def gh_user() -> str:
    return run(["gh", "api", "user", "--jq", ".login"], check=False).strip()
