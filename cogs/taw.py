from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import aiohttp
from bs4 import BeautifulSoup
import asyncpraw
from datetime import datetime, timedelta, timezone
import pytz
from utils.battalions import battalion_autocomplete, resolve_battalion


class TAW(commands.Cog, name="taw"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="donate",
        description="Get a link to the division's donation page",
    )
    @app_commands.describe(post="Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.")
    async def donate(self, context: Context, post: bool = False) -> None:
        """
        Get a link to all the division's donation pages.

        :param context: The hybrid command context.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """

        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Donate", style=discord.ButtonStyle.link, url='https://donations.taw.net/'))

        embed = discord.Embed(title="Arma Division Server Donation", description="""Every donation helps keep the community running, supports infrastructure costs, and allows us to continue improving the experience for everyone.""", color=0xBEBEFE)

        await context.send(embed=embed, view=buttons, ephemeral=(not post))


    @commands.hybrid_command(
        name="info",
        description="Get information related to a specific battalion.",
    )
    @app_commands.describe(post="Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.")
    @app_commands.autocomplete(battalion=battalion_autocomplete(feature="info"))
    async def info(self, context: Context, battalion: str | None = None, post: bool = False) -> None:
        """
        Get a link to all the division's donation pages.

        :param context: The hybrid command context.
        :param battalion: The battalion the user is interested in joining.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """

        unit = await resolve_battalion(context, battalion, feature="info")
        if unit is None:
            return
        settings = unit.settings["info"]
        embeds = [discord.Embed.from_dict(data) for data in settings["embeds"]]
        if unit.settings.get("operations"):
            events = discord.Embed(title="Events", color=0xBEBEFE)
            events.add_field(name="Operation Times", value=await operation_times(unit.settings["operations"], timeonly=True))
            embeds.insert(0, events)
        content = settings.get("heading", unit.name)
        if post:
            await context.channel.send(content=content, embeds=embeds)
            await context.send("Posted message to channel for everyone to see.", ephemeral=True)
        else:
            await context.send(content=content, embeds=embeds, ephemeral=True)


    @commands.hybrid_command(name="signup", description="Get information on how to sign up to the unit.")
    @app_commands.describe(post="Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.")
    @app_commands.autocomplete(battalion=battalion_autocomplete(feature="operations"))
    async def signup(self, context: Context, battalion: str = None, post: bool = False) -> None:
        """
        Get information on how to sign up to the unit.

        :param context: The hybrid command context.
        :param battalion: The battalion the user is interested in joining.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """

        taw_join_info = discord.Embed(title="""Joining TAW""", description="""To begin your journey with TAW, visit [www.taw.net](https://taw.net/) and register for the Arma division. During the registration process, you will have the option to choose the battalion that best suits your preferences and schedule.\n
        Once you have completed the registration, it is crucial to reach out to the drill instructor of the selected battalion to initiate your onboarding process. They will guide you through the necessary steps and provide you with all the information you need to get started.\n
        Additionally, please ensure that you have downloaded and installed any required mods and TeamSpeak software in advance, as these are essential for participating in our operations. Each battalion may have specific requirements, so be sure to check the details provided during registration or contact your drill instructor for further assistance. Use `/signup [battalion]` to get more information on the battalion you are interested in joining.""",
        color=0xBEBEFE)

        operations = discord.Embed(title="""Operation Times""", description="""Check out the operations schedule for each battalion below, its recommended to be there atleast 15 minutes before:""", color=0xBEBEFE)
        unit = await resolve_battalion(context, battalion, feature="operations")
        if unit is None:
            return
        operations.add_field(name=unit.name, value=await operation_times(unit.settings["operations"]), inline=False)

        if post:
            await context.channel.send(embeds=[taw_join_info, operations])
            await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
        else:
            await context.send(embeds=[taw_join_info, operations], ephemeral=True)


    @commands.hybrid_command(name="form", description="Get a link to the battalion-wide form and responses.", )
    @app_commands.autocomplete(battalion=battalion_autocomplete(feature="form"))
    async def form(self, context: Context, battalion: str | None = None) -> None:
        """
        Get a link to the battalion-wide form and responses.

        :param context: The hybrid command context.
        """
        unit = await resolve_battalion(context, battalion, feature="form")
        if unit is None:
            return
        settings = unit.settings["form"]
        buttons = discord.ui.View()
        for button in settings["buttons"]:
            buttons.add_item(discord.ui.Button(label=button["label"], url=button["url"]))
        await context.send(
            embeds=[discord.Embed.from_dict(data) for data in settings["embeds"]],
            view=buttons, ephemeral=True,
        )


    @commands.hybrid_command(name="social", description="Get a link to all the battalion's social pages", )
    async def social(self, context: Context, post: bool = False) -> None:
        """
        Get a link to all the battalion's social pages

        :param context: The hybrid command context.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """
        latest_reddit_url = await pull_recent_reddit_post_url(self, "tawarmadivision")

        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Twitch", style=discord.ButtonStyle.link, url='https://www.twitch.tv/tawarmadivision'))
        buttons.add_item(discord.ui.Button(label="Reddit", style=discord.ButtonStyle.link, url=latest_reddit_url))
        buttons.add_item(discord.ui.Button(label="Twitter", style=discord.ButtonStyle.link, url='https://x.com/TheArmaUnit'))
        buttons.add_item(discord.ui.Button(label="Instagram", style=discord.ButtonStyle.link, url='https://www.instagram.com/taw.armadivision/'))
        buttons.add_item(discord.ui.Button(label="Facebook", style=discord.ButtonStyle.link, url='https://www.facebook.com/tawarma'))
        buttons.add_item(discord.ui.Button(label="TikTok", style=discord.ButtonStyle.link, url='https://www.tiktok.com/@arma.division.taw?_t=8nqYd9WmCub&_r=1'))

        embed = discord.Embed(title="Social Links", description=f"Check out all of our social pages, be sure to give a follow and a like on our recent posts!", color=0xBEBEFE)
        embed.set_image(url="https://i.imgur.com/LOaHCV2.png")

        if post:
            await context.channel.send(content=f"**Be sure to like, share and follow our social media accounts!**\n Latest Reddit Post: {latest_reddit_url}\n", view=buttons)
            await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
        else:
            await context.send(content=f"**Be sure to like, share and follow our social media accounts!**\n Latest Reddit Post: {latest_reddit_url}\n", view=buttons, ephemeral=True)



async def setup(bot) -> None:
    await bot.add_cog(TAW(bot))


async def operation_times(events: list[dict], *, timeonly: bool = False) -> str:
    lines = []
    for event in events:
        timestamp = await get_next_event_timestamp(
            event["weekday"], event["hour"], event["minute"], timeonly=timeonly
        )
        lines.append(f"- {event['label']} @ {timestamp}")
    return "\n".join(lines)


async def get_next_event_timestamp(day: int = 4, hour: int = 19, minute: int = 0, timeonly: bool = False) -> str:

    time_now = datetime.now(pytz.timezone('Europe/London'))
    time_event = time_now + (timedelta(days=((day-1) - time_now.weekday()) % 7))
    time_event = time_event.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if time_event < time_now:
        time_event += timedelta(days=7)

    if timeonly:
        timestamp = f"<t:{round(time_event.timestamp())}:t>"
    else:
        timestamp = f"<t:{round(time_event.timestamp())}:f> (<t:{round(time_event.timestamp())}:R>)"

    return timestamp


async def pull_recent_reddit_post_url(self, username: str):
    reddit = asyncpraw.Reddit(client_id=self.bot.config['reddit']['id'], client_secret=self.bot.config['reddit']['secret'], user_agent="lookup latest submission by u/hbturpin")

    user = await reddit.redditor(username)
    submissions = user.submissions.new(limit=1)

    async for link in submissions:
        return "https://www.reddit.com" + link.permalink
    else:
        return 'https://www.reddit.com/user/tawarmadivision/'


async def scrape_bio_text(taw_username):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://taw.net/member/{taw_username}.aspx") as response:
            if response.status != 200:
                raise ValueError(f"Failed to fetch the page, status code: {response.status}")
            
            # Parse the HTML content
            page_content = await response.text()
            soup = BeautifulSoup(page_content, "html.parser")
            
            
            # Locate the <div> containing the bio
            bio_div = soup.find("div", id="dossierbio")
            print("bio_div:",bio_div)
            if not bio_div:
                await session.close()
                return None
            
            # Locate the <h2> and ensure it contains "Bio:"
            bio_header = bio_div.find("h2", text="Bio:")
            print("bio_header:",bio_header)
            if not bio_header:
                await session.close()
                return None
            
            # Locate the <p> tag and the <code> tag inside it
            bio_paragraph = bio_div.find("p")
            print("bio_para:",bio_paragraph)
            if not bio_paragraph:
                await session.close()
                return None
            
            code_tag = bio_paragraph.find("code", class_="inline")
            print("code_tag:",code_tag)
            if not code_tag:
                await session.close()
                return None
            
            # Return the text within the <code> tag
            await session.close()
            return code_tag.get_text(strip=True)
