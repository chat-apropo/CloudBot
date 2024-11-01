from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError

from cloudbot import hook
from cloudbot.util import formatting

headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7,de;q=0.6",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "priority": "u=0, i",
    "sec-ch-ua": '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
}


@hook.command("bible", "passage", singlethread=True)
def bible(text, reply):
    """<passage> - Prints the specified passage from the Bible"""
    passage = text.strip()
    params = {"passage": passage, "formatting": "plain", "type": "json"}
    try:
        r = requests.get(
            "https://labs.bible.org/api/",
            params=params,
            headers=headers,
        )
        r.raise_for_status()
        response = r.json()[0]
    except HTTPError as e:
        reply(
            formatting.truncate(
                "Something went wrong, either you entered an invalid passage or the API is down",
                400,
            )
        )
        raise e

    book = response["bookname"]
    ch = response["chapter"]
    ver = response["verse"]
    txt = response["text"]
    return f"\x02{book} {ch}:{ver}\x02 {txt}"
