import json
import mimetypes
import random
import string
from dataclasses import dataclass, fields
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from typing import List, Optional, Tuple
from urllib.parse import quote

import magic
import requests

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.queue import Queue

INFERENCE_API = "https://api-inference.huggingface.co/models/{model}"
BASE_API = "https://huggingface.co/api/"

ALIASES = {
    "image": "stabilityai/stable-diffusion-xl-base-1.0",
    "anime": "cagliostrolab/animagine-xl-3.1",
    "pixel": "nerijs/pixel-art-xl",
    "icon": "kopyl/ui-icons-256",
    "music": "facebook/musicgen-small",
    "gpt": "openai-community/gpt2",
    "sentiment": "cardiffnlp/twitter-roberta-base-sentiment-latest",
    "speak": "facebook/mms-tts-eng",
}


def filter_unexpected_fields(cls):
    original_init = cls.__init__

    def new_init(self, *args, **kwargs):
        expected_fields = {field.name for field in fields(cls)}
        cleaned_kwargs = {key: value for key,
                          value in kwargs.items() if key in expected_fields}
        original_init(self, *args, **cleaned_kwargs)

    cls.__init__ = new_init
    return cls


@filter_unexpected_fields
@dataclass
class ModelInfo:
    _id: str
    createdAt: str
    downloads: int
    id: str
    likes: int
    modelId: str
    private: bool
    tags: List[str]
    library_name: Optional[str] = None
    pipeline_tag: Optional[str] = None

    @property
    def api_url(self):
        return f"{BASE_API}models/{self.id}"

    @property
    def app_url(self):
        return f"https://huggingface.co/{self.modelId}"

    @property
    def created_at(self):
        return datetime.strptime(self.createdAt, '%Y-%m-%dT%H:%M:%S.%fZ')

    def __str__(self):
        """IRC friendly string representation of the model info."""
        bold = "\x02"
        italic = "\x1d"
        return (
            f"{bold}{self.modelId}{bold} - ⬇️ {self.downloads} -👍 {self.likes} - 🏷️ "
            f"{formatting.truncate(', '.join([italic + t + italic for t in self.tags]), 200)} - 🕒 {self.created_at} - {self.app_url}"
        )


def upload_file(file) -> str:
    with open(file, 'rb') as f:
        data = f.read()

    headers = {
        "filename": Path(file).name,
        "bin": "cloudbot".replace("#", "--"),
    }
    response = requests.post('https://filebin.net/', data=data, headers=headers)
    response.raise_for_status()
    report = response.json()
    bin = report["bin"]["id"]
    file = report["file"]["filename"]
    return f"https://filebin.net/{bin}/{file}"


class IrcResponseWrapper:
    content_type = ["text/plain"]

    def __init__(self, response: requests.Response):
        response.raise_for_status()
        self.response = response

    def as_text(self) -> List[str]:
        return [self.response.text]


class JsonIrcResponseWrapper(IrcResponseWrapper):
    content_type = ["application/json"]

    def as_text(self) -> List[str]:
        try:
            obj = json.loads(self.response.content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return super().as_text()

        if isinstance(obj, list) and "generated_text" in obj[0]:
            output = [" - ".join(r["generated_text"] for r in obj)]
        else:
            output = [""] + formatting.json_format(obj)

        return output# + [json.dumps(obj)]


class FileIrcResponseWrapper(IrcResponseWrapper):
    content_type = ["application/octet-stream"]

    def as_text(self) -> List[str]:
        with TemporaryDirectory() as temp_dir:
            content_disposition = self.response.headers.get("Content-Disposition")
            filename = None
            if content_disposition:
                filename = content_disposition.split("filename=")[1].strip('"')

            if not filename:
                mime = magic.from_buffer(self.response.content, mime=True)
                extension = mimetypes.guess_extension(mime)
                random_filename = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))
                filename = f"{random_filename}{extension}"

            file_path = f"{temp_dir}/{filename or 'file.jpeg'}"
            with open(file_path, "wb") as file:
                file.write(self.response.content)
            try:
                url = upload_file(file_path)
            except requests.exceptions.HTTPError as e:
                return [f"error: {e} - {e.response.text}", file_path]

        return [url]


class ImageIrcResponseWrapper(FileIrcResponseWrapper):
    content_type = ["image/png", "image/jpeg", "image/gif", "image/webp"]


class AudioIrcResponseWrapper(FileIrcResponseWrapper):
    content_type = ["audio/mpeg", "audio/ogg", "audio/wav", "audio/flac"]


class VideoIrcResponseWrapper(FileIrcResponseWrapper):
    content_type = ["video/mp4", "video/webm"]


def irc_response_builder(response: requests.Response) -> IrcResponseWrapper:
    def all_subclasses(cls):
        return set(cls.__subclasses__()).union(
            [s for c in cls.__subclasses__() for s in all_subclasses(c)])

    content_type_list = response.headers.get("Content-Type", "").split(";")
    reponse_wrappers_list = all_subclasses(IrcResponseWrapper)
    for content_type in content_type_list:
        for wrapper in reponse_wrappers_list:
            if content_type.lower() in wrapper.content_type:
                return wrapper(response)
    return IrcResponseWrapper(response)


class HuggingFaceClient:
    def __init__(self, api_tokens: "list[str]"):
        self.api_tokens = iter(api_tokens)
        self.session = requests.Session()
        self.refresh_headers()

    def refresh_headers(self) -> None:
        self.session.headers.update(
            {"Authorization": f"Bearer {self.next_token()}"}
        )

    def next_token(self) -> str:
        return next(self.api_tokens)

    def _send(self, payload: dict, model: str) -> requests.Response:
        data = json.dumps(payload)
        response = self.session.request(
            "POST", INFERENCE_API.format(model=model), data=data
        )
        return response

    def send(self, text: str, model: str) -> requests.Response:
        inputs = {"inputs": text}
        return self._send(inputs, model)

    def search_model(self, query: str) -> List[ModelInfo]:
        query = quote(query)
        response = self.session.get(
            BASE_API + f"models?search={query}"
        )
        response.raise_for_status()
        return [ModelInfo(
            **model
        ) for model in response.json()]

    @staticmethod
    def check_loading_model(response: dict) -> Optional[Tuple[str, int]]:
        if (
            "estimated_time" in response
            and "error" in response
            and "currently loading" in response["error"]
        ):
            estimated_time = int(response["estimated_time"])
            if estimated_time < 120 and estimated_time > 0:
                return(
                    f"⏳ Model is currently loading. I will retry in a few minutes and give your response. Please don't spam. Estimated time: {estimated_time} seconds.",
                    estimated_time
                )
            else:
                return(
                    f"⏳ Model is currently loading and will take some minutes. Try again later. Estimated time: {estimated_time // 60} minutes.",
                    estimated_time
                )
        return None


@lru_cache
def get_queue():
    return Queue()


current_queue = Queue()


@hook.command("huggingface_next", "hfn", autohelp=False)
def hfn(text: str, chan: str, nick: str):
    """[nick] - gets the next result from the last hugingface search"""
    global current_queue
    args = text.strip().split()
    if len(args) > 0:
        nick = args[0]

    results_queue = get_queue()
    results = results_queue[chan][nick]
    if len(results) == 0:
        return "No [more] results found for " + nick

    try:
        current_queue[chan][nick] = [results.pop() for _ in range(3)]
    except IndexError:
        return "No results found for " + nick
    return [f"{i+1})  {str(c)}" for i, c in enumerate(current_queue[chan][nick])]


@hook.command("huggingface", "hf")
def hf(bot, text: str, chan: str, nick: str):
    """<query> - searches for a model on huggingface"""
    api_key = bot.config.get_api_key("huggingface")
    if not api_key:
        return "error: missing api key for huggingface"

    text = text.strip()
    client = HuggingFaceClient([api_key])
    queue = get_queue()
    queue[chan][nick] = client.search_model(text)
    return hfn("", chan, nick)


def _hfi(bot, reply, text: str, chan: str, nick: str, is_retry=False):
    global current_queue
    api_key = bot.config.get_api_key("huggingface")
    if not api_key:
        return "error: missing api key for huggingface"

    client = HuggingFaceClient([api_key])
    try:
        model, text = text.split(maxsplit=1)
    except ValueError:
        return "Usage: .hfi <model> <text>"

    if model.isdigit() and int(model) > 0:
        results = current_queue[chan][nick]
        if len(results) == 0:
            return f"Cannot pick model {model} because you haven't searched for anything or you've already seen all the results. Try .hf <query> or .hf first"
        if len(results) < int(model):
            return f"Cannot pick model {model} because there are only {len(results)} results"
        model = results[int(model) - 1].modelId

    try:
        response = client.send(text, model)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        check = None
        try:
            check = HuggingFaceClient.check_loading_model(e.response.json())
        except json.JSONDecodeError:
            pass
        if check is not None and not is_retry:
            reply(check[0])
            sleep(check[1])
            return _hfi(bot, reply, model + " " + text, chan, nick, is_retry=True)
        return f"error: {e} - {e.response.text}"

    try:
        output = irc_response_builder(response).as_text()
    except requests.exceptions.HTTPError as e:
        return f"error: {e} - {e.response.text}"

    return output


@hook.command("hfinference", "hfi")
def hfi(bot, reply, text: str, chan: str, nick: str):
    """<model> <text> - sends text to the model for inference. Model can be the number of a result from the last search or actual model id"""
    return _hfi(bot, reply, text, chan, nick)


@hook.command("hfalias", "hfa")
def hfa(bot, reply, text: str, chan: str, nick: str):
    """<alias> <text> - sends text to the aliased model for inference. Similar to .hfi. Use .hfa list to see available aliases"""
    if text.strip() == "list":
        return ["The following aliases are available: "] + formatting.json_format(ALIASES)

    if len(text.strip().split()) < 2:
        return "Usage: .hfa <alias> <text>"

    alias, query = text.strip().split(maxsplit=1)
    if alias == "list":
        return ["The following aliases are available: "] + formatting.json_format(ALIASES)

    if alias not in ALIASES:
        return "error: alias not found. Use .hfa list to see available aliases"

    text = ALIASES[alias] + " " + query
    return _hfi(bot, reply, text, chan, nick)
