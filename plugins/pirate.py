from urllib.parse import quote_plus

import requests

from cloudbot import hook

PIRATE_INSULT_API = "https://pirate.monkeyness.com/api/insult"
PIRATE_TRANSLATE_API = "https://pirate.monkeyness.com/api/translate?english="


@hook.command("pirateinsult", autohelp=False)
def pirate_insult() -> str:
    """- Get a random pirate insult."""
    try:
        response = requests.get(PIRATE_INSULT_API, timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except Exception as e:
        return f"Error: {e}"


@hook.command("pirate")
def pirate_translate(text: str) -> str:
    """<text> - Translate text to pirate speak."""
    if not text.strip():
        return "Error: You must provide text to translate."
    try:
        url = PIRATE_TRANSLATE_API + quote_plus(text)
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except Exception as e:
        return f"Error: {e}"
