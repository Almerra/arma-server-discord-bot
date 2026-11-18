import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import tempfile

import aiohttp
import discord


class MissionUploadError(Exception):
    pass


@dataclass(frozen=True)
class MissionUploadResult:
    filename: str
    local: bool


def mission_filename(filename: str) -> str:
    if (
        not filename.lower().endswith(".pbo")
        or len(filename) > 255
        or len(filename) <= 4
        or filename.startswith((".", " "))
        or re.search(r'[<>:"/\\|?*%\x00-\x1f]', filename)
        or filename.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
    ):
        raise MissionUploadError("Please attach a .pbo file with a valid, plain mission filename.")
    return filename.lower()


def _local_directory(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        raise MissionUploadError("The configured local missions path is not a directory.")
    return True


async def _save_local(attachment: discord.Attachment, directory: Path, filename: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".mission-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            await attachment.save(file.file)
        await asyncio.to_thread(os.replace, temporary, directory / filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def _panel_transfer(
    attachment: discord.Attachment,
    filename: str,
    *,
    address: str,
    game: str,
    username: str,
    password: str,
) -> None:
    if not address or not username or not password:
        raise MissionUploadError("The mission upload panel address or bot-account credentials are not configured.")
    if not game:
        raise MissionUploadError("The mission upload panel game is not configured.")
    base = address.rstrip("/")
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(
        timeout=timeout, cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as session:
        async with session.post(
            f"{base}/api/session",
            json={"username": username, "password": password},
            headers={"X-Requested-With": "XMLHttpRequest"},
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise MissionUploadError(f"Panel login failed (HTTP {response.status}). Check the bot account.")
            body = await response.json()
            if not isinstance(body, dict) or body.get("success") is not True:
                raise MissionUploadError("The panel did not confirm a successful login.")

        with tempfile.TemporaryDirectory(prefix="mission-upload-") as directory, open(
            Path(directory) / filename, "w+b"
        ) as file:
            await attachment.save(file)
            file.seek(0)
            data = aiohttp.FormData()
            data.add_field("game", game)
            data.add_field("missions", file, filename=filename, content_type="application/octet-stream")
            async with session.post(
                f"{base}/api/missions", data=data, allow_redirects=False
            ) as response:
                if response.status != 200:
                    raise MissionUploadError(
                        f"Panel mission upload failed (HTTP {response.status}). "
                        "Check the bot account's missions.create permission and panel logs."
                    )
                body = await response.json()
                if not isinstance(body, dict) or body.get("success") is not True:
                    raise MissionUploadError("The panel did not confirm a successful mission upload.")


async def upload_mission(
    attachment: discord.Attachment,
    *,
    address: str,
    local_path: str = "",
    game: str = "arma3",
    username: str = "",
    password: str = "",
) -> MissionUploadResult:
    filename = mission_filename(attachment.filename)
    local = bool(local_path) and await asyncio.to_thread(_local_directory, Path(local_path))
    if local:
        await _save_local(attachment, Path(local_path), filename)
    else:
        await _panel_transfer(
            attachment, filename, address=address, game=game,
            username=username, password=password,
        )
    return MissionUploadResult(filename, local)
