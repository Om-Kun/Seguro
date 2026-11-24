"""
DOM-level browser automation (Playwright driving Brave) on a SEPARATE Seguro profile
(~/SeguroProjects/brave-profile). Chromium ≥136 ignores remote debugging on the default
profile, and keeping the agent out of the user's main profile is safer anyway.
Playwright's sync API is not thread-safe, so every call runs on one dedicated thread.
"""
import os
import re
import concurrent.futures

from seguro.tools.registry import registry
from seguro.tools.browser import _get_brave_path

PROFILE_DIR = os.path.expanduser(r"~\SeguroProjects\brave-profile")
_EXEC = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="seguro_playwright")
_STATE = {"pw": None, "ctx": None, "page": None}
BLOCKED_ACTIONS = re.compile(r"^\s*(send|post|publish|buy now|purchase|place order|pay|checkout)\b", re.IGNORECASE)


def _on_thread(fn, *a, **kw):
    return _EXEC.submit(fn, *a, **kw).result(timeout=120)


def _ensure_page():
    if _STATE["page"] is not None and not _STATE["page"].is_closed():
        return _STATE["page"]
    from playwright.sync_api import sync_playwright
    brave = _get_brave_path()
    if not brave:
        raise RuntimeError("Brave is not installed.")
    if _STATE["pw"] is None:
        _STATE["pw"] = sync_playwright().start()
    if _STATE["ctx"] is None:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        _STATE["ctx"] = _STATE["pw"].chromium.launch_persistent_context(
            PROFILE_DIR, executable_path=brave, headless=False, viewport={"width": 1280, "height": 800})
    ctx = _STATE["ctx"]
    # Always our own tab: Brave may restore the previous session's tabs on launch and replace them
    # moments later, which would close a borrowed tab under us (TargetClosedError).
    _STATE["page"] = ctx.new_page()
    return _STATE["page"]


def page():
    """For other modules (e.g. gmail) that already run on the Playwright thread."""
    return _ensure_page()


def run_on_browser_thread(fn, *a, **kw):
    return _on_thread(fn, *a, **kw)


def _locator(p, target: str):
    t = target.strip()
    if t.startswith(("#", ".", "[", "//", "css=", "xpath=", "text=", "role=")) or re.match(r"^[a-z]+[\[#.:]", t):
        return p.locator(t).first
    # plain words: try label, placeholder, then visible text
    for loc in (p.get_by_label(t, exact=False), p.get_by_placeholder(t, exact=False),
                p.get_by_role("button", name=t), p.get_by_text(t, exact=False)):
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return p.locator(t).first


@registry.register(
    name="browser_page_open",
    description="Opens a URL in Seguro's automation browser (Brave, separate profile) for DOM-level control. Returns title and visible text.",
    parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
)
def browser_page_open(url: str) -> dict:
    def go():
        p = _ensure_page()
        from seguro.tools.browser import _to_url
        u = url.strip()
        target = "http://" + u if u.startswith(("localhost", "127.0.0.1")) else _to_url(u)
        resp = p.goto(target, wait_until="domcontentloaded", timeout=30000)
        return {"ok": resp is None or resp.ok, "url": p.url, "title": p.title(),
                "status": resp.status if resp else None, "text": p.inner_text("body")[:2000],
                **({} if resp is None or resp.ok else {"error": f"HTTP {resp.status}"})}
    try:
        return _on_thread(go)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@registry.register(
    name="browser_page_fill",
    description="Types text into an input on the open page. target = CSS selector, label, or placeholder text.",
    parameters={"type": "object", "properties": {"target": {"type": "string"}, "text": {"type": "string"}},
                "required": ["target", "text"]}
)
def browser_page_fill(target: str, text: str) -> dict:
    def do():
        p = _ensure_page()
        loc = _locator(p, target)
        loc.fill(text, timeout=10000)
        return {"ok": True, "target": target, "value": loc.input_value(timeout=5000)}
    try:
        return _on_thread(do)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@registry.register(
    name="browser_page_click",
    description="Clicks an element on the open page. target = CSS selector or the button/link text. Returns the page text after the click.",
    parameters={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}
)
def browser_page_click(target: str) -> dict:
    def do():
        p = _ensure_page()
        loc = _locator(p, target)
        label = (loc.inner_text(timeout=5000) or loc.get_attribute("aria-label") or "").strip()
        if BLOCKED_ACTIONS.match(label) or BLOCKED_ACTIONS.match(target):
            # Dispatch-time gates catch risky targets by name; this catches elements whose
            # label only becomes visible at click time.
            return {"ok": False, "error": f"Refusing to click '{label or target}' (send/post/buy). Needs explicit approval."}
        loc.click(timeout=10000)
        p.wait_for_load_state("domcontentloaded", timeout=10000)
        return {"ok": True, "clicked": label or target, "url": p.url, "title": p.title(),
                "text": p.inner_text("body")[:2000]}
    try:
        return _on_thread(do)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@registry.register(
    name="browser_page_text",
    description="Returns the visible text of the open page (or of one element if target is given).",
    parameters={"type": "object", "properties": {"target": {"type": "string"}}}
)
def browser_page_text(target: str = "") -> dict:
    def do():
        p = _ensure_page()
        text = _locator(p, target).inner_text(timeout=10000) if target else p.inner_text("body")
        return {"ok": True, "url": p.url, "title": p.title(), "text": text[:4000]}
    try:
        return _on_thread(do)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@registry.register(
    name="browser_page_screenshot",
    description="Saves a PNG screenshot of the open page (only the page, never Seguro's own windows).",
    parameters={"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}
)
def browser_page_screenshot(filename: str) -> dict:
    from seguro.tools.files import normalize_user_path
    from seguro.safety.gate import safety_gate
    out = normalize_user_path(filename if filename.lower().endswith(".png") else filename + ".png")
    ok, reason = safety_gate.check_path(out)
    if not ok:
        return {"ok": False, "error": reason}

    def do():
        p = _ensure_page()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        p.screenshot(path=out, full_page=True)
        return {"ok": True, "path": out, "url": p.url, "title": p.title()}
    try:
        return _on_thread(do)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def close_browser():
    def do():
        if _STATE["ctx"] is not None:
            _STATE["ctx"].close()
        if _STATE["pw"] is not None:
            _STATE["pw"].stop()
        _STATE.update(pw=None, ctx=None, page=None)
    try:
        _on_thread(do)
    except Exception:
        pass
