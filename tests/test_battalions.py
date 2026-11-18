from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from cogs.ctc import CTC
from cogs.misdev import MisdevOffice, MissionSubmissionModal
from cogs.sao import ServerAdminOffice, StartStopButtonSelection, ModRequestModal, BugReportModal
from cogs.taw import TAW
from utils.battalions import (
    BattalionSelectionError, battalion_autocomplete, battalion_choices,
    battalions, resolve_battalion, select_battalion,
)


def config():
    return {
        "discord": {
            "guild_id": 1,
            "default_battalion": "bravo",
            "alpha": {
                "name": "First Battalion",
                "mission_development": {"forum_channel_id": 10, "forum_moderators": [100]},
                "server_admin_office": {"office_channel_id": 11, "forum_moderators": [100]},
                "combat_training_centre": {"queue_channel_id": 12},
                "operations": [{"label": "Sunday", "weekday": 7, "hour": 19, "minute": 30}],
                "info": {"heading": "Alpha info", "embeds": [{"title": "Alpha mods"}]},
                "form": {"embeds": [{"title": "Alpha form"}], "buttons": []},
            },
            "bravo": {
                "name": "Second Battalion",
                "mission_development": {"forum_channel_id": 20, "forum_moderators": [200]},
                "server_admin_office": {"office_channel_id": 21, "forum_moderators": [200]},
                "combat_training_centre": {"queue_channel_id": 22},
            },
            "unconfigured": {"name": "Unconfigured Battalion"},
        },
        "arma_server_web_admin": {"alpha": {"address": "https://alpha.example"}, "bravo": {}},
    }


def interaction(bot, channel_id=99, roles=(100,)):
    target = MagicMock()
    target.client = bot
    target.guild.id = 1
    target.channel.id = channel_id
    target.channel.parent_id = None
    target.user = MagicMock(spec=discord.Member)
    target.user.id = 50
    target.user.roles = [SimpleNamespace(id=role) for role in roles]
    target.response.send_message = AsyncMock()
    target.response.send_modal = AsyncMock()
    target.response.defer = AsyncMock()
    target.followup.send = AsyncMock()
    return target


class BattalionRoutingTests(unittest.TestCase):
    def test_discovers_arbitrary_keys_and_filters_by_feature(self):
        c = config()
        self.assertEqual([u.key for u in battalions(c, office="mission_development")], ["alpha", "bravo"])
        self.assertEqual([u.key for u in battalions(c, panel=True)], ["alpha", "bravo"])
        self.assertEqual([u.key for u in battalions(c, feature="info")], ["alpha"])

    def test_matches_key_or_name_and_rejects_unknown(self):
        c = config()
        self.assertEqual(select_battalion(c, None, "SECOND BATTALION").key, "bravo")
        self.assertEqual(select_battalion(c, None, "ALPHA").key, "alpha")
        with self.assertRaises(BattalionSelectionError):
            select_battalion(c, None, "missing")

    def test_infers_channel_thread_and_queue(self):
        for channel in (
            SimpleNamespace(id=10, parent_id=None),
            SimpleNamespace(id=99, parent_id=10),
            SimpleNamespace(id=12, parent_id=None),
        ):
            self.assertEqual(select_battalion(config(), channel).key, "alpha")

    def test_explicit_choice_overrides_channel(self):
        self.assertEqual(select_battalion(config(), SimpleNamespace(id=10), "bravo").key, "bravo")

    def test_ambiguity_never_uses_default_battalion(self):
        with self.assertRaisesRegex(BattalionSelectionError, "Please select"):
            select_battalion(config(), None)

    def test_unavailable_feature_does_not_redirect_to_other_battalion(self):
        with self.assertRaisesRegex(BattalionSelectionError, "not configured"):
            select_battalion(config(), SimpleNamespace(id=20), feature="info")
        self.assertEqual(select_battalion(config(), None, feature="info").key, "alpha")

    def test_shared_channels_do_not_guess(self):
        c = config()
        c["discord"]["bravo"]["server_admin_office"]["office_channel_id"] = 11
        with self.assertRaises(BattalionSelectionError):
            select_battalion(c, SimpleNamespace(id=11))

    def test_autocomplete_filter_and_limit(self):
        self.assertEqual([v.value for v in battalion_choices(battalions(config()), "second")], ["bravo"])
        many = {"discord": {f"unit{i}": {"name": f"Unit {i}"} for i in range(40)}}
        self.assertEqual(len(battalion_choices(battalions(many), "")), 25)

    def test_ctc_primary_is_configured_not_hard_coded(self):
        bot = SimpleNamespace(config=config())
        self.assertEqual(CTC(bot).units.primary, "bravo")
        bot.config["discord"]["default_battalion"] = "alpha"
        self.assertEqual(CTC(bot).units.primary, "alpha")


class BattalionCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(
            config=config(), logger=MagicMock(),
            arma_server_web_admin=SimpleNamespace(
                mission_upload=AsyncMock(return_value=SimpleNamespace(filename="test.altis.pbo", local=True)),
                server_start=AsyncMock(return_value=SimpleNamespace(status=200, url="https://alpha.example")),
            ),
        )

    async def test_autocomplete_uses_live_config_without_ctc_cog(self):
        target = interaction(self.bot)
        autocomplete = battalion_autocomplete(office="mission_development")
        self.assertEqual([u.value for u in await autocomplete(target, "")], ["alpha", "bravo"])
        del self.bot.config["discord"]["bravo"]["mission_development"]
        self.assertEqual([u.value for u in await autocomplete(target, "")], ["alpha"])

    async def test_wrong_guild_or_dm_is_rejected(self):
        target = interaction(self.bot)
        target.guild.id = 2
        self.assertIsNone(await resolve_battalion(target, "alpha"))
        target.guild = None
        self.assertIsNone(await resolve_battalion(target, "alpha"))
        self.assertEqual(target.response.send_message.await_count, 2)

    async def test_upload_uses_chosen_battalion_and_its_permissions(self):
        cog = MisdevOffice(self.bot)
        target = interaction(self.bot, roles=(100,))
        attachment = SimpleNamespace(filename="test.altis.pbo")
        await cog._upload_mission(target, attachment, battalion="bravo")
        self.bot.arma_server_web_admin.mission_upload.assert_not_awaited()
        target.user.roles = [SimpleNamespace(id=200)]
        await cog._upload_mission(target, attachment, battalion="bravo")
        self.assertEqual(self.bot.arma_server_web_admin.mission_upload.call_args.args[2], "bravo")

    async def test_played_uses_selected_battalion_for_tags_and_member_pings(self):
        settings = self.bot.config["discord"]["alpha"]
        settings["mission_development"]["forum_tags"] = {
            "played": 1, "approved": 2, "pending_review": 3, "scheduled": 4,
        }
        settings["roles"] = {"members": [101, 102]}
        target = interaction(self.bot, channel_id=99)
        target.channel.parent_id = 10
        target.channel.name = "Mission: Test"
        target.channel.add_tags = AsyncMock()
        target.channel.remove_tags = AsyncMock()
        with patch("cogs.misdev.commands.Context.from_interaction", new_callable=AsyncMock):
            await MisdevOffice.mission_played.callback(MisdevOffice(self.bot), target)
        feedback = target.followup.send.call_args.kwargs
        self.assertEqual(feedback["content"], "<@&101> <@&102>")
        self.assertEqual(feedback["allowed_mentions"].to_dict(), {"parse": [], "roles": [101, 102]})

    async def test_upload_choices_exclude_battalions_without_panel_configuration(self):
        del self.bot.config["arma_server_web_admin"]["bravo"]
        target = interaction(self.bot)
        choices = await battalion_autocomplete(office="mission_development", panel=True)(target, "")
        self.assertEqual([choice.value for choice in choices], ["alpha"])
        await MisdevOffice(self.bot)._upload_mission(
            target, SimpleNamespace(filename="test.altis.pbo"), battalion="bravo"
        )
        self.bot.arma_server_web_admin.mission_upload.assert_not_awaited()

    async def test_context_menu_infers_source_message_not_invoking_channel(self):
        cog = MisdevOffice(self.bot)
        target = interaction(self.bot, channel_id=10, roles=(200,))
        source = SimpleNamespace(
            channel=SimpleNamespace(id=99, parent_id=20), attachments=[SimpleNamespace(filename="test.altis.pbo")],
            jump_url="https://discord.com/channels/1/20/99",
        )
        await cog.upload_mission_menu.callback(target, source)
        self.assertEqual(self.bot.arma_server_web_admin.mission_upload.call_args.args[2], "bravo")
        self.assertIn(source.jump_url, target.followup.send.call_args.args[0])

    async def test_ambiguous_context_menu_does_not_upload(self):
        cog = MisdevOffice(self.bot)
        target = interaction(self.bot)
        source = SimpleNamespace(channel=target.channel, attachments=[], jump_url="https://example.com")
        await cog.upload_mission_menu.callback(target, source)
        self.bot.arma_server_web_admin.mission_upload.assert_not_awaited()
        self.assertIn("Please select", target.response.send_message.call_args.args[0])

    async def test_submission_and_office_modals_retain_selection(self):
        target = interaction(self.bot)
        await MisdevOffice.mission_submission.callback(
            MisdevOffice(self.bot), target, SimpleNamespace(filename="test.altis.pbo"), battalion="bravo"
        )
        modal = target.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, MissionSubmissionModal)
        self.assertEqual(modal.battalion, "bravo")
        for name, modal_class in (("mod_request", ModRequestModal), ("bug_report", BugReportModal)):
            with patch.object(modal_class, "wait", new_callable=AsyncMock):
                # The command reads the modal interaction after wait; emulate a submission.
                with patch.object(modal_class, "interaction", target, create=True):
                    await getattr(ServerAdminOffice, name).callback(ServerAdminOffice(self.bot), target, battalion="bravo")
            self.assertEqual(target.response.send_modal.call_args.args[0].battalion, "bravo")

    async def test_server_commands_and_buttons_preserve_destination(self):
        cog = ServerAdminOffice(self.bot)
        target = MagicMock(spec=commands.Context)
        target.bot = self.bot
        target.guild.id = 1
        target.author.id = 50
        target.send = AsyncMock()
        await ServerAdminOffice.server_start.callback(cog, target, "test-server", battalion="bravo")
        self.assertEqual(self.bot.arma_server_web_admin.server_start.call_args.args[2], "bravo")
        view = StartStopButtonSelection(cog, target, ["server-a"], "bravo")
        other = StartStopButtonSelection(cog, target, ["server-b"], "alpha")
        self.assertEqual([o.label for o in view.serverSelect.options], ["server-a"])
        self.assertEqual([o.label for o in other.serverSelect.options], ["server-b"])
        view.server_id = "server-a"
        user_interaction = interaction(self.bot)
        with patch("cogs.sao.commands.Context.from_interaction", new_callable=AsyncMock, return_value=target):
            await view.serverStartButton.callback(user_interaction)
        self.assertEqual(self.bot.arma_server_web_admin.server_start.call_args.args[2], "bravo")

    async def test_taw_uses_configured_content_and_schedules(self):
        cog = TAW(self.bot)
        target = MagicMock(spec=commands.Context)
        target.bot = self.bot
        target.guild.id = 1
        target.send = AsyncMock()
        await TAW.info.callback(cog, target, battalion="alpha")
        self.assertEqual(target.send.call_args.kwargs["content"], "Alpha info")
        self.assertEqual(target.send.call_args.kwargs["embeds"][-1].title, "Alpha mods")
        await TAW.signup.callback(cog, target, battalion="alpha")
        self.assertEqual(target.send.call_args.kwargs["embeds"][-1].fields[0].name, "First Battalion")
        await TAW.form.callback(cog, target, battalion="alpha")
        self.assertEqual(target.send.call_args.kwargs["embeds"][0].title, "Alpha form")

    async def test_all_cogs_register_with_real_discord_command_tree(self):
        async with commands.Bot(command_prefix="!", intents=discord.Intents.none()) as bot:
            bot.config = config()
            await bot.add_cog(MisdevOffice(bot))
            await bot.add_cog(ServerAdminOffice(bot))
            await bot.add_cog(TAW(bot))
            self.assertIsNotNone(bot.tree.get_command("donate"))
            self.assertIsNotNone(bot.tree.get_command("info"))
            self.assertTrue(bot.tree.get_command("mission_upload").get_parameter("battalion").autocomplete)
            self.assertTrue(bot.tree.get_command("server_start").get_parameter("battalion").autocomplete)

    async def test_dispatcher_returns_choices_for_every_battalion_option(self):
        async with commands.Bot(command_prefix="!", intents=discord.Intents.none()) as bot:
            bot.config = config()
            for cog in (MisdevOffice(bot), ServerAdminOffice(bot), TAW(bot)):
                await bot.add_cog(cog)
            ctc = CTC(bot)
            checked = 0
            for command in [*bot.tree.walk_commands(), *ctc.badge.walk_commands()]:
                if not isinstance(command, discord.app_commands.Command):
                    continue
                with self.subTest(command=command.qualified_name):
                    names = [param.name for param in command.parameters]
                    self.assertNotIn("unit", names)
                    self.assertNotIn("server_type", names)
                    parameter = command.get_parameter("battalion")
                    if parameter is None:
                        continue
                    self.assertTrue(parameter.autocomplete)
                    self.assertFalse(parameter.required)
                    target = interaction(bot)
                    target.response.is_done.return_value = False
                    target.response.autocomplete = AsyncMock()
                    await command._invoke_autocomplete(
                        target, "battalion", SimpleNamespace(battalion="")
                    )
                    choices = target.response.autocomplete.call_args.args[0]
                    self.assertTrue(choices)
                    self.assertIn("alpha", [choice.value for choice in choices])
                    checked += 1
            self.assertGreaterEqual(checked, 19)

    async def test_ctc_battalion_keyword_reaches_existing_resolution(self):
        cog = CTC(self.bot)
        target = interaction(self.bot)
        with patch.object(cog, "resolve_unit", new_callable=AsyncMock, return_value=None) as resolve:
            await cog.badge_catalogue.callback(cog, target, battalion="alpha")
        resolve.assert_awaited_once_with(target, "alpha")
