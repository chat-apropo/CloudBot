import copy
import importlib
import re
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, List, Literal

import pywikibot
import requests

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util import formatting
from plugins.huggingface import FileIrcResponseWrapper
from plugins.wikis import WIKI_APIS, search

API_URL = "https://g4f.cloud.mattf.one/api/completions"
MAX_SUMMARIZE_MESSAGES = 1000
AGI_HISTORY_LENGTH = 50
MAX_USER_HISTORY_LENGTH = 32
RoleType = Literal["user", "assistant"]

WIKI = ("wikih4ks", "wh")


def patch_input(wiki_password: str):
    def mock_input(question, password=False, default="", force=False):
        if password:
            return wiki_password
        from pywikibot import input as original_input

        return original_input(question, password=password, default=default, force=force)

    pywikibot.input = mock_input


@hook.on_start()
def on_start():
    wiki_password = bot.config.get_api_key("wiki_password")
    if not wiki_password:
        return
    patch_input(wiki_password)


def detect_code_blocks(markdown_text: str) -> list[str]:
    code_block_pattern = re.compile(r"```\S*(.*)```", re.DOTALL)
    return code_block_pattern.findall(markdown_text)


@dataclass
class Message:
    role: RoleType
    content: str
    timestamp: float = datetime.timestamp(datetime.now())

    def as_dict(self):
        return {
            "role": self.role,
            "content": self.content,
        }


def get_completion(messages: List[Message]) -> str:
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
    }

    json_data = {
        "messages": [message.as_dict() for message in messages],
    }

    response = requests.post(API_URL, headers=headers, json=json_data)
    response.raise_for_status()
    return response.json()["completion"]


def upload_responses(nick: str, messages: List[Message], header: str) -> str:
    bar = "-" * 80
    lb = "\n"
    text_contents = (
        header
        + "\n" * 4
        + f"{lb}{bar}{lb*2}".join(
            f"{nick if message.role == 'user' else 'bot'}: {message.content}" for message in messages
        )
    )
    with tempfile.NamedTemporaryFile(suffix=".txt") as f:
        with open(f.name, "wb") as file:
            file.write(text_contents.encode("utf-8"))
        image_url = FileIrcResponseWrapper.upload_file(f.name, "st")
    return image_url


gpt_messages_cache: dict[tuple[str, str], Deque[Message]] = {}


@hook.command("gpt")
def gpt_command(text: str, nick: str, chan: str) -> str:
    """<text> - Get a response from text generating LLM."""
    global gpt_messages_cache

    channick = (chan, nick)
    if channick not in gpt_messages_cache:
        gpt_messages_cache[channick] = deque(maxlen=MAX_USER_HISTORY_LENGTH)

    gpt_messages_cache[channick].append(Message(role="user", content=text))
    try:
        response = get_completion(list(gpt_messages_cache[channick]))
    except requests.HTTPError as e:
        return f"Error: {e}"
    gpt_messages_cache[channick].append(Message(role="assistant", content=response))
    truncated = formatting.truncate_str(response, 350)
    if len(truncated) < len(response):
        paste_url = upload_responses(
            nick,
            list(gpt_messages_cache[channick]),
            f"{nick}'s GPT conversation in {chan}",
        )
        return f"{truncated} (full response: {paste_url})"
    return truncated


def create_web_app(text: str, history: list[Message] | Deque[Message]) -> str:
    history.append(
        Message(
            role="user",
            content=text
            + "\nMake sure to put everything in a single html file so it can be a single code block meant to be"
            " directly used in a browser as it is. Do not explain, just show the code.",
        )
    )
    try:
        response = get_completion(list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"

    history.append(Message(role="assistant", content=response))
    # Match on multi line markdown block '````'
    code_blocks = detect_code_blocks(response)
    if not code_blocks:
        return "No code block found in the response. Try .gptclear or see what happened with .gptpaste."

    with tempfile.NamedTemporaryFile(suffix=".html") as f:
        with open(f.name, "wb") as file:
            file.write(code_blocks[0].encode("utf-8").strip())
        html_url = FileIrcResponseWrapper.upload_file(f.name, "st")
        paste_url = html_url.removesuffix(".html") + "/p"
        return f"{paste_url}. Try online: {html_url}"


@hook.command("gptweb", "gptapp")
def gpt_app(text: str, nick: str, chan: str) -> str:
    """<text> - Create a single page html web app on the fly with gpt"""
    global gpt_messages_cache

    channick = (chan, nick)
    if channick not in gpt_messages_cache:
        gpt_messages_cache[channick] = deque(maxlen=MAX_USER_HISTORY_LENGTH)

    return create_web_app(text, gpt_messages_cache[channick])


@hook.command("agiweb", "agiapp")
def agi_app(conn, text: str, nick: str, chan: str) -> str:
    """<text> - Create a single page html web app on the fly with gpt"""
    messages = generate_agi_history(conn, chan)
    return create_web_app(text, messages)


@hook.command("gpth", "gpthistory", "gptpaste", autohelp=False)
def gpt_paste_command(nick: str, chan: str, text: str) -> str:
    """[nick] - Pastes the GPT conversation history with nick if specified."""
    global gpt_messages_cache

    text = text.strip()
    if text:
        nick = text

    channick = (chan, nick)
    if channick in gpt_messages_cache:
        return upload_responses(
            nick,
            list(gpt_messages_cache[channick]),
            f"{nick}'s GPT conversation in {chan}",
        )
    return f"No conversation history for {nick}. Start a conversation with .gpt."


@hook.command("gptclear", autohelp=False)
def gpt_clear_command(nick: str, chan: str) -> str:
    """Clear the conversation cache."""
    global gpt_messages_cache

    channick = (chan, nick)
    if channick in gpt_messages_cache:
        gpt_messages_cache.pop(channick)
        return "Conversation cache cleared."
    return "No conversation cache to clear."


last_summary = ""


def summarize(
    messages: List[str],
    image: bool,
    nick: str,
    chan: str,
    bot,
    reply,
    what: str = "conversation",
    max_words: int | None = None,
) -> str | List[str] | None:
    global last_summary
    if image:
        question_header = (
            f"Please convert the following {what} into an image prompt for a text generating model with only the main"
            " keywords separated by comma: \n```"
        )
    else:
        question_header = f"Please summarize the following {what}: \n```"

    summarize_body = question_header + "\n".join(messages) + "\n```"

    if max_words:
        summarize_body += f"Use at most {max_words} words."

    try:
        response = get_completion([Message(role="user", content=summarize_body)])
    except requests.HTTPError as e:
        return f"Error: {e}"

    if image:
        from plugins.huggingface import (
            ALIASES,
            HuggingFaceClient,
            attempt_inference,
            process_response,
        )

        api_key = bot.config.get_api_key("huggingface")
        if not api_key:
            return "error: missing api key for huggingface"

        client = HuggingFaceClient([api_key])
        response = attempt_inference(client, summarize_body, ALIASES["image"].id, reply)
        if isinstance(response, str):
            return formatting.truncate(response, 420)
        return formatting.truncate(process_response(response, chan, nick), 420)
    else:
        # Output at most 3 messages
        last_summary = response
        output = formatting.chunk_str(response.replace("\n", " - "))
        if len(output) > 3:
            paste_url = upload_responses(
                nick,
                [Message(role="assistant", content=response)],
                f"{nick}'s GPT summary in {chan}",
            )
            output[2] = formatting.truncate(output[2], 350) + " (full response: " + paste_url + ")"
            return output[:3]
        return output


@hook.command("summarize", "summary", autohelp=False)
def summarize_command(bot, reply, text: str, chan: str, nick: str, conn) -> str | List[str] | None:
    """Summarizes the contents of the last chat messages. Optionally pass a number for max words and nicks to summarize. Sorry yeah if your nick is a number fuck you"""
    image = False
    worcount = None
    if text.strip().lower() == "image":
        image = True

    args = text.split()
    worcount = None
    nicks = []
    for arg in args:
        if arg.strip().lower().isdigit():
            worcount = int(arg)
        else:
            nicks.append(arg.casefold())

    inner = []
    i = 0
    for name, _timestamp, msg in reversed(conn.history[chan]):
        if nicks and name.casefold() not in nicks:
            continue

        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {}: {}"
        else:
            mod_msg = msg
            fmt = "{}: {}"
        inner.append(fmt.format(name, mod_msg))
        i += 1
        if i >= MAX_SUMMARIZE_MESSAGES:
            break

    messages = list(reversed(inner))
    if not messages:
        reply("Nothing found in history to summarize")
        return
    return summarize(messages, image, nick, chan, bot, reply, max_words=worcount)


@hook.command("sumsum", "sumsummarize", "sumsummary", autohelp=False)
def sumsum(bot, text: str, reply, nick: str, chan: str, conn) -> str | List[str] | None:
    """Summarizes the last summary"""
    global last_summary
    if not last_summary:
        return "No summary to summarize."
    return summarize([last_summary], False, nick, chan, bot, reply, what="text even more")


agi_messages_cache: Deque[tuple[float, str]] = deque(maxlen=AGI_HISTORY_LENGTH)


def generate_agi_history(conn, chan: str) -> list[Message]:
    global agi_messages_cache
    prefix = conn.config["command_prefix"]

    inner: list[tuple[RoleType, float, str]] = []
    i = 0
    for name, timestamp, msg in reversed(conn.history[chan]):
        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {}: {}"
        else:
            mod_msg = msg
            fmt = "{}: {}"
        # Skip bot commands
        if mod_msg.startswith(prefix):
            continue
        inner.append(("user", timestamp, fmt.format(name, mod_msg)))
        i += 1
        if i >= AGI_HISTORY_LENGTH:
            break

    inner.extend(("assistant", timestamp, msg) for timestamp, msg in agi_messages_cache)
    sorted_messages = sorted(inner, key=lambda x: x[1])
    messages = copy.deepcopy(sorted_messages)

    # We remove the bot's message from the begining of the history so that it doesn't think it started the conversation
    # on it's own
    for msg in sorted_messages:
        role = msg[0]
        if role == "assistant":
            messages = messages[1:]
            agi_messages_cache.popleft()
        else:
            break

    messages.insert(
        0,
        (
            "user",
            -1,
            (
                "You are watching a conversation between multiple users in a chatroom and they can interact with you"
                " through the .agi command."
            ),
        ),
    )
    return [Message(role=role, content=text) for role, _, text in messages]


@hook.command("agi", "sentient", autohelp=False)
def gpts_command(reply, text: str, nick: str, chan: str, conn) -> str | List[str] | None:
    """<text> - Get a response from text generating LLM that is aware of the conversation."""
    # Same logic as .summarize but with the last 30 messages and the user's message
    messages = generate_agi_history(conn, chan)
    try:
        response = get_completion(messages)
    except requests.HTTPError as e:
        return f"Error: {e}"

    # Output at most 3 messages
    output = formatting.chunk_str(response.replace("\n", " - "))
    for message in output:
        agi_messages_cache.append((datetime.timestamp(datetime.now()), message))
    if len(output) > 3:
        paste_url = upload_responses(
            nick,
            [Message(role="assistant", content=response)],
            f"GPT conversation in {chan}",
        )
        output[2] = formatting.truncate(output[2], 350) + " (full response: " + paste_url + ")"
        return output[:3]
    return output


@hook.command("agipaste", autohelp=False)
def agi_paste_command(nick: str, conn, chan: str) -> str:
    """Pastes the AGI context window."""
    messages = generate_agi_history(conn, chan)
    return upload_responses("", messages, f"AGI conversation in {chan}")


@hook.command("gpredict", "gptpredict", "gptpred", "predict", autohelp=False)
def gpredict_command(bot, reply, text: str, chan: str, nick: str, conn) -> str | List[str] | None:
    """<nick> - Predict what the given user might say next based on their chat history."""
    if not text.strip():
        return "Error: You must provide a nick to predict."

    if len(text.split()) > 1:
        return "Error: Only one nick can be provided."

    target_nick = text.strip().casefold()
    messages = []
    was_user_in_history = False
    for name, _timestamp, msg in reversed(conn.history[chan]):
        # Skip bot commands
        if msg.startswith("."):
            continue
        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
        else:
            mod_msg = msg

        if name.casefold() == target_nick:
            messages.append(Message(role="assistant", content=mod_msg))
            was_user_in_history = True
        else:
            messages.append(Message(role="user", content=f"{name} said: {mod_msg}"))

        if len(messages) >= AGI_HISTORY_LENGTH:
            break

    if not was_user_in_history or not messages:
        return f"No chat history found for {text.strip()}."

    messages.reverse()  # Ensure messages are in chronological order
    messages.insert(
        0,
        Message(
            role="user",
            content=(
                "You are in a conversation with multiple people in a chat. Try to behave relaxed, casual and in"
                " character like another user."
            ),
        ),
    )
    messages.append(
        Message(
            role="user",
            content=f"Continue the conversation responding as {target_nick}. Make sure to stay in character.",
        )
    )
    try:
        response = get_completion(messages)
    except requests.HTTPError as e:
        return f"Error: {e}"

    return f"<{target_nick}> {formatting.truncate_str(response, 350)}"


def edit_wiki(bot, reply, chan: str, nick: str, prompt: str, history: Deque[Message] | list[Message]) -> str:
    user = bot.config.get_api_key("wiki_username")
    history.append(
        Message(
            role="user",
            content=prompt
            + "\nOutput the result as a mediawiki code block meant for a wiki page. Make sure to use mediawiki markup"
            " or html tags that mediawiki supports in the code block.",
        )
    )
    try:
        response = get_completion(list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"

    code_blocks = detect_code_blocks(response)
    if not code_blocks:
        return "No code block found in the response. Try .gptclear or see what happened with .gptpaste."

    wiki_text = code_blocks[0].strip()
    if not wiki_text:
        return "Error: No text found in the response."

    # Extract the title from the wiki text as the first line without surrounding "=="
    match = re.search(r"^=+\s*(.*?)\s*=+", wiki_text, re.MULTILINE)
    if match:
        title = match.group(1).strip()
    else:
        return "Error: No title found in the response."

    site = pywikibot.Site(url=WIKI_APIS[WIKI], user=user)
    page = pywikibot.Page(site, title)

    if page.exists():
        reply(f"Editing page at {page.full_url()} ...")
        # Refrese the prompt to avoid the bot thinking it is a new page, give it the old page as a context
        history[-1].content = (
            f"Please edit the following page content:\n```mediawiki\n{page.text}\n```\n\n{wiki_text}\n"
            + history[-1].content
        )
        try:
            response = get_completion(list(history))
        except requests.HTTPError as e:
            return f"Error: {e}"

        history.append(Message(role="assistant", content=response))
        code_blocks = detect_code_blocks(response)
        if not code_blocks:
            return "No code block found in the response. Try .gptclear or see what happened with .gptpaste."

        wiki_text = code_blocks[0].strip()
        if not wiki_text:
            return "Error: No text found in the response."
    else:
        reply(f"Creating page {page.full_url()} ...")
        history.append(Message(role="assistant", content=response))

    error = "Unknown error"
    for _ in range(3):
        page.text = wiki_text
        try:
            page.save("Edited by GPT bot from irc")
            break
        except Exception as e:
            # Reload module to get the new password
            importlib.reload(pywikibot)
            patch_input(bot.config.get_api_key("wiki_password"))
            site = pywikibot.Site(url=WIKI_APIS[WIKI], user=user)
            page = pywikibot.Page(site, title)
            error = str(e)
    else:
        return f"Error: {error}"

    return search(WIKI, title, chan, nick)


@hook.command("gptwiki", autohelp=False)
def gptwiki(bot, reply, text: str, chan: str, nick: str, conn) -> list[str] | str:
    """<text> - Create or edit a wiki page on demand from AI prompt"""
    global gpt_messages_cache
    channick = (chan, nick)
    if channick not in gpt_messages_cache:
        gpt_messages_cache[channick] = deque(maxlen=MAX_USER_HISTORY_LENGTH)

    return edit_wiki(bot, reply, chan, nick, text, gpt_messages_cache[channick])


@hook.command("agiwiki", autohelp=False)
def agiwiki(bot, reply, text: str, chan: str, nick: str, conn) -> list[str] | str:
    """<text> - Create or edit a wiki page on demand from AI prompt"""
    messages = generate_agi_history(conn, chan)
    return edit_wiki(bot, reply, chan, nick, text, messages)


@hook.command("vibeadd", "vibecreate", autohelp=False)
def vibe(text: str, chan: str, nick: str) -> str:
    """<name> <prompt> - Vibe create a new game"""
    if not text.strip():
        return "Usage: .vibeadd <name> <prompt>"

    if len(text.split()) < 2:
        return "Usage: .vibeadd <name> <prompt>"

    api_key = bot.config.get_api_key("vibegames_api_key")
    api_url = bot.config.get_api_key("vibegames_api_url")

    name, prompt = text.split(maxsplit=1)
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    json_data = {
        "content": prompt,
    }

    response = requests.post(f"{api_url}/api/ai/{name}", headers=headers, json=json_data)
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        return f"Error: {e} - {response.text}"
    response_json = response.json()
    if response_json["status"] != "success":
        return f"Error: {response_json['message']} - {response.text}"
    return f"Created {name} at {api_url}{response_json['html_path']} with prompt: {prompt}"


@hook.command("vibeedit", autohelp=False)
def vibe_edit(text: str, chan: str, nick: str) -> str:
    """<name> <prompt> - Vibe edit a game"""
    if not text.strip():
        return "Usage: .vibeedit <name> <prompt>"

    if len(text.split()) < 2:
        return "Usage: .vibeedit <name> <prompt>"

    api_key = bot.config.get_api_key("vibegames_api_key")
    api_url = bot.config.get_api_key("vibegames_api_url")

    name, prompt = text.split(maxsplit=1)
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    json_data = {
        "content": prompt,
    }

    response = requests.put(f"{api_url}/api/ai/{name}", headers=headers, json=json_data)
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        return f"Error: {e} - {response.text}"
    response_json = response.json()
    if response_json["status"] != "success":
        return f"Error: {response_json['message']} - {response.text}"
    return f"Edited {name} at {api_url}{response_json['html_path']} with prompt: {prompt}"
