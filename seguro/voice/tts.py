import sys
import threading
from typing import Optional

class TTSVoice:
    """
    Non-blocking speech synthesis engine for Seguro V2.
    Uses native Windows SAPI COM with SVSFlagsAsync so speech feedback
    runs in background without freezing agent loops or mouse movements.
    """

    def __init__(self, rate: int = 1, volume: int = 100):
        self.rate = rate
        self.volume = volume
        self._voice = None
        self._lock = threading.Lock()
        self._init_sapi()

    def _init_sapi(self):
        try:
            import win32com.client
            # SAPI COM voice
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._voice.Rate = self.rate
            self._voice.Volume = self.volume
        except Exception as e:
            # Fallback or silent mode
            self._voice = None

    def speak(self, text: str, non_blocking: bool = True):
        """
        Speaks text. If non_blocking=True, returns immediately (0.001s).
        """
        if not text or not text.strip():
            return

        clean_text = text.strip()
        print(f"[Seguro Voice]: \"{clean_text}\"")

        if not self._voice:
            return

        # SVSFlagsAsync = 1 in SAPI COM
        flags = 1 if non_blocking else 0
        try:
            with self._lock:
                self._voice.Speak(clean_text, flags)
        except Exception:
            # Re-init voice if COM disconnected
            try:
                self._init_sapi()
                if self._voice:
                    self._voice.Speak(clean_text, flags)
            except Exception:
                pass

    def stop(self):
        """Stops currently playing speech immediately."""
        if self._voice:
            try:
                # SVSFPurgeBeforeSpeak = 2
                self._voice.Speak("", 2)
            except Exception:
                pass

tts = TTSVoice()
