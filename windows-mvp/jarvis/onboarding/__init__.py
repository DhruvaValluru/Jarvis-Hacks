"""Alfred's first-run setup: a wizard in the app's window (app/ui/onboarding/) over /api/onboarding/*.

Steps, in order; each can be revisited later from Settings:
  welcome    what Alfred is, and a quick check of this PC (only an unsupported Windows blocks)
  consent    the data notice; I agree records consent and starts Alfred's engine (required)
  import     an old windows-mvp\\.env from the console version, when there is one (or the user picks it)
  anthropic  the Anthropic API key, checked with a free call (skippable: simple commands only)
  safety     computer use on or off, and every way to stop Alfred (required to read)
  speech     microphone and the local speech model download (optional)
  openai     OpenAI key for Telegram voice notes and spoken replies (optional)
  telegram   bot token, Photon secret, and linking the user's account with a code (optional)
  pcs        pairing code for the user's other PCs, this PC's name, sharing it (optional)
  finish     start Alfred at sign-in (off by default), try a command, done

Progress is kept in data_dir()/onboarding.json (state.py); until the setup is finished the app opens it
first. Windows-only and optional modules are imported only when a step uses them.
"""

from .state import STEPS


def register(router, service, store, *, shell=None, **options):
    """Add the setup's routes to the app's router. shell is the App (server.py): with it the setup
    page is served at /onboarding/ and agreeing to the notice starts the app's service the same way.
    Returns the Onboarding object (routes.py)."""
    from .routes import Onboarding

    wizard = Onboarding(service, store, shell=shell, **options)
    wizard.add_routes(router)
    return wizard


__all__ = ["STEPS", "register"]
