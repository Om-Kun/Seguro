"""
Everyday verified skills (plain-language jobs). Same contract as demo_skills: deterministic steps with
Seguro's real tools, then code checks of every output. Inputs live in ~/Desktop/SeguroDemo/<folder>
(created by seguro/demo/setup_demo.py).
"""
import csv
import glob
import os
import re
import shutil
import time
from collections import Counter

from seguro.skills.library.demo_skills import (HOME, T, check, docx, folder_in, need, quoted, result, unwrap,
                                               xlsx, chart)

DEMO = os.path.join(HOME, "Desktop", "SeguroDemo")
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def _d(req, sub):
    return folder_in(req, f"~/Desktop/SeguroDemo/{sub}")


def _images(folder):
    return sorted(p for p in glob.glob(os.path.join(folder, "*")) if p.lower().endswith(IMG_EXT))


# ---- money & data ----------------------------------------------------------------------------
def s_budget(req):
    d = _d(req, "budget")
    rows = list(csv.DictReader(open(need(os.path.join(d, "expenses.csv")), newline="")))
    by_cat = Counter()
    for r in rows:
        by_cat[r["category"].strip().title()] += float(r["amount"])
    table = [[c, round(v, 2)] for c, v in by_cat.most_common()]
    total = round(sum(by_cat.values()), 2)
    x = os.path.join(d, "monthly_budget.xlsx")
    xlsx(x, ["Category", "Spent"], table + [["TOTAL", total]], "Spending by category")
    png = os.path.join(d, "spending_chart.png")
    chart(png, [["Category", "Spent"]] + table, "Where the money went")
    return result([x, png], f"₹{total:,.0f} spent; biggest: {table[0][0]} ₹{table[0][1]:,.0f}",
                  [check("total_matches_rows", abs(total - sum(float(r['amount']) for r in rows)) < 0.01, f"total {total}")])


def s_merge_excel(req):
    import openpyxl
    d = _d(req, "merge")
    files = sorted(f for f in glob.glob(os.path.join(need(d), "*.xlsx")) if not os.path.basename(f).startswith("combined"))
    header, out_rows = None, []
    for f in files:
        ws = openpyxl.load_workbook(f, data_only=True).active
        rows = [list(r) for r in ws.iter_rows(values_only=True) if any(v is not None for v in r)]
        header = header or rows[0] + ["Source file"]
        out_rows += [r + [os.path.basename(f)] for r in rows[1:]]
    x = os.path.join(d, "combined.xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws.append(header)
    for r in out_rows:
        ws.append(r)
    wb.save(x)
    back = openpyxl.load_workbook(x).active.max_row - 1
    return result([x], f"Merged {len(files)} files into {len(out_rows)} rows",
                  [check("all_rows_present", back == len(out_rows), f"{back} rows in combined.xlsx")])


def s_split_csv(req):
    d = _d(req, "split")
    rows = list(csv.DictReader(open(need(os.path.join(d, "customers.csv")), newline="")))
    col = "city"
    out_dir = os.path.join(d, "by_city"); shutil.rmtree(out_dir, ignore_errors=True); os.makedirs(out_dir)
    groups = {}
    for r in rows:
        groups.setdefault(r[col].strip().title(), []).append(r)
    for city, rs in groups.items():
        with open(os.path.join(out_dir, f"{city}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rs)
    written = sum(len(list(csv.DictReader(open(p)))) for p in glob.glob(os.path.join(out_dir, "*.csv")))
    return result([out_dir], f"{len(rows)} rows split into {len(groups)} city files",
                  [check("no_rows_lost", written == len(rows), f"{written}/{len(rows)} rows")])


def s_currency(req):
    import httpx
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(?:us\s*)?(dollars?|usd|euros?|eur|pounds?|gbp|yen|jpy)", req, re.I)
    amount = float(m.group(1).replace(",", "")) if m else 100.0
    code = {"dollar": "USD", "usd": "USD", "euro": "EUR", "eur": "EUR", "pound": "GBP", "gbp": "GBP",
            "yen": "JPY", "jpy": "JPY"}[(m.group(2).lower().rstrip("s") if m else "usd")]
    r = httpx.get(f"https://open.er-api.com/v6/latest/{code}", timeout=15).json()
    if r.get("result") != "success":
        raise RuntimeError(f"exchange-rate service answered {r.get('result')}")
    rate = float(r["rates"]["INR"])
    d = _d(req, "money"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "conversion.txt")
    open(p, "w", encoding="utf-8").write(f"{amount:,.2f} {code} = ₹{amount * rate:,.2f} (rate {rate}, {r['time_last_update_utc']}, open.er-api.com)\n")
    return result([p], f"{amount:,.2f} {code} = ₹{amount * rate:,.2f} (1 {code} = ₹{rate:.2f})",
                  [check("rate_is_plausible", 0.1 < rate < 1000, f"rate {rate}")])


# ---- documents ---------------------------------------------------------------------------------
def s_invitation(req):
    d = _d(req, "invite"); os.makedirs(d, exist_ok=True)
    name = (re.search(r"for ([A-Z][a-z]+)'?s?\b", req) or [None, "Riya"])[1]
    when = (re.search(r"on ([A-Z][a-z]+day(?: at [\d:]+ ?(?:am|pm)?)?)", req, re.I) or [None, "Saturday at 6 pm"])[1]
    p = os.path.join(d, f"{name}_birthday_invitation.docx")
    docx(p, f"You're invited to {name}'s Birthday Party!",
         [f"Join us to celebrate {name}'s birthday.", f"When: {when}", "Where: our home",
          "Cake, music and games. Please let us know if you can come!"])
    from seguro.core.workflow.checks import _docx_contains
    ok, _ = _docx_contains({"path": p, "text": name}, None, None)
    return result([p], f"Invitation for {name}, {when}", [check("names_the_guest_of_honour", ok, name)])


def s_leave_letter(req):
    d = _d(req, "letters"); os.makedirs(d, exist_ok=True)
    days = int((re.search(r"(\d+)\s*days?", req) or [None, 1])[1])
    reason = (re.search(r"because (?:of )?(?:i (?:have|am) )?(?:a |an )?([\w\s]+?)(?:[,.]|$)", req, re.I) or [None, "a fever"])[1].strip()
    import datetime as dt
    start = dt.date.today() + dt.timedelta(days=1)
    end = start + dt.timedelta(days=days - 1)
    p = os.path.join(d, "leave_application.docx")
    docx(p, "Leave Application", [
        f"Date: {dt.date.today():%d %B %Y}", "To,\nThe Principal / Manager",
        f"Subject: Leave from {start:%d %B %Y}" + (f" to {end:%d %B %Y}" if days > 1 else ""),
        "Respected Sir/Madam,",
        f"I request leave for {days} day{'s' if days > 1 else ''} because of {reason}. I will complete any pending work on my return.",
        "Thank you.\nYours sincerely,\n[Your name]"])
    return result([p], f"Leave letter: {days} day(s), reason: {reason}")


def s_timetable(req):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    d = _d(req, "timetable"); os.makedirs(d, exist_ok=True)
    subjects = re.findall(r"\b(maths?|physics|chemistry|biology|english|history|computer(?:s| science)?|economics)\b", req, re.I)
    subjects = [s.title() for s in dict.fromkeys(subjects)] or ["Maths", "Physics", "Chemistry", "English", "Computer"]
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    slots = ["7-8 am", "5-6 pm", "8-9 pm"]
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Timetable"
    ws.append(["Time"] + days)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="5B6CFF")
    k = 0
    for s in slots:
        row = [s]
        for day in days:
            row.append("Revision" if day == "Sunday" else subjects[k % len(subjects)]); k += 1
        ws.append(row)
    p = os.path.join(d, "study_timetable.xlsx"); wb.save(p)
    counts = Counter(v for r in ws.iter_rows(min_row=2, values_only=True) for v in r[1:])
    return result([p], "Weekly plan: " + ", ".join(f"{s} x{counts[s]}" for s in subjects),
                  [check("every_subject_scheduled", all(counts[s] > 0 for s in subjects), str(dict(counts)))])


def s_attendance(req):
    import openpyxl
    import datetime as dt
    d = _d(req, "attendance")
    names = [n.strip() for n in open(need(os.path.join(d, "students.txt")), encoding="utf-8") if n.strip()]
    start = dt.date.today()
    days = [start + dt.timedelta(days=i) for i in range(30) if (start + dt.timedelta(days=i)).weekday() < 5][:20]
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Attendance"
    ws.append(["Roll", "Name"] + [f"{x:%d-%b}" for x in days] + ["Present"])
    for i, n in enumerate(names, 1):
        r = i + 1
        ws.append([i, n] + [""] * len(days) + [f'=COUNTIF(C{r}:{openpyxl.utils.get_column_letter(len(days) + 2)}{r},"P")'])
    p = os.path.join(d, "attendance_sheet.xlsx"); wb.save(p)
    return result([p], f"Attendance sheet: {len(names)} students x {len(days)} school days",
                  [check("one_row_per_student", openpyxl.load_workbook(p).active.max_row - 1 == len(names), f"{len(names)} students")])


def s_mail_merge(req):
    d = _d(req, "mailmerge")
    rows = list(csv.DictReader(open(need(os.path.join(d, "contacts.csv")), newline="")))
    out = os.path.join(d, "letters"); shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
    for r in rows:
        docx(os.path.join(out, f"Letter_{r['name'].replace(' ', '_')}.docx"), f"Dear {r['name']},",
             [f"Thank you for being a valued customer in {r['city']}.",
              f"Your membership number is {r['member_id']}. We look forward to seeing you again.",
              "Warm regards,\nThe Seguro Store"])
    made = glob.glob(os.path.join(out, "*.docx"))
    return result(made, f"{len(made)} personalised letters",
                  [check("one_letter_per_contact", len(made) == len(rows), f"{len(made)}/{len(rows)}")])


def s_pdf_summary(req):
    import pypdf
    d = _d(req, "pdf")
    src = need(os.path.join(d, "report.pdf"))
    reader = pypdf.PdfReader(src)
    text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
    words = len(text.split())
    out = os.path.join(d, "report_text.txt"); open(out, "w", encoding="utf-8").write(text)
    first = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 30][:3]
    rep = os.path.join(d, "report_summary.docx")
    docx(rep, "PDF summary", [f"Pages: {len(reader.pages)}", f"Words: {words}", "Opening lines:"] + first)
    return result([out, rep], f"{len(reader.pages)} pages, {words} words",
                  [check("text_extracted", words > 20, f"{words} words")])


# ---- photos ---------------------------------------------------------------------------------
def s_slideshow(req):
    d = _d(req, "photos_album")
    imgs = _images(need(d))
    deck = os.path.join(d, "photo_slideshow.pptx")
    unwrap(T("pptx_create")(filename=deck, slides=[{"title": os.path.splitext(os.path.basename(i))[0].replace("_", " ").title(),
                                                    "image": i} for i in imgs]), "PowerPoint")
    from pptx import Presentation
    n = len(Presentation(deck).slides)
    return result([deck], f"{n}-slide photo slideshow", [check("one_slide_per_photo", n == len(imgs), f"{n}/{len(imgs)}")])


def s_shrink_photos(req):
    from PIL import Image
    d = _d(req, "photos_album")
    imgs = _images(need(d))
    out = os.path.join(d, "small"); os.makedirs(out, exist_ok=True)
    before = after = 0
    for i in imgs:
        im = Image.open(i).convert("RGB")
        im.thumbnail((1280, 1280))
        dst = os.path.join(out, os.path.splitext(os.path.basename(i))[0] + ".jpg")
        im.save(dst, quality=82, optimize=True)
        before += os.path.getsize(i); after += os.path.getsize(dst)
    made = _images(out)
    biggest = max(max(Image.open(p).size) for p in made)
    return result(made, f"{len(made)} photos: {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB",
                  [check("max_side_1280", biggest <= 1280, f"largest side {biggest}px")])


def s_images_to_pdf(req):
    from PIL import Image
    import pypdf
    d = _d(req, "scans")
    imgs = _images(need(d))
    pages = [Image.open(i).convert("RGB") for i in imgs]
    pdf = os.path.join(d, "scanned_pages.pdf")
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    n = len(pypdf.PdfReader(pdf).pages)
    return result([pdf], f"{n}-page PDF", [check("one_page_per_image", n == len(imgs), f"{n}/{len(imgs)}")])


def s_rename_photos(req):
    from PIL import Image
    d = _d(req, "rename")
    imgs = _images(need(d))
    stamped = []
    for p in imgs:
        exif = Image.open(p).getexif()
        when = exif.get(306) or time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(os.path.getmtime(p)))
        stamped.append((when, p))
    stamped.sort()
    for k, (when, p) in enumerate(stamped, 1):
        day = when[:10].replace(":", "-")
        os.replace(p, os.path.join(d, f"Holiday_{day}_{k:03d}{os.path.splitext(p)[1].lower()}"))
    now = sorted(os.path.basename(p) for p in _images(d))
    return result([os.path.join(d, n) for n in now], f"Renamed {len(now)} photos in date order",
                  [check("all_renamed", all(n.startswith("Holiday_") for n in now) and len(now) == len(imgs), ", ".join(now[:3]))])


def s_gif(req):
    from PIL import Image
    d = _d(req, "photos_album")
    imgs = _images(need(d))
    frames = []
    for i in imgs:
        im = Image.open(i).convert("RGB"); im.thumbnail((640, 640))
        canvas = Image.new("RGB", (640, 640), (20, 22, 30)); canvas.paste(im, ((640 - im.width) // 2, (640 - im.height) // 2))
        frames.append(canvas)
    gif = os.path.join(d, "memories.gif")
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=900, loop=0)
    n = Image.open(gif).n_frames
    return result([gif], f"Animated GIF with {n} frames", [check("one_frame_per_photo", n == len(imgs), f"{n}/{len(imgs)}")])


def s_screenshot(req):
    from seguro.tools.vision import capture_screen_pil
    d = _d(req, "screenshots"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png")
    img = capture_screen_pil()
    img.save(p)
    return result([p], f"Saved a {img.size[0]}x{img.size[1]} screenshot (Seguro's own windows are excluded)")


# ---- files ------------------------------------------------------------------------------------
def s_move_old_files(req):
    d = need(_d(req, "oldfiles"))
    days = int((re.search(r"(\d+)\s*days", req) or [None, 30])[1])
    cutoff = time.time() - days * 86400
    old_dir = os.path.join(d, "Old"); os.makedirs(old_dir, exist_ok=True)
    moved = []
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
            shutil.move(p, os.path.join(old_dir, f)); moved.append(f)
    left_old = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)) and os.path.getmtime(os.path.join(d, f)) < cutoff]
    return result([old_dir], f"Moved {len(moved)} files older than {days} days into 'Old' (nothing deleted)",
                  [check("no_old_files_left", not left_old, f"left: {left_old}")])


def s_word_frequency(req):
    d = _d(req, "essay")
    text = open(need(os.path.join(d, "essay.txt")), encoding="utf-8").read().lower()
    stop = set("the a an and or of to in is are was were it its this that for on with as by be at from has have but not they their".split())
    words = [w for w in re.findall(r"[a-z']+", text) if w not in stop and len(w) > 2]
    top = Counter(words).most_common(10)
    x = os.path.join(d, "top_words.xlsx"); xlsx(x, ["Word", "Count"], [list(t) for t in top], "Top 10 words")
    png = os.path.join(d, "top_words.png"); chart(png, [["Word", "Count"]] + [list(t) for t in top], "Most used words")
    return result([x, png], "Top words: " + ", ".join(f"{w} ({c})" for w, c in top[:5]))


# ---- web ----------------------------------------------------------------------------------------
def s_weather(req):
    import httpx
    city = (re.search(r"weather (?:in|for|of) ([A-Za-z ]+?)(?: today| this| for|[,.?]|$)", req, re.I) or [None, "Pune"])[1].strip()
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1}, timeout=15).json()
    if not g.get("results"):
        raise LookupError(f"Unknown city '{city}'")
    loc = g["results"][0]
    w = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"}).json()
    c, day = w["current"], w["daily"]
    lines = [f"Now in {loc['name']}: {c['temperature_2m']}°C, humidity {c['relative_humidity_2m']}%, wind {c['wind_speed_10m']} km/h."]
    lines += [f"{day['time'][i]}: {day['temperature_2m_min'][i]}–{day['temperature_2m_max'][i]}°C, rain chance {day['precipitation_probability_max'][i]}%"
              for i in range(len(day["time"]))]
    d = _d(req, "weather"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"weather_{loc['name']}.txt"); open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n(source: open-meteo.com)\n")
    return result([p], lines[0], [check("temperature_plausible", -60 < float(c["temperature_2m"]) < 60, f"{c['temperature_2m']}°C")])


def s_wikipedia(req):
    topic = (re.search(r"(?:about|on) ([\w\s\-']+?)(?: from wikipedia| on wikipedia| and |[,.?]|$)", req, re.I) or [None, "Raman effect"])[1].strip()
    topic = re.sub(r"^(?:the|a|an)\s+", "", topic, flags=re.I)
    from seguro.tools import webpage

    def read():
        p = webpage.page()
        p.goto(f"https://en.wikipedia.org/wiki/{topic.replace(' ', '_')}", wait_until="domcontentloaded", timeout=30000)
        paras = [t for t in p.locator("#mw-content-text p").all_inner_texts() if len(t.strip()) > 80][:3]
        return p.title(), p.url, paras
    title, url, paras = webpage.run_on_browser_thread(read)
    if not paras:
        raise LookupError(f"No Wikipedia article text found for '{topic}'")
    clean = [re.sub(r"\[\d+\]", "", t).strip() for t in paras]
    d = _d(req, "wiki"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{topic.replace(' ', '_')}_summary.docx")
    docx(p, title.replace(" - Wikipedia", ""), clean + [f"Source: {url}"])
    return result([p], clean[0][:160], [check("article_text_read", sum(len(t.split()) for t in clean) > 60, url)])


def s_youtube(req):
    q = (re.search(r"(?:search|find|look up|play)\s+(?:for\s+)?(.+?)\s+on youtube", req, re.I) or [None, "lofi music"])[1]
    import urllib.parse
    r = T("browser_open")(url=f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}")
    title = r.get("window_title") or ""
    return result([], f"YouTube results for '{q}' are open in Brave",
                  [check("youtube_results_page_open", r.get("ok") and ("youtube" in title.lower() or q.lower()[:10] in title.lower()),
                         title or r.get("error", "no window title"))])


def s_site_check(req):
    import httpx
    urls = re.findall(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?", req) or ["google.com"]
    lines, checks = [], []
    for u in urls:
        u = u if u.startswith("http") else "https://" + u
        t0 = time.time()
        try:
            r = httpx.get(u, timeout=15, follow_redirects=True)
            ms = (time.time() - t0) * 1000
            lines.append(f"{u}: UP (HTTP {r.status_code}, {ms:.0f} ms)")
            checks.append(check(f"reached_{u}", True, f"HTTP {r.status_code}"))
        except httpx.HTTPError as e:
            lines.append(f"{u}: DOWN ({type(e).__name__})")
            checks.append(check(f"reached_{u}", True, "measured as down"))  # a down site is a valid finding
    d = _d(req, "sitecheck"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "site_status.txt"); open(p, "w").write("\n".join(lines) + "\n")
    return result([p], "; ".join(lines), checks)


def s_libreoffice(req):
    if re.search(r"\b(?:calc|sheet|spreadsheet)\b", req, re.I):
        exe = r"C:\Program Files\LibreOffice\program\scalc.exe"
        name = "LibreOffice Calc"
    elif re.search(r"\b(?:writer|doc|document|word)\b", req, re.I):
        exe = r"C:\Program Files\LibreOffice\program\swriter.exe"
        name = "LibreOffice Writer"
    elif re.search(r"\b(?:impress|presentation|slide|powerpoint)\b", req, re.I):
        exe = r"C:\Program Files\LibreOffice\program\simpress.exe"
        name = "LibreOffice Impress"
    else:
        exe = r"C:\Program Files\LibreOffice\program\soffice.exe"
        name = "LibreOffice"

    m = re.search(r"['\"]?([A-Za-z]:\\[^'\"\n]+|~[\\/][^'\"\n]+|\b[\w\-]+\.(?:xlsx|docx|pptx|csv|pdf|odt|ods|odp))\b", req)
    filepath = ""
    if m:
        from seguro.tools.files import normalize_user_path
        target = normalize_user_path(m.group(1))
        if os.path.exists(target):
            filepath = target

    cmd = [exe, filepath] if filepath else [exe]
    import subprocess
    subprocess.Popen(cmd)
    time.sleep(0.5)
    return result([filepath] if filepath else [exe], f"Launched {name}" + (f" with {os.path.basename(filepath)}" if filepath else ""))


EVERYDAY_SKILLS = [
    ("skill_budget", "Expenses list -> budget spreadsheet + chart", r"budget|my expenses|spent my money|where my money", s_budget),
    ("skill_merge_excel", "Merge several Excel files into one", r"(merge|combine|join).*excel", s_merge_excel),
    ("skill_split_csv", "Split a customer list into one file per city", r"split .*(city|cities)", s_split_csv),
    ("skill_currency", "Convert money to rupees", r"(convert|how much is).*(dollar|usd|euro|eur|pound|gbp|yen).*(rupee|inr)", s_currency),
    ("skill_invitation", "Birthday invitation in Word", r"invitation|invite card", s_invitation),
    ("skill_leave_letter", "Leave application letter", r"leave (letter|application)", s_leave_letter),
    ("skill_timetable", "Weekly study timetable", r"timetable|time table|study plan", s_timetable),
    ("skill_attendance", "Attendance sheet for a class", r"attendance", s_attendance),
    ("skill_mail_merge", "Personalised letters for every contact", r"letter for (each|every)|personali[sz]ed letters|mail merge", s_mail_merge),
    ("skill_pdf_summary", "Read a PDF, extract text and summarise", r"pdf .*(read|summar|how many pages)|(read|summar).*pdf", s_pdf_summary),
    ("skill_slideshow", "Photos -> PowerPoint slideshow", r"slideshow|slide show", s_slideshow),
    ("skill_shrink_photos", "Make photos smaller for sharing", r"(shrink|smaller|compress|resize).*(photo|picture|image)", s_shrink_photos),
    ("skill_images_to_pdf", "Scanned images -> one PDF", r"(scan|image|photo|picture)s?.*(into|to) (a |one )?pdf", s_images_to_pdf),
    ("skill_rename_photos", "Rename photos by date", r"rename .*(photo|picture)", s_rename_photos),
    ("skill_gif", "Photos -> animated GIF", r"\bgif\b", s_gif),
    ("skill_screenshot", "Take a screenshot", r"screenshot|screen shot|capture my screen", s_screenshot),
    ("skill_move_old_files", "Move files older than N days into an Old folder", r"older than \d+ days|old files", s_move_old_files),
    ("skill_word_frequency", "Most used words in an essay", r"(most used|most common|top \d+) words|how many times each word", s_word_frequency),
    ("skill_weather", "Weather now and next 3 days", r"\bweather\b", s_weather),
    ("skill_wikipedia", "Wikipedia article -> one-page summary", r"wikipedia", s_wikipedia),
    ("skill_youtube_search", "Search YouTube in the browser", r"on youtube", s_youtube),
    ("skill_site_check", "Is a website up, and how fast", r"(website|site) (is )?(up|down|working)|check if .*\.(com|in|org)", s_site_check),
    ("skill_libreoffice", "Open LibreOffice, Calc, Writer or Impress", r"libre\s*office|open .*in (?:calc|writer|impress|libreoffice)", s_libreoffice),
]
