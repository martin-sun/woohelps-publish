import json
from pathlib import Path

OUT = Path("data/pending/2026-11-24")

def load(n):
    p = OUT / n
    return p, json.loads(p.read_text(encoding="utf-8"))

def save(p, d):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

# 03: koji marinade -> rice koji
p, d = load("03-van-japan-market.json")
assert "味噌腌料" in d["content_zh"]
d["content_zh"] = d["content_zh"].replace("味噌腌料", "米曲腌料")
save(p, d)
print("03 fixed:", len(d["content_zh"]))

# 02: early bird +fees
p, d = load("02-van-makeit.json")
assert "早鸟票 10 加元起，现场票 15 加元" in d["content_zh"]
d["content_zh"] = d["content_zh"].replace("早鸟票 10 加元起，现场票 15 加元", "早鸟票 10 加元起（另加手续费），现场票 15 加元")
d["price"] = d["price"].replace("早鸟票 10 加元起，现场票 15 加元", "早鸟票 10 加元起（另加手续费），现场票 15 加元")
save(p, d)
print("02 fixed:", d["price"])

# 05: soften unsourced claim
p, d = load("05-edm-streetcar.json")
assert "座位有限，每年都抢得很快。" in d["content_zh"]
d["content_zh"] = d["content_zh"].replace("座位有限，每年都抢得很快。", "座位有限，建议提前订票。")
save(p, d)
print("05 fixed")

# 08: warm cider
p, d = load("08-edm-rutherford.json")
assert "热苹果酒" in d["content_zh"]
d["content_zh"] = d["content_zh"].replace("热苹果酒", "热苹果西打")
save(p, d)
print("08 fixed")

# 12: use 2026 poster
p, d = load("12-lon-argyle.json")
d["image_urls"] = [str(OUT / "lon-argyle" / "argyle-2026.png")]
d["images_note"] = "原图1张（TodoCanada 来源页 2026 年海报）"
save(p, d)
print("12 fixed:", d["image_urls"])

# 13: drop unsourced claim
p, d = load("13-lon-rudolph.json")
assert "周六下午场最抢手，建议提前订票。" in d["content_zh"]
d["content_zh"] = d["content_zh"].replace("周六下午场最抢手，建议提前订票。", "建议提前订票。")
save(p, d)
print("13 fixed:", len(d["content_zh"]))
print("ALL FIXES APPLIED")
