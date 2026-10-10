"""Offline voice test, same on every PC (no microphone needed).

Windows' built-in voice says fixed phrases into a WAV file, Whisper transcribes
it, and the router must turn each into the expected command.
"""

import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import win32com.client  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

from jarvis import router  # noqa: E402

CASES = {
    "Open Notepad": ("open_app", {"name": "notepad"}),
    "Set volume to thirty percent": ("set_volume", {"percent": 30}),
    "Next song": ("media_control", {"action": "next"}),
    "What time is it": ("system_status", {}),
}


def speak_to_wav(text: str, path: str) -> None:
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono, what Whisper wants
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(path, 3)  # SSFMCreateForWrite
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()


def read_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


model = WhisperModel(os.environ.get("JARVIS_WHISPER_MODEL", "base.en"), device="cpu", compute_type="int8")
failures = 0
with tempfile.TemporaryDirectory() as tmp:
    for phrase, expected in CASES.items():
        path = os.path.join(tmp, "say.wav")
        speak_to_wav(phrase, path)
        segments, _ = model.transcribe(read_wav(path), language="en", beam_size=1, vad_filter=True)
        heard = " ".join(s.text.strip() for s in segments)
        got = router.route(heard)
        status = "ok" if got == expected else "FAIL"
        failures += status == "FAIL"
        print(f"[{status}] said {phrase!r} -> heard {heard!r} -> {got}")
if failures:
    sys.exit(f"{failures} voice case(s) failed")
print("VOICE PIPELINE PASSED")
