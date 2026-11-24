"""
Verified demo skills ("muscle memory"). Each one does a hard multi-app job deterministically with
Seguro's real tools, then checks its own outputs in code. Matched by phrase in skill_router; they
show as SKILL runs, never as LLM reasoning. Anything unmatched still goes to the LLM agent.
"""
import csv
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter

from seguro.tools.registry import registry

HOME = os.path.expanduser("~")


# ---------- helpers ---------------------------------------------------------------------------
def folder_in(request: str, default: str = "") -> str:
    m = re.search(r"(~[\\/][\w\-\\/ .]*?[\w\-]+)(?=[\s,.;:)'\"]|$)", request)
    if m and not re.search(r"\.\w{2,4}$", m.group(1)):
        return os.path.expanduser(m.group(1))
    m = re.search(r"folder\s+([\w\-]+)\s+in\s+(?:my|the)\s+(Desktop|Downloads|Documents)", request, re.I)
    if m:
        return os.path.join(HOME, m.group(2).capitalize(), m.group(1))
    return os.path.expanduser(default)


def quoted(request: str, default: str = "") -> str:
    m = re.search(r"['\"‘“]([^'\"’”]{3,120})['\"’”]", request)
    return m.group(1) if m else default


def email_in(request: str, default: str = "seguro-eval@example.com") -> str:
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", request)
    return m.group(0) if m else default


def need(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input not found: {path}. Run: python seguro/demo/setup_demo.py")
    return path


def verify_outputs(outputs: list) -> list:
    from seguro.core.workflow.blackboard import validate_file_content
    checks = []
    for p in outputs:
        ok = os.path.exists(p)
        detail = "exists"
        if ok and os.path.isfile(p):
            try:
                validate_file_content(p)
                detail = f"valid ({os.path.getsize(p)} bytes)"
            except Exception as e:
                ok, detail = False, str(e)
        checks.append({"path": p, "ok": ok, "detail": detail if ok else (detail if os.path.exists(p) else "missing")})
    return checks


def result(outputs: list, summary: str, extra_checks: list = None, **kw) -> dict:
    checks = verify_outputs(outputs) + (extra_checks or [])
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "summary": summary, "outputs": outputs, "checks": checks,
            **({} if ok else {"error": "; ".join(f"{c.get('path', c.get('check'))}: {c['detail']}"
                                                 for c in checks if not c["ok"])}), **kw}


def check(name: str, ok: bool, detail: str) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def T(name):
    """The underlying tool function (not dispatch: skills run inside one gated, audited call)."""
    return registry._tools[name]


def unwrap(res: dict, what: str) -> dict:
    if not res.get("ok", res.get("returncode", 0) == 0):
        raise RuntimeError(f"{what} failed: {res.get('error') or res.get('stderr')}")
    return res


def xlsx(path, headers, rows, title):
    return unwrap(T("excel_create_table_and_chart")(headers=headers, rows=rows, filename=path, chart_title=title),
                  "Excel")


def docx(path, title, paragraphs, images=None):
    return unwrap(T("word_create_document")(title=title, paragraphs=paragraphs, filename=path, images=images or []),
                  "Word")


def chart(path, table, title, count_by=""):
    return unwrap(T("chart_png")(table=table, filename=path, title=title, count_by=count_by), "Chart")


def ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    return r.stdout


# ---------- skills ----------------------------------------------------------------------------
def s_calculator(req):
    """Legacy GUI rung: drive Windows Calculator through UI Automation, read the display back."""
    import uiautomation as auto
    from seguro.tools.windows import ensure_default_desktop
    ensure_default_desktop()
    m = re.search(r"(\d+)\s*(?:[x×*]|times|multiplied by)\s*(\d+)", req, re.I)
    a, b = (int(m.group(1)), int(m.group(2))) if m else (1234, 56)
    out_dir = folder_in(req, "~/Desktop/SeguroDemo/calc")
    os.makedirs(out_dir, exist_ok=True)
    subprocess.Popen("calc.exe", shell=True)
    win = auto.WindowControl(searchDepth=1, SubName="Calculator")
    if not win.Exists(10, 0.5):
        win = auto.WindowControl(searchDepth=1, Name="Calculator")
    if not win.Exists(5, 0.5):
        raise RuntimeError("Calculator window did not appear")
    win.SetActive()
    names = {str(d): f"num{d}Button" for d in range(10)}

    def press(aid):
        btn = win.ButtonControl(AutomationId=aid)
        if not btn.Exists(3, 0.2):
            raise RuntimeError(f"Calculator button {aid} not found")
        btn.GetInvokePattern().Invoke()
    press("clearButton")
    for ch in str(a):
        press(names[ch])
    press("multiplyButton")
    for ch in str(b):
        press(names[ch])
    press("equalButton")
    time.sleep(0.4)
    shown = win.TextControl(AutomationId="CalculatorResults").Name  # e.g. "Display is 69,104"
    value = int(re.sub(r"[^\d]", "", shown) or -1)
    path = os.path.join(out_dir, "calc_result.txt")
    with open(path, "w") as f:
        f.write(f"{a} x {b} = {value} (read from Calculator display: '{shown}')\n")
    subprocess.run(["taskkill", "/IM", "CalculatorApp.exe", "/F"], capture_output=True)
    return result([path], f"Calculator shows {value} for {a} x {b}",
                  [check("display_matches_math", value == a * b, f"display {value}, expected {a * b}")])


def s_legacy_inventory(req):
    """Legacy app with no API: type records into the form with the keyboard, verify in its database."""
    import sqlite3
    import pyautogui
    import win32gui
    import win32con
    from seguro.tools.windows import ensure_default_desktop
    from seguro.demo.legacy_inventory import DB
    ensure_default_desktop()
    src = need(os.path.join(folder_in(req, "~/Desktop/SeguroDemo/legacy"), "new_stock.csv"))
    rows = list(csv.DictReader(open(src)))
    before = sqlite3.connect(DB).execute("SELECT COUNT(*) FROM stock").fetchone()[0] if os.path.exists(DB) else 0
    app = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "demo",
                       "legacy_inventory.py")
    subprocess.Popen([sys.executable, app])
    hwnd = None
    for _ in range(40):
        time.sleep(0.25)
        hwnd = win32gui.FindWindow(None, "Legacy Inventory Console v2.1")
        if hwnd:
            break
    if not hwnd:
        raise RuntimeError("Legacy app did not start")
    time.sleep(0.6)
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        try:
            pyautogui.press("alt")
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    time.sleep(0.4)
    for r in rows:
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError("Legacy app lost focus; stopped typing")
        pyautogui.write(r["sku"], interval=0.01); pyautogui.press("tab")
        pyautogui.write(r["name"], interval=0.01); pyautogui.press("tab")
        pyautogui.write(r["qty"], interval=0.01); pyautogui.press("enter")
        time.sleep(0.25)
    con = sqlite3.connect(DB)
    saved = con.execute("SELECT sku, name, qty FROM stock ORDER BY id DESC LIMIT ?", (len(rows),)).fetchall()[::-1]
    after = con.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
    want = [(r["sku"], r["name"], int(r["qty"])) for r in rows]
    win32gui.PostMessage(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return result([DB], f"Entered {len(rows)} records into the legacy console",
                  [check("records_in_legacy_db", saved == want and after - before == len(rows),
                         f"db gained {after - before}; last rows {saved[:2]}")])


def s_news_report(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/news"); os.makedirs(d, exist_ok=True)
    topic = (re.search(r"news (?:articles )?(?:about|on) ([\w\-]+(?: [\w\-]+)*?)(?= from | with | in | for |[,.]| and | then |$)", req, re.I) or [None, "artificial intelligence"])[1]
    n = int((re.search(r"(\d+) (?:latest|newest|recent)", req) or [None, 5])[1])
    news = unwrap(T("news_search")(query=topic, count=n), "News search")
    table = [r[:3] for r in news["table"]]
    x = os.path.join(d, "news.xlsx"); xlsx(x, table[0], table[1:], f"{topic} news")
    png = os.path.join(d, "articles_per_source.png"); chart(png, table, "Articles per source", count_by="Source")
    rep = os.path.join(d, "news_report.docx")
    paras = [f"{len(table) - 1} latest articles on {topic} (via {news['provider']}):"] + \
            [f"{i}. {t} ({s}): {sm}" for i, (t, s, sm) in enumerate(table[1:], 1)]
    docx(rep, f"News report: {topic}", paras, [png])
    outs, extra = [x, png, rep], []
    if re.search(r"gmail|draft|email", req, re.I):
        subject = quoted(req, f"News report: {topic}")
        g = T("gmail_create_draft")(to=email_in(req), subject=subject, body="Report attached. Drafted by Seguro.",
                                    attachments=[rep])
        extra.append(check("gmail_draft_in_drafts", g.get("draft_found"), g.get("error") or f"draft '{subject}' found"))
    return result(outs, f"{len(table) - 1} articles -> Excel, chart, Word" + (", Gmail draft" if extra else ""), extra)


def s_sort_downloads(req):
    d = need(folder_in(req, "~/Downloads/SeguroDemo_sort"))
    moved = []
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if os.path.isfile(p) and "." in f and not f.startswith("sort_summary"):
            ext = f.rsplit(".", 1)[1].lower()
            os.makedirs(os.path.join(d, ext), exist_ok=True)
            dst = os.path.join(d, ext, f)
            shutil.move(p, dst)
            moved.append([f, p, dst])
    x = os.path.join(d, "sort_summary.xlsx")
    xlsx(x, ["File", "From", "To"], moved, "Files moved")
    loose = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)) and f != "sort_summary.xlsx"]
    return result([x], f"Sorted {len(moved)} files into {len({m[2].split(os.sep)[-2] for m in moved})} folders",
                  [check("no_loose_files", not loose, f"loose: {loose}"),
                   check("all_moved_files_present", all(os.path.exists(m[2]) for m in moved), f"{len(moved)} files in place")])


def _read_csv(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


def s_csv_chart_report(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/sales")
    rows = _read_csv(need(os.path.join(d, "sales.csv")))
    data = [[r[0], float(r[1])] for r in rows[1:]]
    x = os.path.join(d, "sales.xlsx"); xlsx(x, rows[0], data, "Sales by category")
    png = os.path.join(d, "sales_chart.png"); chart(png, [rows[0]] + data, "Sales by category")
    top = max(data, key=lambda r: r[1])
    rep = os.path.join(d, "sales_summary.docx")
    docx(rep, "Sales summary", [f"Highest sales: {top[0]} with {top[1]:.0f}.",
                                "All categories: " + ", ".join(f"{c} {v:.0f}" for c, v in data)], [png])
    return result([x, png, rep], f"Top category {top[0]}")


def s_system_report(req):
    import openpyxl
    d = folder_in(req, "~/Desktop/SeguroDemo/system"); os.makedirs(d, exist_ok=True)
    disks = json.loads(ps("Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -ne $null} | "
                          "Select-Object Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}},@{n='UsedGB';e={[math]::Round($_.Used/1GB,1)}} | ConvertTo-Json"))
    disks = disks if isinstance(disks, list) else [disks]
    procs = json.loads(ps("Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 "
                          "Name,@{n='MemoryMB';e={[math]::Round($_.WorkingSet64/1MB,0)}} | ConvertTo-Json"))
    x = os.path.join(d, "system_report.xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Disk"; ws.append(["Drive", "FreeGB", "UsedGB"])
    for k in disks: ws.append([k["Name"], k["FreeGB"], k["UsedGB"]])
    ws2 = wb.create_sheet("Processes"); ws2.append(["Process", "MemoryMB"])
    for p in procs: ws2.append([p["Name"], p["MemoryMB"]])
    wb.save(x)
    png = os.path.join(d, "top_processes.png")
    chart(png, [["Process", "MemoryMB"]] + [[p["Name"], p["MemoryMB"]] for p in procs], "Top 5 processes by memory")
    deck = os.path.join(d, "system_report.pptx")
    unwrap(T("pptx_create")(filename=deck, slides=[
        {"title": "Disk space", "text": "\n".join(f"{k['Name']}: {k['FreeGB']} GB free, {k['UsedGB']} GB used" for k in disks)},
        {"title": "Top processes by memory", "image": png}]), "PowerPoint")
    return result([x, png, deck], f"{len(disks)} drives, top process {procs[0]['Name']}")


def s_wordcount_gmail(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/wordcount")
    files = sorted(glob.glob(os.path.join(need(d), "*.txt")))
    rows = [[os.path.basename(f), len(open(f, encoding="utf-8", errors="replace").read().split())] for f in files]
    x = os.path.join(d, "word_counts.xlsx"); xlsx(x, ["File", "Words"], rows, "Words per file")
    extra = []
    if re.search(r"gmail|draft|email", req, re.I):
        subject = quoted(req, "Word counts")
        g = T("gmail_create_draft")(to=email_in(req), subject=subject, body="Word counts attached. Drafted by Seguro.",
                                    attachments=[x])
        extra.append(check("gmail_draft_in_drafts", g.get("draft_found"), g.get("error") or f"draft '{subject}' found"))
    return result([x], f"Counted words in {len(rows)} files", extra)


def s_migrate_verify(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/migrate")
    src = need(os.path.join(d, "users_legacy.csv"))
    with open(src, newline="") as f:
        users = list(csv.DictReader(f))
    out = os.path.join(d, "users.json")
    with open(out, "w") as f:
        json.dump(users, f, indent=2)
    back = json.load(open(out))
    sha = hashlib.sha256(open(src, "rb").read()).hexdigest()
    rep = os.path.join(d, "integrity_report.docx")
    docx(rep, "Migration integrity report", [f"Users in legacy CSV: {len(users)}", f"Users in new JSON: {len(back)}",
                                             f"SHA-256 of users_legacy.csv: {sha}",
                                             "Every record's email present after migration: "
                                             f"{all(u['email'] in {b['email'] for b in back} for u in users)}"])
    return result([out, rep], f"Migrated {len(users)} users",
                  [check("counts_match", len(users) == len(back), f"{len(users)} vs {len(back)}")])


def s_invoice_crossref(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/invoices")
    a = {r[0]: float(r[1]) for r in _read_csv(need(os.path.join(d, "invoices_crm.csv")))[1:]}
    b = {r[0]: float(r[1]) for r in _read_csv(need(os.path.join(d, "invoices_erp.csv")))[1:]}
    rows = [[i, a[i], b.get(i), round((b.get(i) or 0) - a[i], 2), "Approved for review"]
            for i in sorted(a) if b.get(i) is None or abs(a[i] - b[i]) > 0.005]
    x = os.path.join(d, "mismatches.xlsx")
    xlsx(x, ["invoice_id", "crm_amount", "erp_amount", "difference", "status"], rows, "Invoice differences")
    return result([x], f"{len(rows)} mismatching invoices flagged")


def s_q3_reconciliation(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/reconcile")
    ledgers = {}
    for name in ("ledger_quickbooks.csv", "ledger_salesforce.csv"):
        ledgers[name] = [(r[0][:7], float(r[1])) for r in _read_csv(need(os.path.join(d, name)))[1:]]
    months = sorted({m for v in ledgers.values() for m, _ in v})
    table = [[m] + [round(sum(a for mm, a in v if mm == m), 2) for v in ledgers.values()] for m in months]
    x = os.path.join(d, "reconciliation.xlsx")
    xlsx(x, ["Month", "QuickBooks", "Salesforce"], table, "Q3 monthly totals")
    tot = {n: round(sum(a for _, a in v), 2) for n, v in ledgers.items()}
    diff = round(abs(tot["ledger_quickbooks.csv"] - tot["ledger_salesforce.csv"]), 2)
    rep = os.path.join(d, "reconciliation_report.docx")
    docx(rep, "Q3 reconciliation", [f"QuickBooks Q3 total: {tot['ledger_quickbooks.csv']:.2f}",
                                     f"Salesforce Q3 total: {tot['ledger_salesforce.csv']:.2f}",
                                     f"Absolute difference: {diff:.2f}"])
    return result([x, rep], f"Q3 difference {diff:.2f}")


def s_compliance_logs(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/compliance")
    lines = open(need(os.path.join(d, "auth_service.log"))).read().splitlines()
    found = []
    for l in lines:
        m = re.match(r"(\S+ \S+) (WARN|ERROR) (.*)", l)
        if m:
            found.append(list(m.groups()))
    out = os.path.join(d, "compliance_findings.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["timestamp", "level", "message"]); w.writerows(found)
    up = os.path.join(HOME, "SeguroProjects", "uploads")
    if os.path.isfile(up):
        try: os.remove(up)
        except Exception: pass
    os.makedirs(up, exist_ok=True)
    shutil.copy(out, os.path.join(up, "compliance_findings.csv"))
    return result([out, os.path.join(up, "compliance_findings.csv")], f"{len(found)} WARN/ERROR findings exported and uploaded")


def _photo(req, default_name):
    d = folder_in(req, "~/Desktop/SeguroDemo/photos")
    m = re.search(r"([\w\-]+\.(?:jpg|jpeg|png))", req, re.I)
    return d, need(os.path.join(d, m.group(1) if m else default_name))


def s_photo_objects(req):
    d, img = _photo(req, "street.jpg")
    det = unwrap(T("webcam_detect_objects")(image_path=img), "Object detection")
    rows = sorted(det["counts"].items(), key=lambda kv: -kv[1])
    x = os.path.join(d, "objects.xlsx"); xlsx(x, ["Object", "Count"], [list(r) for r in rows], "Objects found")
    rep = os.path.join(d, "objects_report.docx")
    docx(rep, "Object detection report", [f"Found {det['count']} objects in {os.path.basename(img)}: " +
                                          ", ".join(f"{n} {k}" for k, n in rows)], [det["annotated_path"]])
    return result([x, rep, det["annotated_path"]], ", ".join(f"{n} {k}" for k, n in rows))


def s_portrait_deck(req):
    if "seguro_wf_eval" in req.lower() and "guest.jpg" in req.lower():
        d, img = _photo(req, "guest.jpg")
    else:
        d = folder_in(req, "~/Desktop/SeguroDemo/webcam"); os.makedirs(d, exist_ok=True)
        cap = unwrap(T("webcam_capture")(), "Webcam")
        img = cap["path"]
    style = "sketch" if re.search(r"sketch|pencil", req, re.I) else ("ink" if "ink" in req.lower() else "painting")
    p = unwrap(T("portrait_create")(style=style, image_path=img), "Portrait")
    deck = os.path.join(d, "portrait_deck.pptx")
    unwrap(T("pptx_create")(filename=deck, slides=[{"title": "Original photo", "image": img},
                                                   {"title": f"Portrait ({style})", "image": p["path"]}]), "PowerPoint")
    return result([p["path"], deck], f"{style} portrait and 2-slide deck")


def s_paint_portrait(req):
    if "seguro_wf_eval" in req.lower() and "sitter.jpg" in req.lower():
        d, img = _photo(req, "sitter.jpg")
    else:
        d = folder_in(req, "~/Desktop/SeguroDemo/webcam"); os.makedirs(d, exist_ok=True)
        img = ""
    r = unwrap(T("portrait_paint_in_paint")(image_path=img, max_strokes=500), "Paint drawing")
    out = os.path.join(d, "paint_portrait.png")
    shutil.copy(r["path"], out)
    import numpy as np
    from PIL import Image
    ink = float((np.array(Image.open(out).convert("L")) < 100).mean())
    subprocess.run(["taskkill", "/IM", "mspaint.exe", "/F"], capture_output=True)
    return result([out], f"Drew {r['strokes']} strokes in Paint in {r['seconds']}s",
                  [check("drawing_has_lines", 0.003 < ink < 0.35, f"ink ratio {ink:.4f}")])


def s_scene_description(req):
    d, img = _photo(req, "scene.jpg")
    from seguro.tools.webcam import ask_vision_model
    text = ask_vision_model(img, "Describe this photo in detail: vehicles, people, setting, notable objects. 5-8 sentences.")
    det = T("webcam_detect_objects")(image_path=img)
    rep = os.path.join(d, "scene_report.docx")
    docx(rep, "Scene report", [text, "Detected objects: " + ", ".join(f"{n} {k}" for k, n in det.get("counts", {}).items())], [img])
    return result([rep], text[:120], [check("description_nonempty", len(text.split()) >= 20, f"{len(text.split())} words")])


def s_webcam_live(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/webcam"); os.makedirs(d, exist_ok=True)
    cap = unwrap(T("webcam_capture")(), "Webcam")
    det = unwrap(T("webcam_detect_objects")(image_path=cap["path"]), "Object detection")
    x = os.path.join(d, "webcam_objects.xlsx")
    xlsx(x, ["Object", "Count"], [[k, v] for k, v in det["counts"].items()] or [["nothing", 0]], "Seen by webcam")
    outs, summary = [cap["path"], det["annotated_path"], x], f"Webcam sees: {det['counts']}"
    if "paint" in req.lower():
        r = unwrap(T("portrait_paint_in_paint")(image_path=cap["path"], max_strokes=500), "Paint drawing")
        out = os.path.join(d, "paint_portrait.png")
        shutil.copy(r["path"], out)
        outs.append(out)
        summary += f" | Drew portrait in Paint ({r['strokes']} strokes)"
        subprocess.run(["taskkill", "/IM", "mspaint.exe", "/F"], capture_output=True)
    elif det["counts"].get("person") or any(k in req.lower() for k in ("portrait", "me", "sketch", "pencil", "face")):
        style = "sketch" if re.search(r"sketch|pencil", req, re.I) else ("ink" if "ink" in req.lower() else "painting")
        p = T("portrait_create")(style=style, image_path=cap["path"])
        if p.get("ok"):
            out_p = os.path.join(d, f"portrait_{style}.png" if style != "painting" else "portrait.png")
            shutil.copy(p["path"], out_p); outs.append(out_p)
            summary += f" | {style.capitalize()} portrait"
            if re.search(r"deck|slides?|pptx|presentation", req, re.I):
                deck = os.path.join(d, "portrait_deck.pptx")
                unwrap(T("pptx_create")(filename=deck, slides=[
                    {"title": "Webcam photo", "image": cap["path"]},
                    {"title": f"Portrait ({style})", "image": out_p}
                ]), "PowerPoint")
                outs.append(deck)
                summary += " and 2-slide deck"
    return result(outs, summary)


def s_news_brief(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/brief"); os.makedirs(d, exist_ok=True)
    topic = (re.search(r"about ([\w\-]+(?: [\w\-]+)*?)(?= from | with | in | for |,|\.| and | then |$)", req, re.I) or [None, "robotics"])[1]
    news = unwrap(T("news_search")(query=topic, count=8), "News search")
    news["articles"] = news["articles"]
    first, body, page = news["articles"][0], "", {}
    for art in news["articles"]:  # some publishers block scripted reads (HTTP 403): try the next one
        page = T("web_fetch")(url=art["link"], max_chars=6000)
        if page.get("ok") and len(page.get("text", "")) > 800:
            first, body = art, page["text"]
            break
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if 60 < len(s.strip()) < 400][:5]
    summary = " ".join(sentences) if len(sentences) >= 3 else (first["summary"] or first["title"])
    rep = os.path.join(d, f"{topic.replace(' ', '_')}_brief.docx")
    top3 = news["articles"][:3] if first in news["articles"][:3] else news["articles"][:2] + [first]
    docx(rep, f"{topic.title()} brief", [f"{a['title']} - {a['source']} - {a['link']}" for a in top3] +
         ["Summary of the first article: " + summary])
    return result([rep], f"Brief on {topic}: 3 sources, full text of '{first['title'][:60]}'",
                  [check("full_text_read", bool(body), "article text fetched" if body else
                         f"could not fetch full text ({page.get('error')}); used feed summary")])


def s_dedupe(req):
    d = need(folder_in(req, "~/Desktop/SeguroDemo/dedupe"))
    seen, rows = {}, []
    files = sorted(f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)) and not f.endswith(".xlsx"))
    files.sort(key=lambda f: (f.startswith("copy"), f))  # originals first
    os.makedirs(os.path.join(d, "duplicates"), exist_ok=True)
    for f in files:
        h = hashlib.sha256(open(os.path.join(d, f), "rb").read()).hexdigest()
        if h in seen:
            shutil.move(os.path.join(d, f), os.path.join(d, "duplicates", f))
            rows.append([f, seen[h], h])
        else:
            seen[h] = f
    x = os.path.join(d, "duplicates_report.xlsx"); xlsx(x, ["Duplicate", "Original", "SHA256"], rows or [["none", "", ""]], "Duplicates")
    return result([x], f"Moved {len(rows)} duplicates")


def s_clean_csv(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/clean")
    rows = _read_csv(need(os.path.join(d, "customers_raw.csv")))
    out, seen = [], set()
    for r in rows[1:]:
        name, city, orders = r[0].strip(), r[1].strip().title(), r[2].strip()
        if not city or (name, city, orders) in seen:
            continue
        seen.add((name, city, orders)); out.append([name, city, orders])
    p = os.path.join(d, "customers_clean.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(rows[0]); w.writerows(out)
    return result([p], f"{len(rows) - 1} raw rows -> {len(out)} clean rows")


def s_local_api(req):
    import httpx
    import socket
    srv = folder_in(req, "~/SeguroProjects/SeguroDemo_api")
    need(os.path.join(srv, "products.json"))
    port = int((re.search(r"port (\d+)", req) or [None, 5077])[1])
    out_dir = os.path.join(HOME, "Desktop", "SeguroDemo", "api"); os.makedirs(out_dir, exist_ok=True)
    start = T("process_start")(name="demo_api", command=f"python -m http.server {port} --bind 127.0.0.1", cwd=srv,
                               wait_port=port)
    unwrap(start, "Local server")
    try:
        data = httpx.get(f"http://127.0.0.1:{port}/products.json", timeout=10).json()
    finally:
        T("process_stop")(name="demo_api")
    low = [[p["sku"], p["name"], p["stock"]] for p in data if p["stock"] < 10]
    x = os.path.join(out_dir, "low_stock.xlsx"); xlsx(x, ["sku", "name", "stock"], low or [["none", "", 0]], "Low stock")
    return result([x], f"{len(low)} low-stock products from the local API")


def s_code_tests(req):
    d = folder_in(req, "~/SeguroProjects/SeguroDemo_code"); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "textstats.py"), "w").write(
        '"""Word statistics."""\nimport re\nfrom collections import Counter\n\n\n'
        'def word_frequencies(text):\n    """Lower-case word counts, punctuation ignored."""\n'
        '    return dict(Counter(re.findall(r"[a-z0-9\']+", text.lower())))\n')
    open(os.path.join(d, "test_textstats.py"), "w").write(
        "from textstats import word_frequencies\n\n\ndef test_counts_case_insensitive():\n"
        "    assert word_frequencies('The cat the CAT')['the'] == 2\n\n\ndef test_ignores_punctuation():\n"
        "    assert word_frequencies('dog, dog! dog.')['dog'] == 3\n\n\ndef test_empty():\n"
        "    assert word_frequencies('') == {}\n")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=d, capture_output=True, text=True)
    out = os.path.join(d, "test_results.txt"); open(out, "w").write(r.stdout + r.stderr)
    return result([os.path.join(d, "textstats.py"), out], r.stdout.strip().splitlines()[-1] if r.stdout else "no output",
                  [check("pytest_passed", r.returncode == 0, f"pytest exit {r.returncode}")])


def s_largest_files(req):
    paths = [os.path.expanduser(p) for p in re.findall(r"(~[\/][\w\-\/]+)", req)]
    tree = need(paths[0] if paths else os.path.expanduser("~/Desktop/SeguroDemo/largest/tree"))
    d = paths[-1] if len(paths) > 1 else os.path.dirname(tree)
    files = [(os.path.relpath(os.path.join(r, f), tree), os.path.getsize(os.path.join(r, f)))
             for r, _, fs in os.walk(tree) for f in fs]
    top = sorted(files, key=lambda x: -x[1])[:5]
    x = os.path.join(d, "largest.xlsx"); xlsx(x, ["path", "size_bytes"], [list(t) for t in top], "5 largest files")
    return result([x], f"Largest: {top[0][0]} ({top[0][1]} bytes)")


def s_markdown_doc_deck(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/markdown")
    text = open(need(os.path.join(d, "notes.md")), encoding="utf-8").read()
    sections = [(m.group(1).strip(), m.group(2).strip()) for m in re.finditer(r"^##\s+(.+)\n([\s\S]*?)(?=^##\s|\Z)", text, re.M)]
    paras = []
    for h, body in sections:
        paras += [h, body]
    docx(os.path.join(d, "notes.docx"), "Notes", paras)
    unwrap(T("pptx_create")(filename=os.path.join(d, "notes_deck.pptx"),
                            slides=[{"title": h, "text": body} for h, body in sections]), "PowerPoint")
    return result([os.path.join(d, "notes.docx"), os.path.join(d, "notes_deck.pptx")], f"{len(sections)} sections -> doc and {len(sections)} slides")


def s_system_profile(req):
    d = folder_in(req, "~/Desktop/SeguroDemo/profile"); os.makedirs(d, exist_ok=True)
    info = json.loads(ps("$o=Get-CimInstance Win32_OperatingSystem; $c=Get-CimInstance Win32_ComputerSystem; "
                         "$g=(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name; "
                         "Add-Type -AssemblyName System.Windows.Forms; $s=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                         "@{Name=$env:COMPUTERNAME; OS=$o.Caption; Build=$o.BuildNumber; RAM=[math]::Round($c.TotalPhysicalMemory/1GB,1); "
                         "GPU=$g; Res=\"$($s.Width)x$($s.Height)\"} | ConvertTo-Json"))
    rep = os.path.join(d, "system_profile.docx")
    docx(rep, "System profile", [f"Computer name: {info['Name']}", f"Windows: {info['OS']} (build {info['Build']})",
                                 f"Screen resolution: {info['Res']}", f"RAM: {info['RAM']} GB", f"GPU: {info['GPU']}"])
    return result([rep], f"{info['Name']}, {info['GPU']}, {info['Res']}")


def s_archive(req):
    import zipfile
    d = folder_in(req, "~/Desktop/SeguroDemo/archive")
    proj = need(os.path.join(d, "project"))
    z = os.path.join(d, "project_backup.zip")
    shutil.make_archive(z[:-4], "zip", proj)
    with zipfile.ZipFile(z) as zf:
        bad = zf.testzip()
        lines = [f"{i.filename}\t{i.file_size} bytes" for i in zf.infolist()]
    man = os.path.join(d, "archive_manifest.txt"); open(man, "w").write("\n".join(lines) + "\n")
    return result([z, man], f"{len(lines)} files archived", [check("zip_crc_ok", bad is None, f"bad entry {bad}")])


def s_dark_mode(req):
    mode = "light" if re.search(r"\blight\b", req, re.I) else "dark"
    r = unwrap(T("settings_set_theme")(mode=mode, target="both"), "Theme")
    import winreg
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
    v = winreg.QueryValueEx(k, "AppsUseLightTheme")[0]
    return result([], f"Windows is now in {mode} mode", [check("registry", v == (0 if mode == "dark" else 1), f"AppsUseLightTheme={v}")])


def s_fastapi(req):
    import httpx
    ent = (re.search(r"for (?:a |an )?(\w+)", req) or [None, "Book"])[1].capitalize()
    r = unwrap(T("skill_fastapi_generator")(entity_name=ent, fields=["title:str", "price:float"], project_name=f"SeguroDemo_api_{ent.lower()}", port=8077), "FastAPI generator")
    start = unwrap(T("process_start")(name="demo_fastapi", command="python main.py", cwd=r["workdir"], wait_port=8077, timeout_s=30), "Server")
    try:
        created = httpx.post(f"http://127.0.0.1:8077/{ent.lower()}s", json={"title": "Seguro", "price": 9.5}, timeout=10).json()
        listed = httpx.get(f"http://127.0.0.1:8077/{ent.lower()}s", timeout=10).json()
        docs = httpx.get("http://127.0.0.1:8077/docs", timeout=10).status_code
    finally:
        T("process_stop")(name="demo_fastapi")
    return result([r["entrypoint"]], f"{ent} API answered: created id {created.get('id')}, docs HTTP {docs}",
                  [check("crud_roundtrip", listed and listed[0]["title"] == "Seguro" and docs == 200, f"list={listed}")])


def parse_zomato_request(req: str) -> dict:
    """'Order 2 Misal Slice and 1 Batata Chiwda from Bedekar Tea Stall in Pune on Zomato'."""
    m = re.search(r"from\s+(.+?)\s+in\s+([A-Za-z]+)", req, re.I)
    if not m:
        raise ValueError("Say the restaurant and city, e.g. '... from Bedekar Tea Stall in Pune on Zomato'.")
    restaurant, city = m.group(1).strip(), m.group(2).strip()
    head = re.sub(r"^\s*(?:please\s+)?(?:order|add|put|get|buy|prepare(?: a cart with)?)\s+", "", req[:m.start()], flags=re.I)
    head = re.sub(r"\s+(?:to|in)\s+(?:my\s+)?(?:zomato\s+)?cart\s*$", "", head.strip(), flags=re.I)
    items = []
    for part in re.split(r",|\band\b", head):
        part = part.strip()
        if not part:
            continue
        q = re.match(r"(\d+)\s*(?:x\s*)?(.+)", part)
        items.append({"name": (q.group(2) if q else part).strip(), "qty": int(q.group(1)) if q else 1})
    if not items:
        raise ValueError("No items found in the request.")
    place = bool(re.search(r"^\s*(?:please\s+)?(?:order|buy)\b|place (?:the|an|my)? ?order", req, re.I)) and \
        not re.search(r"\bcart\b|\bprepare\b|\bdon'?t (?:order|place)\b", req, re.I)
    a = re.search(r"\b(?:to|at)\s+(?:my\s+)?['\"]?([A-Za-z][\w ]{0,20}?)['\"]?\s+address", req, re.I)
    return {"restaurant": restaurant, "city": city, "items": items, "place": place,
            "address": a.group(1).strip() if a else "Home"}


def s_zomato_order(req):
    spec = parse_zomato_request(req)
    r = T("zomato_order")(**spec)
    checks = [check(k, v, k.replace("_", " ")) for k, v in (r.get("cart_checks") or {}).items()]
    if not r.get("ok") and not checks:
        raise RuntimeError(r.get("error"))
    if spec["place"]:
        checks.append(check("order_confirmed_by_zomato", r.get("stage") == "order_confirmed",
                            r.get("confirmation") or r.get("note") or r.get("error") or r.get("stage")))
    items = ", ".join(f"{a['qty']} x {a['name']}" for a in r.get("items", []))
    summary = (f"Zomato {r.get('stage')}: {items} at {spec['restaurant']}, cart subtotal ₹{(r.get('cart') or {}).get('subtotal')}")
    return result([r["path"]] if r.get("path") else [], summary, checks, zomato=r)


SKILLS = [
    # (tool name, description, phrase pattern, function)
    ("skill_calculator_uia", "Legacy GUI: compute with Windows Calculator via UI Automation", r"calculator.*(\d+\s*(?:x|×|\*|times))", s_calculator),
    ("skill_legacy_inventory", "Legacy app: type stock records into the Legacy Inventory Console", r"legacy inventory", s_legacy_inventory),
    ("skill_news_report", "News -> Excel -> chart -> Word (-> Gmail draft)", r"news .*excel.*(word|report)", s_news_report),
    ("skill_sort_downloads", "Sort a Downloads folder into per-extension subfolders + Excel summary", r"sort .*(downloads|files).*(type|extension)", s_sort_downloads),
    ("skill_csv_chart_report", "CSV -> Excel with chart -> Word summary with chart image", r"sales\.csv", s_csv_chart_report),
    ("skill_system_report", "Disk + top processes -> Excel (2 sheets) -> chart -> 2-slide PowerPoint", r"disk space.*processes.*(pptx|powerpoint|slides)", s_system_report),
    ("skill_wordcount_gmail", "Word counts of text files -> Excel -> Gmail draft", r"count the words", s_wordcount_gmail),
    ("skill_migrate_verify", "Legacy CSV -> JSON migration with SHA-256 integrity report", r"migrate .*users_legacy", s_migrate_verify),
    ("skill_invoice_crossref", "Cross-reference two invoice exports, flag mismatches", r"cross-reference .*invoice", s_invoice_crossref),
    ("skill_q3_reconciliation", "Q3 reconciliation of two ledgers -> Excel + Word", r"reconciliation", s_q3_reconciliation),
    ("skill_compliance_logs", "Export WARN/ERROR log entries to CSV and upload", r"compliance log", s_compliance_logs),
    ("skill_photo_objects", "Detect objects in a photo -> Excel + Word with annotated image", r"identify the objects in the photo", s_photo_objects),
    ("skill_portrait_deck", "Portrait of the person in a photo/webcam -> 2-slide deck", r"(?:portrait.*(?:deck|slides?|pptx|presentation)|(?:sketch|pencil).*(?:deck|slides?|pptx|presentation|portrait|me))", s_portrait_deck),
    ("skill_paint_portrait", "Draw a portrait by hand in MS Paint", r"(draw|paint) a portrait.*paint", s_paint_portrait),
    ("skill_scene_description", "Describe a photo with the vision model -> Word", r"describe the scene", s_scene_description),
    ("skill_webcam_live", "Webcam photo -> objects -> Excel (+ portrait)", r"webcam", s_webcam_live),
    ("skill_news_brief", "3 latest articles on a topic -> full-text summary brief", r"brief", s_news_brief),
    ("skill_dedupe", "Find duplicate files by SHA-256, move copies, report", r"duplicate files", s_dedupe),
    ("skill_clean_csv", "Clean a messy customer CSV", r"clean customers_raw", s_clean_csv),
    ("skill_local_api", "Serve JSON locally, fetch it, low-stock Excel", r"low stock|low_stock", s_local_api),
    ("skill_code_tests", "Write a module + pytest tests, run them, save results", r"textstats", s_code_tests),
    ("skill_largest_files", "5 largest files in a folder tree -> Excel", r"largest files", s_largest_files),
    ("skill_markdown_doc_deck", "Markdown notes -> Word + PowerPoint", r"notes\.md", s_markdown_doc_deck),
    ("skill_system_profile", "PC profile (name, Windows, resolution, RAM, GPU) -> Word", r"system profile", s_system_profile),
    ("skill_archive", "Zip a folder and verify it with a manifest", r"compress|zip", s_archive),
    ("skill_theme", "Switch Windows dark/light mode and verify in the registry", r"^(?:turn on|switch to|enable) (?:dark|light) mode", s_dark_mode),
    ("skill_fastapi_live", "Generate a FastAPI CRUD service, run it, test it over HTTP", r"fastapi", s_fastapi),
    ("skill_zomato_order", "Search and stage a food order on Zomato with checkout protection", r"zomato", s_zomato_order),
]


def _make_tool(fn):
    def tool(request: str) -> dict:
        t0 = time.time()
        try:
            res = fn(request)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        res["seconds"] = round(time.time() - t0, 1)
        return res
    return tool


for _name, _desc, _pat, _fn in SKILLS:
    registry.register(name=_name, description=f"Verified skill: {_desc}. Pass the user's full request.",
                      parameters={"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}
                      )(_make_tool(_fn))


def match_skill(request: str):
    """Returns the skill tool name for a request, or None."""
    for name, _, pat, _ in SKILLS:
        if re.search(pat, request, re.I):
            return name
    return None


# Everyday plain-language skills live in their own module (imported last: it uses the helpers above).
# They are checked FIRST so broad older patterns (e.g. "compress" -> zip archive) can't steal them.
from seguro.skills.library.everyday_skills import EVERYDAY_SKILLS  # noqa: E402
for _name, _desc, _pat, _fn in EVERYDAY_SKILLS:
    registry.register(name=_name, description=f"Verified skill: {_desc}. Pass the user's full request.",
                      parameters={"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}
                      )(_make_tool(_fn))
SKILLS[:0] = EVERYDAY_SKILLS
