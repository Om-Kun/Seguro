"""Builds the data tables for WORKFLOW_REPORT.md from eval result files (numbers come only from them)."""
import json
import os
import statistics
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    with open(path, encoding="utf-8") as f:
        return [r for r in json.load(f) if r.get("category") == "workflow"]


def table(results_by_mode: dict) -> str:
    modes = list(results_by_mode)
    ids = sorted({r["id"] for rs in results_by_mode.values() for r in rs})
    out = ["| Task | " + " | ".join(f"{m} pass" for m in modes) + " | " +
           " | ".join(f"{m} median s" for m in modes) + " |",
           "|---|" + "---|" * (2 * len(modes))]
    for tid in ids:
        cells, times = [], []
        for m in modes:
            rs = [r for r in results_by_mode[m] if r["id"] == tid]
            p = sum(r["status"] == "PASS" for r in rs)
            cells.append(f"{p}/{len(rs)}" if rs else "–")
            times.append(f"{statistics.median([r['elapsed_s'] for r in rs]):.0f}" if rs else "–")
        out.append(f"| {tid} | " + " | ".join(cells) + " | " + " | ".join(times) + " |")
    for m in modes:
        rs = results_by_mode[m]
        tasks_ok = sum(1 for tid in ids if sum(r["status"] == "PASS" for r in rs if r["id"] == tid) >= 2)
        out.append(f"\n**{m}:** {sum(r['status'] == 'PASS' for r in rs)}/{len(rs)} runs pass; "
                   f"{tasks_ok}/{len(ids)} tasks pass ≥2/3; "
                   f"false 'done' (workflow said done, checker failed): "
                   f"{sum(1 for r in rs if r['status'] != 'PASS' and r.get('agent_status') == 'success')}; "
                   f"replans: {sum(r.get('replans', 0) for r in rs)}; "
                   f"escalated nodes: {sum(r.get('escalated_nodes', 0) for r in rs)}; "
                   f"planner fallbacks: {sum(1 for r in rs if r.get('plan_fallback'))}")
    return "\n".join(out)


def node_time_table(results_by_mode: dict) -> str:
    agg = defaultdict(list)
    for m, rs in results_by_mode.items():
        for r in rs:
            for node, t in (r.get("node_times_s") or {}).items():
                agg[(m, r["id"])].append((node, t))
    out = ["| Mode | Task | Slowest nodes (s) |", "|---|---|---|"]
    for (m, tid), pairs in sorted(agg.items()):
        top = sorted(pairs, key=lambda p: -p[1])[:3]
        out.append(f"| {m} | {tid} | " + ", ".join(f"{n} {t:.0f}" for n, t in top) + " |")
    return "\n".join(out)


def failures(results_by_mode: dict) -> str:
    out = []
    for m, rs in results_by_mode.items():
        for r in rs:
            if r["status"] != "PASS":
                out.append(f"- **{m} / {r['id']} run {r.get('run')}**: checker: {(r.get('error') or '')[:220]} "
                           f"| workflow: {(r.get('evidence') or '')[:260]}")
    return "\n".join(out) or "None."


if __name__ == "__main__":
    files = dict(a.split("=", 1) for a in sys.argv[1:])  # mode=path
    data = {m: load(p) for m, p in files.items()}
    print("## Results\n\n" + table(data) + "\n\n## Where time goes\n\n" + node_time_table(data) +
          "\n\n## Failures\n\n" + failures(data))
