import os

from cloudbot import hook
from cloudbot.bot import bot

api_key = bot.config.get_api_key("openwheater")

try:
    os.environ['API_KEY'] = api_key
    from pyweather import curweath
except Exception as e:
    raise Exception("Error: missing api key for openweather") from e


@hook.command("we")
def weater(text):
    x = curweath.by_cname(text)
    if 'message' in x:
        return x['message']
    return f"{x.name} (\x02Country\x02: {x.sys.country}, \x02lat\x02: {x.coord.lat}, \x02long\x02: {x.coord.lon}) -- \x02{x.weather[0].description}\x02 {round(x.main.temp-273.15) }Cº \x02min\x02: {round(x.main.temp_min-273.15)}Cº \x02max\x02: {round(x.main.temp_max-273.15)}Cº \x02sensation\x02: {round(x.main.feels_like-273.15)}Cº \x02humidity\x02: {x.main.humidity}%"
