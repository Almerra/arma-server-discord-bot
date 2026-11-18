# arma-server-discord-bot

This repository uses [Python-Discord-Bot-Template](https://github.com/kkrypt0nn/Python-Discord-Bot-Template/tree/main) created by [kkrypt0nn](https://github.com/kkrypt0nn) as a template, check out their repository for getting started and installation guides. This bot is built for the TAW AM2 Community with commands that connect to our web panel to stop/start servers.


## Battalion configuration

Discord-wide settings such as `guild_id` and `invite_link` live under `discord`.
Battalion-specific roles and offices live under `discord.am1`,
`discord.am2`, `discord.am3` or `discord.am4`.

The five office sections are `server_admin_office`, `recruitment_headquarters`,
`combat_training_centre`, `field_specialist_office` and `mission_development`.
Office settings belong to their battalion, not to the Discord root.
The cogs use [shared battalion utilities](utils/battalions.py) to discover
configured battalions and offer their names as autocomplete choices. A battalion
is a `discord` object with a `name` or office section; keys are not hard-coded.
Commands offer only battalions configured for their office/panel/content.
An explicit `battalion` option, consistently named across the cogs,
selects the destination. Otherwise the configured office/forum/queue identifies
the battalion, or the sole eligible battalion is used. Ambiguous choices ask
the user to select instead of silently choosing a default. Context-menu uploads
use the source message's channel. Existing per-battalion permission checks
still apply; selecting a battalion does not grant access.

CTC retains its existing request/panel and staff-role routing. Set
`discord.default_battalion` for legacy CTC rows/panels; the local config keeps
the existing destination. That setting is not an implicit upload/server target.
The mission exporter also accepts `--battalion` and uses the configured default.

TAW `/info` and `/form` read each battalion's `info` and `form` objects, containing
Discord embed dictionaries (`embeds`) and, for forms, `buttons` (`label`, `url`).
`info.heading` supplies its heading. `/signup` reads that battalion's `operations`
list (`label`, ISO `weekday` 1–7, `hour`, `minute`, Europe/London time).
The division-wide `/donate` and social commands remain division-wide. Existing
TAW content has been moved into the local config, including modpack links.

After changing command options, restart/reload the bot and run the owner's
`sync guild` command for guild-scoped commands or `sync global` for global
commands, using the configured prefix. If guild copies were previously synced,
sync that guild too: older guild commands can shadow updated global commands.
Restarting alone does not publish command schema changes to Discord.
Battalion `name` sits beside its office sections. CTC options,
including `site_url`, `catalogue` and `manager_ids`, belong inside each battalion's CTC
section; there is no shared CTC block or nested `units` block. AM1 and AM2's
existing CTC settings have been migrated, with their unit keys preserved.

When updating an existing deployment, move the five top-level `discord` office
sections under `discord.am2` before restarting. Preserve their contents; if an
AM2 office already exists, merge it explicitly rather than overwriting different
settings. See [config.json.template](config.json.template) for the new layout.

## Mission feedback notifications

`/mission_played` explicitly allows mentions of the configured
selected battalion's `roles.members` roles on its feedback message. The roles must be
mentionable, or the bot must have **Mention @everyone, @here, and All Roles**
permission in the channel. Members need access to the thread; their personal
notification settings can still suppress push notifications.

## Mission uploads

MisDev and server-admin moderators (the configured `forum_moderators` roles) can
use `/mission_upload mission_pbo:<attachment> battalion:<key>` to deploy a `.pbo`. This
does not submit a mission for review or change `/mission_submission`. Uploading
always replaces the same filename; filenames are lowercased to match the panel.

To upload an attachment already posted in Discord, right-click the message
(or long-press on mobile), then choose **Apps > Upload mission**. This uses the
same moderator restrictions and infers the battalion from the message's channel,
using game `arma3`. If ambiguous, use the slash command to select a battalion. The private
result includes a link to the source message. The message must contain exactly
one `.pbo` attachment; for multiple PBOs, use `/mission_upload` with the desired
file. Other attachment types on the message are ignored. Reload the cog or
restart the bot and sync application commands to register the context menu.

Set `mission_path` under `arma_server.<battalion>` in your
configuration (see [config.json.template](config.json.template)):

```json
"mission_path": "S:\\Servers\\Arma3\\MPMissions"
```

Set `mission_path` to the actual missions directory used by the panel's
`games.arma3.missionPath`. This is an example path, not an automatically
discovered location. If the directory exists on the bot host, the bot saves
there using atomic replacement without contacting the panel.
If the setting is empty or missing, or the directory does not exist locally, it
uploads through the selected battalion's panel `address`.
A permissions/write error in an existing local directory is reported, not
silently retried through the panel.

Uploads use game `arma3`. Local saves use `arma_server.<battalion>.mission_path`,
so that folder must match the game's missions directory.

The bot reuses `arma_server_web_admin.<battalion>.public_username` and
`public_password`. That panel account needs `missions.create`
permission for web uploads. The bot uses the
panel's `/api/session` cookie login, not individual `/server_login` credentials.
Use HTTPS unless the panel connection is on a trusted local network. Credentials
and a panel connection are unnecessary for a local save. Direct local saves do
not refresh the panel's cached mission list.

For remote replacement, deploy the companion panel update to
`lib/missions.js`: older panel versions reject an existing filename.

Run focused bot tests with `python -m unittest discover -s tests`.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE.md](LICENSE.md) file for details
