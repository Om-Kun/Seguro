import re
import urllib.parse
from typing import Optional, Dict, Any

class FastRouter:
    """
    Sub-300ms deterministic router for unambiguous commands.
    Directly produces tool call specifications without waiting for LLM inference.
    """

    def __init__(self):
        self.rules = [
            # Google Sheets
            (
                re.compile(r"^(?:open|launch|create|new)\s+(?:a\s+)?(?:google\s+)?sheet[s]?\s*$", re.IGNORECASE),
                lambda m: {"tool": "google_sheets_open", "args": {}}
            ),
            # Google Docs
            (
                re.compile(r"^(?:open|launch|create|new)\s+(?:a\s+)?(?:google\s+)?doc[s]?\s*$", re.IGNORECASE),
                lambda m: {"tool": "google_docs_open", "args": {}}
            ),
            # Google Slides
            (
                re.compile(r"^(?:open|launch|create|new)\s+(?:a\s+)?(?:google\s+)?(?:slide[s]?|presentation)\s*$", re.IGNORECASE),
                lambda m: {"tool": "google_slides_open", "args": {}}
            ),
            # Play on YouTube
            (
                re.compile(r"^(?:play|watch)\s+(.+?)\s+on\s+youtube\s*$", re.IGNORECASE),
                self._route_youtube
            ),
            # Search query
            (
                re.compile(r"^(?:search|google|search\s+for)\s+(.+)$", re.IGNORECASE),
                self._route_search
            ),
            # Create folder / directory
            (
                re.compile(r"^(?:create|make)\s+(?:a\s+)?(?:directory|folder)\s+([a-zA-Z0-9_\-\.]+)\s+on\s+desktop\s*$", re.IGNORECASE),
                lambda m: {"tool": "files_mkdir", "args": {"path": f"~/Desktop/{m.group(1)}"}}
            ),
            # Create CSV file
            (
                re.compile(r"^create\s+(?:a\s+)?csv\s+file\s+([a-zA-Z0-9_\-\.]+)\s+on\s+desktop\s+with\s+header\s+([^\s]+)\s+and\s+rows\s+(.+)$", re.IGNORECASE),
                self._route_create_csv
            ),
            # Close app / window
            (
                re.compile(r"^(?:close|quit|exit)\s+([a-zA-Z0-9\s\.\-_]+)$", re.IGNORECASE),
                self._route_close
            ),
            # Open / launch app
            (
                re.compile(r"^(?:open|launch|start|run)\s+([a-zA-Z0-9\s\.\-_]+)$", re.IGNORECASE),
                self._route_launch
            ),
        ]

    # Signs of a multi-step or question-bearing request; those must go to the brain loop.
    COMPOUND = re.compile(r"\b(and|then|tell me|what|which|after|with|from|into|save|type|write)\b|[,;?]", re.IGNORECASE)

    def match(self, prompt: str) -> Optional[Dict[str, Any]]:
        """
        Tests prompt against fast deterministic patterns.
        Returns tool execution spec: {'tool': str, 'args': dict} or None.
        """
        clean_p = prompt.strip().rstrip(".!")
        for pattern, handler in self.rules:
            m = pattern.match(clean_p)
            if m:
                res = handler(m)
                if res:
                    return res
        return None

    def _route_youtube(self, match: re.Match) -> dict:
        query = match.group(1).strip()
        if self.COMPOUND.search(query):
            return None
        encoded = urllib.parse.quote_plus(query)
        return {
            "tool": "browser_open",
            "args": {"url": f"https://www.youtube.com/results?search_query={encoded}"}
        }

    def _route_search(self, match: re.Match) -> dict:
        query = match.group(1).strip()
        if self.COMPOUND.search(query) or re.match(r"^(the\s+)?web\b", query, re.IGNORECASE):
            return None
        return {
            "tool": "browser_search",
            "args": {"query": query}
        }

    def _route_create_csv(self, match: re.Match) -> dict:
        filename = match.group(1).strip()
        headers = match.group(2).strip()
        rows_str = match.group(3).strip()
        raw_rows = rows_str.split(" and ")
        lines = [headers] + [r.strip() for r in raw_rows if r.strip()]
        return {
            "tool": "files_write",
            "args": {"path": f"~/Desktop/{filename}", "content": "\n".join(lines)}
        }

    def _route_launch(self, match: re.Match) -> dict:
        app_name = match.group(1).strip()
        # Only take the fast path when the name is a known app, never free text.
        if self.COMPOUND.search(app_name) or len(app_name.split()) > 4:
            return None
        from seguro.tools.apps import COMMON_ALIASES, _APPS_CACHE
        if app_name.lower() not in COMMON_ALIASES and app_name.lower() not in _APPS_CACHE:
            return None
        return {
            "tool": "apps_launch",
            "args": {"name": app_name}
        }

    def _route_close(self, match: re.Match) -> dict:
        app_name = match.group(1).strip()
        if self.COMPOUND.search(app_name) or len(app_name.split()) > 4:
            return None
        return {
            "tool": "windows_close",
            "args": {"title_contains": app_name}
        }

fast_router = FastRouter()
