def determine_rung(tool_name: str) -> str:
    """Classifies a tool into the execution ladder (for HUD badges and reports)."""
    t = tool_name.lower()
    if t.startswith("skill_") or t.startswith("settings_"):
        return "SKILL"
    if t in ("run_powershell", "run_python", "process_start", "process_stop") or t.startswith("files_"):
        return "SHELL"
    if t.startswith(("excel_", "word_", "pptx_", "chart_", "browser_", "web_", "news_", "gmail_", "google_")):
        return "API"
    if t.startswith("uia_") or t.startswith("windows_") or t == "apps_launch":
        return "UIA"
    if t in ("look_at_screen", "ground_and_click", "click_xy", "type_text", "hotkey", "scroll", "screenshot_window"):
        return "VISION"
    if "delegate" in t:
        return "DELEGATE"
    if "cloud" in t:
        return "CLOUD"
    return "CORE"
