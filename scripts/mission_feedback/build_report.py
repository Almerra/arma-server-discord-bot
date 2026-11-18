"""
Stage 3: Merge the exported missions with extracted themes into graphable CSVs and charts.

Inputs (output/):
  missions_raw.json  - from export.py
  themes.json        - optional, theme analysis:
      {"taxonomy": [{"theme": str, "description": str}],
       "missions": {"<thread_id>": {"summary": str,
                                    "themes": [{"theme": str, "sentiment": "positive"|"negative", "mentions": int}]}}}
Outputs (output/):
  missions.csv           - one row per mission
  mission_themes.csv     - one row per mission/theme/sentiment (long format)
  mission_feedback.csv   - one row per feedback item
  charts/*.png
"""
import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import date

from export import CATEGORIES, DEFAULT_OUT, overall_avg, rating_stats

ROLLING_WINDOW = 5


def load_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def build_rows(missions, themes):
    mission_rows, theme_rows, feedback_rows = [], [], []
    for m in missions:
        stats = {c: rating_stats(m["ratings"][c]) for c in CATEGORIES}
        t = themes.get(m["thread_id"], {})
        mission_themes = t.get("themes", [])
        pos = [x["theme"] for x in mission_themes if x.get("sentiment") == "positive"]
        neg = [x["theme"] for x in mission_themes if x.get("sentiment") == "negative"]
        mission_rows.append([
            m["date_played"], m["date_source"], m["mission"], m["author"], m["map"], m["play_count"],
            *[v for c in CATEGORIES for v in stats[c]], overall_avg(stats),
            len(m["feedback"]), "; ".join(pos), "; ".join(neg), t.get("summary", ""), m["thread_url"],
        ])
        for x in mission_themes:
            theme_rows.append([m["date_played"], m["mission"], x["theme"], x.get("sentiment", ""), x.get("mentions", 1)])
        for fb in m["feedback"]:
            feedback_rows.append([m["date_played"], m["mission"], fb["source"], fb["user"], fb["created_at"],
                                  fb["good"], fb["improve"], fb["other"], fb["text"]])
    return mission_rows, theme_rows, feedback_rows


def plot_ratings(missions, path, plt):
    rows = []
    for m in missions:
        stats = {c: rating_stats(m["ratings"][c]) for c in CATEGORIES}
        overall = overall_avg(stats)
        if overall != "" and m["date_played"]:
            rows.append((date.fromisoformat(m["date_played"]), {c: stats[c][0] for c in CATEGORIES}, overall))
    if not rows:
        print("No rated missions, skipping ratings chart")
        return
    dates = [r[0] for r in rows]
    fig, ax = plt.subplots(figsize=(14, 6))
    for c, label in zip(CATEGORIES, ("Concept", "Zeus", "NCO / Leadership")):
        pts = [(d, s[c]) for d, s, _ in rows if s[c] != ""]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", linewidth=1, alpha=0.5, label=label)
    overall = [r[2] for r in rows]
    rolling = [sum(overall[max(0, i - ROLLING_WINDOW + 1):i + 1]) / len(overall[max(0, i - ROLLING_WINDOW + 1):i + 1]) for i in range(len(overall))]
    ax.plot(dates, overall, marker="s", color="black", linewidth=1.5, label="Overall")
    ax.plot(dates, rolling, color="red", linewidth=2.5, linestyle="--", label=f"Overall ({ROLLING_WINDOW}-mission rolling avg)")
    ax.set_ylim(1, 5.1)
    ax.set_ylabel("Average rating (1-5)")
    ax.set_title("Mission ratings over time")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_top_themes(theme_rows, path, plt, top_n=20):
    pos, neg = Counter(), Counter()
    for _, _, theme, sentiment, mentions in theme_rows:
        (pos if sentiment == "positive" else neg)[theme] += int(mentions or 1)
    totals = Counter({t: pos[t] + neg[t] for t in set(pos) | set(neg)})
    themes = [t for t, _ in totals.most_common(top_n)][::-1]
    fig, ax = plt.subplots(figsize=(12, max(4, 0.4 * len(themes))))
    ax.barh(themes, [-neg[t] for t in themes], color="#d9534f", label="Negative")
    ax.barh(themes, [pos[t] for t in themes], color="#5cb85c", label="Positive")
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("Mentions (negative ← | → positive)")
    ax.set_title("Most common feedback themes")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_themes_by_month(theme_rows, path, plt, top_n=15):
    counts = {"negative": defaultdict(Counter), "positive": defaultdict(Counter)}
    totals = Counter()
    for d, _, theme, sentiment, mentions in theme_rows:
        if not d or sentiment not in counts:
            continue
        counts[sentiment][theme][d[:7]] += int(mentions or 1)
        totals[theme] += int(mentions or 1)
    months = sorted({mo for s in counts.values() for c in s.values() for mo in c})
    themes = [t for t, _ in totals.most_common(top_n)]
    fig, axes = plt.subplots(1, 2, figsize=(max(12, 1.2 * len(months) + 6), max(4, 0.45 * len(themes))), sharey=True)
    for ax, sentiment, cmap in zip(axes, ("negative", "positive"), ("Reds", "Greens")):
        grid = [[counts[sentiment][t][mo] for mo in months] for t in themes]
        im = ax.imshow(grid, aspect="auto", cmap=cmap)
        ax.set_xticks(range(len(months)), months, rotation=45, ha="right")
        ax.set_yticks(range(len(themes)), themes)
        ax.set_title(f"{sentiment.capitalize()} theme mentions per month")
        fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--no-charts", action="store_true")
    args = p.parse_args()

    raw = load_json(os.path.join(args.out, "missions_raw.json"))
    if raw is None:
        raise SystemExit("missions_raw.json not found - run export.py first")
    missions = sorted(raw["missions"], key=lambda m: m["date_played"] or "")
    theme_data = load_json(os.path.join(args.out, "themes.json"), {"missions": {}})
    themes = theme_data.get("missions", {})
    if not themes:
        print("themes.json not found or empty - theme columns will be blank")

    mission_rows, theme_rows, feedback_rows = build_rows(missions, themes)
    write_csv(os.path.join(args.out, "missions.csv"),
              ["date_played", "date_source", "mission", "author", "map", "play_count",
               *[f"{c}_{s}" for c in CATEGORIES for s in ("avg", "votes")], "overall_avg",
               "feedback_count", "positive_themes", "negative_themes", "summary", "thread_url"], mission_rows)
    write_csv(os.path.join(args.out, "mission_themes.csv"),
              ["date_played", "mission", "theme", "sentiment", "mentions"], theme_rows)
    write_csv(os.path.join(args.out, "mission_feedback.csv"),
              ["date_played", "mission", "source", "user", "created_at", "good", "improve", "other", "text"], feedback_rows)
    print(f"Wrote {len(mission_rows)} missions, {len(theme_rows)} theme rows, {len(feedback_rows)} feedback rows to {args.out}")

    if args.no_charts:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts = os.path.join(args.out, "charts")
    os.makedirs(charts, exist_ok=True)
    plot_ratings(missions, os.path.join(charts, "ratings_over_time.png"), plt)
    if theme_rows:
        plot_top_themes(theme_rows, os.path.join(charts, "top_themes.png"), plt)
        plot_themes_by_month(theme_rows, os.path.join(charts, "themes_by_month.png"), plt)
    print(f"Charts written to {charts}")


if __name__ == "__main__":
    main()
