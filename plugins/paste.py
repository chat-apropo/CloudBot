import os

from cloudbot import hook


@hook.command("filebin", "pastebin", autohelp=False)
def pastebin(nick: str, chan: str) -> str:
    """Clear the conversation cache."""
    filebin = os.environ.get("FILEBIN_URL")
    if filebin is None:
        return "No FILEBIN_URL set."

    return f"Upload files or paste code at: {filebin}"
