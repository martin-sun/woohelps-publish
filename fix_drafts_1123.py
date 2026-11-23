# -*- coding: utf-8 -*-
"""Apply independent-review fixes to 2026-11-23 activity drafts, then re-check."""
import json
from pathlib import Path

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/pending/2026-11-23"

def load(n):
    p = OUT / n
    return p, json.loads(p.read_text(encoding="utf-8"))

def save(p, d):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

# 01: skating hours 10:00-22:00 (21:45 is rental cutoff); drop storage claim; mark opening tentative
p, d = load("01-tor-ice-skating.json")
d["content_zh"] = d["content_zh"].replace(
    "市政广场溜冰场 11 月 28 日开门，整个冬天都能来滑：",
    "市政广场溜冰场 11 月 28 日（暂定）开门，整个冬天都能来滑：")
d["content_zh"] = d["content_zh"].replace(
    "冰场每天 10:00 开到 21:45，12 月 24 日和 25 日开到 22:00。天气太暖时冰面可能临时关闭，出门前最好先确认一下。",
    "冰场每天 10:00–22:00，租冰鞋到 21:45 截止。天气太暖时冰面可能临时关闭，出门前最好先确认一下。")
d["content_zh"] = d["content_zh"].replace(
    "每天 10:00–21:45（12月24、25日 10:00–22:00）",
    "每天 10:00–22:00（租冰鞋至 21:45）")
d["content_zh"] = d["content_zh"].replace(
    "；记得穿暖和、防滑的鞋，冰场边可以存放随身物品。",
    "；记得穿暖和、防滑的鞋。")
d["end_time_local"] = "2027-03-15 22:00"
d["review_notes"] = "日期、时段、票价、地址均出自 TodoCanada 详情页；滑冰时间为每天 10:00–22:00，21:45 为租冰鞋截止时间（复查纠正）；开幕日来源标注 tentative，正文已写“暂定”。"
save(p, d)
print("fixed 01")

# 02: drop floats/band claim and road-closure claim
p, d = load("02-tor-etobicoke-parade.json")
d["content_zh"] = d["content_zh"].replace(
    "花车、乐队和圣诞老人压轴出场。",
    "圣诞老人压轴出场。")
d["content_zh"] = d["content_zh"].replace(
    "小提示：游行期间沿线道路会封闭，开车去要提前查好绕行路线；坐 TTC 到湖滨一带再步行过去更方便。",
    "小提示：坐 TTC 到湖滨一带再步行过去很方便。")
d["review_notes"] = "日期、时间、路线、时长、免费均出自 TodoCanada 详情页；“35th St”与来源的 “Thirty-Fifth St” 为同一条街；花车/乐队与封路两处无来源细节已按复查删除。"
save(p, d)
print("fixed 02")

# 03: drop food claim
p, d = load("03-tor-made-market.json")
d["content_zh"] = d["content_zh"].replace(
    "逛累了就在市集里吃点东西，顺便跟摊主聊聊东西是怎么做的。",
    "慢慢逛、跟摊主聊聊，顺便把圣诞礼物清单划掉大半。")
d["review_notes"] = "日期、时段、地址、免费入场、75 多家摊主、免费合影时段均出自 TodoCanada 详情页；来源未提餐饮的相关句子已按复查删除。"
save(p, d)
print("fixed 03")

# 05: soften brunch/tea "ticketed" claim
p, d = load("05-reg-winter-wonderland.json")
d["content_zh"] = d["content_zh"].replace(
    "12 月 6 日的早午餐和下午茶是另外售票的专场，不含在这次的免费参观里。",
    "12 月 6 日的早午餐和下午茶是另外的专场活动，不含在这次的免费参观里。")
d["review_notes"] = "日期与 16:00–19:00 时段出自 Conexus 官网（TodoCanada 详情页未写时段，已用官网交叉核实）；12 月 6 日早午餐与下午茶是否售票未经核实，正文已按复查弱化为“另外的专场活动”。"
save(p, d)
print("fixed 05")

# 07: fee_amount stays 5.0 (hard check requires >0 for ticketed events); record why.
# 2026 Regina price genuinely unpublished (MRKT homepage: tickets drop 6 weeks before each market).
p, d = load("07-reg-holiday-edit.json")
d["review_notes"] = ("日期 Dec 12–13 与两天时段已用 theeventpulse（11 月 23 日确认）与 MRKT 官网报名页交叉核实；"
    "TodoCanada 正文里“Dec 13–14”与“星期六 Dec 13”为 2025 年旧文案，已按 2026 年改写正确。"
    "2026 年里贾纳票价未公布（MRKT 官网：门票每场提前六周发售），price 字段已写明待公布；"
    "fee_amount 5.0 为占位值（与 2025 年票价及主办方 2026 年萨斯卡通场票价一致），因硬检查要求收费活动 fee_amount 大于 0 而保留，未按复查建议改为 0。")
save(p, d)
print("fixed 07 (notes only)")

# 09: soften non-refundable claim
p, d = load("09-hal-neptune-carol.json")
d["content_zh"] = d["content_zh"].replace(
    "小提示：热门场次容易满，建议提前在线买票；票不可退，买之前先确认好日期和时间。",
    "小提示：热门场次容易满，建议提前在线买票；买之前先确认好日期和时间，并确认退换票政策。")
d["review_notes"] = ("演出日期、场次时间、场馆、演员与木偶师均出自 Neptune 官网与 Family Fun Halifax；"
    "票价 40–70 加元出自 TodoCanada 哈利法克斯圣诞指南；口罩专场与放松专场日期出自活动指南页。"
    "“票不可退”在官网未找到依据，已按复查弱化为购票前确认退换票政策。结束时间取末场开演时间（演出时长官网未公布）。")
save(p, d)
print("fixed 09")

# 10: remove 2025-edition story details (Sparkle / It Snowed / Meaghan Smith); use verified 2026 theme segments
p, d = load("10-hal-light-show.json")
d["content_zh"] = d["content_zh"].replace(
    "现在放的是节日主题“Everything is Sparkling”：讲的是小精灵 Sparkle 去圣诞老人作坊的奇幻旅程，配的是本地歌手 Meaghan Smith 写的歌《It Snowed》。12 月 4 日到 7 日换成哈利法克斯爆炸纪念主题，12 月 27 日起是冬季主题，12 月 30 日到 1 月 2 日是新年主题。",
    "灯光秀分四个主题时段：节日主题 11 月 14 日到 12 月 26 日，哈利法克斯爆炸纪念主题 12 月 4 日到 7 日，冬季主题 12 月 27 日到 1 月 17 日，新年主题 12 月 30 日到 1 月 2 日。")
d["review_notes"] = ("演出已于 11 月 6 日开幕，开始时间按进行中活动惯例（PVM 市集先例）设为发布次日 11 月 24 日 17:00；"
    "日期与主题分段出自 Tourism Nova Scotia 2026 年指南，17:00–23:00 出自市中心商会页面。"
    "商会页面上的 Sparkle 故事与《It Snowed》为 2025 年版内容，2026 年未确认沿用，已按复查删除，改用已验证的 2026 年主题分段。")
save(p, d)
print("fixed 10")

print("DONE fixes")
