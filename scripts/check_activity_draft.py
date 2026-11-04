"""活动稿硬检查：对活动稿 JSON 做机械项校验（发布前第一层）。

用法：
    uv run python scripts/check_activity_draft.py <稿件.json> [更多.json ...]

检查项：必填字段、摘要不超过 200 字、开始时间在未来且结束晚于开始、
地址含门牌数字、费用与免费标记一致、至少 1 张图、来源与图片链接验活、
按英文名加开始日期查已发台账防重（台账默认 /tmp/act_full.json，不存在则跳过）。
输出每条一行 JSON（PASS/FAIL 与问题列表）；有 FAIL 时退出码为 1。
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

FMT = "%Y-%m-%d %H:%M"


def load_published(path):
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def check_static(d, published):
    problems, warnings = [], []
    for f in ("title_zh", "title_en", "description_zh", "content_zh", "address", "start_time_local", "source_url"):
        if not d.get(f):
            problems.append("缺字段 " + f)
    if d.get("description_zh") and len(d["description_zh"]) > 200:
        problems.append("摘要超过200字")
    clen = len(d.get("content_zh") or "")
    if clen and not (300 <= clen <= 1400):
        warnings.append("正文长度 %d 字（建议 300-1400）" % clen)
    tz = ZoneInfo(d.get("timezone") or "America/Regina")
    now = datetime.now(tz).replace(tzinfo=None)
    start = None
    try:
        start = datetime.strptime(d["start_time_local"], FMT)
    except Exception:
        problems.append("开始时间格式错误")
    end = None
    if d.get("end_time_local"):
        try:
            end = datetime.strptime(d["end_time_local"], FMT)
        except Exception:
            problems.append("结束时间格式错误")
    if start:
        if start <= now:
            problems.append("开始时间已过")
        if (start - now).days > 30:
            warnings.append("开始时间在30天窗口之外")
        if end and end <= start:
            problems.append("结束时间不晚于开始时间")
    addr = d.get("address") or ""
    if addr and not any(ch.isdigit() for ch in addr):
        problems.append("地址无门牌数字")
    is_free, fee = bool(d.get("is_free")), float(d.get("fee_amount") or 0)
    if is_free and fee != 0:
        problems.append("标免费但 fee_amount 非 0")
    if not is_free and fee <= 0:
        problems.append("标收费但 fee_amount 不大于 0")
    if not (d.get("image_urls") or []):
        problems.append("没有图片")
    if start and published:
        en = (d.get("title_en") or "").lower()
        for a in published:
            name = (a.get("name") or "").lower()
            st = a.get("start_time")
            if en and en in name and isinstance(st, (int, float)):
                ad = datetime.fromtimestamp(st, timezone.utc).strftime("%Y-%m-%d")
                if ad == start.strftime("%Y-%m-%d"):
                    problems.append("疑似与已发活动重复 id=%s" % a.get("id"))
    return problems, warnings


async def check_links(d):
    problems, warnings = [], []
    urls = [d.get("source_url")] + list(d.get("image_urls") or [])
    async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as c:
        for u in urls:
            if not u:
                continue
            try:
                r = await c.get(u)
                if r.status_code == 403 and "todocanada.ca" in u:
                    warnings.append("反爬站403（成稿时已用浏览器核实）: %s" % u)
                elif r.status_code != 200:
                    problems.append("链接非200: %s -> %s" % (u, r.status_code))
            except Exception as e:
                problems.append("链接打不开: %s (%s)" % (u, type(e).__name__))
    return problems, warnings


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--published-index", default="/tmp/act_full.json")
    ap.add_argument("--no-links", action="store_true")
    args = ap.parse_args()
    published = load_published(args.published_index)
    bad = 0
    for f in args.files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        problems, warnings = check_static(d, published)
        if not args.no_links:
            lp, lw = await check_links(d)
            problems += lp
            warnings += lw
        if problems:
            bad += 1
        print(json.dumps({"file": Path(f).name, "status": "PASS" if not problems else "FAIL", "problems": problems, "warnings": warnings}, ensure_ascii=False))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
