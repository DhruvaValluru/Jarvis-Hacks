"""The setup's API under /api/onboarding/, behind the app's sign-in like every other /api route.

  GET    state  system  consent  settings  legacy  speech  speech/download  telegram/link  autostart
  POST   step {step, status}  consent {version}  legacy/preview {text}  legacy/import {text?}
         anthropic {key, save_anyway?}  safety {computer_use}  speech/download  speech/test
         openai {key}  telegram/token {token}  telegram/spectrum {secret}  telegram/link
         pcs/new {replace?}  pcs/join {code}  try
  PUT    microphone {name}  pcs {name?, serve?}  autostart {on}
  DELETE speech/download (cancel)  telegram/link (cancel)

Keys go straight to the secret store (Windows Credential Manager) and are never sent back, only
Store.secret_hint. Changing a setting restarts what reads it, as the Settings page does.

The voice test runs through the service when it has voice_test(mic) -> {"heard", "reply"} (listen
once, run what was heard, return the reply); without it the setup says so and moves on.
"""

import dataclasses
import os
import re
import socket
import tempfile
import threading
from pathlib import Path

from .. import config, consent, settings
from ..app.http import HTTPError, Request, Response
from ..service import NotReady, QueueFull
from ..service.bridge import KEYS as TELEGRAM_KEYS
from . import speech, system, validators
from .link import LinkError, TelegramLink
from .state import DONE, REQUIRED, SKIPPED, STEPS, TITLES, VERSION, SetupState

TRY_COMMAND = "open notepad"
MAX_ENV_TEXT = 64 * 1024
MODEL_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{0,39}")
ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]*")
SECRET_LOOKING = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD")
SECRETS_SHOWN = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "SPECTRUM_PROJECT_SECRET", "JARVIS_TOKEN")
NOT_READY = {
    "welcome": "Alfred needs Windows 10 (version 1809 or later) or Windows 11.",
    "consent": "Read the notice and press I agree first.",
    "anthropic": "Save an Anthropic API key first, or skip this step.",
    "openai": "Save an OpenAI API key first, or skip this step.",
    "telegram": "Save the bot token and the Photon secret, then link your account. Or skip this step.",
    "pcs": "Make or paste a pairing code first, or skip this step.",
    "finish": "Read the notice and press I agree first.",
}


class Onboarding:
    def __init__(self, service, store, *, shell=None, state=None, autostart=None, downloads=None,
                 anthropic_client=None):
        from ..service.fake import FakeService

        self.service, self.store, self.shell = service, store, shell
        self.state = state or SetupState()
        self.autostart = autostart or system.Autostart()
        self.downloads = downloads or speech.ModelDownload()
        self.anthropic_client = anthropic_client  # client_factory(key) for tests; None: the Anthropic SDK
        self.link = TelegramLink(service, store, on_linked=lambda _owner: self.state.mark("telegram", DONE))
        self.legacy_files = list(dict.fromkeys([config.MVP_DIR / ".env", config.app_dir() / ".env"]))
        self.any_os = isinstance(service, FakeService)  # the stand-in engine runs anywhere
        self.bot: dict = {}  # the bot the last token check found: username, name, link

    def add_routes(self, router) -> None:
        add = router.add
        api = "/api/onboarding"
        add("GET", f"{api}/state", lambda req: Response.json(self.view()))
        add("POST", f"{api}/step", self.step)
        add("GET", f"{api}/system", lambda req: Response.json(system.check(any_os=self.any_os)))
        add("GET", f"{api}/consent", self.get_consent)
        add("POST", f"{api}/consent", self.agree)
        add("GET", f"{api}/settings", lambda req: Response.json(self.current()))
        add("GET", f"{api}/legacy", self.legacy)
        add("POST", f"{api}/legacy/preview", self.legacy_preview)
        add("POST", f"{api}/legacy/import", self.legacy_import)
        add("POST", f"{api}/anthropic", self.anthropic)
        add("POST", f"{api}/safety", self.safety)
        add("GET", f"{api}/speech", self.get_speech)
        add("PUT", f"{api}/microphone", self.microphone)
        add("GET", f"{api}/speech/download", lambda req: Response.json(self.downloads.progress()))
        add("POST", f"{api}/speech/download", self.download)
        add("DELETE", f"{api}/speech/download", lambda req: Response.json(self.downloads.cancel()))
        add("POST", f"{api}/speech/test", self.voice_test)
        add("POST", f"{api}/openai", self.openai)
        add("POST", f"{api}/telegram/token", self.telegram_token)
        add("POST", f"{api}/telegram/spectrum", self.telegram_spectrum)
        add("GET", f"{api}/telegram/link", lambda req: Response.json(self.link_status()))
        add("POST", f"{api}/telegram/link", self.link_start)
        add("DELETE", f"{api}/telegram/link", self.link_cancel)
        add("POST", f"{api}/pcs/new", self.pcs_new)
        add("POST", f"{api}/pcs/join", self.pcs_join)
        add("PUT", f"{api}/pcs", self.pcs)
        add("GET", f"{api}/autostart", lambda req: Response.json(self.autostart.status()))
        add("PUT", f"{api}/autostart", self.set_autostart)
        add("POST", f"{api}/try", self.try_it)
        if getattr(self.shell, "static", None) is not None:  # the page itself, like the app's other files
            add("GET", "/onboarding/", self.page, auth=False)
            add("GET", "/onboarding", lambda req: Response(303, b"", {"Location": "/onboarding/"}), auth=False)

    def page(self, req: Request) -> Response:
        return self.shell.static(dataclasses.replace(req, path="/onboarding/index.html"))

    # --- steps ------------------------------------------------------------------
    def view(self) -> dict:
        data = self.state.read()
        steps = [{"name": name, "title": TITLES[name], "status": data["steps"].get(name, ""),
                  "required": name in REQUIRED, "available": self._available(name, data),
                  "ready": self._ready(name)} for name in STEPS]
        current = next((s["name"] for s in steps if not s["status"] and s["available"]), "finish")
        return {"version": VERSION, "complete": bool(data["completed_at"]), "completed_at": data["completed_at"],
                "current": current, "steps": steps}

    def step(self, req: Request) -> Response:
        body = req.json()
        name, status = body.get("step"), body.get("status")
        if name not in STEPS:
            raise HTTPError(404, "No such setup step.")
        if status not in (DONE, SKIPPED):
            raise HTTPError(400, "A step is either done or skipped.")
        if status == SKIPPED and name in REQUIRED:
            raise HTTPError(409, f"{TITLES[name]} can't be skipped.")
        if status == DONE and not self._ready(name):
            raise HTTPError(409, NOT_READY.get(name, "That step isn't finished yet."))
        if status == DONE and name == "finish":
            answered = self.state.read()["steps"]
            missing = [TITLES[n] for n in STEPS if n in REQUIRED and n != "finish" and answered.get(n) != DONE]
            if missing:
                raise HTTPError(409, f"Go through these steps first: {', '.join(missing)}.")
        self.state.mark(name, status)
        return Response.json(self.view())

    def _available(self, name: str, data: dict) -> bool:
        if name == "import":  # only with an old .env, or once the user chose one
            return name in data["steps"] or self.legacy_file() is not None
        return True

    def _ready(self, name: str) -> bool:
        """Whether a step may be marked done."""
        if name == "welcome":
            return self.any_os or system.windows()["supported"]
        if name in ("consent", "finish"):
            return consent.accepted()
        if name == "anthropic":
            return self._have("ANTHROPIC_API_KEY") or self._have("ANTHROPIC_AUTH_TOKEN")
        if name == "openai":
            return self._have("OPENAI_API_KEY")
        if name == "telegram":
            return all(self._have(k) for k in ("TELEGRAM_BOT_TOKEN", "SPECTRUM_PROJECT_SECRET", "TELEGRAM_OWNER_ID"))
        if name == "pcs":
            return self._have("JARVIS_TOKEN")
        return True

    def _have(self, key: str) -> bool:
        return bool(self.store.get(key) or os.environ.get(key))

    def current(self) -> dict:
        """What the setup shows as already set. Keys only as hints."""
        saved = self.store.values()
        status = self.service.status()
        secrets_out = {}
        for key in SECRETS_SHOWN:
            hint = self.store.secret_hint(key)
            secrets_out[key] = {"set": bool(hint), "hint": hint}
        return {
            "secrets": secrets_out,
            "secrets_saved": bool(getattr(self.store.secrets, "persistent", False)),
            "computer_use": saved.get("JARVIS_COMPUTER_USE", "1") != "0",
            "name": saved.get("JARVIS_NAME", ""),
            "default_name": status.pc_name or socket.gethostname(),
            "serve": saved.get("JARVIS_SERVE", "0") == "1",
            "telegram_owner": saved.get("TELEGRAM_OWNER_ID", ""),
            "telegram": status.telegram,
            "bot": self.bot,
            "engine": status.engine,
        }

    # --- consent ----------------------------------------------------------------
    def get_consent(self, req: Request) -> Response:
        return Response.json({"accepted": consent.accepted(), "version": consent.TERMS_VERSION,
                              "notice_markdown": consent.notice_markdown()})

    def agree(self, req: Request) -> Response:
        version = req.json().get("version")
        if version is not None and version != consent.TERMS_VERSION:
            raise HTTPError(409, "The notice changed while it was open. Read the new one, then agree.")
        consent.record("app")
        start = getattr(self.shell, "start_service", None) or self.service.start  # the app also starts Telegram
        start()
        self.state.mark("consent", DONE)
        return Response.json({"accepted": True, "state": self.view()})

    # --- old settings -----------------------------------------------------------
    def legacy_file(self) -> Path | None:
        return next((Path(p) for p in self.legacy_files if Path(p).is_file()), None)

    def legacy(self, req: Request) -> Response:
        path = self.legacy_file()
        keys = _listing(config.read_env_file(path)) if path else []
        return Response.json({"found": path is not None, "path": str(path or ""), "keys": keys})

    def legacy_preview(self, req: Request) -> Response:
        return Response.json({"keys": _listing(_with_file(_env_text(req.json().get("text")), config.read_env_file))})

    def legacy_import(self, req: Request) -> Response:
        text = req.json().get("text")
        try:
            if text is None:
                path = self.legacy_file()
                if path is None:
                    raise HTTPError(404, "Alfred didn't find old settings next to it. Choose the file instead.")
                imported = self.store.import_legacy_env(path)
            else:
                imported = _with_file(_env_text(text), self.store.import_legacy_env)
        except ValueError as e:
            raise HTTPError(400, str(e)) from e
        if not imported:
            raise HTTPError(400, "That file has no settings in it.")
        self.state.mark("import", DONE)
        restarting = self._apply(set(imported))
        return Response.json({"imported": imported, "restarting": restarting, "state": self.view()})

    # --- keys -------------------------------------------------------------------
    def anthropic(self, req: Request) -> Response:
        body = req.json()
        key = body.get("key")
        check = validators.check_anthropic(key, self.anthropic_client)
        unchecked = body.get("save_anyway") is True and check.result in ("offline", "error")
        if not (check.ok or unchecked):
            return _failed(check)
        self._save_secret("ANTHROPIC_API_KEY", validators.clean(key))
        self.state.mark("anthropic", DONE)
        message = check.message if check.ok else "Saved without checking. Alfred tells you if Anthropic turns it down."
        return Response.json({**check.as_dict(), "message": message, "saved": True,
                              "hint": self.store.secret_hint("ANTHROPIC_API_KEY"),
                              "restarting": self._apply({"ANTHROPIC_API_KEY"})})

    def openai(self, req: Request) -> Response:
        key = req.json().get("key")
        check = validators.check_openai(key)
        if not check.ok:
            return _failed(check)
        self._save_secret("OPENAI_API_KEY", validators.clean(key))
        self.state.mark("openai", DONE)
        return Response.json({**check.as_dict(), "saved": True, "hint": self.store.secret_hint("OPENAI_API_KEY"),
                              "restarting": self._apply({"OPENAI_API_KEY"})})

    # --- safety -----------------------------------------------------------------
    def safety(self, req: Request) -> Response:
        on = req.json().get("computer_use")
        if not isinstance(on, bool):
            raise HTTPError(400, "Computer use is either on or off.")
        changed = set()
        if self.store.values().get("JARVIS_COMPUTER_USE", "1") != ("1" if on else "0"):
            self.store.set("JARVIS_COMPUTER_USE", "1" if on else "0")
            changed.add("JARVIS_COMPUTER_USE")
        self.state.mark("safety", DONE)
        return Response.json({"computer_use": on, "restarting": self._apply(changed), "state": self.view()})

    # --- speech -----------------------------------------------------------------
    def model_name(self) -> str:
        name = config.lookup(self.store)("JARVIS_WHISPER_MODEL") or self.store.get("JARVIS_WHISPER_MODEL") or "base.en"
        return name if MODEL_NAME.fullmatch(name) else "base.en"

    def get_speech(self, req: Request) -> Response:
        name = self.model_name()
        return Response.json({
            "microphones": speech.microphones(), "selected": self.store.get("JARVIS_MIC") or "",
            "model": name, "present": speech.present(name), "download": self.downloads.progress(),
            "voice_test": callable(getattr(self.service, "voice_test", None)),
        })

    def microphone(self, req: Request) -> Response:
        name = req.json().get("name")
        if name in (None, ""):
            self.store.unset("JARVIS_MIC")
            return Response.json({"selected": ""})
        if not isinstance(name, str) or name not in [d["name"] for d in speech.microphones()["devices"]]:
            raise HTTPError(400, "That microphone isn't plugged in any more. Pick another one.")
        self.store.set("JARVIS_MIC", name)
        return Response.json({"selected": name})

    def download(self, req: Request) -> Response:
        try:
            return Response.json(self.downloads.start(self.model_name()))
        except RuntimeError as e:
            raise HTTPError(409, str(e)) from e

    def voice_test(self, req: Request) -> Response:
        test = getattr(self.service, "voice_test", None)
        if not callable(test):
            return Response.json({"supported": False, "message": (
                "This version of Alfred can't run the voice test from here yet. Your microphone and the speech "
                "model are set up, so you can go on.")})
        try:
            result = test(mic=self.store.get("JARVIS_MIC") or None)
        except NotReady as e:
            raise HTTPError(409, str(e) or "Alfred isn't ready yet. Try again in a few seconds.") from e
        except Exception as e:  # noqa: BLE001 - no microphone, no model, ...: say so on the page
            raise HTTPError(409, f"The voice test didn't work ({type(e).__name__}). You can skip it.") from e
        result = result if isinstance(result, dict) else {}
        return Response.json({"supported": True, "heard": str(result.get("heard") or ""),
                              "reply": str(result.get("reply") or "")})

    # --- Telegram ---------------------------------------------------------------
    def telegram_token(self, req: Request) -> Response:
        token = req.json().get("token")
        check = validators.check_telegram_token(token)
        if not check.ok:
            return _failed(check)
        self._save_secret("TELEGRAM_BOT_TOKEN", validators.clean(token))
        self.bot = dict(check.info)
        return Response.json({**check.as_dict(), "hint": self.store.secret_hint("TELEGRAM_BOT_TOKEN"),
                              "restarting": self._apply({"TELEGRAM_BOT_TOKEN"})})

    def telegram_spectrum(self, req: Request) -> Response:
        secret = req.json().get("secret")
        check = validators.check_spectrum_secret(secret)
        if not check.ok:
            return _failed(check)
        self._save_secret("SPECTRUM_PROJECT_SECRET", validators.clean(secret))
        return Response.json({**check.as_dict(), "hint": self.store.secret_hint("SPECTRUM_PROJECT_SECRET"),
                              "restarting": self._apply({"SPECTRUM_PROJECT_SECRET"})})

    def link_status(self) -> dict:
        tg = getattr(self.service, "telegram", None)
        return {**self.link.status(), "telegram": self.service.status().telegram,
                "telegram_error": getattr(tg, "last_error", "") or "", "bot": self.bot}

    def link_start(self, req: Request) -> Response:
        try:
            self.link.start()
        except LinkError as e:
            raise HTTPError(409, str(e)) from e
        return Response.json(self.link_status())

    def link_cancel(self, req: Request) -> Response:
        self.link.cancel()
        return Response.json(self.link_status())

    # --- other PCs --------------------------------------------------------------
    def pcs_new(self, req: Request) -> Response:
        if self.store.has_secret("JARVIS_TOKEN") and req.json().get("replace") is not True:
            return Response.json({"error": "This PC already has a pairing code. A new one means your other PCs "
                                           "need the new code too.", "result": "exists",
                                  "hint": self.store.secret_hint("JARVIS_TOKEN")}, 409)
        code = validators.new_pairing_code()
        self._save_secret("JARVIS_TOKEN", code)
        return Response.json({"pairing_code": code, "grouped": validators.group(code),
                              "restarting": self._apply({"JARVIS_TOKEN"})})

    def pcs_join(self, req: Request) -> Response:
        text = req.json().get("code")
        check = validators.check_pairing_code(text)
        if not check.ok:
            return _failed(check)
        self._save_secret("JARVIS_TOKEN", validators.normalize_pairing_code(text))
        return Response.json({**check.as_dict(), "hint": self.store.secret_hint("JARVIS_TOKEN"),
                              "restarting": self._apply({"JARVIS_TOKEN"})})

    def pcs(self, req: Request) -> Response:
        body = req.json()
        saved, changed = self.store.values(), set()
        if "name" in body:
            check = validators.check_pc_name(body["name"])
            if not check.ok:
                return _failed(check)
            if saved.get("JARVIS_NAME", "") != check.info["name"]:
                self.store.set("JARVIS_NAME", check.info["name"])
                changed.add("JARVIS_NAME")
        if "serve" in body:
            if not isinstance(body["serve"], bool):
                raise HTTPError(400, "Sharing this PC is either on or off.")
            if (saved.get("JARVIS_SERVE", "0") == "1") != body["serve"]:
                self.store.set("JARVIS_SERVE", "1" if body["serve"] else "")
                changed.add("JARVIS_SERVE")
        return Response.json({"saved": sorted(changed), "restarting": self._apply(changed)})

    # --- finish -------------------------------------------------------------------
    def set_autostart(self, req: Request) -> Response:
        on = req.json().get("on")
        if not isinstance(on, bool):
            raise HTTPError(400, "Starting with Windows is either on or off.")
        try:
            return Response.json(self.autostart.set(on))
        except OSError as e:
            raise HTTPError(409, str(e) or "Windows didn't let Alfred change its startup list.") from e

    def try_it(self, req: Request) -> Response:
        if not self.service.wait_ready(20):
            raise HTTPError(409, "Alfred isn't ready yet. Wait until it says Ready, then try again.")
        try:
            result = self.service.run_and_wait(TRY_COMMAND, "desktop", timeout=60)
        except (NotReady, QueueFull) as e:
            raise HTTPError(409, str(e)) from e
        return Response.json({"command": TRY_COMMAND, "ok": result.ok, "replies": result.replies,
                              "stopped": result.stopped, "timed_out": result.timed_out})

    # --- helpers ------------------------------------------------------------------
    def _save_secret(self, key: str, value: str) -> None:
        try:
            self.store.set_secret(key, value)
        except ValueError as e:
            raise HTTPError(400, str(e)) from e

    def _apply(self, keys: set) -> bool:
        """Restart what reads these settings (as the Settings page does). True if the engine restarts."""
        if not keys:
            return False
        status = self.service.status()
        engine = bool(keys - set(TELEGRAM_KEYS)) and status.engine not in ("off", "needs_consent")
        if engine:
            self.service.restart_engine("settings")
        if keys & set(TELEGRAM_KEYS) and status.telegram not in ("off", "not_configured"):
            threading.Thread(target=self._restart_telegram, daemon=True, name="alfred-telegram-restart").start()
        return engine

    def _restart_telegram(self) -> None:
        self.service.stop_telegram()
        self.service.start_telegram()


def _failed(check: validators.Check) -> Response:
    return Response.json({"error": check.message, **check.as_dict()}, 422)


def _env_text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPError(400, "That file is empty.")
    if len(value) > MAX_ENV_TEXT:
        raise HTTPError(400, "That file is too big to be Alfred's old settings.")
    return value


def _with_file(text: str, fn):
    """fn(path) on a private temporary copy of text in Alfred's folder, removed straight after."""
    fd, name = tempfile.mkstemp(prefix="import-", suffix=".env", dir=config.data_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        return fn(Path(name))
    finally:
        Path(name).unlink(missing_ok=True)


def _listing(values: dict) -> list[dict]:
    """Old settings to show before importing them: names, plain values, and only hints of secrets."""
    out = []
    for key, value in values.items():
        if not ENV_KEY.fullmatch(key):
            continue
        hidden = settings.is_secret(key) or bool(SECRET_LOOKING.search(key))
        shown = (settings.hint(value) if settings.is_secret(key) else "(hidden)") if hidden else value[:80]
        out.append({"name": key, "secret": hidden, "value": shown})
    return out
