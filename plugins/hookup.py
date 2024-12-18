import json
import random
import time
from typing import Any, Dict

from sqlalchemy import and_, select

from cloudbot import hook
from cloudbot.util.database import metadata
from cloudbot.util.textgen import TextGenerator
from plugins.history import seen_table

hookups: Dict[str, Any] = {}


@hook.on_start()
def load_data(bot):
    hookups.clear()
    with open((bot.data_path / "hookup.json"), encoding="utf-8") as f:
        hookups.update(json.load(f))


@hook.command(autohelp=False)
def hookup(db, chan):
    """- matches two users from the channel in a sultry scene."""
    if seen_table.name not in metadata.tables:
        return None

    times = time.time() - 86400
    results = db.execute(
        select(
            [seen_table.c.name],
            and_(seen_table.c.chan == chan, seen_table.c.time > times),
        ).order_by(seen_table.c.time)
    ).fetchall()

    if not results or len(results) < 2:
        return "something went wrong"

    # Make sure the list of people is unique
    people = sorted({row[0] for row in results})
    random.shuffle(people)
    person1, person2 = people[:2]
    variables = {
        "user1": person1,
        "user2": person2,
    }

    generator = TextGenerator(hookups["templates"], hookups["parts"], variables=variables)

    return generator.generate_string()
