import html
import json
import tempfile
from pathlib import Path

from havoc.experiment import CLASSES, STRATEGIES, failure_class, rate_cell
from havoc.faults import Fault
from havoc.regression import replay_regression

CSS = """
:root{--bg:#fbfbf9;--fg:#1d1d1b;--muted:#66665f;--line:#dcdcd4;--card:#f1f1ec;--accent:#b4401f;--ok:#2d7a46;--bad:#b4401f;--chip:#e6e6de}
@media (prefers-color-scheme: dark){:root{--bg:#161614;--fg:#ecece6;--muted:#9b9b92;--line:#34342f;--card:#201f1d;--accent:#e8825f;--ok:#6cc487;--bad:#e8825f;--chip:#2c2b28}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:860px;margin:0 auto;padding:40px 20px 80px}
h1{font-size:2.2rem;margin:0 0 4px}h2{margin-top:2.6rem;border-top:1px solid var(--line);padding-top:1.4rem}h3{margin-top:1.8rem}
.sub{color:var(--muted);margin:0 0 24px}a{color:var(--accent)}
code{font:0.85em ui-monospace,Consolas,monospace;background:var(--chip);padding:1px 5px;border-radius:4px;word-break:break-word}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:22px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card b{display:block;font-size:1.7rem;line-height:1.2}.card span{color:var(--muted);font-size:.9rem}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:12px 0;font-size:.93rem}
th,td{border-bottom:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 9px;margin:1px 3px 1px 0;font-size:.78rem}
.chip.fault{background:var(--accent);color:var(--bg)}.chip.unsafe{background:var(--bad);color:var(--bg)}
.flow{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:16px 0}
.flow div{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:.9rem}
.flow i{color:var(--muted);font-style:normal}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px;margin:14px 0}
summary{cursor:pointer;font-weight:600}
.timeline{list-style:none;padding:0;margin:14px 0}
.call{border-left:3px solid var(--ok);padding:6px 0 6px 12px;margin:10px 0}.call.error{border-color:var(--bad)}
.seen,.real{font-size:.88rem;margin-top:3px;word-break:break-word}
.seen span,.real span{color:var(--muted);margin-right:6px;font-size:.78rem;text-transform:uppercase}
.real{color:var(--accent)}
.note{color:var(--muted);font-size:.92rem}
"""


def esc(text):
    return html.escape(str(text), quote=True)


def short(text, limit=260):
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def chip(text, kind=""):
    return f'<span class="chip {kind}">{esc(text)}</span>'


def table(headers, rows):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="wrap"><table><tr>{head}</tr>{body}</table></div>'


def response_text(response):
    for block in (response or {}).get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "")
    return json.dumps(response)


def timeline(trace):
    items = []
    for record in trace:
        faults = "".join(
            chip(f["id"] + ("" if f["applied"] else " (no effect)"), "fault")
            for f in record.get("faults", [])
        )
        status = "error" if record["is_error"] else "ok"
        extra = ""
        if "original" in record:
            extra = (
                '<div class="real"><span>the server really sent</span> '
                f'{esc(short(response_text(record["original"])))}</div>'
            )
        elif record.get("forwarded") is False:
            extra = '<div class="real"><span>this call never reached the server</span></div>'
        items.append(
            f'<li class="call {status}"><div><b>{esc(record["tool"])}</b> '
            f'<code>{esc(short(json.dumps(record["args"]), 120))}</code> {faults}</div>'
            f'<div class="seen"><span>the agent saw</span> {esc(short(response_text(record["response"])))}</div>'
            f"{extra}</li>"
        )
    return '<ol class="timeline">' + "".join(items) + "</ol>"


def case_block(case):
    meta = case["meta"]
    plan = "".join(chip(f.id, "fault") for f in case["plan"])
    signature = "".join(chip(s, "unsafe") for s in case["signature"])
    violations = "".join(f"<li>{esc(v)}</li>" for v in case["violations"]) or "<li>none</li>"
    notes = f'<p class="note">{esc(meta["notes"])}</p>' if meta.get("notes") else ""
    return (
        f'<details><summary>{esc(meta["name"])} <span class="note">({esc(meta["task"])})</span></summary>'
        f"{notes}<p>Faults injected: {plan}</p>"
        f"<p>What went wrong: {signature}</p>"
        f"{timeline(case['trace'])}"
        f"<p><b>What the agent told the customer:</b> {esc(short(case['answer'], 500))}</p>"
        f"<p><b>Safety checks:</b></p><ul>{violations}</ul></details>"
    )


async def collect_cases(root):
    root = Path(root)
    cases = []
    if not root.exists():
        return cases
    for folder in sorted(p for p in root.iterdir() if (p / "meta.json").exists()):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            data, outcome = await replay_regression(folder, Path(tmp) / "work", tmp)
        cases.append(
            {
                "meta": data["meta"],
                "plan": data["plan"],
                "trace": outcome.trace,
                "answer": outcome.result.answer,
                "violations": [f"{v.invariant}: {v.message}" for v in outcome.violations],
                "signature": sorted(outcome.signature),
            }
        )
    return cases


def render_page(summary, cases, repo_url="", title="havoc"):
    sr, sf = summary["single"]
    cr, cf = summary["compound"]
    pure = summary["pure_compound"]
    p_value = summary["p_value"]
    explained = summary["explained"]

    cards = "".join(
        f'<div class="card"><b>{value}</b><span>{esc(label)}</span></div>'
        for value, label in [
            (f"{sf} of {sr}", "single faults broke the agent"),
            (f"{cf} of {cr}", "pairs of faults broke the agent"),
            (str(len(pure)), "failures that needed both faults of a pair"),
            (f"{explained} of {cf}", "failing pairs were just one bad fault plus noise"),
        ]
    )

    strategy_rows = []
    for name in STRATEGIES:
        if name in summary["by_strategy"]:
            runs, failed = summary["by_strategy"][name]
            counts = summary["by_class"].get(name, {})
            strategy_rows.append(
                [esc(name), str(runs), esc(rate_cell(failed, runs))]
                + [str(counts.get(c, 0)) for c in CLASSES]
            )

    verdict = ""
    if p_value is not None:
        meaning = "which is not a meaningful difference" if p_value >= 0.05 else "which is a statistically detectable difference"
        verdict = (
            f"<p>Comparing the failure rate of single faults with that of pairs gives a Fisher exact "
            f"p-value of {p_value:.3f}, {meaning}. Counting failing runs is not the interesting part, "
            "the interesting part is which failures only appear when two faults combine.</p>"
        )

    sensitivity = ""
    if summary["sensitivity"]:
        shown = [s for s in STRATEGIES if s in summary["by_strategy"]]
        rows = [
            [str(limit)] + [str(row.get(s, 0)) for s in shown]
            for limit, row in summary["sensitivity"].items()
        ]
        sensitivity = (
            "<h3>How much the retry limit matters</h3>"
            "<p>A retry storm means the same call failing N or more times. I first used 3 and found "
            "that it flagged an agent that had recovered correctly, so I moved it to 4. "
            "The table shows failing runs at each limit.</p>"
            + table(["N"] + shown, rows)
        )

    single_rows = []
    checks = {(a["task"], a["key"]): a for a in summary["single_checks"]}
    for (task, fault_id), signature in sorted(summary["single_causes"].items()):
        single_rows.append(
            [esc(task), f"<code>{esc(fault_id)}</code>", chip(failure_class(signature)), esc(", ".join(signature))]
        )

    pure_blocks = []
    for (task, ids), a in sorted(pure.items()):
        rates = ", ".join(f"temperature {t}: {h} of {n}" for t, (h, n) in a["rates"].items())
        alone = "".join(
            f"<li><code>{esc(fid)}</code> alone: "
            + esc(", ".join(f"temperature {t}: {h} of {n}" for t, (h, n) in per.items()))
            + "</li>"
            for fid, per in a["singles"].items()
        )
        pure_blocks.append(
            f"<h3>{esc(task)}</h3><p>{' + '.join(f'<code>{esc(i)}</code>' for i in ids)}</p>"
            f"<p>Breaks: {esc(', '.join(a['signature']))} ({failure_class(a['signature'])}). "
            f"Fails again in fresh runs: {esc(rates)}. Status: <b>{esc(a['status'])}</b>.</p>"
            f"<ul>{alone}</ul>"
        )

    repo = f'<a href="{esc(repo_url)}">source code on GitHub</a>' if repo_url else ""
    cases_html = "".join(case_block(c) for c in cases) or "<p>No saved cases yet.</p>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head><body><main>
<h1>{esc(title)}</h1>
<p class="sub">Fault injection and failure shrinking for AI agents that use MCP tools. {repo}</p>

<p>Most agent tests only cover the happy path. This project breaks the tools an agent depends on, on purpose,
and watches what the agent does to a real database. It tries single faults and pairs of faults, and shrinks
any failing combination to the smallest set that still fails.</p>

<div class="cards">{cards}</div>

<p class="note">One model (Gemini 3.5 Flash-Lite), five customer-support tasks, free-tier limits.
These numbers describe this setup, not agents in general.</p>

<h2>How it works</h2>
<div class="flow"><div>agent</div><i>→</i><div>proxy that records and breaks tool calls</div><i>→</i><div>MCP server and database</div><i>→</i><div>checks on the final state</div></div>
<p>The proxy sits between the agent and its tools and can fail a call before or after it runs, cut a result in half,
corrupt or drop a field, or add a prompt injection to the output. After each run, safety checks read the database,
not what the agent claims it did.</p>

<h2>Results</h2>
{table(["Strategy", "Runs", "Runs that failed", "unsafe", "runaway", "incomplete"], strategy_rows)}
<p class="note">unsafe: a safety check fired (a false claim, a refund over the limit, mail to a stranger).
runaway: the agent looped or ran out of turns. incomplete: the task was not done and nothing unsafe happened.</p>
{verdict}
{sensitivity}

<h3>Single faults that broke the agent</h3>
{table(["Task", "Fault", "Kind", "What the checks reported"], single_rows)}

<h2>Failures that needed two faults</h2>
<p>Of the failing pairs, {explained} were explained by one of their own faults failing alone, and
{len(summary['studied'])} were studied by shrinking and re-running. {len(pure)} turned out to need both faults,
meaning neither one failed on its own in fresh runs.</p>
{''.join(pure_blocks) or '<p>None yet.</p>'}

<h2>Replay a recorded failure</h2>
<p>Each case below is replayed from saved model responses, with no model and no network.
Open one to see what the agent saw next to what the server really sent.</p>
{cases_html}

<h2>Limits</h2>
<ul>
<li>One model, one domain, five tasks.</li>
<li>Even at temperature 0 the same run can differ, so a failure is a rate, not a yes or no.</li>
<li>The checks that read the agent's answer are heuristics.</li>
<li>Single-fault failures are single samples unless re-run, and pairs that failed were re-run, so the two are not held to the same standard.</li>
<li>Delta debugging and fault injection for agents both existed before this. I applied them to MCP agents with state-based checks.</li>
</ul>
</main></body></html>"""
