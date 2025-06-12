import math
import random
import re
import time
from datetime import datetime
from time import time

import sqlalchemy
from cachetools import TTLCache
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
from cloudbot.event import EventType
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

# Database table for storing trivia questions
trivia_table = Table(
    "trivia",
    database.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", sqlalchemy.DateTime),
    Column("creator", String),
    Column("question", String),
    Column("answer", String),
    Column("prize", Integer),
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


def get_total_beans(db) -> int:
    """Get the total number of beans in circulation."""
    query = select([sqlalchemy.func.sum(beans_table.c.beans).label("total_beans")])
    result = db.execute(query).fetchone()
    return result["total_beans"] if result["total_beans"] is not None else 0


@hook.command("totalbeans", autohelp=False)
def total_beans(db) -> str:
    """- Shows the total number of beans in circulation."""
    total_beans = get_total_beans(db)
    return f"🌍 There are 🫘 {total_beans:,} beans in circulation! 🌍"


slot_cooldown_cache = TTLCache(maxsize=1000, ttl=3600 * 24 * 2)  # Cache for slot cooldowns


@hook.command("slots", autohelp=False)
def slots(text: str, nick: str, chan: str, reply, db, conn) -> str:
    """[bet] - Play the slot machine! Default bet is 5 beans. Win big or lose it all!"""
    emojis = ["🍒", "🍋", "🍉", "⭐", "🔔", "🍇", "🍊", "🍓", "🍍", "💎"]

    total_beans = get_total_beans(db)
    bot_beans = get_beans(conn.nick, db)
    bot_market_share = bot_beans / total_beans if total_beans > 0 else 0

    min_bet = 3
    if bot_market_share > 0.3:
        min_bet = 2
    if bot_market_share > 0.5:
        min_bet = 1

    max_prize = 100

    # Cooldown settings
    attempts_per_cooldown = 3
    cooldown_time_base = 15  # seconds
    cooldown_bet_multiplier = 2
    # Is also multiplied by the bet multiplier
    cooldown_time_multiplier = 1.5

    # Determine bet amount
    try:
        bet = int(text.strip()) if text else min_bet
    except ValueError:
        return "Please provide a valid number for your bet."

    if bet < min_bet:
        return f"Minimum bet is {min_bet} beans."

    bet_multiplier = bet / min_bet

    # Check cooldown
    current_time = math.floor(time())
    if nick not in slot_cooldown_cache:
        slot_cooldown_cache[nick] = {
            "remaining_plays": attempts_per_cooldown,
            "cooldown_until": 0,
            "accumulated_bet": min_bet,
        }

    cooldown_entry = slot_cooldown_cache[nick]

    wait_time = cooldown_entry["cooldown_until"] - current_time
    cooldown_msg = f"⏳ You need to wait {wait_time} seconds before playing again. Increase your bet to {cooldown_entry['accumulated_bet']} to play now. �����"
    if wait_time > 0 and bet < cooldown_entry["accumulated_bet"]:
        return cooldown_msg

    # If not in cooldown, reset accumulated bet
    if wait_time <= 0:
        slot_cooldown_cache[nick] = {
            "remaining_plays": cooldown_entry["remaining_plays"],
            "cooldown_until": cooldown_entry["cooldown_until"],
            "accumulated_bet": min_bet,
        }
        cooldown_entry = slot_cooldown_cache[nick]

    # Update cooldown cache
    if cooldown_entry["remaining_plays"] > 0:
        slot_cooldown_cache[nick]["remaining_plays"] -= 1
    else:
        # User increased bet, accept it
        if bet > cooldown_entry["accumulated_bet"] and wait_time > 0:
            slot_cooldown_cache[nick] = {
                "remaining_plays": attempts_per_cooldown - 1,
                "cooldown_until": cooldown_entry["cooldown_until"],
                "accumulated_bet": cooldown_entry["accumulated_bet"],
            }
        # User did not increase bet, apply new cooldown
        else:
            cooldown_time = round(
                cooldown_time_base * cooldown_time_multiplier * cooldown_entry["accumulated_bet"] / min_bet
            )
            slot_cooldown_cache[nick] = {
                "remaining_plays": attempts_per_cooldown,
                "cooldown_until": current_time + cooldown_time,
                "accumulated_bet": cooldown_entry["accumulated_bet"] * cooldown_bet_multiplier,
            }
            return f"⏳ You entered a cooldown! You can play again in {cooldown_time:.2f} seconds. Increase your bet to {slot_cooldown_cache[nick]['accumulated_bet']} beans to play now ⏳"

    cooldown_entry = slot_cooldown_cache[nick]
    # reply(f"{cooldown_entry['accumulated_bet']=}")
    # reply(f"{cooldown_entry['remaining_plays']=}")
    # reply(f"{cooldown_entry['cooldown_until']=}")
    is_cooldown = wait_time > 0

    if is_cooldown:
        bet_multiplier = max(min(bet / min_bet, (bet) / (cooldown_entry["accumulated_bet"])), 1)

    user_beans = get_beans(nick, db)
    if user_beans < bet:
        return f"You don't have enough beans to bet {bet}. You only have {user_beans} beans."

    max_prize = math.ceil(bet_multiplier * max_prize)
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
        return f"{result} JACKPOT! You won {max_prize:,} beans!" + (" ⏳" if is_cooldown else "")
    elif matches == 2:
        prize = math.ceil(bet_multiplier * (max_prize / 2))
        if not transfer_beans(conn.nick, nick, prize, db):
            return "The bot doesn't have enough beans to pay out your prize. Try again later!"
        return f"{result} You won {prize:,} beans!" + (" ⏳" if is_cooldown else "")
    elif matches == 1:
        return f"{result} Almost there! Keep trying! You lost {bet:,} beans."
    else:
        return f"{result} Better luck next time! You lost {bet:,} beans."


# Trivia functions
def add_trivia(creator: str, question: str, answer: str, prize: int, db) -> int:
    """Add a new trivia question and return its ID."""
    creator = creator.lower()
    query = trivia_table.insert().values(
        timestamp=datetime.now(), creator=creator, question=question, answer=answer, prize=prize
    )
    result = db.execute(query)
    db.commit()
    return result.inserted_primary_key[0]


def get_trivia(trivia_id: int, db):
    """Get a trivia question by ID."""
    query = select([trivia_table]).where(trivia_table.c.id == trivia_id)
    return db.execute(query).fetchone()


def get_trivia_by_answer(answer: str, db):
    """Get a trivia question by its answer."""
    answer = answer.lower()
    query = select([trivia_table]).where(trivia_table.c.answer == answer)
    return db.execute(query).fetchone()


def get_latest_user_trivia(creator: str, db):
    """Get the latest trivia question created by a user."""
    creator = creator.lower()
    query = (
        select([trivia_table])
        .where(trivia_table.c.creator == creator)
        .order_by(sqlalchemy.desc(trivia_table.c.timestamp))
        .limit(1)
    )
    return db.execute(query).fetchone()


def get_latest_trivias(limit: int, db):
    """Get the latest trivia questions."""
    query = select([trivia_table]).order_by(sqlalchemy.desc(trivia_table.c.timestamp)).limit(limit)
    return db.execute(query).fetchall()


def get_user_trivias(creator: str, db):
    """Get all trivia questions created by a user."""
    creator = creator.lower()
    query = (
        select([trivia_table])
        .where(trivia_table.c.creator == creator)
        .order_by(sqlalchemy.desc(trivia_table.c.timestamp))
    )
    return db.execute(query).fetchall()


def delete_trivia(trivia_id: int, db) -> bool:
    """Delete a trivia question by ID. Returns True if successful."""
    query = trivia_table.delete().where(trivia_table.c.id == trivia_id)
    result = db.execute(query)
    db.commit()
    return result.rowcount > 0


@hook.command("trivia")
def trivia_cmd(text: str, nick: str, db, conn) -> str | list[str]:
    """
    .trivia add <prize_amount> <question> -> <answer> - Add a new trivia question
    .trivia question [id] - Show a trivia question (latest by default)
    .trivia list - Show latest 3 trivia questions
    .trivia user <nick> - Show trivias from a user
    .trivia delete <id> - Delete your trivia question
    .trivia help - Show help information
    """
    if not text:
        return trivia_cmd("help", nick, db, conn)

    parts = text.strip().split(None, 1)
    subcmd = parts[0].lower()

    if subcmd == "help":
        return [
            "🎮 Trivia Commands 🎮",
            "> .trivia add <prize_amount> <question> -> <answer> - Add a new trivia question with prize",
            "> .trivia question [id] - Show a trivia question (your latest by default)",
            "> .trivia list - Show latest 3 trivia questions",
            "> .trivia user <nick> - Show trivias created by a user",
            "> .trivia delete <id> - Delete your trivia question and get refunded",
            "> .trivia help - Show this help information",
            "",
            "Notes:",
            "- The prize is paid in beans from your account to the bot",
            "- Answers must be a single alphanumeric word",
            "- Use -> to separate your question from the answer",
        ]

    if len(parts) < 2 and subcmd not in ["list", "help"]:
        return "❌ Missing arguments. Use '.trivia help' for usage information."

    if subcmd == "add":
        match = re.match(r"(\d+)\s+(.+?)\s+->\s+(\w+)$", parts[1])
        if not match:
            return "❌ Invalid format. Use: .trivia add <prize_amount> <question> -> <answer>"

        prize = int(match.group(1))
        question = match.group(2).strip()
        answer = match.group(3).strip()

        # Check if prize is positive
        if prize <= 0:
            return "❌ Prize must be a positive number of beans."

        # Check if answer is alphanumeric
        if not answer.isalnum():
            return "❌ Answer must contain only letters and numbers."

        # Check if user has enough beans
        user_beans = get_beans(nick, db)
        if user_beans < prize:
            return f"❌ You don't have enough beans. You have {user_beans}, but the prize is {prize}."

        # Transfer beans to the bot
        if not transfer_beans(nick, conn.nick, prize, db):
            return "❌ Failed to transfer beans. Please try again."

        # Add the trivia question
        trivia_id = add_trivia(nick, question, answer, prize, db)

        return f"✅ Trivia question #{trivia_id} added with a prize of 🫘 {prize} beans!"

    elif subcmd == "question":
        if len(parts) == 1:
            # Show latest question by the user
            trivia = get_latest_user_trivia(nick, db)
            if not trivia:
                return "❌ You haven't created any trivia questions yet."
        else:
            try:
                trivia_id = int(parts[1])
                trivia = get_trivia(trivia_id, db)
                if not trivia:
                    return f"❌ Trivia question #{trivia_id} not found."
            except ValueError:
                return "❌ Invalid trivia ID. Please provide a number."

        return [
            f"📝 Trivia #{trivia['id']} (created by {trivia['creator']})",
            f"Question: {trivia['question']}",
            f"Prize: 🫘 {trivia['prize']} beans",
        ]

    elif subcmd == "list":
        trivias = get_latest_trivias(3, db)
        if not trivias:
            return "❌ No trivia questions found."

        result = ["🎯 Latest Trivia Questions 🎯"]
        for t in trivias:
            result.append(f"#{t['id']}: \"{t['question']}\" - Prize: 🫘 {t['prize']} beans (by {t['creator']})")

        return result

    elif subcmd == "user":
        target = parts[1].strip()
        trivias = get_user_trivias(target, db)
        if not trivias:
            return f"❌ No trivia questions found for user {target}."

        result = [f"🧩 Trivia Questions by {target} 🧩"]
        for t in trivias:
            result.append(f"#{t['id']}: \"{t['question']}\" - Prize: 🫘 {t['prize']} beans")

        return result

    elif subcmd == "delete":
        try:
            trivia_id = int(parts[1])
            trivia = get_trivia(trivia_id, db)

            if not trivia:
                return f"❌ Trivia question #{trivia_id} not found."

            if trivia["creator"].lower() != nick.lower():
                return "❌ You can only delete your own trivia questions."

            # Check if the bot can pay back the prize
            bot_beans = get_beans(conn.nick, db)
            if bot_beans < trivia["prize"]:
                return "❌ The bot doesn't have enough beans to refund your prize. Try again later."

            # Transfer beans back to the creator
            if not transfer_beans(conn.nick, nick, trivia["prize"], db):
                return "❌ Failed to refund beans. Please try again later."

            # Delete the trivia question
            if delete_trivia(trivia_id, db):
                return f"✅ Trivia question #{trivia_id} deleted. You've been refunded 🫘 {trivia['prize']} beans."
            else:
                # If delete fails, we need to return the beans to the bot
                transfer_beans(nick, conn.nick, trivia["prize"], db)
                return "❌ Failed to delete trivia question. Please try again."

        except ValueError:
            return "❌ Invalid trivia ID. Please provide a number."

    else:
        return f"❌ Unknown subcommand: {subcmd}. Use '.trivia help' for usage information."


@hook.regex(re.compile(r"^\s*(\S+)\s*$", re.I))
def track_trivia_answers(match, event, db, conn) -> str | None:
    if event.type is EventType.action:
        return
    answer = match.group(1).strip()
    if not answer:
        return
    trivia = get_trivia_by_answer(answer, db)
    if not trivia:
        return

    # Transfer beans to the winner
    if not transfer_beans(conn.nick, event.nick, trivia["prize"], db):
        return "❌ The bot doesn't have enough beans to pay out the prize. Try again later!"

    # Delete the trivia question after answering
    delete_trivia(trivia["id"], db)
    return (
        f"🎉 {event.nick} answered correctly! The answer was '{trivia['answer']}'. "
        f"You won 🫘 {trivia['prize']} beans! 🎉"
    )
