import copy
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, List, Literal

import requests

from cloudbot import hook
from cloudbot.util import formatting
from plugins.huggingface import FileIrcResponseWrapper

API_URL = "https://g4f.cloud.mattf.one/api/completions"
MAX_SUMMARIZE_MESSAGES = 1000
AGI_HISTORY_LENGTH = 50
RoleType = Literal["user", "assistant"]


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
        gpt_messages_cache[channick] = deque(maxlen=16)

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


@hook.command("gptweb", "gptapp")
def gpt_app(text: str, nick: str, chan: str) -> str:
    """<text> - Create a single page html web app on the fly with gpt"""
    global gpt_messages_cache

    channick = (chan, nick)
    if channick not in gpt_messages_cache:
        gpt_messages_cache[channick] = deque(maxlen=16)

    gpt_messages_cache[channick].append(
        Message(
            role="user",
            content=text
            + "\nMake sure to put everything in a single html file so it can be a single code block meant to be directly used in a browser as it is. Do not explain, just show the code.",
        )
    )
    try:
        response = get_completion(list(gpt_messages_cache[channick]))
    except requests.HTTPError as e:
        return f"Error: {e}"

    gpt_messages_cache[channick].append(Message(role="assistant", content=response))
    # Match on multi line markdown block '````'
    code = ""
    start = False
    for line in response.splitlines():
        if "```" in line:
            start = not start
            if not start:
                break
            continue
        if "<html>" in line:
            start = True
        if "</html>" in line:
            start = False
        if start:
            code += line + "\n"

    if not code:
        return "No code block found in the response. Try .gptclear or see what happened with .gptpaste."

    with tempfile.NamedTemporaryFile(suffix=".html") as f:
        with open(f.name, "wb") as file:
            file.write(code.encode("utf-8"))
        html_url = FileIrcResponseWrapper.upload_file(f.name, "st")
        paste_url = html_url.removesuffix(".html") + "/p"
        return f"{paste_url}. Try online: {html_url}"


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
        question_header = f"Please convert the following {what} into an image prompt for a text generating model with only the main keywords separated by comma: \n```"
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

    inner: list[tuple[RoleType, float, str]] = []
    i = 0
    for name, timestamp, msg in reversed(conn.history[chan]):
        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {}: {}"
        else:
            mod_msg = msg
            fmt = "{}: {}"
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
            "You are watching a conversation between multiple users in a chatroom and they can interact with you through the .agi command.",
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
