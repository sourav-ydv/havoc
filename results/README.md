# Saved results

This folder holds the data behind the numbers in the main README, so they can be checked without re-running anything.

## main-v3

The experiment on Gemini 3.5 Flash-Lite, after the retry limit and the claim checks were corrected.

- `runs.jsonl`: one line per discovery run (single faults, retry pairs and random pairs): the fault plan, whether it failed, the failure signature, turns and tool calls
- `analysis.jsonl`: the follow-up on failing plans: which were explained by a single fault, which were shrunk and re-run, the fresh-run counts, and the re-runs of failing single faults
- `results.md`: the report generated from the two files above
- `config.json`: the budget and seed used

To print the report again from this data:

```bash
python scripts/experiment.py report --dir results/main-v3
```

To rebuild the report page from it:

```bash
python scripts/build_report.py --dir results/main-v3 --repo https://github.com/sourav-ydv/havoc --replication results/groq
```

`tests/test_results_data.py` checks that the headline numbers in the README come out of these files.

## prompt-fix

`small_refund-sequential.json` holds the 40-run comparison of the normal system prompt against the variant with one extra rule, for the refund failure: the hits, the trials and the Fisher exact p-values for each case.

## groq

Two headline plans from the Gemini experiment, repeated on Groq at temperature 0.7, ten runs each. The README comparison is taken from these files together with the Gemini counts in `prompt-fix/` and `main-v3/analysis.jsonl`.

- `small_refund-baseline.json`: both `issue_refund` calls fail, and the first call alone. The counted failure is a false refund claim plus the task check.
- `ticket-pair-baseline.json`: both `create_ticket` calls fail, and the first call alone. The counted failure is the task check.

The files store hit counts, not the traces.

## What is not here

The model responses behind the runs live in a local cache and are not saved here, so these files can be inspected and re-scored by the report code, but not re-run without a model. The three regression cases in `regressions/` do carry their model responses and replay offline.
