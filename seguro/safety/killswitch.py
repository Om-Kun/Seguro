import sys
import threading
import time
import keyboard
import pyautogui

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Rule: pyautogui.FAILSAFE = True (mouse to corner aborts)
pyautogui.FAILSAFE = True

class Killswitch:
    def __init__(self, hotkey: str = "f12"):
        self.hotkey = hotkey.lower()
        self._cancel_event = threading.Event()
        self._hooked = False
        self.init_hook()

    def init_hook(self):
        try:
            keyboard.add_hotkey(self.hotkey, self.trigger)
            self._hooked = True
        except Exception as e:
            print(f"[Killswitch] Warning: Failed to hook {self.hotkey}: {e}")

    def trigger(self):
        self._cancel_event.set()
        print("\n🚨 [KILLSWITCH] F12 ACTIVATED! ALL ACTIONS ABORTED IMMEDIATELY! 🚨")

    def is_triggered(self) -> bool:
        return self._cancel_event.is_set()

    def reset(self):
        self._cancel_event.clear()

# Global default instance
killswitch = Killswitch()
