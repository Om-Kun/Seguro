"""Every plain-English demo prompt must reach its intended verified skill (no network, no GUI)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import seguro.core.orchestrator  # noqa: F401  (registers tools)
from seguro.demo.setup_demo import PROMPTS
from seguro.skills.library.demo_skills import match_skill
from seguro.tools.registry import registry

EXPECTED = {
    "Do a sum in the Calculator": "skill_calculator_uia", "Type stock into an old program": "skill_legacy_inventory",
    "Expenses → budget sheet": "skill_budget", "Sales file → chart + summary": "skill_csv_chart_report",
    "Combine Excel files": "skill_merge_excel", "Split customers by city": "skill_split_csv",
    "Clean up a messy list": "skill_clean_csv", "Notes → Word + PowerPoint": "skill_markdown_doc_deck",
    "Read a PDF for me": "skill_pdf_summary", "Most used words in my essay": "skill_word_frequency",
    "Birthday invitation": "skill_invitation", "Leave letter": "skill_leave_letter", "Study timetable": "skill_timetable",
    "Class attendance sheet": "skill_attendance", "A letter for every customer": "skill_mail_merge",
    "Move old data to a new system": "skill_migrate_verify", "Find invoice mistakes": "skill_invoice_crossref",
    "Quarter-end accounts check": "skill_q3_reconciliation", "Security log check": "skill_compliance_logs",
    "PC health → PowerPoint": "skill_system_report", "PC details report": "skill_system_profile",
    "What's in this photo?": "skill_photo_objects", "Describe a photo": "skill_scene_description",
    "Sketch portrait slides": "skill_portrait_deck", "Draw a portrait in Paint": "skill_paint_portrait",
    "Webcam: what do you see?": "skill_webcam_live", "Photo slideshow": "skill_slideshow",
    "Shrink photos for WhatsApp": "skill_shrink_photos", "Animated GIF": "skill_gif",
    "Rename photos by date": "skill_rename_photos", "Scans → one PDF": "skill_images_to_pdf",
    "Take a screenshot": "skill_screenshot", "Tidy my Downloads": "skill_sort_downloads",
    "Find duplicate files": "skill_dedupe", "Biggest files": "skill_largest_files",
    "Move old files away": "skill_move_old_files", "Backup as a zip": "skill_archive",
    "News → Excel → Word → Gmail": "skill_news_report", "Word counts → Gmail draft": "skill_wordcount_gmail",
    "Robotics news brief": "skill_news_brief", "Weather": "skill_weather", "Dollars to rupees": "skill_currency",
    "Wikipedia summary": "skill_wikipedia", "YouTube search": "skill_youtube_search",
    "Are these websites up?": "skill_site_check", "Zomato cart (no order)": "skill_zomato_order",
    "Zomato order to Home": "skill_zomato_order", "Program + tests": "skill_code_tests",
    "Build a web service": "skill_fastapi_live", "Local product server": "skill_local_api", "Dark mode": "skill_theme",
}


def test_51_prompts_route_to_their_skill():
    assert len(PROMPTS) == 51
    got = {title: match_skill(prompt) for _c, title, prompt, _n in PROMPTS}
    wrong = {t: (got[t], EXPECTED.get(t)) for t in got if got[t] != EXPECTED.get(t)}
    assert not wrong, wrong
    assert all(name in registry._tools for name in set(EXPECTED.values()))


def test_ordinary_requests_still_reach_the_agent():
    for p in ("open notepad", "what time is it in Tokyo", "compress the logs folder into a zip"):
        assert match_skill(p) in (None, "skill_archive")
    assert match_skill("open notepad") is None
