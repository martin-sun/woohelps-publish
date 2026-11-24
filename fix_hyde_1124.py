import json
p = "data/pending/2026-11-24/11-lon-hyde-market.json"
d = json.load(open(p))
old = "晚上 6 点半点亮社区圣诞树，7 点整放烟花，市集周日上午继续。"
new = "晚上 6 点半点亮社区圣诞树，7 点整放烟花。市集上本地摊位不少，正好把圣诞礼物清单划掉几项，周日上午继续。"
assert old in d["content_zh"]
d["content_zh"] = d["content_zh"].replace(old, new)
json.dump(d, open(p, "w"), ensure_ascii=False, indent=1)
print("new length:", len(d["content_zh"]))
