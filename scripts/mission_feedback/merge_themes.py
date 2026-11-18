"""
Stage 2 helper: merge per-batch theme files (output/themes_batch_*.json) into output/themes.json.

Each batch file maps thread_id -> {"summary": str, "themes": [{"theme", "sentiment", "mentions"}]}.
Theme names are validated against TAXONOMY; anything unknown is reported so it can be renamed
via RENAMES or added to TAXONOMY before re-running.
"""
import argparse
import glob
import json
import os
import re
from collections import Counter

from export import DEFAULT_OUT

TAXONOMY = [
    "Enemy numbers & difficulty",
    "Enemy durability & AI behaviour",
    "Pacing & downtime",
    "Objective design",
    "Travel & transport between objectives",
    "Zeus execution & adaptability",
    "Concept, story & RP/immersion",
    "Server performance / FPS",
    "Briefing & intel clarity",
    "Platoon command & comms",
    "Squad/fireteam leadership & teamwork",
    "Support tasking (CAS/DT/logi)",
    "Enemy air & air defence",
    "Budget, supplies & resupply",
    "Engagement range",
    "Bugs & mod/script issues",
    "Friendly fire & PID",
    "Gear & loadout restrictions",
    "Scaling to attendance/squad sizes",
    "Map, weather & night setting",
    "Enemy counterattacks, QRFs & defensive phases",
]

# Maps proposed/variant theme names onto the taxonomy, e.g. {"NEW: Vehicle availability": "Budget, supplies & resupply"}
RENAMES: dict[str, str] = {}

CODE_RE = re.compile(r"^(\d+)([+-])(\d+)$")


def parse_compact(path):
    """Parse `thread_id|summary|codes` lines, where codes are e.g. `8-3 21+2` (theme N, +/- sentiment, M mentions)."""
    missions = {}
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            thread_id, summary, codes = line.split("|", 2)
            themes = []
            for code in codes.split():
                m = CODE_RE.match(code)
                if not m or not 1 <= int(m.group(1)) <= len(TAXONOMY):
                    raise SystemExit(f"{path}:{lineno}: bad theme code {code!r}")
                themes.append({
                    "theme": TAXONOMY[int(m.group(1)) - 1],
                    "sentiment": "positive" if m.group(2) == "+" else "negative",
                    "mentions": int(m.group(3)),
                })
            missions[thread_id.strip()] = {"summary": summary.strip(), "themes": themes}
    return missions


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=DEFAULT_OUT)
    args = p.parse_args()

    missions = {}
    for path in sorted(glob.glob(os.path.join(args.out, "themes_batch_*.json"))):
        with open(path, encoding="utf-8") as f:
            missions.update(json.load(f))
    for path in sorted(glob.glob(os.path.join(args.out, "themes_batch_*.txt"))):
        missions.update(parse_compact(path))
    if not missions:
        raise SystemExit("No themes_batch_*.json / .txt files found")

    unknown = Counter()
    for data in missions.values():
        merged: dict[tuple, int] = {}
        for t in data.get("themes", []):
            name = RENAMES.get(t["theme"], t["theme"])
            if name not in TAXONOMY:
                unknown[name] += 1
            key = (name, t["sentiment"])
            merged[key] = merged.get(key, 0) + int(t.get("mentions", 1) or 1)
        data["themes"] = [{"theme": n, "sentiment": s, "mentions": c} for (n, s), c in merged.items()]

    with open(os.path.join(args.out, "themes.json"), "w", encoding="utf-8") as f:
        json.dump({"taxonomy": TAXONOMY, "missions": missions}, f, indent=2, ensure_ascii=False)
    print(f"Merged {len(missions)} missions into themes.json")
    if unknown:
        print("Themes not in TAXONOMY (add to RENAMES or TAXONOMY and re-run):")
        for name, n in unknown.most_common():
            print(f"  {n:3d}  {name}")


if __name__ == "__main__":
    main()
