# 🛡️ SEGURO V2 EMPIRICAL BENCHMARK EVALUATION REPORT

**Evaluation Mode:** `HYBRID`  
**Hardware Platform:** NVIDIA GeForce RTX 3060 12GB | Windows 11 Workstation  
**Date / Timestamp:** `2026-10-16 07:49:49`  
**Philosophy Enforced:** *Tools first. Pixels last. Cloud when stuck. Never fake an output.*

---

## 1. Executive Summary & Core KPIs

| Metric | Measured Value | Spec Target |
|---|---|---|
| **Overall Accuracy** | **100.0%** (1/1) | ≥ 70% Local / ≥ 90% Hybrid |
| **Median Latency** | **127.77s** | < 2.50s |
| **P90 Latency** | **127.77s** | < 8.00s |
| **False success claims** | **0** | 0 |

---

## 2. Category Performance Breakdown

| Category | Tasks | Passed | Accuracy (%) | Median Latency (s) |
|---|---|---|---|---|
| `workflow` | 1 | 1 | **100.0%** | 127.77s |

---

## 3. Detailed Per-Task Execution Ledger

| Task ID | Category | Status | Latency | Steps | Output / Evidence |
|---|---|---|---|---|---|
| `wf_01_news_report_gmail` | `workflow` | ✅ PASS | 127.77s | 16 | 5/5 steps verified (news_search, make_sheet, chart_png, writ |

---

## 4. Notes

- Tools actually called in this run: `auto_output`, `chart_png`, `excel_create_table_and_chart`, `gmail_create_draft`, `news_search`, `node_output`, `word_create_document`.
- Runs whose checker only reads the agent's own status (not independent evidence): 0.
- Tool calls are logged to `seguro/logs/YYYY-MM-DD.jsonl`.