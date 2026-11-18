# arma-server-discord-bot

This repository uses [Python-Discord-Bot-Template](https://github.com/kkrypt0nn/Python-Discord-Bot-Template/tree/main) created by [kkrypt0nn](https://github.com/kkrypt0nn) as a template, check out their repository for getting started and installation guides. This bot is built for the TAW AM2 Community with commands that connect to our web panel to stop/start servers.


## Mission uploads

MisDev and server-admin moderators (the configured `forum_moderators` roles) can
use `/mission_upload mission_pbo:<attachment>` to deploy a `.pbo` to AM2. This
does not submit a mission for review or change `/mission_submission`. Uploading
always replaces the same filename; filenames are lowercased to match the panel.

To upload an attachment already posted in Discord, right-click the message
(or long-press on mobile), then choose **Apps > Upload mission**. This uses the
same moderator restrictions and uploads to AM2 with game `arma3`. The private
result includes a link to the source message. The message must contain exactly
one `.pbo` attachment; for multiple PBOs, use `/mission_upload` with the desired
file. Other attachment types on the message are ignored. Reload the cog or
restart the bot and sync application commands to register the context menu.

Add `mission_folder_path` under `arma_server_web_admin.am2` in your
configuration (see [config.json.template](config.json.template)):

```json
"mission_folder_path": "S:\\Servers\\Arma3\\MPMissions"
```

Set `mission_folder_path` to the actual missions directory used by the panel's
`games.arma3.missionPath`. This is an example path, not an automatically
discovered location. If the directory exists on the bot host, the bot saves
there using atomic replacement, then requests a panel mission-list refresh.
If empty or missing, it uploads through the configured AM2 panel `address`.
A permissions/write error in an existing local directory is reported, not
silently retried through the panel.

The command defaults to game `arma3`; its optional `game` argument selects the
game ID for web-panel uploads. Local saves always use `mission_folder_path`,
so that folder must match the selected game.

The bot reuses `arma_server_web_admin.am2.public_username` and
`public_password`. That panel account needs `missions.create`
permission for uploads and `missions.view` for refreshes. The bot uses the
panel's `/api/session` cookie login, not individual `/server_login` credentials.
Use HTTPS unless the panel connection is on a trusted local network. Credentials
are unnecessary for a local save, but without them the bot warns that the panel
list could not be refreshed.

For remote replacement, deploy the companion panel update to
`lib/missions.js`: older panel versions reject an existing filename.

Run focused bot tests with `python -m unittest discover -s tests`.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE.md](LICENSE.md) file for details
