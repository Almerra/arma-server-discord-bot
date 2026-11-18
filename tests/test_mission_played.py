from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.misdev import MissionFeedbackButton, MisdevOffice


class MissionPlayedTests(unittest.IsolatedAsyncioTestCase):
    async def test_feedback_allows_only_configured_role_mentions(self):
        for role_ids in ([30, 40], ["30", "40"], []):
            with self.subTest(role_ids=role_ids):
                bot = SimpleNamespace(
                    config={"discord": {
                        "guild_id": 1,
                        "mission_development": {
                            "forum_channel_id": 2,
                            "forum_moderators": [10],
                            "forum_tags": {
                                "played": 100, "approved": 101,
                                "pending_review": 102, "scheduled": 103,
                            },
                        },
                        "server_admin_office": {"forum_moderators": [20]},
                        "am2": {"roles": {"members": role_ids}},
                    }},
                    logger=MagicMock(),
                )
                interaction = MagicMock()
                interaction.guild.id = 1
                interaction.user.id = 50
                interaction.user.roles = [SimpleNamespace(id=10)]
                interaction.channel.parent_id = 2
                interaction.channel.name = "Mission: Test"
                interaction.channel.add_tags = AsyncMock()
                interaction.channel.remove_tags = AsyncMock()
                interaction.response.defer = AsyncMock()
                interaction.followup.send = AsyncMock()

                with patch("cogs.misdev.commands.Context.from_interaction", new_callable=AsyncMock):
                    await MisdevOffice.mission_played.callback(MisdevOffice(bot), interaction)

                calls = interaction.followup.send.call_args_list
                self.assertEqual(len(calls), 4)
                for call in calls[:3]:
                    self.assertEqual(len(call.kwargs["poll"].answers), 5)
                    self.assertNotIn("content", call.kwargs)

                feedback = calls[-1].kwargs
                self.assertEqual(feedback["content"], " ".join(f"<@&{role}>" for role in role_ids))
                self.assertIsInstance(feedback["view"], MissionFeedbackButton)
                self.assertEqual(
                    feedback["allowed_mentions"].to_dict(),
                    {"parse": [], "roles": [int(role) for role in role_ids]},
                )
                interaction.channel.add_tags.assert_awaited()
                interaction.channel.remove_tags.assert_awaited()
