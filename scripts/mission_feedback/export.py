"""
Stage 1: Export played missions, poll ratings and feedback from the MisDev forum.

Logs in with the bot TOKEN from the repo .env, reads every (active + archived) thread in
the selected battalion's mission_development.forum_channel_id and writes:
  output/missions_raw.json      - full data incl. feedback text (input for theme extraction)
  output/missions_ratings.csv   - ratings only, one row per mission

Requires the "Message Content" privileged intent enabled for the bot in the Discord dev portal.
"""
import argparse
import csv
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from utils.battalions import select_battalion

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

CATEGORIES = ("concept", "zeus", "nco")
FEEDBACK_HEADER = re.compile(r"^#\s*Feedback from\s*<@!?(\d+)>", re.IGNORECASE)
FEEDBACK_SECTIONS = {
    "good": re.compile(r"\*\*What did you find good about the Operation\?\*\*\s*(.*?)(?=\n\*\*What could be improved\?\*\*|\Z)", re.S),
    "improve": re.compile(r"\*\*What could be improved\?\*\*\s*(.*?)(?=\n\*\*Anything else\?\*\*|\Z)", re.S),
    "other": re.compile(r"\*\*Anything else\?\*\*\s*(.*)\Z", re.S),
}
USER_MENTION = re.compile(r"<@!?(\d+)>")
MAP_LINE = re.compile(r"\*\*Map:\*\*\s*(.+)")
MIN_FREE_TEXT_LEN = 15

log = logging.getLogger("mission_export")


def poll_category(question: str):
    q = (question or "").lower()
    if not q.startswith("rate"):
        return None
    if "concept" in q:
        return "concept"
    if "zeus" in q:
        return "zeus"
    if "nco" in q or "leadership" in q:
        return "nco"
    return None


def poll_question_text(poll: discord.Poll) -> str:
    q = poll.question
    return q if isinstance(q, str) else getattr(q, "text", str(q))


def clean_text(text: str) -> str:
    text = (text or "").strip()
    return "" if text in ("-", "None") else text


def parse_feedback_sections(content: str) -> dict:
    sections = {}
    for key, rx in FEEDBACK_SECTIONS.items():
        match = rx.search(content)
        sections[key] = clean_text(match.group(1)) if match else ""
    return sections


def is_meaningful_free_text(text: str) -> bool:
    stripped = re.sub(r"https?://\S+|<a?:\w+:\d+>|<@[!&]?\d+>", "", text or "").strip()
    return len(stripped) >= MIN_FREE_TEXT_LEN


def rating_stats(votes: dict) -> tuple:
    total = sum(votes.values())
    if not total:
        return ("", 0)
    return (round(sum(int(k) * v for k, v in votes.items()) / total, 2), total)


def overall_avg(stats: dict):
    avgs = [s[0] for s in stats.values() if s[0] != ""]
    return round(sum(avgs) / len(avgs), 2) if avgs else ""


class Exporter(discord.Client):
    def __init__(self, config: dict, args):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.args = args
        self.user_names: dict[int, str] = {}
        self.exit_code = 0
        self._started = False

    async def on_ready(self):
        if self._started:
            return
        self._started = True
        log.info("Logged in as %s", self.user)
        try:
            await self.export()
        except Exception:
            log.exception("Export failed")
            self.exit_code = 1
        finally:
            await self.close()

    async def resolve_user(self, guild: discord.Guild, user_id) -> str:
        if not user_id:
            return ""
        user_id = int(user_id)
        if user_id in self.user_names:
            return self.user_names[user_id]
        member = guild.get_member(user_id)
        if member:
            name = member.display_name
        else:
            try:
                name = (await guild.fetch_member(user_id)).display_name
            except discord.HTTPException:
                try:
                    user = await self.fetch_user(user_id)
                    name = user.global_name or user.name
                except discord.HTTPException:
                    name = str(user_id)
        self.user_names[user_id] = name
        return name

    async def collect_threads(self, forum: discord.ForumChannel) -> list[discord.Thread]:
        threads: dict[int, discord.Thread] = {t.id: t for t in forum.threads}
        for t in await forum.guild.active_threads():
            if t.parent_id == forum.id:
                threads[t.id] = t
        async for t in forum.archived_threads(limit=None):
            threads[t.id] = t
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        result = sorted(threads.values(), key=lambda t: t.created_at or epoch)
        if self.args.since:
            result = [t for t in result if (t.created_at or epoch) >= self.args.since]
        return result

    async def export(self):
        unit = select_battalion(
            self.config, None,
            self.args.battalion or self.config["discord"].get("default_battalion"),
            office="mission_development",
        )
        md = unit.settings["mission_development"]
        guild_id = int(self.config["discord"]["guild_id"])
        guild = self.get_guild(guild_id) or await self.fetch_guild(guild_id)
        forum = guild.get_channel(int(md["forum_channel_id"])) or await self.fetch_channel(int(md["forum_channel_id"]))
        played_tag_id = int(md["forum_tags"]["played"])

        threads = await self.collect_threads(forum)
        log.info("Found %d threads in #%s", len(threads), forum.name)

        missions = []
        for i, thread in enumerate(threads, 1):
            if self.args.limit and len(missions) >= self.args.limit:
                break
            try:
                mission = await self.process_thread(guild, thread, played_tag_id)
            except discord.Forbidden:
                log.warning("No access to thread %s (%s), skipping", thread.name, thread.id)
                continue
            if mission:
                missions.append(mission)
                log.info("[%d/%d] %s - played %s (%s), %d feedback", i, len(threads), mission["mission"],
                         mission["date_played"], mission["date_source"], len(mission["feedback"]))
            else:
                log.info("[%d/%d] %s - not played, skipped", i, len(threads), thread.name)

        missions.sort(key=lambda m: m["date_played"] or "")
        self.write_outputs(missions)

    async def process_thread(self, guild, thread: discord.Thread, played_tag_id: int):
        has_played_tag = any(tag.id == played_tag_id for tag in thread.applied_tags)

        starter = None
        polls = []
        form_feedback = []
        user_messages = []
        last_message_at = None

        async for msg in thread.history(limit=None, oldest_first=True):
            last_message_at = msg.created_at
            if msg.id == thread.id:
                starter = msg
                continue

            if msg.author.bot:
                if msg.poll:
                    cat = poll_category(poll_question_text(msg.poll))
                    if cat:
                        polls.append((cat, msg))
                    continue
                header = FEEDBACK_HEADER.match(msg.content or "")
                if header:
                    form_feedback.append({"user_id": int(header.group(1)), "created_at": msg.created_at,
                                          **parse_feedback_sections(msg.content)})
                continue

            if msg.content:
                user_messages.append(msg)

        if not has_played_tag and not polls:
            return None

        # Votes are summed across every poll set, as a thread may have been played more than once
        ratings = {c: {str(n): 0 for n in range(1, 6)} for c in CATEGORIES}
        poll_dates = []
        for cat, msg in polls:
            poll_dates.append(msg.created_at)
            for answer in msg.poll.answers:
                try:
                    score = int(str(answer.text).strip())
                except ValueError:
                    continue
                if 1 <= score <= 5:
                    ratings[cat][str(score)] += answer.vote_count or 0
        play_count = max(sum(1 for c, _ in polls if c == cat) for cat in CATEGORIES)

        if poll_dates:
            date_played, date_source = min(poll_dates), "poll"
        elif form_feedback:
            date_played, date_source = min(f["created_at"] for f in form_feedback), "feedback"
        else:
            date_played, date_source = last_message_at, "last_message"

        # Free-text feedback only counts once the mission has been played; earlier messages are review chatter
        feedback_cutoff = date_played if date_source in ("poll", "feedback") else None
        feedback = []
        for f in form_feedback:
            feedback.append({
                "source": "form",
                "user_id": str(f["user_id"]),
                "user": await self.resolve_user(guild, f["user_id"]),
                "created_at": f["created_at"].isoformat(),
                "good": f["good"], "improve": f["improve"], "other": f["other"], "text": "",
            })
        if feedback_cutoff:
            for msg in user_messages:
                if msg.created_at >= feedback_cutoff and is_meaningful_free_text(msg.content):
                    feedback.append({
                        "source": "message",
                        "user_id": str(msg.author.id),
                        "user": getattr(msg.author, "display_name", msg.author.name),
                        "created_at": msg.created_at.isoformat(),
                        "good": "", "improve": "", "other": "", "text": msg.content.strip(),
                    })

        # The bot creates the thread, so the real author is the first user mention in the starter post
        author_id = None
        map_name = ""
        if starter:
            mention = USER_MENTION.search(starter.content or "")
            if mention:
                author_id = int(mention.group(1))
            elif not starter.author.bot:
                author_id = starter.author.id
            map_match = MAP_LINE.search(starter.content or "")
            if map_match:
                map_name = map_match.group(1).strip()
        if author_id is None:
            author_id = thread.owner_id

        return {
            "thread_id": str(thread.id),
            "mission": re.sub(r"^\s*Mission:\s*", "", thread.name, flags=re.I).strip(),
            "thread_name": thread.name,
            "thread_url": thread.jump_url,
            "author_id": str(author_id) if author_id else "",
            "author": await self.resolve_user(guild, author_id),
            "map": map_name,
            "tags": [t.name for t in thread.applied_tags],
            "has_played_tag": has_played_tag,
            "date_played": date_played.date().isoformat() if date_played else "",
            "date_source": date_source,
            "play_dates": sorted({d.date().isoformat() for d in poll_dates}),
            "play_count": play_count,
            "ratings": ratings,
            "description": (starter.content if starter else "")[:1500],
            "feedback": feedback,
        }

    def write_outputs(self, missions: list[dict]):
        os.makedirs(self.args.out, exist_ok=True)
        json_path = os.path.join(self.args.out, "missions_raw.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"exported_at": datetime.now(timezone.utc).isoformat(), "missions": missions}, f, indent=2, ensure_ascii=False)

        csv_path = os.path.join(self.args.out, "missions_ratings.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date_played", "date_source", "mission", "author", "map", "play_count",
                             *[f"{c}_{s}" for c in CATEGORIES for s in ("avg", "votes")], "overall_avg",
                             "feedback_count", "thread_url"])
            for m in missions:
                stats = {c: rating_stats(m["ratings"][c]) for c in CATEGORIES}
                writer.writerow([m["date_played"], m["date_source"], m["mission"], m["author"], m["map"], m["play_count"],
                                 *[v for c in CATEGORIES for v in stats[c]], overall_avg(stats),
                                 len(m["feedback"]), m["thread_url"]])
        log.info("Wrote %d missions to %s and %s", len(missions), json_path, csv_path)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    p.add_argument("--battalion", help="Battalion key/name; defaults to discord.default_battalion")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--limit", type=int, default=0, help="Stop after N played missions (for testing)")
    p.add_argument("--since", type=lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc),
                   help="Only threads created on/after YYYY-MM-DD")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("discord").setLevel(logging.WARNING)
    load_dotenv(os.path.join(ROOT, ".env"))
    token = os.getenv("TOKEN")
    if not token:
        sys.exit("TOKEN not found in .env")
    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    client = Exporter(config, args)
    try:
        client.run(token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        sys.exit("Enable the 'Message Content' privileged intent for the bot in the Discord developer portal.")
    sys.exit(client.exit_code)


if __name__ == "__main__":
    main()
