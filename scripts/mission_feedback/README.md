# Mission Feedback Export

A one-off pipeline. It pulls every played mission from the MisDev forum, aggregates the three rating polls, collects feedback, tags it with themes, and outputs graphable CSVs and charts.

## Setup

```powershell
cd scripts\mission_feedback
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

The script uses the bot `TOKEN` from the repo `.env` and the selected battalion's
`mission_development` section of `config.json`. Pass `--battalion <key>` or set
`discord.default_battalion`. The bot needs the **Message Content** privileged
intent enabled in the Discord developer portal.

## Stages

1. **Export.** `.\.venv\Scripts\python export.py [--limit N] [--since YYYY-MM-DD]`
   - Reads all active and archived forum threads.
   - A thread counts as a played mission if it has the `played` tag or contains the rating polls ("Rate the mission concept / zeuses / NCOs").
   - Writes `output/missions_raw.json` and `output/missions_ratings.csv`.
   - `date_played` is the date the polls were posted. Older threads without polls fall back to the last message date, and `date_source` says which was used.
   - Votes are summed if a thread was played more than once.
   - Feedback is the `/mission_feedback` form posts plus members' messages posted after the polls.
2. **Themes.** Feedback is tagged against a fixed list of themes (see `TAXONOMY` in `merge_themes.py`), each marked positive or negative with a mention count.
   - Tagging was done once with an AI assistant, which produced `output/themes_batch_*.json` or compact `output/themes_batch_*.txt` files. The compact format has one line per mission, `thread_id|summary|codes`. For example, `8-3 21+2` means theme 8 is negative with 3 mentions and theme 21 is positive with 2. Theme numbers are 1-based positions in `TAXONOMY`.
   - `.\.venv\Scripts\python merge_themes.py` merges these into `output/themes.json` and reports any theme names that aren't in the list.
3. **Report.** `.\.venv\Scripts\python build_report.py` writes:
   - `missions.csv`: one row per mission with date, author, map, average and vote count for each poll, overall average, positive and negative themes, and a summary.
   - `mission_themes.csv`: one row per mission, theme and sentiment (long format, good for pivot charts).
   - `mission_feedback.csv`: every feedback item.
   - `charts/ratings_over_time.png`, `charts/top_themes.png`, `charts/themes_by_month.png`.

`output/` contains member names and feedback and is gitignored.
