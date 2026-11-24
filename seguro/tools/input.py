import time
import pyautogui
import pyperclip
import win32gui
from seguro.tools.registry import registry

# Synthetic input goes to whatever window has focus. Never let it land in these (e.g. the user
# chatting with Claude, or Seguro's own windows). Extendable via config safety.protected_windows.
DEFAULT_PROTECTED = ["Claude", "Seguro", "ChatGPT"]


def _protected_titles():
    try:
        from seguro.llm.client import load_config
        return DEFAULT_PROTECTED + list(load_config().get("safety", {}).get("protected_windows", []))
    except Exception:
        return DEFAULT_PROTECTED


def focus_guard():
    """Returns an error dict if the focused window must not receive synthetic input, else None."""
    title = win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    hit = next((p for p in _protected_titles() if p.lower() in title.lower()), None)
    if hit:
        return {"ok": False, "error": f"Refusing to send input: the focused window '{title[:60]}' is protected "
                                      f"({hit}). Focus the target app first (windows_focus)."}
    return None

@registry.register(
    name="type_text",
    description="Types text into the currently active control. Uses instant clipboard paste for speed and code preservation.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text or code to type"}
        },
        "required": ["text"]
    }
)
def type_text(text: str) -> dict:
    blocked = focus_guard()
    if blocked:
        return blocked
    if len(text) > 8 or "\n" in text or "\t" in text:
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    else:
        pyautogui.write(text, interval=0.01)
    return {"ok": True, "chars": len(text)}

@registry.register(
    name="hotkey",
    description="Presses a combination of keys (e.g. ['ctrl', 's'], ['alt', 'f4'], ['enter']).",
    parameters={
        "type": "object",
        "properties": {
            "keys": {"type": "array", "items": {"type": "string"}, "description": "Key names sequence"}
        },
        "required": ["keys"]
    }
)
def hotkey(keys: list) -> dict:
    from seguro.tools.registry import _parse_list
    keys = _parse_list(keys)
    blocked = focus_guard()
    if blocked:
        return blocked
    clean_keys = [str(k).strip().lower() for k in keys if str(k).strip()]
    invalid = [k for k in clean_keys if k not in pyautogui.KEYBOARD_KEYS]
    if not clean_keys or invalid:
        return {"ok": False, "error": f"Invalid key name(s) {invalid or clean_keys}. Example: ['ctrl', 's']."}
    pyautogui.hotkey(*clean_keys)
    return {"ok": True, "keys": clean_keys}

@registry.register(
    name="click_xy",
    description="Clicks at specific physical screen coordinates.",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X coordinate in physical pixels"},
            "y": {"type": "integer", "description": "Y coordinate in physical pixels"},
            "double": {"type": "boolean", "description": "Set true to double click"},
            "right": {"type": "boolean", "description": "Set true to right click"}
        },
        "required": ["x", "y"]
    }
)
def click_xy(x: int, y: int, double: bool = False, right: bool = False) -> dict:
    blocked = focus_guard()
    if blocked:
        return blocked
    pyautogui.moveTo(x, y, duration=0.10)
    if double:
        pyautogui.doubleClick(x, y)
    elif right:
        pyautogui.rightClick(x, y)
    else:
        pyautogui.click(x, y)
    return {"ok": True, "coords": (x, y)}

@registry.register(
    name="scroll",
    description="Scrolls vertically at current cursor position.",
    parameters={
        "type": "object",
        "properties": {
            "amount": {"type": "integer", "description": "Scroll clicks (negative for down, positive for up)"}
        },
        "required": ["amount"]
    }
)
def scroll(amount: int = -500) -> dict:
    pyautogui.scroll(amount)
    return {"ok": True, "amount": amount}
