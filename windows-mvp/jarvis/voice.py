"""Speech out (Windows' built-in SAPI voice) and speech in (local Whisper via faster-whisper)."""

import queue
import threading
import time

SAMPLE_RATE = 16000


class Speaker:
    """Speaks on a background thread so timers can talk too."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._q: queue.Queue[str] = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        if enabled:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
        except Exception:  # noqa: BLE001 - no voice available; stay silent
            self.enabled = False
            self._idle.set()
            return
        while True:
            text = self._q.get()
            self._idle.clear()
            try:
                voice.Speak(text)
            except Exception:  # noqa: BLE001
                pass
            if self._q.empty():
                self._idle.set()

    def say(self, text: str) -> None:
        if self.enabled and text:
            self._idle.clear()
            self._q.put(text)

    def wait(self, timeout: float = 30) -> None:
        if self.enabled:
            self._idle.wait(timeout)


class Listener:
    """Records one utterance from the default microphone and transcribes it."""

    def __init__(self, model_name: str = "base.en"):
        import numpy  # noqa: F401 - fail early if voice extras are missing
        import sounddevice  # noqa: F401
        from faster_whisper import WhisperModel

        print(f"Loading speech model '{model_name}' (first run downloads it)...")
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8")
        self.noise = self._calibrate()

    def _calibrate(self) -> float:
        import numpy as np
        import sounddevice as sd

        audio = sd.rec(int(0.6 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
        return float(np.sqrt(np.mean(audio**2)) + 1e-6)

    def record(self, max_wait: float = 8.0, max_len: float = 15.0, silence: float = 0.9):
        """Wait for speech, record until a pause. Returns float32 audio or None if nothing was said."""
        import numpy as np
        import sounddevice as sd

        threshold = max(self.noise * 3.0, 0.01)
        block = int(0.05 * SAMPLE_RATE)
        chunks, started, quiet, waited = [], False, 0.0, 0.0
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block) as stream:
            while True:
                data, _ = stream.read(block)
                level = float(np.sqrt(np.mean(data**2)))
                if not started:
                    waited += 0.05
                    chunks = (chunks + [data])[-6:]  # keep a little audio from before speech starts
                    if level > threshold:
                        started = True
                    elif waited > max_wait:
                        return None
                    continue
                chunks.append(data)
                quiet = quiet + 0.05 if level < threshold else 0.0
                if quiet >= silence or len(chunks) * 0.05 > max_len:
                    break
        return np.concatenate(chunks)[:, 0]

    def transcribe(self, audio) -> str:
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    def listen(self, **kw) -> str:
        audio = self.record(**kw)
        if audio is None:
            return ""
        t = time.time()
        text = self.transcribe(audio)
        print(f"  (heard in {time.time() - t:.1f}s)")
        return text
