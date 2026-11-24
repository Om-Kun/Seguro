import os
import re
import json
import subprocess
from rapidfuzz import process, fuzz
from seguro.tools.registry import registry

INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps_index.json")

COMMON_ALIASES = {
    "notepad": "notepad.exe",
    "calc": r'"C:\Program Files\LibreOffice\program\scalc.exe"' if os.path.exists(r"C:\Program Files\LibreOffice\program\scalc.exe") else "calc.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "ms paint": "mspaint.exe",
    "libreoffice": r'"C:\Program Files\LibreOffice\program\soffice.exe"',
    "libre office": r'"C:\Program Files\LibreOffice\program\soffice.exe"',
    "soffice": r'"C:\Program Files\LibreOffice\program\soffice.exe"',
    "writer": r'"C:\Program Files\LibreOffice\program\swriter.exe"',
    "swriter": r'"C:\Program Files\LibreOffice\program\swriter.exe"',
    "libreoffice writer": r'"C:\Program Files\LibreOffice\program\swriter.exe"',
    "libre office writer": r'"C:\Program Files\LibreOffice\program\swriter.exe"',
    "scalc": r'"C:\Program Files\LibreOffice\program\scalc.exe"',
    "libreoffice calc": r'"C:\Program Files\LibreOffice\program\scalc.exe"',
    "libre office calc": r'"C:\Program Files\LibreOffice\program\scalc.exe"',
    "impress": r'"C:\Program Files\LibreOffice\program\simpress.exe"',
    "simpress": r'"C:\Program Files\LibreOffice\program\simpress.exe"',
    "libreoffice impress": r'"C:\Program Files\LibreOffice\program\simpress.exe"',
    "libre office impress": r'"C:\Program Files\LibreOffice\program\simpress.exe"',
    "word": r'"C:\Program Files\LibreOffice\program\swriter.exe"' if os.path.exists(r"C:\Program Files\LibreOffice\program\swriter.exe") else "winword.exe",
    "excel": r'"C:\Program Files\LibreOffice\program\scalc.exe"' if os.path.exists(r"C:\Program Files\LibreOffice\program\scalc.exe") else "excel.exe",
    "powerpoint": r'"C:\Program Files\LibreOffice\program\simpress.exe"' if os.path.exists(r"C:\Program Files\LibreOffice\program\simpress.exe") else "powerpnt.exe",
    "ppt": r'"C:\Program Files\LibreOffice\program\simpress.exe"' if os.path.exists(r"C:\Program Files\LibreOffice\program\simpress.exe") else "powerpnt.exe",
    "cmd": "start cmd.exe",
    "terminal": "wt.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "brave": r'"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"',
    "chrome": r'"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"',
    "vs code": "code",
    "vscode": "code",
    "spotify": "start spotify:",
    "settings": "start ms-settings:",
}

def build_apps_index() -> dict:
    """Queries Windows Get-StartApps to index every desktop and Store app."""
    apps = {}
    try:
        ps_cmd = "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=8)
        if res.returncode == 0 and res.stdout.strip():
            raw_list = json.loads(res.stdout)
            if isinstance(raw_list, dict):
                raw_list = [raw_list]
            for item in raw_list:
                name = item.get("Name", "").strip()
                app_id = item.get("AppID", "").strip()
                if name and app_id:
                    apps[name.lower()] = app_id
    except Exception as e:
        print(f"[Apps] Warning: Get-StartApps failed: {e}")

    try:
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(apps, f, indent=2)
    except Exception:
        pass

    return apps

def load_apps_index() -> dict:
    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return build_apps_index()

# Pre-load apps index
_APPS_CACHE = load_apps_index()

@registry.register(
    name="apps_launch",
    description="Launches any installed Windows desktop or Microsoft Store application by name.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name or alias of the application (e.g. 'Notepad', 'Calculator', 'Photoshop', 'Brave')"}
        },
        "required": ["name"]
    }
)
def apps_launch(name: str) -> dict:
    global _APPS_CACHE
    clean_name = name.strip().lower()

    # 1. Check direct aliases
    if clean_name in COMMON_ALIASES:
        target = COMMON_ALIASES[clean_name]
        if target.startswith("start "):
            subprocess.Popen(target, shell=True)
            return {"status": "launched", "method": "uri_protocol", "target": target}
        else:
            subprocess.Popen(target, shell=True)
            return {"status": "launched", "method": "alias_binary", "target": target}

    # 2. Check Apps Index (Get-StartApps)
    if not _APPS_CACHE:
        _APPS_CACHE = build_apps_index()

    if clean_name in _APPS_CACHE:
        app_id = _APPS_CACHE[clean_name]
        subprocess.Popen(f'explorer.exe "shell:AppsFolder\\{app_id}"', shell=True)
        return {"status": "launched", "method": "start_app_id", "app_id": app_id}

    # 3. Fuzzy matching across installed apps
    choices = list(_APPS_CACHE.keys())
    match = process.extractOne(clean_name, choices, scorer=fuzz.token_sort_ratio)
    if match and match[1] >= 70:
        matched_name = match[0]
        app_id = _APPS_CACHE[matched_name]
        subprocess.Popen(f'explorer.exe "shell:AppsFolder\\{app_id}"', shell=True)
        return {"status": "launched", "method": "fuzzy_match", "matched_name": matched_name, "score": match[1], "app_id": app_id}

    # No match. Never fall back to running the model's string through a shell.
    suggestion = f" Closest installed app: '{match[0]}' ({match[1]:.0f}%)." if match else ""
    return {"status": "error", "message": f"No installed application matches '{name}'.{suggestion}"}
