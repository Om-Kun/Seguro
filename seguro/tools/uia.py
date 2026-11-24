import time
import threading
import pyautogui
from seguro.tools.registry import registry

# Cache for latest snapshot elements
_LATEST_SNAPSHOT = {}

class UIASnapshotter:
    def __init__(self, timeout_s: float = 1.5):
        self.timeout_s = timeout_s

    def capture_elements(self, window_title: str = None) -> list:
        elements = []
        try:
            import uiautomation as auto
            target_win = None
            if window_title:
                target_win = auto.WindowControl(searchDepth=3, SubName=window_title)
            else:
                target_win = auto.GetForegroundControl()

            if not target_win or not target_win.Exists(0.1, 0.1):
                target_win = auto.GetRootControl()

            if not target_win:
                return []

            target_types = {
                "ButtonControl", "EditControl", "MenuItemControl", "ListItemControl",
                "TreeItemControl", "TabItemControl", "CheckBoxControl", "RadioButtonControl",
                "ComboBoxControl", "HyperlinkControl", "DocumentControl"
            }

            idx = 0
            def walk(ctrl, depth=0):
                nonlocal idx
                if depth > 20 or idx >= 200:
                    return
                try:
                    c_type = ctrl.ControlTypeName
                    name = ctrl.Name.strip() if ctrl.Name else ""
                    rect = ctrl.BoundingRectangle
                    
                    if c_type in target_types and rect and (rect.right - rect.left > 6) and (rect.bottom - rect.top > 6):
                        elements.append({
                            "id": idx,
                            "type": c_type.replace("Control", ""),
                            "name": name[:40],
                            "rect": (rect.left, rect.top, rect.right, rect.bottom),
                            "center": ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2),
                            "_ctrl": ctrl
                        })
                        idx += 1

                    for child in ctrl.GetChildren():
                        walk(child, depth + 1)
                except Exception:
                    pass

            # Walk in a thread with hard timeout
            t = threading.Thread(target=walk, args=(target_win,))
            t.daemon = True
            t.start()
            t.join(self.timeout_s)

        except Exception as e:
            print(f"[UIA] Snapshot warning: {e}")

        return elements

snapshotter = UIASnapshotter()

@registry.register(
    name="uia_snapshot",
    description="Captures accessibility tree of interactive elements (buttons, inputs, menus) on the active window in ~50ms.",
    parameters={
        "type": "object",
        "properties": {
            "window_title": {"type": "string", "description": "Optional window title substring to inspect"}
        }
    }
)
def uia_snapshot(window_title: str = None) -> dict:
    global _LATEST_SNAPSHOT
    elements = snapshotter.capture_elements(window_title)
    
    _LATEST_SNAPSHOT = {e["id"]: e for e in elements}
    
    # Text representation for LLM prompt
    formatted = []
    for e in elements:
        l, t, r, b = e["rect"]
        formatted.append(f"[{e['id']}] {e['type']} \"{e['name']}\" ({l},{t},{r},{b})")

    is_poor = len(elements) < 5
    return {
        "ok": True,
        "count": len(elements),
        "uia_poor": is_poor,
        "elements_text": "\n".join(formatted) if formatted else "No UIA elements found (UIA-poor window)."
    }

@registry.register(
    name="uia_click",
    description="Clicks an interactive UI element by its index from the latest uia_snapshot.",
    parameters={
        "type": "object",
        "properties": {
            "element_id": {"type": "integer", "description": "Element index from uia_snapshot"}
        },
        "required": ["element_id"]
    }
)
def uia_click(element_id: int) -> dict:
    elem = _LATEST_SNAPSHOT.get(element_id)
    if not elem:
        return {"ok": False, "error": f"Element [{element_id}] not found in latest snapshot."}

    ctrl = elem.get("_ctrl")
    # Try pattern invocation first (InvokePattern)
    if ctrl:
        try:
            pattern = ctrl.GetInvokePattern()
            if pattern:
                pattern.Invoke()
                return {"ok": True, "method": "uia_invoke", "element": elem["name"]}
        except Exception:
            pass

    # Fallback to physical click at center coordinates
    cx, cy = elem["center"]
    pyautogui.moveTo(cx, cy, duration=0.10)
    pyautogui.click()
    return {"ok": True, "method": "physical_click", "element": elem["name"], "coords": (cx, cy)}

@registry.register(
    name="uia_set_text",
    description="Sets text value of an input field identified by its uia_snapshot index.",
    parameters={
        "type": "object",
        "properties": {
            "element_id": {"type": "integer", "description": "Element index from uia_snapshot"},
            "text": {"type": "string", "description": "Text value to enter"}
        },
        "required": ["element_id", "text"]
    }
)
def uia_set_text(element_id: int, text: str) -> dict:
    elem = _LATEST_SNAPSHOT.get(element_id)
    if not elem:
        return {"ok": False, "error": f"Element [{element_id}] not found in latest snapshot."}

    ctrl = elem.get("_ctrl")
    if ctrl:
        try:
            val_pattern = ctrl.GetValuePattern()
            if val_pattern:
                val_pattern.SetValue(text)
                return {"ok": True, "method": "uia_value_pattern", "element": elem["name"]}
        except Exception:
            pass

    # Fallback: click and paste via clipboard (only if the focused window may receive input)
    import pyperclip
    from seguro.tools.input import focus_guard
    blocked = focus_guard()
    if blocked:
        return blocked
    cx, cy = elem["center"]
    pyautogui.click(cx, cy)
    time.sleep(0.1)
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    return {"ok": True, "method": "clipboard_paste", "element": elem["name"]}
