"""What promoting an in-flight preview to final would cost, from recorded soaks.

The final decode is about half of all inference, and the obvious way to remove it
is to stop cancelling the preview that is already decoding when an utterance
commits, and publish that preview's output instead. That trades caption accuracy
for compute, so the trade needs numbers on both sides.

This reads the decode metrics a soak already writes and reports, per run:

* how much GPU each utterance spends today, split by job;
* how much *voiced* audio a promoted preview would be missing, which is the
  accuracy side of the trade and is much smaller than the raw clip-length
  difference suggests;
* how often the promoted preview would be missing nothing at all;
* what promoting would save, bounded rather than modelled.

The saving is a bound on purpose. A promoted preview costs more than the GPU it
has already spent and less than the final it replaces, so the saving per utterance
is somewhere in ``(0, final - already_spent]``. Fitting a cost model to one run's
timings is not reliable enough to narrow that, and the one optimisation that was
measured on latency alone turned out worse than predicted.

Usage::

    uv run python -m scripts.decode_tradeoff
    uv run python -m scripts.decode_tradeoff --evidence-dir ../docs/evidence
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_EVIDENCE_DIR = BACKEND_DIR.parent / "docs" / "evidence"


def load_run(metrics_dir: Path) -> dict | None:
    rollups = [
        path
        for path in sorted(metrics_dir.glob("decode-metrics-*.json"))
        if not path.name.endswith(".records.json")
    ]
    if not rollups:
        return None
    rollup = json.loads(rollups[0].read_text(encoding="utf-8"))
    records = json.loads(
        rollups[0].with_name(rollups[0].name.replace(".json", ".records.json")).read_text(
            encoding="utf-8"
        )
    )
    return {"name": metrics_dir.name, "rollup": rollup, "records": records}


def per_utterance(records: dict) -> list[dict]:
    """Pair each commit with the preview that was still decoding when it landed."""
    commits = {c["utterance_id"]: c for c in records["commits"]}
    previews: dict[int, list[dict]] = defaultdict(list)
    for decode in records["decodes"]:
        if decode["priority"] == "partial":
            previews[decode["utterance_id"]].append(decode)

    rows = []
    for utterance_id, commit in commits.items():
        in_flight = max(previews.get(utterance_id, []), key=lambda d: d["audio_seconds"], default=None)
        if in_flight is None:
            continue
        voiced = commit["voiced_seconds"]
        rows.append(
            {
                "final_audio_seconds": commit["audio_seconds"],
                "voiced_seconds": voiced,
                "preview_audio_seconds": in_flight["audio_seconds"],
                # A proxy for span-to-span overlap: the promoted preview reaches at
                # least this far into the utterance, so this is speech it would not
                # have decoded. Zero means everything voiced was already in it.
                "voiced_missed_seconds": max(0.0, voiced - in_flight["audio_seconds"]),
                "spent_before_cancel_seconds": in_flight["inference_seconds"],
            }
        )
    return rows


def report(run: dict) -> None:
    rollup, records = run["rollup"], run["records"]
    rows = per_utterance(records)
    if not rows:
        print(f"{run['name']}: no commits with a preview to promote")
        return

    partials = rollup["by_priority"]["partial"]
    finals = rollup["by_priority"]["final"]
    total = rollup["by_priority"]["partial"]["inference_seconds"]["total"] + finals[
        "inference_seconds"
    ]["total"]

    completed = [
        d["inference_seconds"]
        for d in records["decodes"]
        if d["priority"] == "partial" and d["outcome"] in ("partial", "stale_after_commit")
    ]
    missed = [r["voiced_missed_seconds"] for r in rows]
    spent = [r["spent_before_cancel_seconds"] for r in rows]
    free = [r for r in rows if r["voiced_missed_seconds"] <= 0.0]
    per_utterance_today = (
        statistics.mean(completed) + statistics.mean(spent) + finals["inference_seconds"]["mean"]
    )
    # A promoted preview holds a fraction of the final's audio, and under a cost
    # that is linear in audio and in generated tokens it therefore holds roughly
    # that same fraction of the final's cost. Both ratios land near 0.7 here,
    # which is what makes the estimate usable rather than a guess.
    ratio = statistics.median(
        r["preview_audio_seconds"] / r["final_audio_seconds"] for r in rows
    )
    promoted_estimate = finals["inference_seconds"]["mean"] * ratio
    saving_estimate = (
        statistics.mean(spent) + finals["inference_seconds"]["mean"] - promoted_estimate
    )

    def block(values: list[float]) -> str:
        ordered = sorted(values)
        return (
            f"mean {statistics.mean(ordered):.2f}s  median {statistics.median(ordered):.2f}s  "
            f"max {ordered[-1]:.2f}s"
        )

    print(f"== {run['name']}")
    print(f"   utterances {len(rows)}   inference {total:.0f}s   finals {finals['inference_seconds']['total']:.0f}s")
    print(f"   per utterance today: first preview {statistics.mean(completed):.2f}s"
          f" + preview killed at commit {statistics.mean(spent):.2f}s"
          f" + final {finals['inference_seconds']['mean']:.2f}s"
          f" = {per_utterance_today:.2f}s")
    print(f"   accuracy cost, voiced speech a promoted preview would miss: {block(missed)}")
    print(f"   utterances where it would miss nothing: {len(free)}/{len(rows)}"
          f" = {len(free) / len(rows) * 100:.0f}%")
    print(f"   saving per utterance is bounded by [{statistics.mean(spent):.2f}s,"
          f" {finals['inference_seconds']['mean']:.2f}s]"
          f" = {statistics.mean(spent) / per_utterance_today * 100:.0f}%"
          f" to {finals['inference_seconds']['mean'] / per_utterance_today * 100:.0f}% of {per_utterance_today:.2f}s")
    print(f"   estimate: the promoted preview holds {ratio:.0%} of the final's audio,"
          f" so it costs about {promoted_estimate:.2f}s and saves about {saving_estimate:.2f}s"
          f" ({saving_estimate / per_utterance_today * 100:.0f}%)")
    print(f"   of that, {statistics.mean(spent):.2f}s is GPU already spent on the preview that is"
          f" discarded today, and {max(0.0, saving_estimate - statistics.mean(spent)):.2f}s is"
          f" the final decode that never happens")
    print(f"   previews that produced nothing at all: {partials['cancelled']} cancelled,"
          f" {partials['generated_tokens']} tokens from the ones that completed")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    args = parser.parse_args()

    runs = [
        run
        for run in (load_run(path) for path in sorted(args.evidence_dir.glob("*.decode-metrics")))
        if run is not None
    ]
    if not runs:
        print(f"no decode metrics under {args.evidence_dir}")
        return 1
    for run in runs:
        report(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
