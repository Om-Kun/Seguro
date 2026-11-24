"""
Creates fixture data for the demo prompts and writes DEMO_PROMPTS.md.
    python seguro/demo/setup_demo.py            # create/refresh all fixtures
    python seguro/demo/run_demo.py [--only N]   # run the prompts through Seguro and report
"""
import csv
import json
import os
import random
import shutil
import sys

HOME = os.path.expanduser("~")
DEMO = os.path.join(HOME, "Desktop", "SeguroDemo")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROMPTS = [
    # (category, title, prompt in plain English, what it needs)
    ("Old-style apps", "Do a sum in the Calculator", "Open the Calculator and work out 1234 times 56, then save the answer for me", ""),
    ("Old-style apps", "Type stock into an old program", "Type the new stock list into our old Legacy Inventory program", ""),
    ("Office work", "Expenses → budget sheet", "Look at my expenses list and make me a budget sheet with a chart of where my money went", ""),
    ("Office work", "Sales file → chart + summary", "Look at my sales.csv, make an Excel chart, and write a short Word summary saying which category sold the most", ""),
    ("Office work", "Combine Excel files", "Combine all the Excel files in my merge folder into one", ""),
    ("Office work", "Split customers by city", "Split my customer list into separate files for each city", ""),
    ("Office work", "Clean up a messy list", "Clean customers_raw.csv: fix extra spaces and city names, remove repeats and rows with no city", ""),
    ("Office work", "Notes → Word + PowerPoint", "Turn my notes.md into a Word document and a PowerPoint with one slide per heading", ""),
    ("Office work", "Read a PDF for me", "Read report.pdf and tell me how many pages it has and what it says", ""),
    ("Office work", "Most used words in my essay", "Show me the 10 most used words in my essay with a chart", ""),
    ("Letters & school", "Birthday invitation", "Make a birthday invitation for Riya's party on Saturday at 6 pm", ""),
    ("Letters & school", "Leave letter", "Write a leave letter to my principal for 2 days because I have a fever", ""),
    ("Letters & school", "Study timetable", "Make me a weekly study timetable for Maths, Physics, Chemistry and English", ""),
    ("Letters & school", "Class attendance sheet", "Make an attendance sheet for my class from the students list", ""),
    ("Letters & school", "A letter for every customer", "Write a personalised letter for every customer in my contacts list", ""),
    ("Business", "Move old data to a new system", "Migrate our old user list users_legacy.csv to the new system and prove nothing got lost", ""),
    ("Business", "Find invoice mistakes", "Cross-reference the invoices in our CRM and ERP exports and flag any amounts that don't match", ""),
    ("Business", "Quarter-end accounts check", "Do the Q3 reconciliation of our two account books and tell me the difference", ""),
    ("Business", "Security log check", "Check the compliance log for warnings and errors, export them to a spreadsheet and upload it", ""),
    ("Business", "PC health → PowerPoint", "Check my free disk space and the processes using the most memory, then make a 2-slide PowerPoint about it", ""),
    ("Business", "PC details report", "Make a system profile of this PC: its name, Windows version, screen size, RAM and graphics card", ""),
    ("Photos & camera", "What's in this photo?", "Identify the objects in the photo street.jpg and make a report with the picture", ""),
    ("Photos & camera", "Describe a photo", "Describe the scene in scene.jpg in detail and save it as a Word report", "the vision model"),
    ("Photos & camera", "Sketch portrait slides", "Make a pencil sketch portrait of the person in guest.jpg and put it in a 2-slide presentation", ""),
    ("Photos & camera", "Draw a portrait in Paint", "Draw a portrait of the person in sitter.jpg by hand in MS Paint", "hands off the mouse ~15 s"),
    ("Photos & camera", "Webcam: what do you see?", "Look through the webcam, tell me what objects you see, and paint a portrait of me", "a webcam"),
    ("Photos & camera", "Photo slideshow", "Turn my holiday photos into a slideshow", ""),
    ("Photos & camera", "Shrink photos for WhatsApp", "Shrink my holiday photos so I can send them on WhatsApp", ""),
    ("Photos & camera", "Animated GIF", "Make a GIF from my holiday photos", ""),
    ("Photos & camera", "Rename photos by date", "Rename my holiday photos by the date they were taken", ""),
    ("Photos & camera", "Scans → one PDF", "Put my scanned pages into one PDF", ""),
    ("Photos & camera", "Take a screenshot", "Take a screenshot of my screen and save it", ""),
    ("Files & folders", "Tidy my Downloads", "Tidy up the folder SeguroDemo_sort in my Downloads: sort the files into folders by type and give me an Excel list of what moved", ""),
    ("Files & folders", "Find duplicate files", "Find duplicate files in my dedupe folder and move the extra copies aside", ""),
    ("Files & folders", "Biggest files", "Find the 5 largest files in my largest/tree folder and list them in Excel", ""),
    ("Files & folders", "Move old files away", "Move files older than 30 days in my oldfiles folder into an Old folder", ""),
    ("Files & folders", "Backup as a zip", "Zip up my project folder as a backup and check the zip is OK", ""),
    ("Internet", "News → Excel → Word → Gmail", "Get the 5 latest news about artificial intelligence, put them in an Excel sheet with a chart, write a Word report and save it as a Gmail draft to seguro-eval@example.com with the subject 'Seguro demo: AI news'", "internet, Gmail login"),
    ("Internet", "Word counts → Gmail draft", "Count the words in each of my chapter files, put the counts in Excel and email it to seguro-eval@example.com as a Gmail draft with the subject 'Seguro demo: word counts'", "Gmail login"),
    ("Internet", "Robotics news brief", "Write me a short brief about robotics with links to the 3 latest articles", "internet"),
    ("Internet", "Weather", "What's the weather in Pune today?", "internet"),
    ("Internet", "Dollars to rupees", "How much is 100 US dollars in Indian rupees?", "internet"),
    ("Internet", "Wikipedia summary", "Read about the Raman effect on Wikipedia and make me a one-page summary", "internet"),
    ("Internet", "YouTube search", "Search for lofi study music on YouTube", "internet"),
    ("Internet", "Are these websites up?", "Check if google.com and zomato.com are up", "internet"),
    ("Food order", "Zomato cart (no order)", "Add 2 Misal Slice and 1 Batata Chiwda to my cart from Bedekar Tea Stall in Pune on Zomato", "internet"),
    ("Food order", "Zomato order to Home", "Order 2 Misal Slice and 1 Batata Chiwda from Bedekar Tea Stall in Pune on Zomato to my Home address", "Zomato login; asks your OK with the total; you pay yourself"),
    ("Developer", "Program + tests", "Write a small Python program called textstats that counts words, write tests for it and run them", ""),
    ("Developer", "Build a web service", "Build a FastAPI web service for Book, run it and test that it works", ""),
    ("Developer", "Local product server", "Start our little product server, fetch the product list from it and list the products with low stock", ""),
    ("Settings", "Dark mode", "Turn on dark mode", "asks your OK (settings change)"),
]


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def fresh(path):
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


def setup():
    random.seed(42)
    os.makedirs(DEMO, exist_ok=True)
    fresh(os.path.join(DEMO, "calc"))
    d = fresh(os.path.join(DEMO, "legacy"))
    w(os.path.join(d, "new_stock.csv"), "sku,name,qty\n" + "\n".join(
        f"LG-{100 + i},{n},{q}" for i, (n, q) in enumerate([("Copper wire 2mm", 40), ("Relay 12V", 15), ("Fuse 5A", 120),
                                                           ("Terminal block", 33), ("Cable tie pack", 75)])))
    fresh(os.path.join(DEMO, "news"))
    s = fresh(os.path.join(HOME, "Downloads", "SeguroDemo_sort"))
    from PIL import Image
    Image.new("RGB", (64, 48), (200, 120, 40)).save(os.path.join(s, "holiday.jpg"))
    Image.new("RGB", (64, 48), (40, 120, 200)).save(os.path.join(s, "screenshot.png"))
    for n, b in {"notes.txt": b"hello", "todo.txt": b"x", "export.csv": b"a,b\n1,2", "readme.md": b"# readme"}.items():
        with open(os.path.join(s, n), "wb") as f:
            f.write(b)
    d = fresh(os.path.join(DEMO, "sales"))
    w(os.path.join(d, "sales.csv"), "Category,Sales\n" + "\n".join(f"{c},{random.randint(100, 999)}" for c in
                                                                     ["Books", "Games", "Music", "Toys", "Tools", "Garden"]))
    fresh(os.path.join(DEMO, "system"))
    d = fresh(os.path.join(DEMO, "wordcount"))
    words = "alpha beta gamma delta epsilon zeta eta theta".split()
    for i in range(1, 5):
        w(os.path.join(d, f"chapter{i}.txt"), " ".join(random.choice(words) for _ in range(random.randint(40, 300))))
    d = fresh(os.path.join(DEMO, "migrate"))
    rows = [{"id": i, "name": f"user{i:03d}", "email": f"user{i:03d}@corp.example", "plan": random.choice(["free", "pro", "team"])} for i in range(1, 51)]
    with open(os.path.join(d, "users_legacy.csv"), "w", newline="") as f:
        cw = csv.DictWriter(f, fieldnames=list(rows[0])); cw.writeheader(); cw.writerows(rows)
    d = fresh(os.path.join(DEMO, "invoices"))
    ids = [f"INV-{1000 + i}" for i in range(30)]
    crm = {i: round(random.uniform(100, 5000), 2) for i in ids}
    erp = dict(crm)
    for i in random.sample(ids, 4):
        erp[i] = round(erp[i] + random.uniform(5, 300), 2)
    for name, data in (("invoices_crm.csv", crm), ("invoices_erp.csv", erp)):
        w(os.path.join(d, name), "invoice_id,amount\n" + "\n".join(f"{k},{v}" for k, v in data.items()))
    d = fresh(os.path.join(DEMO, "reconcile"))
    for name in ("ledger_quickbooks.csv", "ledger_salesforce.csv"):
        w(os.path.join(d, name), "date,amount\n" + "\n".join(
            f"2026-{m:02d}-{random.randint(10, 28)},{random.uniform(50, 900):.2f}" for m in (7, 8, 9) for _ in range(6)))
    d = fresh(os.path.join(DEMO, "compliance"))
    lv = ["INFO"] * 6 + ["WARN"] * 2 + ["ERROR"]
    w(os.path.join(d, "auth_service.log"), "\n".join(
        f"2026-10-15 1{i % 10}:{i % 60:02d}:00 {random.choice(lv)} service=auth msg=event_{i}" for i in range(200)))
    import ultralytics
    assets = os.path.join(os.path.dirname(ultralytics.__file__), "assets")
    d = fresh(os.path.join(DEMO, "photos"))
    shutil.copy(os.path.join(assets, "bus.jpg"), os.path.join(d, "street.jpg"))
    shutil.copy(os.path.join(assets, "bus.jpg"), os.path.join(d, "scene.jpg"))
    shutil.copy(os.path.join(assets, "zidane.jpg"), os.path.join(d, "guest.jpg"))
    shutil.copy(os.path.join(assets, "zidane.jpg"), os.path.join(d, "sitter.jpg"))
    fresh(os.path.join(DEMO, "webcam"))
    fresh(os.path.join(DEMO, "brief"))
    d = fresh(os.path.join(DEMO, "dedupe"))
    blobs = [os.urandom(random.randint(200, 3000)) for _ in range(5)]
    for i, b in enumerate(blobs):
        with open(os.path.join(d, f"file_{i}.bin"), "wb") as f:
            f.write(b)
    for i in (0, 2, 4):
        with open(os.path.join(d, f"copy_of_file_{i}.bin"), "wb") as f:
            f.write(blobs[i])
    d = fresh(os.path.join(DEMO, "clean"))
    raw = [(f"  Customer {i} ", random.choice(["pune", "PUNE", "Mumbai ", "mumbai"]), random.randint(1, 99)) for i in range(20)]
    raw += [raw[3], raw[7], ("Customer 99", "", 5)]
    w(os.path.join(d, "customers_raw.csv"), "name,city,orders\n" + "\n".join(f"{a},{b},{c}" for a, b, c in raw))
    d = fresh(os.path.join(HOME, "SeguroProjects", "SeguroDemo_api"))
    w(os.path.join(d, "products.json"), json.dumps([{"sku": f"SKU-{i}", "name": f"Item {i}", "stock": random.randint(0, 60)} for i in range(12)]))
    fresh(os.path.join(HOME, "SeguroProjects", "SeguroDemo_code"))
    d = fresh(os.path.join(DEMO, "largest"))
    for sub in ("a", "b/c", "d"):
        for k in range(4):
            p = os.path.join(d, "tree", sub, f"f{k}.dat")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"\0" * random.randint(1000, 900000))
    d = fresh(os.path.join(DEMO, "markdown"))
    w(os.path.join(d, "notes.md"), "\n\n".join(f"## {h}\n{h} details: " + " ".join(["point"] * 12)
                                              for h in ["Problem", "Approach", "Results", "Next Steps"]))
    fresh(os.path.join(DEMO, "profile"))
    d = fresh(os.path.join(DEMO, "archive"))
    for i in range(6):
        w(os.path.join(d, "project", f"part{i}.txt"), "x" * random.randint(10, 500))
    setup_everyday()
    write_prompts_md()
    print(f"Demo fixtures ready in {DEMO} (+ Downloads/SeguroDemo_sort, SeguroProjects/SeguroDemo_*).")


def setup_everyday():
    """Sample data for the everyday skills (seguro/skills/library/everyday_skills.py)."""
    import time as _t
    from PIL import Image, ImageDraw
    import openpyxl
    d = fresh(os.path.join(DEMO, "budget"))
    cats = ["Food", "Rent", "Travel", "Shopping", "Bills", "Fun"]
    w(os.path.join(d, "expenses.csv"), "date,category,amount\n" + "\n".join(
        f"2026-10-{random.randint(1, 28):02d},{random.choice(cats)},{random.randint(80, 4000)}" for _ in range(40)))
    d = fresh(os.path.join(DEMO, "merge"))
    for name in ("sales_north.xlsx", "sales_south.xlsx", "sales_west.xlsx"):
        wb = openpyxl.Workbook(); ws = wb.active; ws.append(["Product", "Units", "Revenue"])
        for k in range(random.randint(4, 9)):
            ws.append([f"Item {k}", random.randint(1, 50), random.randint(500, 9000)])
        wb.save(os.path.join(d, name))
    d = fresh(os.path.join(DEMO, "split"))
    w(os.path.join(d, "customers.csv"), "name,city,phone\n" + "\n".join(
        f"Customer {i},{random.choice(['Pune', 'Mumbai', 'Nashik', 'Nagpur'])},98{random.randint(10000000, 99999999)}" for i in range(30)))
    d = fresh(os.path.join(DEMO, "attendance"))
    w(os.path.join(d, "students.txt"), "\n".join(["Aarav Patil", "Diya Shah", "Kabir Joshi", "Meera Kulkarni",
                                                    "Rohan Deshmukh", "Sara Khan", "Tanvi Pawar", "Vivaan Rao"]))
    d = fresh(os.path.join(DEMO, "mailmerge"))
    w(os.path.join(d, "contacts.csv"), "name,city,member_id\n" + "\n".join(
        f"{n},{c},M-{1000 + i}" for i, (n, c) in enumerate([("Anita Gokhale", "Pune"), ("Raj Mehta", "Mumbai"),
                                                             ("Neha Iyer", "Chennai"), ("Arjun Nair", "Kochi")])))
    d = fresh(os.path.join(DEMO, "pdf"))
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl
    c = rl.Canvas(os.path.join(d, "report.pdf"), pagesize=A4)
    pages = ["Seguro Quarterly Report. This report summarises the progress of the Seguro desktop agent project during the quarter.",
             "Results. The verified skills completed every demo task, and each output was checked by code before success was claimed.",
             "Next steps. The team will improve the language model planner and connect a webcam for live demonstrations."]
    for text in pages:
        y = 780
        for line in [text[i:i + 90] for i in range(0, len(text), 90)]:
            c.drawString(60, y, line); y -= 18
        c.showPage()
    c.save()
    album = fresh(os.path.join(DEMO, "photos_album"))
    rename = fresh(os.path.join(DEMO, "rename"))
    scans = fresh(os.path.join(DEMO, "scans"))
    colors = [(239, 68, 68), (34, 197, 94), (59, 130, 246), (245, 158, 11), (168, 85, 247)]
    places = ["beach", "mountains", "old_fort", "market", "sunset"]
    for k, (col, place) in enumerate(zip(colors, places)):
        im = Image.new("RGB", (2400, 1600), col)
        dr = ImageDraw.Draw(im)
        dr.ellipse((700, 300, 1700, 1300), fill=(255, 255, 255))
        dr.text((100, 100), place.replace("_", " ").title(), fill=(0, 0, 0))
        im.save(os.path.join(album, f"{place}.jpg"), quality=95)
        exif = Image.Exif(); exif[306] = f"2026:05:{10 + (4 - k) * 2:02d} 1{k}:00:00"   # taken out of order
        im.resize((1200, 800)).save(os.path.join(rename, f"IMG_{9000 + k}.jpg"), exif=exif)
    for k in range(3):
        im = Image.new("RGB", (1240, 1754), (250, 250, 248))
        ImageDraw.Draw(im).text((80, 80), f"Scanned page {k + 1}", fill=(0, 0, 0))
        im.save(os.path.join(scans, f"page_{k + 1}.png"))
    d = fresh(os.path.join(DEMO, "oldfiles"))
    now = _t.time()
    for name, age in [("old_invoice.pdf", 90), ("old_notes.txt", 45), ("draft.docx", 40), ("today.txt", 0), ("last_week.csv", 7)]:
        p = os.path.join(d, name); w(p, f"{name} sample")
        os.utime(p, (now - age * 86400, now - age * 86400))
    d = fresh(os.path.join(DEMO, "essay"))
    w(os.path.join(d, "essay.txt"),
      "Technology changes how students learn. Students use technology to read, write and share ideas. "
      "Good learning needs curiosity, practice and feedback. Teachers guide students, and technology gives "
      "students more ways to practice. Practice with feedback builds skill, and skill builds confidence. "
      "Curiosity keeps learning alive; technology can feed curiosity when students use it with purpose.")
    for sub in ("invite", "letters", "timetable", "money", "weather", "wiki", "sitecheck", "screenshots"):
        fresh(os.path.join(DEMO, sub))


def write_prompts_md():
    lines = ["# Seguro demo prompts", "",
             "Setup (re-run anytime to reset the sample data): `python seguro/demo/setup_demo.py`  ",
             "Easiest: open the app with `python seguro/ui/app.py` and click a prompt.  ",
             "Or run one: `python seguro/main.py --mode local_only --no-hud --task \"<prompt>\"`  ",
             "Run all and get a pass/fail table: `python seguro/demo/run_demo.py`", "",
             "Each prompt is handled by a **verified skill**: fixed steps using Seguro's real tools, then code checks "
             "of every result before Seguro says it's done. Anything else you type goes to the AI agent instead.", ""]
    cat = None
    for i, (category, title, prompt, needs) in enumerate(PROMPTS, 1):
        if category != cat:
            lines += [f"## {category}", ""]
            cat = category
        lines += [f"**{i}. {title}**" + (f"  _(needs: {needs})_" if needs else ""), "", "```", prompt, "```", ""]
    w(os.path.join(ROOT, "DEMO_PROMPTS.md"), "\n".join(lines))


if __name__ == "__main__":
    setup()
