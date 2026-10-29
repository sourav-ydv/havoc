import argparse
import asyncio
import json
from pathlib import Path

from havoc.experiment import summarize
from havoc.report_page import collect_cases, render_page
from havoc.store import JsonlStore


def load_replication(path):
    root = Path(path)
    if not root.is_dir():
        return []
    return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(root.glob("*.json"))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name")
    parser.add_argument("--dir")
    parser.add_argument("--out", default="docs")
    parser.add_argument("--repo", default="")
    parser.add_argument("--title", default="havoc")
    parser.add_argument("--regressions", default="regressions")
    parser.add_argument(
        "--replication",
        default="",
        help="folder of saved comparison files, such as results/groq",
    )
    args = parser.parse_args()

    if not args.name and not args.dir:
        parser.error("give --name for a local experiment or --dir for a saved results folder")
    root = Path(args.dir) if args.dir else Path(".havoc/experiments") / args.name
    runs = JsonlStore(root / "runs.jsonl").load()
    analyses = JsonlStore(root / "analysis.jsonl").load()
    if not runs:
        print(f"no runs found in {root}")
        return

    cases = asyncio.run(collect_cases(args.regressions))
    replication = load_replication(args.replication) if args.replication else []
    page = render_page(
        summarize(runs, analyses), cases, args.repo, args.title, replication or None
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"wrote {out / 'index.html'} with {len(cases)} replayable cases")


if __name__ == "__main__":
    main()
