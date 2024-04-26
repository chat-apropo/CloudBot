import re
from time import sleep


from cloudbot import hook
from plugins.huggingface import HuggingFaceClient

LANG_MODEL_MAP = {
    "en": "vennify/t5-base-grammar-correction",
    "de": "MRNH/mbart-german-grammar-corrector",
}


def grammar(text, bot, reply, lang="en", retry=True):
    api_key = bot.config.get_api_key("huggingface")
    if not api_key:
        return "error: missing api key for huggingface"

    if lang not in LANG_MODEL_MAP:
        return f"error: language '{lang}' not supported"

    model = LANG_MODEL_MAP[lang]

    text = text.strip()

    client = HuggingFaceClient([api_key])
    response = client.send(text, model)
    if (
        "estimated_time" in response
        and "error" in response
        and "currently loading" in response["error"]
        and retry
    ):
        estimated_time = int(response["estimated_time"])
        if estimated_time < 120 and estimated_time > 0:
            reply(
                f"⏳ Model is currently loading. I will retry in a few minutes and give your response. Please don't spam. Estimated time: {estimated_time} seconds."
            )
            sleep(estimated_time)
            return grammar(text, bot, reply, lang, retry=False)
        else:
            reply(
                f"⏳ Model is currently loading and will take some minutes. Try again later. Estimated time: {estimated_time} seconds."
            )
            return

    if "error" in response:
        return response["error"]

    def proccess_response(resp: str) -> str:
        resp = resp.strip()
        # Replace " ." in endings with "."
        resp = re.sub(r"\s+\.$", ".", resp)
        return resp.strip()

    generated_text = {proccess_response(r["generated_text"]) for r in response}
    if text.strip() in generated_text:
        return "✅ Perfect grammar! No changes needed."

    return " - ".join(generated_text)


@hook.command("grammar", "grammaren")
def grammar_command(text, message, bot, reply):
    return grammar(text, bot, reply, lang="en")


@hook.command("grammarde", "grammatik")
def grammar_command2(text, message, bot, reply):
    return grammar(text, bot, reply, lang="de")
