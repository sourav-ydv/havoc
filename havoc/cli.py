import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from havoc import __version__
from havoc.experiment import render_markdown, summarize
from havoc.regression import replay_regression
from havoc.store import JsonlStore

DEFAULT_CASE = "refund-retry-pair"
DEFAULT_RESULTS = "results/main-v3"


def find_root(start=None):
    start = Path(start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "regressions").is_dir() and (candidate / "pyproject.toml").exists():
            return candidate
    return start


def regression_cases(root):
    folder = Path(root) / "regressions"
    if not folder.is_dir():
        return []
    return sorted(
        path
        for path in folder.iterdir()
        if (path / "meta.json").exists()
        and (path / "plan.json").exists()
        and (path / "cassette.json").exists()
    )


def key_line(root):
    if os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY"):
        return "api key: set in the environment; havoc run does not use it"
    if (Path(root) / ".env").exists():
        return "api key: a .env file is present; havoc run does not read it"
    return "api key: not set, and not needed for havoc run or havoc report"


def diagnose(root):
    root = Path(root)
    found = regression_cases(root)
    lines = [f"python {sys.version.split()[0]}", f"havoc {__version__}"]
    ok = True
    if found:
        lines.append(f"regressions: {len(found)} cases")
    else:
        lines.append("regressions: none found (run this from the clone)")
        ok = False
    if (root / "results" / "main-v3" / "runs.jsonl").exists():
        lines.append("results: results/main-v3")
    else:
        lines.append("results: results/main-v3 is missing, havoc report needs it")
        ok = False
    lines.append(key_line(root))
    lines.append("ok" if ok else "not ready")
    return ok, lines


def format_outcome(data, outcome):
    saved = set(data["meta"]["signature"])
    got = set(outcome.signature)
    faults = ", ".join(fault.id for fault in data["plan"])
    lines = [
        data["meta"]["name"],
        f"task: {data['meta']['task']}",
        f"faults: {faults}",
        f"signature: {', '.join(sorted(got)) or 'none'}",
    ]
    if saved <= got:
        lines.append("matched the saved failure")
        return "\n".join(lines), True
    missing = ", ".join(sorted(saved - got))
    lines.append(f"did not match the saved failure, missing {missing}")
    return "\n".join(lines), False


def list_text(root):
    found = regression_cases(root)
    if not found:
        return "no saved cases"
    lines = []
    for path in found:
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        lines.append(f"{path.name}: {meta.get('notes', '')}")
    return "\n".join(lines)


def replay(root, name):
    found = regression_cases(root)
    chosen = next((path for path in found if path.name == name), None)
    if chosen is None:
        known = ", ".join(path.name for path in found) or "none"
        return f"no case named {name}. cases: {known}", False
    with tempfile.TemporaryDirectory() as tmp:
        data, outcome = asyncio.run(replay_regression(chosen, Path(tmp) / "work", Path(tmp)))
    return format_outcome(data, outcome)


def report_text(folder):
    folder = Path(folder)
    runs = folder / "runs.jsonl"
    if not runs.exists():
        return None
    summary = summarize(JsonlStore(runs).load(), JsonlStore(folder / "analysis.jsonl").load())
    return render_markdown(summary)


def resolve_results(root, chosen):
    path = Path(chosen)
    if not path.is_absolute():
        path = Path(root) / path
    return path


def command_doctor(args):
    ok, lines = diagnose(find_root())
    return (0 if ok else 1), "\n".join(lines)


def command_run(args):
    root = find_root()
    if args.list:
        return 0, list_text(root)
    text, ok = replay(root, args.case)
    return (0 if ok else 1), text


def command_report(args):
    folder = resolve_results(find_root(), args.dir)
    text = report_text(folder)
    if text is None:
        return 1, f"no runs found in {folder}"
    return 0, text


def build_parser():
    parser = argparse.ArgumentParser(
        prog="havoc",
        description="Replay a saved agent failure with no API key.",
    )
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="check that this clone can run offline")
    doctor.set_defaults(func=command_doctor)

    run = sub.add_parser("run", help="replay one saved failure through the proxy")
    run.add_argument("--case", default=DEFAULT_CASE)
    run.add_argument("--list", action="store_true")
    run.set_defaults(func=command_run)

    report = sub.add_parser("report", help="print the saved experiment report")
    report.add_argument("--dir", default=DEFAULT_RESULTS)
    report.set_defaults(func=command_report)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    code, text = args.func(args)
    if text:
        print(text)
    return code


def console():
    raise SystemExit(main())