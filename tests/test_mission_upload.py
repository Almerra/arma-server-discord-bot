import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
import discord

from discord.ext import commands

from cogs.misdev import MisdevOffice
from arma_server_web_admin import ArmaServerWebAdmin
from utils.mission_upload import (
    MissionUploadError, MissionUploadResult, mission_filename, upload_mission,
)


def attachment(filename="Operation.Altis.pbo", content=b"mission"):
    mission = SimpleNamespace(filename=filename, read=AsyncMock(return_value=content))

    async def save(file):
        return await discord.Attachment.save(mission, file)

    mission.save = AsyncMock(side_effect=save)
    return mission


class MissionUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.uploads = []
        self.logins = 0
        self.login_status = 200
        self.upload_status = 200
        self.upload_body = {"success": True}

        async def login(request):
            self.logins += 1
            self.assertEqual(request.headers.get("X-Requested-With"), "XMLHttpRequest")
            self.assertEqual(await request.json(), {"username": "bot", "password": "test-password"})
            response = web.json_response({"success": True}, status=self.login_status)
            response.set_cookie("connect.sid", "test-session")
            return response

        async def upload(request):
            self.assertEqual(request.cookies.get("connect.sid"), "test-session")
            reader = await request.multipart()
            game = await reader.next()
            self.assertEqual(game.name, "game")
            self.assertEqual(await game.text(), "arma3")
            mission = await reader.next()
            self.assertEqual(mission.name, "missions")
            self.uploads.append((mission.filename, bytes(await mission.read())))
            return web.json_response(self.upload_body, status=self.upload_status)

        app = web.Application()
        app.router.add_post("/api/session", login)
        app.router.add_post("/api/missions", upload)
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.options = {
            "address": str(self.server.make_url("/")),
            "username": "bot",
            "password": "test-password",
        }

    async def test_local_replaces_without_contacting_panel(self):
        target = self.path / "operation.altis.pbo"
        target.write_bytes(b"old")
        with patch("utils.mission_upload.aiohttp.ClientSession") as session:
            result = await upload_mission(attachment(), local_path=str(self.path), **self.options)
        session.assert_not_called()
        self.assertEqual(result, MissionUploadResult("operation.altis.pbo", True))
        self.assertEqual(target.read_bytes(), b"mission")
        self.assertEqual(self.logins, 0)
        self.assertEqual(self.uploads, [])
        self.assertEqual(list(self.path.iterdir()), [target])

    async def test_missing_local_directory_uses_cookie_authenticated_multipart(self):
        missing = self.path / "missing"
        result = await upload_mission(attachment(), local_path=str(missing), **self.options)
        self.assertFalse(result.local)
        self.assertFalse(missing.exists())
        self.assertEqual(self.uploads, [("operation.altis.pbo", b"mission")])

    async def test_no_local_path_uses_panel(self):
        result = await upload_mission(attachment(), **self.options)
        self.assertFalse(result.local)
        self.assertEqual(len(self.uploads), 1)

    async def test_failed_download_preserves_existing_file_and_cleans_stage(self):
        target = self.path / "operation.altis.pbo"
        target.write_bytes(b"old")
        mission = attachment()

        async def fail(file):
            file.write(b"partial")
            raise OSError("download interrupted")

        mission.save.side_effect = fail
        with self.assertRaises(OSError):
            await upload_mission(mission, local_path=str(self.path), **self.options)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(list(self.path.iterdir()), [target])
        self.assertEqual(self.uploads, [])

    async def test_local_write_error_does_not_fall_back_to_panel(self):
        with patch("utils.mission_upload.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                await upload_mission(attachment(), local_path=str(self.path), **self.options)
        self.assertEqual(list(self.path.iterdir()), [])
        self.assertEqual(self.uploads, [])

    async def test_configured_file_is_not_treated_as_missing_directory(self):
        target = self.path / "file"
        target.touch()
        with self.assertRaisesRegex(MissionUploadError, "not a directory"):
            await upload_mission(attachment(), local_path=str(target), **self.options)
        self.assertEqual(self.uploads, [])

    async def test_local_save_needs_no_panel_configuration(self):
        with patch("utils.mission_upload._panel_transfer", new_callable=AsyncMock) as transfer:
            result = await upload_mission(attachment(), address="", local_path=str(self.path))
        transfer.assert_not_awaited()
        self.assertEqual(result, MissionUploadResult("operation.altis.pbo", True))
        self.assertTrue((self.path / result.filename).exists())

    async def test_remote_requires_credentials_before_downloading(self):
        mission = attachment()
        with self.assertRaisesRegex(MissionUploadError, "not configured"):
            await upload_mission(mission, address=self.options["address"])
        mission.save.assert_not_awaited()

    async def test_login_failure_does_not_download_or_upload(self):
        self.login_status = 401
        mission = attachment()
        with self.assertRaisesRegex(MissionUploadError, "HTTP 401"):
            await upload_mission(mission, **self.options)
        mission.save.assert_not_awaited()
        self.assertEqual(self.uploads, [])

    async def test_upload_permission_failure_is_reported(self):
        self.upload_status = 403
        with self.assertRaisesRegex(MissionUploadError, "HTTP 403"):
            await upload_mission(attachment(), **self.options)

    async def test_success_shaped_http_response_requires_confirmation(self):
        for body in ({"success": False}, [], {}):
            self.upload_body = body
            with self.subTest(body=body):
                with self.assertRaisesRegex(MissionUploadError, "did not confirm"):
                    await upload_mission(attachment(), **self.options)

    def test_invalid_filenames(self):
        for name in (
            "../escape.pbo", r"..\escape.pbo", "bad%2fpbo.pbo", "not.txt",
            "CON.Altis.pbo", ".pbo", "bad:pbo.pbo", "bad\x00.pbo",
        ):
            with self.subTest(name=name):
                with self.assertRaises(MissionUploadError):
                    mission_filename(name)
        self.assertEqual(mission_filename("Operation.Altis.PBO"), "operation.altis.pbo")


class MissionUploadConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_credentials_and_mission_folder_are_forwarded(self):
        client = ArmaServerWebAdmin(
            config={"arma_server_web_admin": {"am2": {
                "address": "https://panel.example",
                "public_username": "public-user",
                "public_password": "test-password",
                "mission_folder_path": r"S:\Missions",
            }}},
            database=None,
        )
        mission = attachment()
        with patch("arma_server_web_admin.upload_mission", new_callable=AsyncMock) as transfer:
            await client.mission_upload(100, 1, "am2", pbo_upload=mission)
            transfer.assert_awaited_once_with(
                mission, address="https://panel.example", local_path=r"S:\Missions",
                game="arma3", username="public-user", password="test-password",
            )
            await client.mission_upload(100, 1, "am2", pbo_upload=mission, game="custom")
            self.assertEqual(transfer.call_args.kwargs["game"], "custom")

    async def test_missing_optional_panel_values_are_forwarded_as_empty(self):
        client = ArmaServerWebAdmin(
            config={"arma_server_web_admin": {"am2": {}}}, database=None,
        )
        mission = attachment()
        with patch("arma_server_web_admin.upload_mission", new_callable=AsyncMock) as transfer:
            await client.mission_upload(100, 1, "am2", pbo_upload=mission)
            transfer.assert_awaited_once_with(
                mission, address="", local_path="", game="arma3", username="", password="",
            )


class MissionUploadCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(
            config={"arma_server_web_admin": {"am2": {}}, "discord": {
                "guild_id": 1,
                "am2": {
                    "mission_development": {"forum_moderators": [10]},
                    "server_admin_office": {"forum_moderators": [20]},
                },
            }},
            logger=MagicMock(),
            arma_server_web_admin=SimpleNamespace(mission_upload=AsyncMock(
                return_value=MissionUploadResult("operation.altis.pbo", False)
            )),
        )
        self.cog = MisdevOffice(self.bot)
        self.interaction = MagicMock()
        self.interaction.client = self.bot
        self.interaction.guild.id = 1
        self.interaction.user = MagicMock(spec=discord.Member)
        self.interaction.user.id = 100
        self.interaction.user.roles = [SimpleNamespace(id=10)]
        self.interaction.response.defer = AsyncMock()
        self.interaction.response.send_message = AsyncMock()
        self.interaction.followup.send = AsyncMock()

    async def invoke(self, mission=None):
        await MisdevOffice.mission_upload.callback(
            self.cog, self.interaction, mission or attachment()
        )

    async def test_each_moderator_role_can_upload(self):
        for role_id in (10, 20):
            self.interaction.user.roles = [SimpleNamespace(id=role_id)]
            await self.invoke()
        self.assertEqual(self.bot.arma_server_web_admin.mission_upload.await_count, 2)
        self.interaction.response.defer.assert_awaited_with(ephemeral=True, thinking=True)
        self.assertEqual(self.bot.arma_server_web_admin.mission_upload.call_args.kwargs["game"], "arma3")

    async def test_upload_helper_forwards_explicit_game(self):
        await self.cog._upload_mission(self.interaction, attachment(), game="custom")
        self.assertEqual(self.bot.arma_server_web_admin.mission_upload.call_args.kwargs["game"], "custom")

    async def test_unauthorized_wrong_guild_and_dm_cannot_upload(self):
        self.interaction.user.roles = []
        await self.invoke()
        self.interaction.guild.id = 2
        await self.invoke()
        self.interaction.guild = None
        await self.invoke()
        self.bot.arma_server_web_admin.mission_upload.assert_not_awaited()

    async def test_invalid_attachment_does_not_transfer(self):
        await self.invoke(attachment("bad.txt"))
        self.bot.arma_server_web_admin.mission_upload.assert_not_awaited()

    async def test_transfer_failure_is_reported_and_logged(self):
        self.bot.arma_server_web_admin.mission_upload.side_effect = MissionUploadError("Panel login failed")
        await self.invoke()
        self.interaction.followup.send.assert_awaited_with("Panel login failed", ephemeral=True)
        self.bot.logger.exception.assert_called_once()

    async def test_discord_download_failure_is_reported(self):
        self.bot.arma_server_web_admin.mission_upload.side_effect = discord.HTTPException(
            SimpleNamespace(status=404, reason="Not Found"), "Attachment deleted"
        )
        await self.invoke()
        self.assertIn("Mission upload failed", self.interaction.followup.send.call_args.args[0])
        self.bot.logger.exception.assert_called_once()

    async def invoke_menu(self, attachments):
        source = SimpleNamespace(
            channel=self.interaction.channel,
            attachments=attachments,
            jump_url="https://discord.com/channels/1/2/3",
        )
        await self.cog.upload_mission_menu.callback(self.interaction, source)
        return source

    async def test_context_menu_uploads_selected_message_and_links_source(self):
        mission = attachment("Operation.Altis.PBO")
        source = await self.invoke_menu([attachment("notes.txt"), mission])
        self.bot.arma_server_web_admin.mission_upload.assert_awaited_once_with(
            100, 1, "am2", pbo_upload=mission, game="arma3"
        )
        self.assertIn(source.jump_url, self.interaction.followup.send.call_args.args[0])
        self.interaction.followup.send.assert_awaited_once()
        self.assertTrue(self.interaction.followup.send.call_args.kwargs["ephemeral"])

    async def test_context_menu_rejects_missing_or_multiple_pbos(self):
        for attachments, expected in (
            ([], "no .pbo"),
            ([attachment("notes.txt")], "no .pbo"),
            ([attachment(), attachment("other.Altis.pbo")], "multiple .pbo"),
        ):
            with self.subTest(expected=expected):
                source = await self.invoke_menu(attachments)
                response = self.interaction.response.send_message.call_args.args[0]
                self.assertIn(expected, response)
                self.assertIn(source.jump_url, response)
        self.bot.arma_server_web_admin.mission_upload.assert_not_awaited()

    async def test_context_menu_preserves_access_restrictions(self):
        self.interaction.user.roles = []
        await self.invoke_menu([attachment()])
        self.interaction.user.roles = [SimpleNamespace(id=20)]
        self.interaction.guild.id = 2
        await self.invoke_menu([attachment()])
        self.interaction.guild = None
        await self.invoke_menu([attachment()])
        self.bot.arma_server_web_admin.mission_upload.assert_not_awaited()

    async def test_context_menu_errors_link_source(self):
        source = await self.invoke_menu([attachment("../invalid.pbo")])
        self.assertIn(source.jump_url, self.interaction.response.send_message.call_args.args[0])
        self.bot.arma_server_web_admin.mission_upload.assert_not_awaited()
        self.bot.arma_server_web_admin.mission_upload.side_effect = MissionUploadError("Upload failed")
        source = await self.invoke_menu([attachment()])
        self.assertIn(source.jump_url, self.interaction.followup.send.call_args.args[0])


class MissionUploadMenuLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_registration_removal_and_reload(self):
        async with commands.Bot(command_prefix="!", intents=discord.Intents.none()) as bot:
            cog = MisdevOffice(bot)
            await bot.add_cog(cog)
            self.assertIs(
                bot.tree.get_command("Upload mission", type=discord.AppCommandType.message),
                cog.upload_mission_menu,
            )
            self.assertTrue(cog.upload_mission_menu.guild_only)
            await bot.remove_cog("misdev")
            self.assertIsNone(bot.tree.get_command("Upload mission", type=discord.AppCommandType.message))
            await bot.add_cog(MisdevOffice(bot))
            self.assertIsNotNone(bot.tree.get_command("Upload mission", type=discord.AppCommandType.message))
