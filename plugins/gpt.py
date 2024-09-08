import tempfile
from collections import deque
from dataclasses import dataclass
from typing import List, Literal

import requests

from cloudbot import hook
from cloudbot.util import formatting

API_URL = "https://g4f.cloud.mattf.one/api/completions"


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str

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


def upload_responses(nick: str, messages: List[Message]) -> str:
    header = f"{nick} conversation".upper()
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


gpt_messages_cache = {}


@hook.command("gpt")
def gpt_command(text: str, nick: str, chan: str) -> str:
    """<text> - Get a response from text generating LLM."""
    global gpt_messages_cache

    channick = frozenset((chan, nick))
    if channick not in gpt_messages_cache:
        gpt_messages_cache[channick] = deque(maxlen=16)

    gpt_messages_cache[channick].append(Message(role="user", content=text))
    response = get_completion(list(gpt_messages_cache[channick]))
    gpt_messages_cache[channick].append(Message(role="assistant", content=response))
    truncated = formatting.truncate_str(response, 350)
    if len(truncated) < len(response):
        paste_url = upload_responses(nick, list(gpt_messages_cache[channick]))
        return f"{truncated} (full response: {paste_url})"
    return truncated


@hook.command("gptclear", autohelp=False)
def gpt_clear_command(nick: str, chan: str) -> str:
    """Clear the conversation cache."""
    global gpt_messages_cache

    channick = frozenset((chan, nick))
    if channick in gpt_messages_cache:
        gpt_messages_cache.pop(channick)
        return "Conversation cache cleared."
    return "No conversation cache to clear."


@hook.command("summarize", "axs", autohelp=False)
def summarize_command(bot, reply, text: str, chan: str, nick: str, conn) -> str | List[str] | None:
    """Summarizes the contents of the last chat messages"""
    api_key = bot.config.get_api_key("huggingface")
    if not api_key:
        return "error: missing api key for huggingface"

    MAX_MESSAGES = 1000
    if text.strip().lower() == "image":
        messages = [
            "Please convert the following conversation into an image prompt for a text generating model with only the main keywords separated by comma: \n```"
        ]
        image = True
    else:
        image = False
        messages = ["Please summarize the following conversation: \n```"]
    inner = []
    i = 0
    for nick, _timestamp, msg in reversed(conn.history[chan]):
        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {}: {}"
        else:
            mod_msg = msg
            fmt = "<{}>: {}"
        inner.append(fmt.format(nick, mod_msg))
        i += 1
        if i >= MAX_MESSAGES:
            break

    messages.extend(reversed(inner))
    summarize_body = "\n".join(messages)
    summarize_body += "```"

    response = get_completion([Message(role="user", content=summarize_body)])

    if image:
        from plugins.huggingface import (
            ALIASES,
            HuggingFaceClient,
            attempt_inference,
            process_response,
        )

        client = HuggingFaceClient([api_key])
        response = attempt_inference(client, summarize_body, ALIASES["image"].id, reply)
        if isinstance(response, str):
            return formatting.truncate(response, 420)
        return formatting.truncate(process_response(response, chan, nick), 420)
    else:
        # Output at most 3 messages
        output = formatting.chunk_str(response.replace("\n", " - "))[0:3]
        return output
