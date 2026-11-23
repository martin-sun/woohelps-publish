import json
from pathlib import Path
from collections import OrderedDict

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/sweep/2026-11-23-activities/tor-reg-hal"
WS, WE = "2026-11-25", "2026-12-23"

for slug in ["toronto", "regina", "halifax"]:
    data = json.loads((OUT / (slug + "-list.json")).read_text())
    seen = OrderedDict()
    for pg in data["pages"]:
        for ev in pg["ld"]:
            key = (ev["name"], ev["start"], ev["venue"])
            if key not in seen:
                seen[key] = ev
    cands = [e for e in seen.values() if WS <= e["start"] <= WE]
    print("\n=== " + slug + ": unique=" + str(len(seen)) + " in-window=" + str(len(cands)) + " ===")
    for e in cands:
        print("- " + e["start"] + ".." + e["end"] + " | " + e["name"] + " | " + e["venue"] + " | " + e["street"] + ", " + e["locality"] + " | " + e["url"])
