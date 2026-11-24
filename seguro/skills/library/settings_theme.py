import ctypes
import winreg
from seguro.tools.registry import registry

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
TARGET_VALUES = {
    "apps": ["AppsUseLightTheme"],
    "system": ["SystemUsesLightTheme"],
    "both": ["AppsUseLightTheme", "SystemUsesLightTheme"],
}

@registry.register(
    name="settings_set_theme",
    description="Switches Windows dark/light mode (per-user registry setting). target: 'apps', 'system' (taskbar/Start) or 'both'.",
    parameters={
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["dark", "light"], "description": "dark or light"},
            "target": {"type": "string", "enum": ["apps", "system", "both"], "description": "What to switch (default: both)"}
        },
        "required": ["mode"]
    }
)
def settings_set_theme(mode: str, target: str = "both") -> dict:
    mode = str(mode).strip().lower()
    target = str(target).strip().lower()
    if mode not in ("dark", "light"):
        return {"ok": False, "error": f"mode must be 'dark' or 'light', got {mode!r}"}
    if target not in TARGET_VALUES:
        return {"ok": False, "error": f"target must be apps/system/both, got {target!r}"}

    value = 0 if mode == "dark" else 1
    names = TARGET_VALUES[target]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY, 0, winreg.KEY_SET_VALUE) as k:
        for n in names:
            winreg.SetValueEx(k, n, 0, winreg.REG_DWORD, value)

    # Tell running apps the colour set changed so they repaint without a sign-out.
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SETTINGCHANGE, 0, "ImmersiveColorSet", SMTO_ABORTIFHUNG, 2000, None
    )
    return {"ok": True, "mode": mode, "target": target, "keys_written": names}
