from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands
from discord.ext.commands import Context


OFFICES = (
    "server_admin_office", "recruitment_headquarters", "combat_training_centre",
    "field_specialist_office", "mission_development",
)


@dataclass(frozen=True)
class Battalion:
    key: str
    settings: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.settings.get("name") or self.key.upper())


class BattalionSelectionError(ValueError):
    pass


def battalions(
    config: dict[str, Any], *, office: str | None = None,
    panel: bool = False, feature: str | None = None,
) -> list[Battalion]:
    result = []
    for key, settings in config.get("discord", {}).items():
        if not isinstance(settings, dict) or not (
            "name" in settings or any(name in settings for name in OFFICES)
        ):
            continue
        if office and office not in settings:
            continue
        if feature and feature not in settings:
            continue
        if panel and key not in config.get("arma_server_web_admin", {}):
            continue
        result.append(Battalion(key, settings))
    return result


def battalion_choices(units: list[Battalion], current: str) -> list[app_commands.Choice[str]]:
    needle = current.casefold()
    return [
        app_commands.Choice(name=unit.name[:100], value=unit.key)
        for unit in units
        if needle in unit.key.casefold() or needle in unit.name.casefold()
    ][:25]


def battalion_autocomplete(*, office: str | None = None, panel: bool = False, feature: str | None = None):
    async def autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return battalion_choices(
            battalions(interaction.client.config, office=office, panel=panel, feature=feature), current
        )
    return autocomplete


def select_battalion(
    config: dict[str, Any], channel, chosen: str | None = None, *,
    office: str | None = None, panel: bool = False, feature: str | None = None,
) -> Battalion:
    eligible = battalions(config, office=office, panel=panel, feature=feature)
    if chosen:
        matches = [u for u in eligible if chosen.casefold() in (u.key.casefold(), u.name.casefold())]
        if len(matches) == 1:
            return matches[0]
        raise BattalionSelectionError(f'Battalion "{chosen}" is not configured for this command.')

    channel_ids = {
        value for value in (getattr(channel, "id", None), getattr(channel, "parent_id", None))
        if isinstance(value, int)
    }
    matches = []
    for unit in battalions(config):
        offices = [unit.settings[name] for name in OFFICES if name in unit.settings]
        if any(not isinstance(settings, dict) for settings in offices):
            raise BattalionSelectionError(f"{unit.name} has an invalid office configuration.")
        ids = {
            int(value) for settings in offices for name, value in settings.items()
            if name in ("office_channel_id", "forum_channel_id", "suggestion_channel_id", "queue_channel_id")
            and value
        }
        if channel_ids & ids:
            matches.append(unit)
    if len(matches) == 1:
        if matches[0] in eligible:
            return matches[0]
        raise BattalionSelectionError(f"{matches[0].name} is not configured for this command.")
    if not matches and len(eligible) == 1:
        return eligible[0]
    if not eligible:
        raise BattalionSelectionError("No battalions are configured for this command.")
    options = ", ".join(f"{u.name} (`{u.key}`)" for u in eligible)
    raise BattalionSelectionError(
        f"Please select a battalion using the command's `battalion` option: {options}. "
        "For message actions, use the battalion's configured forum or use the slash command."
    )


async def resolve_battalion(
    target: discord.Interaction | Context, chosen: str | None = None, *,
    office: str | None = None, panel: bool = False, feature: str | None = None, channel=None,
) -> Battalion | None:
    bot = target.bot if isinstance(target, Context) else target.client
    guild = target.guild
    if guild is None or guild.id != bot.config["discord"]["guild_id"]:
        message = "This command has not been configured for this server."
    else:
        try:
            return select_battalion(
                bot.config, channel if channel is not None else target.channel, chosen,
                office=office, panel=panel, feature=feature,
            )
        except BattalionSelectionError as error:
            message = str(error)
    if isinstance(target, Context):
        await target.send(message, ephemeral=True)
    else:
        await target.response.send_message(message, ephemeral=True)
    return None
