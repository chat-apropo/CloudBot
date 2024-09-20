import tempfile
from collections import deque
from dataclasses import dataclass
from typing import List, Literal
import itertools

from datetime import datetime
import time

import requests

from cloudbot import hook
from cloudbot.util import formatting
from plugins.huggingface import FileIrcResponseWrapper

API_URL = "https://g4f.cloud.mattf.one/api/completions"
MAX_SUMMARIZE_MESSAGES = 1000


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
        paste_url = upload_responses(nick, list(gpt_messages_cache[channick]), f"{nick}'s GPT conversation in {chan}")
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


def summarize(
    messages: List[str],
    image: bool,
    nick: str,
    chan: str,
    bot,
    reply,
    what: str = "conversation",
) -> str | List[str] | None:
    if image:
        question_header = f"Please convert the following {what} into an image prompt for a text generating model with only the main keywords separated by comma: \n```"
    else:
        question_header = f"Please summarize the following {what}: \n```"

    summarize_body = question_header + "\n".join(messages) + "\n```"

    response = get_completion([Message(role="user", content=summarize_body)])

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
        output = formatting.chunk_str(response.replace("\n", " - "))
        if len(output) > 3:
            paste_url = upload_responses(
                nick, [Message(role="assistant", content=response)], f"{nick}'s GPT summary in {chan}"
            )
            output[2] = formatting.truncate(output[2], 350) + " (full response: " + paste_url + ")"
            return output[:3]
        return output


@hook.command("summarize", "summary", autohelp=False)
def summarize_command(bot, reply, text: str, chan: str, nick: str, conn) -> str | List[str] | None:
    """Summarizes the contents of the last chat messages"""
    image = False
    if text.strip().lower() == "image":
        image = True

    inner = []
    i = 0
    for name, _timestamp, msg in reversed(conn.history[chan]):
        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {}: {}"
        else:
            mod_msg = msg
            fmt = "<{}>: {}"
        inner.append(fmt.format(name, mod_msg))
        i += 1
        if i >= MAX_SUMMARIZE_MESSAGES:
            break

    messages = list(reversed(inner))
    return summarize(messages, image, nick, chan, bot, reply)


agi_messages_cache = []
@hook.command("agi", "sentient", autohelp=False)
def gpts_command(reply, text: str, nick: str, chan: str, conn) -> str | List[str] | None:
    """<text> - Get a response from text generating LLM that is aware of the conversation."""
    # Same logic as .summarize but with the last 30 messages and the user's message
    global agi_messages_cache

    history = list(itertools.islice(conn.history[chan], 30))
    for message in agi_messages_cache:
        history.append(message)
    messages = sorted(history, key=lambda message: message[1])[:30]
    print(messages)

    inner = []

    for name, _timestamp, msg in messages:
        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {}: {}"
        else:
            mod_msg = msg
            fmt = "<{}>: {}"
        inner.append(fmt.format(name, mod_msg))

    messages = list(reversed(inner))

    lb = "\n"
    body = f"""
    Given the following IRC conversation:
    ```
    {lb.join(messages)}
    ```

    Briefly and casually answer the following, as a participant of said conversation that has been on it for a while, nicknamed agi:
    {text}
    """
    response = get_completion([Message(role="user", content=body)])

    # Output at most 3 messages
    output = formatting.chunk_str(response.replace("\n", " - "))
    for message in output:
        agi_messages_cache.append(('agi', datetime.timestamp(datetime.now()), message))
    if len(output) > 3:
        paste_url = upload_responses(nick, [Message(role="assistant", content=response)], f"GPT conversation in {chan}")
        output[2] = formatting.truncate(output[2], 350) + " (full response: " + paste_url + ")"
        return output[:3]
    return output
