import json
WIN_START, WIN_END = "2026-11-26", "2026-12-24"
for slug in ["vancouver", "edmonton", "london"]:
    d = json.load(open("data/sweep/2026-11-24-activities/van-edm-lon/%s-list.json" % slug))
    seen = {}
    for p in d["pages"]:
        for e in p["ld"]:
            seen[(e["name"], e["url"])] = e
    print("=====", slug, "unique:", len(seen))
    for e in sorted(seen.values(), key=lambda x: x["start"]):
        s = e["start"]
        if WIN_START <= s <= WIN_END:
            urltail = e["url"].split("/event/")[-1][:70]
            print("  %s..%s | %s | %s | %s | %s" % (s, e["end"], e["name"][:70], e["venue"], e["locality"], urltail))
