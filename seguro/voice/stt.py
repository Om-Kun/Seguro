import os
import sys
import time
import threading
from typing import Optional, Callable
import numpy as np

def looks_hallucinated(text: str) -> bool:
    """Whisper on near-silence produces one short phrase repeated ('Okay. Okay. Okay...')."""
    import re
    words = re.findall(r"[a-z']+", text.lower())
    if len(words) < 4:
        return False
    from collections import Counter
    top, n = Counter(words).most_common(1)[0]
    return n >= 4 and n / len(words) >= 0.5


class SpeechTranscriber:
    """
    Faster-Whisper Push-to-Talk (F9) ASR Engine.
    High-speed int8 GPU/CPU transcription with ~0.3-0.6s latency.
    """

    def __init__(self, model_size: str = "base.en", device: str = "auto", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._recording = False
        self._audio_frames = []
        self._stream = None
        self._sample_rate = 16000

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
                dev = self.device
                if dev == "auto":
                    import torch
                    dev = "cuda" if torch.cuda.is_available() else "cpu"
                
                ct = self.compute_type
                if dev == "cpu" and ct == "float16":
                    ct = "int8"
                    
                self._model = WhisperModel(self.model_size, device=dev, compute_type=ct)
            except Exception as e:
                print(f"[STT] Warning: Failed to load Faster-Whisper: {e}")
                self._model = None
        return self._model

    def start_recording(self):
        """Starts recording audio from microphone."""
        if self._recording:
            return
        self._audio_frames = []
        self._recording = True

        try:
            import sounddevice as sd
            def callback(indata, frames, time_info, status):
                if self._recording:
                    self._audio_frames.append(indata.copy())

            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                callback=callback
            )
            self._stream.start()
        except Exception as e:
            print(f"[STT] Audio input device error: {e}")
            self._recording = False

    def stop_and_transcribe(self) -> str:
        """Stops recording and returns transcribed text string."""
        if not self._recording and not self._audio_frames:
            return ""

        self._recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if not self._audio_frames:
            return ""

        # Concatenate audio frames
        audio_np = np.concatenate(self._audio_frames, axis=0).flatten()
        return self.transcribe_audio_array(audio_np)

    def transcribe_audio_array(self, audio_data: np.ndarray) -> str:
        """Transcribes a raw float32 16kHz audio array."""
        model = self._get_model()
        if model is None:
            return ""

        try:
            t0 = time.time()
            segments, info = model.transcribe(
                audio_data,
                beam_size=1,
                language="en",
                temperature=0.0,
                vad_filter=True,                    # drop non-speech: Whisper invents words on silence/noise
                condition_on_previous_text=False,   # stops "Okay. Okay. Okay..." loops
            )
            text = " ".join([s.text.strip() for s in segments]).strip()
            if looks_hallucinated(text):
                print(f"[STT] Rejected likely hallucination: {text[:80]!r}")
                return ""
            elapsed_ms = (time.time() - t0) * 1000.0
            if text:
                print(f"[STT] Transcribed in {elapsed_ms:.1f}ms: \"{text}\"")
            return text
        except Exception as e:
            print(f"[STT] Transcription error: {e}")
            return ""

transcriber = SpeechTranscriber()
