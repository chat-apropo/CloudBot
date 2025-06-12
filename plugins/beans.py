import math
import random
import re

import sqlalchemy
from sqlalchemy import (
    Column,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    select,
)
from sqlalchemy.sql.base import Executable

from cloudbot import hook
from cloudbot.util import database

# Regular expressions for bean commands
bean_add_re = re.compile(r"^\+(\d+)\s+beans?\s+to\s+(\S+)(?:\s+.*)?$", re.IGNORECASE)
bean_admin_add_re = re.compile(r"^\+\+(\d+)\s+beans?\s+to\s+(\S+)(?:\s+.*)?$", re.IGNORECASE)

# Database table for storing bean balances
beans_table = Table(
    "beans",
    database.metadata,
    Column("nick", String),
    Column("beans", Integer),
    PrimaryKeyConstraint("nick"),
)


def get_beans(nick: str, db) -> int:
    """Get the current bean count for a user."""
    nick = nick.lower()
    beans = db.execute(select([beans_table.c.beans]).where(beans_table.c.nick == nick)).fetchone()

    if beans:
        return beans["beans"]

    return 0


def set_beans(nick: str, amount: int, db) -> None:
    """Set the bean count for a user."""
    nick = nick.lower()
    clause = beans_table.c.nick == nick
    beans = db.execute(select([beans_table.c.beans]).where(clause)).fetchone()
    query: Executable

    if beans:
        query = beans_table.update().values(beans=amount).where(clause)
    else:
        query = beans_table.insert().values(nick=nick, beans=amount)

    db.execute(query)
    db.commit()


def transfer_beans(from_nick: str, to_nick: str, amount: int, db) -> bool:
    """Transfer beans from one user to another."""
    from_nick = from_nick.lower()
    to_nick = to_nick.lower()

    # Get current bean counts
    from_beans = get_beans(from_nick, db)
    to_beans = get_beans(to_nick, db)

    # Check if sender has enough beans
    if from_beans < amount:
        return False

    # Update bean counts
    set_beans(from_nick, from_beans - amount, db)
    set_beans(to_nick, to_beans + amount, db)

    return True


def add_beans(nick: str, amount: int, db) -> None:
    """Add beans to a user (admin function)."""
    nick = nick.lower()
    current_beans = get_beans(nick, db)
    set_beans(nick, current_beans + amount, db)


@hook.command("beans", autohelp=False)
def beans_cmd(text: str, nick: str, db) -> str:
    """[user] - Check how many beans you or another user has."""
    if text:
        target = text.strip()
    else:
        target = nick

    beans = get_beans(target, db)
    return f"🌟 {target} has 🫘 {beans:,} beans! 🌟"


@hook.regex(bean_add_re)
def transfer_beans_cmd(match, nick: str, db, notice) -> str | None:
    """<+amount beans to user> - Transfer beans to another user."""
    amount = int(match.group(1))
    target = match.group(2)

    # Prevent negative transfers
    if amount <= 0:
        return "🚫 Amount must be positive! 🚫"

    # Prevent self-transfers
    if nick.lower() == target.lower():
        return "🤔 You can't transfer beans to yourself! 🤔"

    # Attempt the transfer
    success = transfer_beans(nick, target, amount, db)

    if success:
        sender_beans = get_beans(nick, db)
        target_beans = get_beans(target, db)
        return f"🎉 {nick} gave 🫘 {amount} beans to {target}! 🎉 {nick} now has 🫘 {sender_beans} beans, and {target} has 🫘 {target_beans} beans!"
    else:
        return f"😢 You don't have enough beans for that transfer. You have 🫘 {get_beans(nick, db)} beans. 😢"


@hook.regex(bean_admin_add_re)
def admin_add_beans(match, nick: str, db, notice, has_permission) -> str | None:
    """<++amount beans to user> - Admin command to create beans and give them to a user."""
    if not any(has_permission(per) for per in ["op", "botcontrol"]):
        notice("🚫 You don't have permission to use this command! 🚫")
        return None

    amount = int(match.group(1))
    target = match.group(2)

    # Prevent negative amounts
    if amount <= 0:
        return "🚫 Amount must be positive! 🚫"

    # Add beans to target
    add_beans(target, amount, db)
    target_beans = get_beans(target, db)

    return f"✨ {nick} created 🫘 {amount} beans and gave them to {target}! ✨ {target} now has 🫘 {target_beans} beans!"


def _generate_top_beans_response(top_n: int, db) -> str:
    """Helper function to generate the top beans response."""
    query = (
        select([beans_table.c.nick, beans_table.c.beans]).order_by(sqlalchemy.desc(beans_table.c.beans)).limit(top_n)
    )
    results = db.execute(query).fetchall()

    if not results:
        return "😢 No one has any beans yet! 😢"

    beans_list = [f"{i+1}. {row['nick']} 🫘 ({row['beans']:,} beans)" for i, row in enumerate(results)]
    return f"🏆 Top {top_n} Bean Holders: " + "\n".join(beans_list)


@hook.command("topbeans", autohelp=False)
def top_beans(text: str, nick: str, chan: str, db, notice, message) -> str | None:
    """[number] - Shows the top N users with the most beans (default is 10)."""
    try:
        top_n = int(text.strip()) if text else 10
    except ValueError:
        return "🚫 Please provide a valid number for the top users to display. 🚫"

    response = _generate_top_beans_response(top_n, db)
    if top_n <= 10:
        return response.replace("\n", " ")

    notice(f"📩 {nick}, check your DM for the top {top_n} bean holders!")
    # Loop 10 by 10 splits to generate chunks
    for i in range(0, len(response.splitlines()), 10):
        chunk = " ".join(response.splitlines()[i : i + 10])
        message(chunk, nick)


@hook.command("totalbeans", autohelp=False)
def total_beans(db) -> str:
    """- Shows the total number of beans in circulation."""
    query = select([sqlalchemy.func.sum(beans_table.c.beans).label("total_beans")])
    result = db.execute(query).fetchone()

    total_beans = result["total_beans"] if result["total_beans"] is not None else 0
    return f"🌍 There are 🫘 {total_beans:,} beans in circulation! 🌍"


@hook.command("slots", autohelp=False)
def slots(text: str, nick: str, chan: str, db, conn) -> str:
    """[bet] - Play the slot machine! Default bet is 5 beans. Win big or lose it all!"""
    emojis = ["🍒", "🍋", "🍉", "⭐", "🔔", "🍇", "🍊", "🍓", "🍍", "💎"]
    default_bet = 3
    max_prize = 100

    # Determine bet amount
    try:
        bet = int(text.strip()) if text else default_bet
    except ValueError:
        return "Please provide a valid number for your bet."

    if bet < default_bet:
        return f"Minimum bet is {default_bet} beans."

    user_beans = get_beans(nick, db)
    if user_beans < bet:
        return f"You don't have enough beans to bet {bet}. You only have {user_beans} beans."

    bot_beans = get_beans(conn.nick, db)
    max_prize = math.ceil((bet / default_bet) * max_prize)
    if bot_beans < max_prize:
        return (
            f"The bot doesn't have enough beans to pay out a potential prize of {max_prize:,} beans. Try again later!"
        )

    # Deduct bet from user and add to bot's wallet
    if not transfer_beans(nick, conn.nick, bet, db):
        return f"You don't have enough beans to play! You need at least {bet:,} beans to play the slots."

    # Generate expected and actual slot values
    expected_slots = [random.choice(emojis) for _ in range(3)]
    actual_slots = [random.choice(emojis) for _ in range(3)]
    result = " | ".join(f"{e} {a}" for e, a in zip(expected_slots, actual_slots))

    # Check for win conditions
    matches = sum(e == a for e, a in zip(expected_slots, actual_slots))
    if matches == 3:
        if not transfer_beans(conn.nick, nick, max_prize, db):
            return "The bot doesn't have enough beans to pay out the jackpot. Try again later!"
        return f"{result} JACKPOT! You won {max_prize:,} beans!"
    elif matches == 2:
        prize = math.ceil((bet / default_bet) * (max_prize / 2))
        if not transfer_beans(conn.nick, nick, prize, db):
            return "The bot doesn't have enough beans to pay out your prize. Try again later!"
        return f"{result} You won {prize:,} beans!"
    elif matches == 1:
        return f"{result} Almost there! Keep trying! You lost {bet:,} beans."
    else:
        return f"{result} Better luck next time! You lost {bet:,} beans."
