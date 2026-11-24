import urllib.parse
from seguro.tools.registry import registry
from seguro.tools.browser import browser_open

@registry.register(
    name="skill_youtube_player",
    description="Searches YouTube for any topic, video, or song and opens playback directly in Brave Browser.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query or video title to play on YouTube"}
        },
        "required": ["query"]
    }
)
def skill_youtube_player(query: str) -> dict:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.youtube.com/results?search_query={encoded}"
    res = browser_open(url)
    return {
        "ok": True,
        "query": query,
        "url": url,
        "browser": res.get("browser", "brave")
    }
