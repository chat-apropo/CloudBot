import tempfile
from collections import deque
from dataclasses import dataclass
from typing import List, Literal

import requests

from cloudbot import hook
from cloudbot.util import formatting
from plugins.huggingface import FileIrcResponseWrapper

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
    text_contents = header + "\n"*4 + f"{lb}{bar}{lb*2}".join(f"{nick if message.role == 'user' else 'bot'}: {message.content}" for message in messages)
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
