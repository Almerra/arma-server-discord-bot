"""Discord components for picking a time to run a badge request.

The shared message in the request's thread is persistent: its buttons carry
the request id in their custom_id, like the ticket buttons. Everything a
person opens from it (their hour grid, the time zone picker, the booking
list) is ephemeral, so it can be labelled in that one person's own time.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import discord

from . import booked_for
from . import schedule as sched
from .catalogue import Catalogue
from .views import COLOUR

#: Actions on the shared message. Named explicitly, as with ticket actions.
SCHEDULE_ACTIONS = ("mark", "book", "date", "none", "zone", "hours")

#: Both-free times shown on the shared message; the rest are a button away.
SHOWN_ON_MESSAGE = 5


class ScheduleButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=rf"ctcsched:(?P<action>{'|'.join(SCHEDULE_ACTIONS)}):(?P<rid>[0-9]+)",
):
    """A button on the shared message. Persistent across restarts via its custom_id."""

    def __init__(
        self,
        action: str,
        request_id: int,
        *,
        label: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: str | None = None,
    ) -> None:
        self.action = action
        self.request_id = request_id
        super().__init__(
            discord.ui.Button(
                label=label, style=style, emoji=emoji, custom_id=f"ctcsched:{action}:{request_id}"
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> ScheduleButton:
        return cls(
            match["action"],
            int(match["rid"]),
            label=item.label or "",
            style=item.style,
            emoji=str(item.emoji) if item.emoji else None,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("ctc")
        if cog is None:  # pragma: no cover - only if the cog is unloaded
            await interaction.response.send_message("Badge system is offline.", ephemeral=True)
            return
        await cog.handle_schedule_action(interaction, self.action, self.request_id)


def times_view(request_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        ScheduleButton(
            "mark", request_id, label="Mark my times",
            style=discord.ButtonStyle.primary, emoji="\N{SPIRAL CALENDAR PAD}",
        )
    )
    view.add_item(
        ScheduleButton(
            "book", request_id, label="Book a time",
            style=discord.ButtonStyle.success, emoji="\N{WHITE HEAVY CHECK MARK}",
        )
    )
    view.add_item(ScheduleButton("date", request_id, label="Set a date", emoji="\N{CALENDAR}"))
    view.add_item(ScheduleButton("none", request_id, label="None of these work"))
    view.add_item(
        ScheduleButton("zone", request_id, label="My time zone", emoji="\N{GLOBE WITH MERIDIANS}")
    )
    view.add_item(ScheduleButton("hours", request_id, label="All times"))
    return view


def times_embed(cat: Catalogue, row: Any, summary: sched.Summary) -> discord.Embed:
    """The shared message, kept short: who has marked hours, and the next
    few times both are free. The full lists are behind **All times**.

    Every time on it is a Discord timestamp, so each reader sees it in their
    own time zone without setting anything.
    """
    badge = cat.get(row["badge_key"])
    trainers = " ".join(f"<@{t}>" for t in summary.trainers) or "Instructor"
    lines = [
        f"{trainers}: "
        + (f"{sched.total_hours(summary.trainer_free)} marked" if summary.trainer_free
           else "no hours marked yet"),
        f"<@{row['member_id']}>: "
        + (f"{sched.total_hours(summary.trainee_free)} picked" if summary.trainee_free
           else "no hours picked yet"),
    ]
    embed = discord.Embed(
        colour=COLOUR,
        title=f"\N{SPIRAL CALENDAR PAD} Pick a time · {badge.name if badge else row['badge_key']}",
        description="\n".join(lines),
    )
    when = booked_for(row)
    if when:
        embed.add_field(
            name="\N{CALENDAR} Booked", value=f"<t:{when}:F> (<t:{when}:R>)", inline=False
        )
    elif summary.both:
        embed.add_field(
            name="\N{WHITE HEAVY CHECK MARK} Both free, in your time",
            value=sched.describe(summary.both, limit=SHOWN_ON_MESSAGE),
            inline=False,
        )
    embed.set_footer(text=f"Request #{row['id']} · All times shows every hour marked")
    return embed


def all_times_embed(row: Any, summary: sched.Summary, tz: str | None) -> discord.Embed:
    """Every marked hour, for one person. With their time zone known it is
    grouped a line per day on their clock; without, it falls back to
    timestamps."""

    def show(spans: list[tuple[int, int]]) -> str:
        text = sched.by_day(tz, spans) if tz else sched.describe(spans, limit=20)
        return text if len(text) <= 1024 else text[:1020].rsplit("\n", 1)[0] + "\n…"

    embed = discord.Embed(colour=COLOUR, title=f"All times · request #{row['id']}")
    embed.add_field(name="Instructor around", value=show(summary.trainer_free), inline=False)
    embed.add_field(name="Trainee can do", value=show(summary.trainee_free), inline=False)
    embed.add_field(
        name="\N{WHITE HEAVY CHECK MARK} Both free", value=show(summary.both), inline=False
    )
    embed.set_footer(
        text=f"In your time ({sched.zone_label(tz)})" if tz
        else "Set My time zone for a tidier list"
    )
    return embed


# ------------------------------------------------------------- hour grid


class AvailabilityGrid(discord.ui.View):
    """One person's 24 hours for one day, on their own clock.

    24 hour buttons and a next-day button fill Discord's five rows of five
    exactly. Every tap saves straight away, so there is no Save to forget.

    ``theirs`` is the other side's free time: the trainee's for an
    instructor, the instructors' for a trainee. A trainee can only pick hours
    an instructor is around; an instructor can mark any hour.
    """

    def __init__(
        self,
        cog: Any,
        row: Any,
        *,
        user_id: int,
        side: str,
        tz: str,
        day: int,
        mine: set[int],
        theirs: list[tuple[int, int]],
        now: float,
    ) -> None:
        super().__init__(timeout=840)  # inside the 15 minutes an ephemeral can be edited
        self.cog = cog
        self.row = row
        self.request_id = int(row["id"])
        self.user_id = user_id
        self.side = side
        self.tz = tz
        self.day = day
        self.date = sched.day_of(tz, day, now)

        for index, hour in enumerate(sched.hours_of(tz, self.date)):
            label = f"{hour.hour:02d}"
            style = discord.ButtonStyle.secondary
            disabled = hour.start is None or hour.start <= now
            if hour.start is not None:
                picked = hour.start in mine
                around = sched.covers(theirs, hour.start, hour.start + sched.HOUR)
                if picked:
                    style = discord.ButtonStyle.success
                    if around:
                        label += " \N{CHECK MARK}"
                elif around:
                    style = discord.ButtonStyle.primary
                elif side == "trainee":
                    disabled = True
            self.add_item(_HourButton(self, hour.start, label, style, disabled, row=index // 5))

        following = sched.day_of(tz, (day + 1) % sched.DAYS_AHEAD, now)
        arrow = "\N{BLACK RIGHT-POINTING TRIANGLE}"
        self.add_item(_NextDayButton(self, f"{sched.day_label(following)} {arrow}"))

    def content(self) -> str:
        member = f"<@{self.row['member_id']}>"
        if self.side == "trainer":
            key = (
                f"Tap the hours you are around. Green is you, blue is when {member} can do it, "
                "\N{CHECK MARK} is both."
            )
        else:
            key = (
                "Tap the hours you can do. Blue is when the instructor is around, green is "
                "you. Grey hours have no instructor."
            )
        return (
            f"**{sched.day_label(self.date)}** · your time ({sched.zone_label(self.tz)})"
            f" · day {self.day + 1} of {sched.DAYS_AHEAD}\n{key}"
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id


class _HourButton(discord.ui.Button):
    def __init__(
        self,
        grid: AvailabilityGrid,
        start: int | None,
        label: str,
        style: discord.ButtonStyle,
        disabled: bool,
        *,
        row: int,
    ) -> None:
        super().__init__(label=label, style=style, disabled=disabled, row=row)
        self.grid = grid
        self.start = start

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.grid.cog.toggle_hour(interaction, self.grid, self.start)


class _NextDayButton(discord.ui.Button):
    def __init__(self, grid: AvailabilityGrid, label: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=4)
        self.grid = grid

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.grid.cog.show_day(
            interaction, self.grid, (self.grid.day + 1) % sched.DAYS_AHEAD
        )


# ------------------------------------------------------ time zone, booking


class TimezoneView(discord.ui.View):
    """Pick a time zone. ``then`` is what to open afterwards: ``mark``, ``date`` or nothing."""

    def __init__(
        self, cog: Any, request_id: int, *, current: str | None, then: str | None
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.request_id = request_id
        self.then = then
        self.add_item(_TimezoneSelect(self, current))

    @staticmethod
    def content(current: str | None) -> str:
        if current:
            return f"Your time zone is **{sched.zone_label(current)}**. Pick another to change it."
        return (
            "Which time zone are you in? Discord does not tell the bot, so it asks once "
            "and remembers."
        )


class _TimezoneSelect(discord.ui.Select):
    def __init__(self, parent: TimezoneView, current: str | None) -> None:
        super().__init__(
            placeholder="Your time zone",
            options=[
                discord.SelectOption(label=label, value=tz, default=tz == current)
                for tz, label in sched.TIMEZONES
            ],
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.cog.save_timezone(interaction, self.parent_view, self.values[0])


class SetDateView(discord.ui.View):
    """Book a time the TI and trainee agreed without the grid.

    Day, hour and minute from lists rather than typed, so there is no 05/11
    to read two ways, all on the setter's own clock. Each pick redraws the
    view with the choice kept; the button books once all three are set.
    """

    #: Days offered, counting today. One select holds 25.
    DAYS = 25
    MINUTES = (0, 15, 30, 45)

    def __init__(
        self,
        cog: Any,
        request_id: int,
        *,
        tz: str,
        now: float,
        day: date | None = None,
        hour: int | None = None,
        minute: int | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.request_id = request_id
        self.tz = tz
        self.now = now
        self.day, self.hour, self.minute = day, hour, minute

        days = [sched.day_of(tz, offset, now) for offset in range(self.DAYS)]
        names = {0: "Today · ", 1: "Tomorrow · "}
        self.add_item(_DateSelect(self, "day", "Day", [
            discord.SelectOption(
                label=names.get(i, "") + sched.day_label(d), value=d.isoformat(), default=d == day
            )
            for i, d in enumerate(days)
        ]))
        self.add_item(_DateSelect(self, "hour", "Hour", [
            discord.SelectOption(label=f"{h:02d}", value=str(h), default=h == hour)
            for h in range(24)
        ]))
        self.add_item(_DateSelect(self, "minute", "Minute", [
            discord.SelectOption(label=f":{m:02d}", value=str(m), default=m == minute)
            for m in self.MINUTES
        ]))
        when = self.chosen()
        self.add_item(_SetDateButton(self, when))

    def chosen(self) -> int | None:
        """The picked time as Unix time, once day, hour and minute are all set."""
        if self.day is None or self.hour is None or self.minute is None:
            return None
        return sched.at(self.tz, self.day, self.hour, self.minute)

    def content(self) -> str:
        where = f"your time ({sched.zone_label(self.tz)})"
        when = self.chosen()
        if when is not None:
            label = sched.local_label(self.tz, when)
            return f"**{label}**, {where}. Everyone else sees it in theirs."
        if None not in (self.day, self.hour, self.minute):
            return "That time is skipped by a clock change. Pick another."
        return f"When does it run? Pick the day, hour and minute, in {where}."

    def redraw(self, **picked: Any) -> SetDateView:
        state = {"day": self.day, "hour": self.hour, "minute": self.minute, **picked}
        return SetDateView(self.cog, self.request_id, tz=self.tz, now=self.now, **state)


class _DateSelect(discord.ui.Select):
    def __init__(
        self, parent: SetDateView, field: str, placeholder: str,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(placeholder=placeholder, options=options)
        self.parent_view = parent
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        picked = date.fromisoformat(value) if self.field == "day" else int(value)
        view = self.parent_view.redraw(**{self.field: picked})
        self.parent_view.stop()
        await interaction.response.edit_message(content=view.content(), view=view)


class _SetDateButton(discord.ui.Button):
    def __init__(self, parent: SetDateView, when: int | None) -> None:
        super().__init__(
            label=f"Set for {sched.local_label(parent.tz, when)}" if when else "Set",
            style=discord.ButtonStyle.success,
            emoji="\N{CALENDAR}",
            disabled=when is None,
        )
        self.parent_view = parent
        self.when = when

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.cog.book_time(interaction, self.parent_view.request_id, self.when)


class BookView(discord.ui.View):
    """Pick a start time from the hours both sides are free."""

    def __init__(self, cog: Any, request_id: int, *, starts: list[int], tz: str) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.request_id = request_id
        self.add_item(_BookSelect(self, starts, tz))


class _BookSelect(discord.ui.Select):
    def __init__(self, parent: BookView, starts: list[int], tz: str) -> None:
        super().__init__(
            placeholder=f"Start time, in your time ({sched.zone_label(tz)})",
            options=[
                discord.SelectOption(label=sched.local_label(tz, when), value=str(when))
                for when in starts
            ],
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.cog.book_time(
            interaction, self.parent_view.request_id, int(self.values[0])
        )
