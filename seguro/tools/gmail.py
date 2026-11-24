"""
Gmail drafts in Seguro's automation browser profile. Creates drafts only: the Send button is
never clicked and Ctrl+Enter is never pressed. One-time setup (you sign in yourself):
    python seguro/tools/gmail.py --login
"""
import os
import re
import sys
import time

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.tools.registry import registry
from seguro.tools import webpage

INBOX = "https://mail.google.com/mail/u/0/#inbox"


def _require_login(p):
    p.goto(INBOX, wait_until="domcontentloaded", timeout=45000)
    deadline = time.time() + 20
    while time.time() < deadline:
        if "accounts.google.com" in p.url or "/signin" in p.url or "workspace.google.com" in p.url:
            raise PermissionError("Gmail is not signed in in Seguro's browser profile. "
                                  "Run once: python seguro/tools/gmail.py --login")
        if p.locator("div[gh='cm'], div[role='button']:has-text('Compose')").count() > 0:
            return
        time.sleep(0.5)
    raise TimeoutError("Gmail did not finish loading (no Compose button after 20s).")


def _create(to: str, subject: str, body: str, attachments: list) -> dict:
    p = webpage.page()
    _require_login(p)
    p.locator("div[gh='cm'], div[role='button']:has-text('Compose')").first.click()
    # Gmail keeps hidden copies of compose widgets in the page: only ever use visible ones.
    subject_box = p.locator("input[name='subjectbox']:visible").last
    subject_box.wait_for(state="visible", timeout=15000)
    dialog = p.locator("div[role='dialog']:visible", has=p.locator("input[name='subjectbox']")).last
    to_box = dialog.locator("input[aria-label='To recipients']:visible, input[peoplekit-id]:visible").first
    to_box.click()
    to_box.fill(to)
    to_box.press("Enter")
    subject_box.fill(subject)
    dialog.locator("div[aria-label='Message Body']:visible, div[role='textbox'][aria-label*='Body']:visible").first.fill(body)
    for path in attachments:
        dialog.locator("input[type='file']").first.set_input_files(path)
        name = os.path.basename(path)
        # attachment chip appears with the file name once the upload finished
        dialog.get_by_text(name, exact=False).first.wait_for(state="visible", timeout=60000)
        p.wait_for_timeout(1500)
    p.wait_for_timeout(2000)  # let Gmail autosave the draft
    # Save & close keeps it as a draft. Never the Send button.
    closer = dialog.locator("[aria-label*='Save & close']:visible").first
    if closer.count() > 0:
        closer.click()
    else:
        p.keyboard.press("Escape")
    time.sleep(2.0)
    return {"ok": True, "to": to, "subject": subject, "attachments": [os.path.basename(a) for a in attachments]}


def _find_draft(subject_contains: str, with_attachment: bool) -> dict:
    p = webpage.page()
    _require_login(p)
    q = f'in:drafts subject:"{subject_contains}"' + (" has:attachment" if with_attachment else "")
    p.goto(f"https://mail.google.com/mail/u/0/#search/{q.replace(' ', '+')}", wait_until="domcontentloaded")
    deadline = time.time() + 15
    while time.time() < deadline:
        rows = p.locator("tr.zA:visible")
        if rows.count() > 0:
            text = rows.first.inner_text()
            if subject_contains.lower() in text.lower():
                return {"found": True, "row": re.sub(r"\s+", " ", text)[:200]}
        time.sleep(0.7)
    return {"found": False, "row": ""}


@registry.register(
    name="gmail_create_draft",
    description=("Creates a Gmail DRAFT (never sends) in Seguro's browser profile: recipient, subject, body and "
                 "optional file attachments. The user reviews and sends it themselves."),
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "attachments": {"type": "array", "items": {"type": "string"}, "description": "File paths to attach"}
        },
        "required": ["to", "subject", "body"]
    }
)
def gmail_create_draft(to: str, subject: str, body: str, attachments: list = None) -> dict:
    from seguro.tools.files import normalize_user_path
    files = [normalize_user_path(str(a)) for a in (attachments or [])]
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        return {"ok": False, "error": f"Attachment(s) not found: {missing}"}
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to.strip()):
        return {"ok": False, "error": f"'{to}' is not an email address."}
    try:
        res = webpage.run_on_browser_thread(_create, to.strip(), subject, body, files)
        found = webpage.run_on_browser_thread(_find_draft, subject, bool(files))
        res["draft_found"] = found["found"]
        if not found["found"]:
            res.update(ok=False, error="Compose was filled and closed, but the draft was not found in Drafts.")
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def gmail_draft_check(c: dict, node_id, bb):
    """Checker for the gmail_draft_exists success criterion."""
    found = webpage.run_on_browser_thread(_find_draft, c["subject_contains"], c.get("with_attachment", True))
    return found["found"], (f"draft found: {found['row']}" if found["found"]
                            else f"no draft with subject containing {c['subject_contains']!r}")


from seguro.core.workflow.checks import register_checker  # noqa: E402
register_checker("gmail_draft_exists", gmail_draft_check)


if __name__ == "__main__" and "--login" in sys.argv:
    # A plain Brave window (no automation flags) on Seguro's profile: Google often refuses
    # sign-in inside automation-controlled browsers. The session cookie stays in the profile.
    import subprocess
    from seguro.tools.browser import _get_brave_path
    os.makedirs(webpage.PROFILE_DIR, exist_ok=True)
    proc = subprocess.Popen([_get_brave_path(), f"--user-data-dir={webpage.PROFILE_DIR}",
                             "--no-first-run", "https://mail.google.com/"])
    input("Sign in to Gmail in the Brave window that opened (Seguro profile).\n"
          "When your inbox is visible, CLOSE that Brave window, then press Enter here... ")
    if proc.poll() is None:
        print("That Brave window is still open; close it so Seguro can use the profile.")
    else:
        print("Saved. Seguro can now create drafts in this account.")
