"""Speech out (Windows' built-in SAPI voice) and speech in (local Whisper via faster-whisper).

The setup downloads the speech model into config.models_dir(); Listener loads it from there when it
is present, else faster-whisper uses its own cache as before. JARVIS_MIC picks the microphone (its
name, as the setup lists it); unset or unplugged means Windows' default microphone.
"""

import queue
import threading
import time

from . import config

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

    def __init__(self, model_name: str = "base.en", mic: str | None = None):
        import numpy  # noqa: F401 - fail early if voice extras are missing
        import sounddevice  # noqa: F401
        from faster_whisper import WhisperModel

        root = model_root(model_name)
        print(f"Loading speech model '{model_name}'" + ("..." if root else " (first run downloads it)..."))
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=root)
        self.device = input_device(config.lookup()("JARVIS_MIC") if mic is None else mic)
        self.noise = self._calibrate()

    def _calibrate(self) -> float:
        import numpy as np
        import sounddevice as sd

        audio = sd.rec(int(0.6 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                       device=self.device)
        sd.wait()
        return float(np.sqrt(np.mean(audio**2)) + 1e-6)

    def record(self, max_wait: float = 8.0, max_len: float = 15.0, silence: float = 0.9):
        """Wait for speech, record until a pause. Returns float32 audio or None if nothing was said."""
        import numpy as np
        import sounddevice as sd

        threshold = max(self.noise * 3.0, 0.01)
        block = int(0.05 * SAMPLE_RATE)
        chunks, started, quiet, waited = [], False, 0.0, 0.0
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block,
                            device=self.device) as stream:
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


# --- where the speech model and the microphone come from ---------------------------------
def model_repo(name: str) -> str:
    """The Hugging Face repository faster-whisper downloads a model name from (as its utils._MODELS)."""
    if "/" in name:
        return name
    if name in ("turbo", "large-v3-turbo"):
        return "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    if name.startswith("distil-"):
        return f"Systran/faster-distil-whisper-{name[len('distil-'):]}"
    return f"Systran/faster-whisper-{name}"


def model_folder(name: str):
    """Where snapshot_download(cache_dir=config.models_dir()) keeps this model."""
    return config.models_dir() / ("models--" + model_repo(name).replace("/", "--"))


def model_root(name: str) -> str | None:
    """config.models_dir() if the setup downloaded this model there (faster-whisper's download_root),
    else None: faster-whisper then uses its usual cache."""
    try:
        found = any(model_folder(name).glob("snapshots/*/model.bin"))
    except OSError:
        found = False
    return str(config.models_dir()) if found else None


def input_devices() -> list[dict]:
    """Microphones on the default sound system, each once: [{"index", "name", "default"}]."""
    import sounddevice as sd

    hostapi, default = sd.default.hostapi, sd.default.device[0]
    out, seen = [], set()
    for index, dev in enumerate(sd.query_devices()):
        if dev["hostapi"] != hostapi or dev["max_input_channels"] <= 0 or dev["name"] in seen:
            continue
        seen.add(dev["name"])
        out.append({"index": index, "name": dev["name"], "default": index == default})
    return out


def input_device(name: str | None) -> int | None:
    """The device index for a saved microphone name; None (Windows' default) if unset or not plugged in."""
    if not name:
        return None
    try:
        return next((d["index"] for d in input_devices() if d["name"] == name), None)
    except Exception:  # noqa: BLE001 - no sound system: recording on the default device reports it
        return None
