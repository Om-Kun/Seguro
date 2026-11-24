import os
import time
import subprocess
import urllib.parse
import urllib.request
from seguro.tools.registry import registry

BRAVE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"),
]

def _get_brave_path():
    for p in BRAVE_PATHS:
        if os.path.exists(p):
            return p
    return None

def _brave_titles() -> dict:
    from seguro.tools.windows import windows_list
    return {w["hwnd"]: w["title"] for w in windows_list() if w["title"].lower().endswith(" - brave")}

def _to_url(target: str) -> str:
    """Accepts a URL, a local file path (absolute, ~/..., or bare Desktop filename) or a domain."""
    t = target.strip().strip("'\"")
    if t.lower().startswith(("http://", "https://", "file:///")):
        return t
    from seguro.tools.files import normalize_user_path
    local = normalize_user_path(t)
    if os.path.exists(local):
        return "file:///" + urllib.request.pathname2url(local).lstrip("/")
    looks_local = (t.startswith(("~", "/", "\\")) or (len(t) > 2 and t[1] == ":")
                   or t.lower().endswith((".html", ".htm", ".pdf", ".png", ".txt")) and "/" not in t.split(".")[0])
    if looks_local:
        raise FileNotFoundError(f"Local file '{local}' does not exist (not opening it as a web address).")
    return "https://" + t

def _wait_for_title(before: dict, timeout_s: float = 8.0) -> str:
    """Polls Brave windows until one shows a new or changed title (the loaded page)."""
    deadline = time.time() + timeout_s
    loading_titles = {"new tab - brave", "untitled - brave"}
    while time.time() < deadline:
        time.sleep(0.4)
        for hwnd, title in _brave_titles().items():
            if before.get(hwnd) != title and title.lower() not in loading_titles:
                return title[: -len(" - Brave")]
    return ""

@registry.register(
    name="browser_open",
    description="Opens a URL or a local file (e.g. '~/Desktop/page.html') in Brave and returns the loaded page title.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Web URL or local file path to open"}
        },
        "required": ["url"]
    }
)
def browser_open(url: str) -> dict:
    try:
        url = _to_url(url)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    brave = _get_brave_path()
    if not brave:
        return {"ok": False, "error": "Brave is not installed in any known location.", "url": url}
    before = _brave_titles()
    try:
        subprocess.Popen([brave, url])
    except Exception as e:
        return {"ok": False, "error": f"Failed to start Brave: {e}", "url": url}
    title = _wait_for_title(before)
    return {"ok": True, "browser": "brave", "url": url, "window_title": title}

@registry.register(
    name="browser_search",
    description="Searches the web via Brave Search and returns the results page title.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"}
        },
        "required": ["query"]
    }
)
def browser_search(query: str) -> dict:
    encoded = urllib.parse.quote_plus(query)
    search_url = f"https://search.brave.com/search?q={encoded}"
    return browser_open(search_url)
