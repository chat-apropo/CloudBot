import random
from functools import lru_cache

from cloudbot import hook

BULLETS = 6


@lru_cache(maxsize=1)
def get_barrel():
    index = random.randint(0, BULLETS - 1)
    return [False] * index + [True] + [False] * (BULLETS - index - 1)


@hook.command("russian_roulette", "rr", autohelp=False)
def rr(text, bot, chan, nick, reply):
    """- Start a game of Russian Roulette and test it."""
    barrel = get_barrel()
    if len(barrel) == BULLETS:
        reply("Barrel spinning...")
    shot = barrel.pop()
    if shot:
        reply("BANG! 🩸🤯 🔫   -  You died.")
        get_barrel.cache_clear()
        return
    reply(f"You live. {len(barrel)} bullets left. Use .rrs to re-spin the barrel.")


@hook.command("rrspin", "rrs", autohelp=False)
def respun(text, bot, chan, nick, reply):
    """- Respun the barrel."""
    get_barrel.cache_clear()
    reply("Barrel re-spinning...")
