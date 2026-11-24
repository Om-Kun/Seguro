import time
import ctypes
import win32gui
import win32con
from seguro.tools.registry import registry

def ensure_default_desktop():
    """Attaches calling thread to interactive 'default' desktop if currently isolated."""
    try:
        u32 = ctypes.windll.user32
        hdesk = u32.OpenDesktopW("default", 0, False, 0x01FF)
        if hdesk:
            u32.SetThreadDesktop(hdesk)
    except Exception:
        pass

def _enum_windows():
    ensure_default_desktop()
    results = []
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            text = win32gui.GetWindowText(hwnd).strip()
            if text and text not in ["Program Manager", "Default IME", "MSCTFIME UI"]:
                results.append({"hwnd": hwnd, "title": text})
        return True
    win32gui.EnumWindows(callback, None)
    return results

@registry.register(
    name="windows_list",
    description="Lists all visible top-level desktop windows with their titles and handles.",
    parameters={"type": "object", "properties": {}}
)
def windows_list() -> list:
    return _enum_windows()

@registry.register(
    name="windows_focus",
    description="Brings a window to the foreground by matching its title.",
    parameters={
        "type": "object",
        "properties": {
            "title_contains": {"type": "string", "description": "Substring to search for in window title"}
        },
        "required": ["title_contains"]
    }
)
def windows_focus(title_contains: str) -> dict:
    target = title_contains.lower()
    windows = _enum_windows()
    for win in windows:
        if target in win["title"].lower():
            hwnd = win["hwnd"]
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                return {"status": "focused", "title": win["title"], "hwnd": hwnd}
            except Exception as e:
                return {"status": "error", "message": str(e), "title": win["title"]}
    return {"status": "not_found", "query": title_contains}

@registry.register(
    name="windows_maximize",
    description="Maximizes a visible window matching the specified title substring.",
    parameters={
        "type": "object",
        "properties": {
            "title_contains": {"type": "string", "description": "Substring to search for in window title"}
        },
        "required": ["title_contains"]
    }
)
def windows_maximize(title_contains: str) -> dict:
    target = title_contains.lower()
    for win in _enum_windows():
        if target in win["title"].lower():
            hwnd = win["hwnd"]
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return {"status": "maximized", "title": win["title"]}
    return {"status": "not_found", "query": title_contains}

@registry.register(
    name="windows_close",
    description="Sends close message (WM_CLOSE) to a window matching the title.",
    parameters={
        "type": "object",
        "properties": {
            "title_contains": {"type": "string", "description": "Substring to search for in window title"}
        },
        "required": ["title_contains"]
    }
)
def windows_close(title_contains: str) -> dict:
    target = title_contains.lower()
    for win in _enum_windows():
        if target in win["title"].lower():
            hwnd = win["hwnd"]
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return {"status": "closed", "title": win["title"]}
    return {"status": "not_found", "query": title_contains}
