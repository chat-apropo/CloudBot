import re
from functools import lru_cache
from typing import Iterable, Mapping, Match, Optional, Union
from urllib.parse import quote

import isodate
import requests
from pyyoutube import Client
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util import colors, timeformat
from cloudbot.util.formatting import pluralize_suffix, truncate

youtube_re = re.compile(
    r"(?:youtube.*?(?:v=|/v/)|youtu\.be/|yooouuutuuube.*?id=)([-_a-zA-Z0-9]+)",
    re.I,
)
ytpl_re = re.compile(
    r"(.*:)//(www.youtube.com/playlist|youtube.com/playlist)(:[0-9]+)?(.*)",
    re.I,
)


base_url = "https://www.googleapis.com/youtube/v3/"


@lru_cache
def get_client() -> Client:
    api_key = bot.config.get("api_keys", {}).get("google", None)
    return Client(api_key=api_key)


def remove_tags(text):
    """Remove vtt markup tags."""
    tags = [
        r"</c>",
        r"<c(\.color\w+)?>",
        r"<\d{2}:\d{2}:\d{2}\.\d{3}>",
    ]

    for pat in tags:
        text = re.sub(pat, "", text)

    text = re.sub(
        r"(\d{2}:\d{2}):\d{2}\.\d{3} --> .* align:start position:0%",
        r"\g<1>",
        text,
    )
    # Remove HH:MM:.* lines completely
    text = re.sub(r"(\d{2}:\d{2}):\d{2}\.\d{3} --> ", "", text)
    text = re.sub(r"(\d{2}:\d{2}):\d{2}\.\d{3}", "", text)
    text = re.sub(r"^\s*\d{2}:\d{2}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s+$", "", text, flags=re.MULTILINE)
    return text


def remove_header(lines):
    """Remove vtt file header."""
    pos = -1
    for mark in (
        "##",
        "Language: en",
    ):
        if mark in lines:
            pos = lines.index(mark)
    lines = lines[pos + 1 :]
    return lines


def merge_duplicates(lines):
    """Remove duplicated subtitles.

    Duplacates are always adjacent.
    """
    last_timestamp = ""
    last_cap = ""
    for line in lines:
        if line == "":
            continue
        if re.match(r"^\d{2}:\d{2}$", line):
            if line != last_timestamp:
                yield line
                last_timestamp = line
        else:
            if line != last_cap:
                yield line
                last_cap = line


def merge_short_lines(lines):
    buffer = ""
    for line in lines:
        if line == "" or re.match(r"^\d{2}:\d{2}$", line):
            yield "\n" + line
            continue

        if len(line + buffer) < 80:
            buffer += " " + line
        else:
            yield buffer.strip()
            buffer = line
    yield buffer


def vtt2plantext(text: str) -> str:
    text = remove_tags(text)
    lines = text.splitlines()
    lines = remove_header(lines)
    lines = merge_duplicates(lines)
    lines = list(lines)
    lines = merge_short_lines(lines)
    lines = list(lines)
    return " ".join(lines)


def get_video_info(client: Client, video_url: str) -> "dict[str, str]":
    video_id = get_video_id(video_url)
    videos = client.videos.list(video_id=video_id)
    if videos is None or not videos.items:
        return {"title": "Title not available", "duration": "Duration not available", "transcript": ""}

    video = videos.items[0]
    video_info = {
        "title": video.snippet.title,
        "duration": video.contentDetails.duration,
        "transcript": "",
    }

    prefered_languages = ["en"]
    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            subtitles = transcripts.find_transcript(prefered_languages).fetch()
        except NoTranscriptFound:
            manual = transcripts._manually_created_transcripts
            generated = transcripts._generated_transcripts
            choice = manual or generated
            if choice:
                transcript = choice[list(choice.keys())[0]]
                subtitles = transcript.fetch()
            else:
                subtitles = None

    except TranscriptsDisabled:
        subtitles = None

    if subtitles:
        for part in subtitles:
            video_info["transcript"] += part["text"] + " "

    return video_info


def search_youtube_videos(client: Client, query: str, max_results: int = 10) -> "list[str]":
    video_urls = []
    search = client.search.list(q=quote(query), max_results=max_results)
    if search is None or not search.items:
        return []
    for item in search.items:
        if item.id.kind == "youtube#video":
            video_urls.append(f"https://www.youtube.com/watch?v={item.id.videoId}")

    return video_urls


class APIError(Exception):
    def __init__(self, message: str, response: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.response = response


class NoApiKeyError(APIError):
    def __init__(self) -> None:
        super().__init__("Missing API key")


class NoResultsError(APIError):
    def __init__(self) -> None:
        super().__init__("No results")


def raise_api_errors(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except requests.RequestException as e:
        try:
            data = response.json()
        except ValueError:
            raise e from None

        errors = data.get("errors")
        if not errors:
            errors = data.get("error", {}).get("errors")

        if not errors:
            return

        first_error = errors[0]
        domain = first_error["domain"]
        reason = first_error["reason"]
        raise APIError(f"API Error ({domain}/{reason})", data) from e


def make_short_url(video_id: str) -> str:
    return f"http://youtu.be/{video_id}"


ParamValues = Union[int, str]
ParamMap = Mapping[str, ParamValues]
Parts = Iterable[str]


def do_request(
    method: str,
    parts: Parts,
    params: Optional[ParamMap] = None,
    **kwargs: ParamValues,
) -> requests.Response:
    api_key = bot.config.get_api_key("google_dev_key")
    if not api_key:
        raise NoApiKeyError()

    if params:
        kwargs.update(params)

    kwargs["part"] = ",".join(parts)
    kwargs["key"] = api_key
    return requests.get(base_url + method, kwargs)


def get_video(video_id: str, parts: Parts) -> requests.Response:
    return do_request("videos", parts, params={"maxResults": 1, "id": video_id})


def get_playlist(playlist_id: str, parts: Parts) -> requests.Response:
    return do_request("playlists", parts, params={"maxResults": 1, "id": playlist_id})


def do_search(term: str, result_type: str = "video") -> requests.Response:
    return do_request(
        "search",
        ["snippet"],
        params={"maxResults": 1, "q": term, "type": result_type},
    )


def get_video_description(video_id: str) -> str:
    parts = ["statistics", "contentDetails", "snippet"]
    request = get_video(video_id, parts)
    raise_api_errors(request)

    json = request.json()

    data = json["items"]
    if not data:
        raise NoResultsError()

    item = data[0]
    snippet = item["snippet"]
    statistics = item["statistics"]
    content_details = item["contentDetails"]

    out = "\x02{}\x02".format(snippet["title"])

    if not content_details.get("duration"):
        return out

    length = isodate.parse_duration(content_details["duration"])
    out += " - length \x02{}\x02".format(timeformat.format_time(int(length.total_seconds()), simple=True))
    try:
        total_votes = float(statistics["likeCount"]) + float(statistics["dislikeCount"])
    except (LookupError, ValueError):
        total_votes = 0

    if total_votes != 0:
        # format
        likes = pluralize_suffix(int(statistics["likeCount"]), "like")
        dislikes = pluralize_suffix(int(statistics["dislikeCount"]), "dislike")

        percent = 100 * float(statistics["likeCount"]) / total_votes
        out += f" - {likes}, {dislikes} (\x02{percent:.1f}\x02%)"

    if "viewCount" in statistics:
        views = int(statistics["viewCount"])
        out += " - \x02{:,}\x02 view{}".format(views, "s"[views == 1 :])

    uploader = snippet["channelTitle"]

    upload_time = isodate.parse_datetime(snippet["publishedAt"])
    out += " - \x02{}\x02 on \x02{}\x02".format(uploader, upload_time.strftime("%Y.%m.%d"))

    try:
        yt_rating = content_details["contentRating"]["ytRating"]
    except KeyError:
        pass
    else:
        if yt_rating == "ytAgeRestricted":
            out += colors.parse(" - $(red)NSFW$(reset)")

    return out


def get_video_id(text: str) -> str:
    try:
        request = do_search(text)
    except requests.RequestException as e:
        raise APIError("Unable to connect to API") from e

    raise_api_errors(request)
    json = request.json()

    if not json.get("items"):
        raise NoResultsError()

    video_id = json["items"][0]["id"]["videoId"]  # type: str
    return video_id


@hook.regex(youtube_re)
def youtube_url(match: Match[str]) -> str:
    client = get_client()
    result = get_video_info(client, match.group(1))
    time = timeformat.format_time(int(isodate.parse_duration(result["duration"]).total_seconds()), simple=True)
    return truncate(
        f"\x02{result['title']}\x02, \x02duration:\x02 {time} - {result['transcript']}",
        420,
    )


user_results = {}


@hook.command("ytn")
def youtube_next(text: str, nick: str, reply) -> str:
    global user_results
    client = get_client()
    url = user_results[nick].pop(0)
    result = get_video_info(client, url)
    time = timeformat.format_time(int(isodate.parse_duration(result["duration"]).total_seconds()), simple=True)
    return truncate(
        f"{url}  -  \x02{result['title']}\x02, \x02duration:\x02 {time} - {result['transcript']}",
        420,
    )


@hook.command("youtube", "you", "yt", "y")
def youtube(text: str, nick: str, reply) -> str:
    """<query> - Returns the first YouTube search result for <query>.

    :param text: User input
    """
    global user_results
    client = get_client()
    results = search_youtube_videos(client, text)
    user_results[nick] = results
    return youtube_next(text, nick, reply)


@hook.command("youtime", "ytime")
def youtime(text: str, reply) -> str:
    """<query> - Gets the total run time of the first YouTube search result for <query>."""
    parts = ["statistics", "contentDetails", "snippet"]
    try:
        video_id = get_video_id(text)
        request = get_video(video_id, parts)
        raise_api_errors(request)
    except NoResultsError as e:
        return e.message
    except APIError as e:
        reply(e.message)
        raise

    json = request.json()

    data = json["items"]
    item = data[0]
    snippet = item["snippet"]
    content_details = item["contentDetails"]
    statistics = item["statistics"]

    duration = content_details.get("duration")
    if not duration:
        return "Missing duration in API response"

    length = isodate.parse_duration(duration)
    l_sec = int(length.total_seconds())
    views = int(statistics["viewCount"])
    total = int(l_sec * views)

    length_text = timeformat.format_time(l_sec, simple=True)
    total_text = timeformat.format_time(total, accuracy=8)

    return (
        "The video \x02{}\x02 has a length of {} and has been viewed {:,} times for "
        "a total run time of {}!".format(snippet["title"], length_text, views, total_text)
    )


@hook.regex(ytpl_re)
def ytplaylist_url(match: Match[str]) -> str:
    location = match.group(4).split("=")[-1]
    request = get_playlist(location, ["contentDetails", "snippet"])
    raise_api_errors(request)

    json = request.json()

    data = json["items"]
    if not data:
        raise NoResultsError()

    item = data[0]
    snippet = item["snippet"]
    content_details = item["contentDetails"]

    title = snippet["title"]
    author = snippet["channelTitle"]
    num_videos = int(content_details["itemCount"])
    count_videos = " - \x02{:,}\x02 video{}".format(num_videos, "s"[num_videos == 1 :])
    return f"\x02{title}\x02 {count_videos} - \x02{author}\x02"
