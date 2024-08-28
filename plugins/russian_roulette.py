import random
from functools import lru_cache

from cloudbot import hook
from cloudbot.util.colors import get_color, get_format

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
        reply("Barrel reloaded. Spinning...")
    shot = barrel.pop()
    if shot:
        reply(f"BANG! 🩸🤯 🔫   -  {get_color('red')}You died")
        get_barrel.cache_clear()
        return
    reply(
        f"{get_color('green')}You live{get_format('clear')}. \x02{len(barrel)}\x02 bullets left. Use \x1d.rrs\x1d to re-spin the barrel."
    )


@hook.command("rrspin", "rrs", autohelp=False)
def respun(text, bot, chan, nick, reply):
    """- Respun the barrel."""
    barrel = get_barrel()
    if len(barrel) == BULLETS:
        reply("Start a new game with \x1d.rr\x1d first")
        return
    get_barrel.cache_clear()
    reply("Barrel re-spinning...")
