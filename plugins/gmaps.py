import shlex
from datetime import datetime, timedelta

import googlemaps
import pytz

from cloudbot import hook
from cloudbot.util.formatting import html_to_irc

MAX_HOURLY_REQUESTS = 30
MAX_OUTPUT_LINES = 10

# "driving", "walking", "bicycling" or "transit"

modes_map = {
    "driving": "driving",
    "car": "driving",
    "drive": "driving",
    "walking": "walking",
    "walk": "walking",
    "bicycling": "bicycling",
    "bike": "bicycling",
    "transit": "transit",
    "public": "transit",
    "bus": "transit",
    "train": "transit",
}
modes = ", ".join(f'"{m}"' for m in set(modes_map.values()))

emoji_map = {
    "driving": "🚗",
    "walking": "🚶",
    "bicycling": "🚲",
    "transit": "🚆",
}

last_hour_usages = []


@hook.command("gd", "gmd", "directions", autohelp=False)
def directions(text, event, reply, bot, nick, chan):
    """<type> <origin> to <destination> - Get directions from Google Maps"""
    global last_hour_usage

    now = datetime.now(pytz.timezone("UTC"))

    for i, usage in enumerate(last_hour_usages):
        if usage < now - timedelta(hours=1):
            last_hour_usages.pop(i)
        else:
            break

    if len(last_hour_usages) >= MAX_HOURLY_REQUESTS:
        return "Too many requests. Please try again later."

    api_key = bot.config.get("api_keys", {}).get("google", None)
    try:
        gmaps = googlemaps.Client(key=api_key)
    except ValueError:
        return "No or wrong Google API key configured."

    text = text.strip()
    if not text:
        return "Usage: gd [mode] <origin> to <destination>"

    usage_msg = "Usage: gd [mode] <origin> to <destination>"

    args = shlex.split(text)
    if '"' in text:
        if len(args) == 4:
            mode = args[0].lower()
            if mode not in modes_map:
                return f"Unknown mode: '{mode}'. Possible modes: {modes}"
            mode = modes_map[mode]
            points = " ".join(args[1:]).split(" to ", 1)
        elif len(args) == 3:
            mode = None
            points = text.split(" to ")
        else:
            return usage_msg
    else:
        args = text.split(" ")[0]
        mode = args.lower().strip()
        if mode in modes_map:
            mode = modes_map[mode]
            points = " ".join(args).strip().split(" to ", 1)
        else:
            mode = None
            points = text.split(" to ", 1)

    if len(points) != 2:
        return usage_msg

    reply(f"🔍 Searching directions from '{points[0]}' to '{points[1]}' using '{mode or 'all'}'")
    last_hour_usages.append(now)

    try:
        directions = gmaps.directions(
            points[0],
            points[1],
            mode=mode,
            departure_time=now,
            alternatives=True,
        )
    except googlemaps.exceptions.ApiError as e:
        return f"Error: {e}"

    if not directions:
        return "No results found."

    best_route = directions[0]

    i = 0
    for leg in best_route["legs"]:
        duration = leg["duration"]["text"]
        distance = leg["distance"]["text"]
        from_ = leg["start_address"]
        to = leg["end_address"]
        reply(f"📍 \x02{from_}  -  {to} ({distance}, {duration})")
        for step in leg["steps"]:
            # Only limit in channels
            if i >= MAX_OUTPUT_LINES and chan.startswith("#"):
                reply(f"🔽 More steps: {len(leg['steps']) - i}")
                break
            mode = step["travel_mode"].lower()
            emoji = emoji_map.get(mode, "🚶")
            distance = step.get("distance", {}).get("text", "")
            msg = f"    {emoji} {distance}: {html_to_irc(step['html_instructions'])}"
            if mode == "transit":
                arrival_time = step.get("transit_details", {}).get("arrival_time", {}).get("text", "")
                headsign = step.get("transit_details", {}).get("headsign", "")
                num_stops = step.get("transit_details", {}).get("num_stops", "")
                msg += f" - {headsign} ({arrival_time}) - {num_stops} stops"
            reply(msg)

            i += 1

        if i >= MAX_OUTPUT_LINES:
            break
