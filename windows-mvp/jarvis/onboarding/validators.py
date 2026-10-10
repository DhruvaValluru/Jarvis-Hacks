"""Checks for what the setup asks for: API keys, the Telegram bot, pairing codes and names.

Key checks make one free call to the service the key is for (list models, getMe). Messages never
contain the key, and errors are mapped to plain words: rejected (401), lacks permission (403),
can't reach the service (no connection). Network calls use urllib only (no new dependency);
tests replace `urlopen` here.
"""

import json
import re
import secrets
import ssl
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field

urlopen = urllib.request.urlopen  # tests replace this
TIMEOUT = 15
MAX_ANSWER = 256 * 1024
OPENAI_MODELS = "https://api.openai.com/v1/models"
TELEGRAM_API = "https://api.telegram.org"

KEYLIKE = re.compile(r"[\x21-\x7e]{20,400}")  # one line of printable ASCII, no spaces
BOT_TOKEN = re.compile(r"[0-9]{5,15}:[A-Za-z0-9_-]{30,60}")
BOT_NAME = re.compile(r"[A-Za-z0-9_]{3,64}")
SPECTRUM_SECRET = re.compile(r"[\x21-\x7e]{8,500}")
PC_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,29}")  # as the app's Settings page (server.SETTINGS)
TELEGRAM_ID = re.compile(r"[1-9][0-9]{0,19}")
LINK_CODE = re.compile(r"[0-9]{6}")
PAIRING = re.compile(r"[\x21-\x7e]+")
MIN_PAIRING = 16  # remote.MIN_TOKEN: a shorter JARVIS_TOKEN won't share a PC
MAX_PAIRING = 200


@dataclass
class Check:
    ok: bool
    result: str  # ok, empty, format, rejected, forbidden, offline, error
    message: str
    info: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "result": self.result, "message": self.message, **self.info}


class Offline(Exception):
    """The service couldn't be reached (no internet, DNS, proxy, TLS, timeout)."""


def clean(value) -> str:
    """What someone pasted: surrounding spaces, quotes and line breaks removed."""
    return str(value or "").strip().strip("'\"").strip()


# --- Anthropic ---------------------------------------------------------------
def anthropic_client(key: str):
    import anthropic  # lazy: the app starts without importing the SDK

    return anthropic.Anthropic(api_key=key, max_retries=1, timeout=TIMEOUT)


def check_anthropic(key, client_factory=None) -> Check:
    """Ask Anthropic for one model with this key (free). client_factory(key) returns a client."""
    key = clean(key)
    if not key:
        return Check(False, "empty", "Paste your Anthropic API key first.")
    if not KEYLIKE.fullmatch(key):
        return Check(False, "format", "That doesn't look like an API key. Copy it again from console.anthropic.com.")
    try:
        (client_factory or anthropic_client)(key).models.list(limit=1)
    except Exception as e:  # noqa: BLE001 - every failure becomes a message without the key in it
        return anthropic_error(e)
    return Check(True, "ok", "Anthropic accepted the key.")


def anthropic_error(error: Exception) -> Check:
    status = getattr(error, "status_code", None)
    if status == 401:
        return Check(False, "rejected", "Key rejected: Anthropic doesn't accept this key. "
                                        "Copy it again from console.anthropic.com (API keys).")
    if status == 403:
        return Check(False, "forbidden", "This key lacks permission. Check the key's workspace and "
                                         "permissions in the Anthropic Console, or make a new key.")
    if _connection_error(error):
        return Check(False, "offline", "Can't reach Anthropic: check your internet connection, then try again.")
    if status == 429:
        return Check(False, "error", "Anthropic is limiting requests for this key right now. Try again in a minute.")
    if isinstance(status, int) and status >= 500:
        return Check(False, "error", f"Anthropic had a problem (error {status}). Try again in a minute.")
    return Check(False, "error", f"Anthropic couldn't check the key ({type(error).__name__}).")


def _connection_error(error: Exception) -> bool:
    try:
        import anthropic

        if isinstance(error, anthropic.APIConnectionError):  # includes timeouts
            return True
    except ImportError:
        pass
    return isinstance(error, (OSError, TimeoutError, urllib.error.URLError, Offline))


# --- OpenAI --------------------------------------------------------------------
def check_openai(key) -> Check:
    """GET /v1/models with the key (free)."""
    key = clean(key)
    if not key:
        return Check(False, "empty", "Paste your OpenAI API key first.")
    if not KEYLIKE.fullmatch(key):
        return Check(False, "format", "That doesn't look like an API key. Copy it again from platform.openai.com.")
    request = urllib.request.Request(OPENAI_MODELS, headers={"Authorization": f"Bearer {key}", "User-Agent": "Alfred"})
    try:
        status, _ = fetch(request)
    except Offline:
        return Check(False, "offline", "Can't reach OpenAI: check your internet connection, then try again.")
    if status == 200:
        return Check(True, "ok", "OpenAI accepted the key.")
    if status == 401:
        return Check(False, "rejected", "Key rejected: OpenAI doesn't accept this key. "
                                        "Copy it again from platform.openai.com (API keys).")
    if status == 403:
        return Check(False, "forbidden", "This key lacks permission to list models. Give it the Models "
                                         "permission (or All) in OpenAI's API key settings.")
    if status == 429:
        return Check(False, "error", "OpenAI is limiting requests for this key right now. Try again in a minute.")
    return Check(False, "error", f"OpenAI couldn't check the key (error {status}). Try again in a minute.")


# --- Telegram ----------------------------------------------------------------
def check_telegram_token(token) -> Check:
    """The token's shape, then Telegram's getMe: the bot's @name and t.me link."""
    token = clean(token)
    if not token:
        return Check(False, "empty", "Paste the bot token from @BotFather first.")
    if not BOT_TOKEN.fullmatch(token):
        return Check(False, "format", "That doesn't look like a bot token. It looks like 123456789:AAE..., "
                                      "and @BotFather sends it when you make a bot with /newbot.")
    try:
        status, body = fetch(urllib.request.Request(f"{TELEGRAM_API}/bot{token}/getMe", headers={"User-Agent": "Alfred"}))
    except Offline:
        return Check(False, "offline", "Can't reach Telegram: check your internet connection, then try again.")
    if status in (401, 404):
        return Check(False, "rejected", "Token rejected: Telegram doesn't know this bot token. Copy it again from "
                                        "@BotFather, or get a new one there with /token.")
    if status != 200:
        return Check(False, "error", f"Telegram couldn't check the token (error {status}). Try again in a minute.")
    try:
        answer = json.loads(body)
        bot = answer["result"]
        username = str(bot["username"])
        ok = answer.get("ok") is True and bot.get("is_bot") is True and BOT_NAME.fullmatch(username)
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        return Check(False, "error", "Telegram's answer didn't look right. Try again in a minute.")
    return Check(True, "ok", f"Found your bot @{username}.", {
        "username": username, "name": str(bot.get("first_name") or "")[:64], "link": f"https://t.me/{username}",
    })


def check_spectrum_secret(secret) -> Check:
    secret = clean(secret)
    if not secret:
        return Check(False, "empty", "Paste the Photon Spectrum project secret first.")
    if not SPECTRUM_SECRET.fullmatch(secret):
        return Check(False, "format", "That doesn't look like a project secret (one line, no spaces).")
    return Check(True, "ok", "Saved the Photon Spectrum secret.")


def new_link_code() -> str:
    """Six random digits the user sends to their bot to prove the Telegram account is theirs."""
    return f"{secrets.randbelow(10**6):06d}"


# --- other PCs -----------------------------------------------------------------
def new_pairing_code() -> str:
    return secrets.token_urlsafe(24)  # 32 characters


def group(code: str, size: int = 4) -> str:
    """'abcdefghijkl' -> 'abcd efgh ijkl', easier to read out and compare."""
    return " ".join(code[i:i + size] for i in range(0, len(code), size))


def normalize_pairing_code(text) -> str:
    """What someone pasted or typed: spaces and line breaks (from the grouped display), quotes and
    a JARVIS_TOKEN= prefix (from jarvis.bat --show-token) removed."""
    text = "".join(unicodedata.normalize("NFKC", str(text or "")).split())
    if text.upper().startswith("JARVIS_TOKEN="):
        text = text[len("JARVIS_TOKEN="):]
    return text.strip("'\"")


def check_pairing_code(text) -> Check:
    code = normalize_pairing_code(text)
    if not code:
        return Check(False, "empty", "Paste the pairing code from your other PC first.")
    if not PAIRING.fullmatch(code) or len(code) > MAX_PAIRING:
        return Check(False, "format", "That doesn't look like a pairing code. Copy it again from your other PC.")
    if len(code) < MIN_PAIRING:
        return Check(False, "format", f"That code is too short to be safe (at least {MIN_PAIRING} characters). "
                                      "Make a new one on your first PC.")
    return Check(True, "ok", "Saved. This PC finds your other PCs with this code.")


def check_pc_name(name) -> Check:
    name = str(name or "").strip()
    if name and not PC_NAME.fullmatch(name):
        return Check(False, "format", "Use up to 30 letters, digits, spaces, - and _ for the PC's name.")
    return Check(True, "ok", "Saved.", {"name": name})


# --- HTTP ----------------------------------------------------------------------
def fetch(request: urllib.request.Request, timeout: float = TIMEOUT) -> tuple[int, bytes]:
    """(status, body) for any HTTP answer; raises Offline when there is none. The error text is never
    passed on: a Telegram URL has the bot token in it."""
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(MAX_ANSWER)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_ANSWER)
        except Exception:  # noqa: BLE001
            body = b""
        finally:
            e.close()
        return e.code, body
    except (urllib.error.URLError, OSError, TimeoutError, ssl.SSLError, ValueError) as e:
        raise Offline(type(e).__name__) from None
