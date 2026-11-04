"""发布前硬检查：对一条待发布稿件（JSON）做机械项逐条判定。

用法：
    uv run python scripts/check_news_draft.py --input item.json [--check-links]

检查项（见 .claude/skills/woohelps_news_picking/SKILL.md「发布前检查」）：
空标签、图片与封面重复、封面缺失、background-color 红线、白名单外样式属性、
分节编号连续；--check-links 时再逐个验活来源与图片链接。
输出一行 JSON：{"ok": bool, "errors": [...], "warnings": [...]}；有 error 时退出码 1。
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ALLOWED_STYLE_PROPS = {
    "color", "background-color", "font-size", "font-weight", "font-style",
    "line-height", "text-align", "text-decoration", "white-space",
}
CN_NUM = "一二三四五六七八九十"


def plain(fragment: str) -> str:
    return re.sub(r"<[^>]+>", "", fragment).strip()


def check(item: dict, check_links: bool) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    html = item.get("html") or ""
    if not html and item.get("paragraphs"):
        from src.publisher.news import build_news_html
        html = build_news_html(item)
    if not html:
        errors.append("稿件没有正文 html")

    # 空标签（无文字且无图片的 li / p）
    for tag in ("li", "p"):
        for m in re.finditer(rf"<{tag}(\s[^>]*)?>.*?</{tag}>", html, re.S):
            frag = m.group(0)
            if not plain(frag) and "<img" not in frag:
                errors.append(f"空 <{tag}> 标签（只有换行或空白）")

    # 图片重复与封面
    srcs = re.findall(r"<img[^>]+src=\"([^\"]+)\"", html)
    cover = item.get("cover_image_url") or ""
    if not cover:
        errors.append("缺少封面图 cover_image_url")
    if cover and cover in srcs:
        errors.append("封面图与正文图片重复（同一张图出现两次）")
    seen: set[str] = set()
    for src in srcs:
        if src in seen:
            errors.append(f"正文内图片重复：{src}")
        seen.add(src)

    # 样式红线
    bg = len(re.findall(r"background-color\s*:", html))
    if bg:
        errors.append(f"出现 background-color {bg} 处（正文不用底色）")
    bad_props: set[str] = set()
    for style in re.findall(r"style=\"([^\"]*)\"", html):
        for decl in style.split(";"):
            if ":" in decl:
                prop = decl.split(":", 1)[0].strip().lower()
                if prop and prop not in ALLOWED_STYLE_PROPS:
                    bad_props.add(prop)
    if bad_props:
        errors.append(f"白名单外样式属性：{, .join(sorted(bad_props))}")

    # 分节编号连续
    heads = [plain(m) for m in re.findall(r"<h2>(.*?)</h2>", html, re.S)]
    nums = [CN_NUM.index(t[0]) + 1 for t in heads if t and t[0] in CN_NUM and t[1:2] == "、"]
    if nums and nums != list(range(1, len(nums) + 1)):
        errors.append(f"分节编号不连续：{nums}")

    # 链接验活（可选）
    if check_links:
        import urllib.request
        urls = set(re.findall(r"href=\"(https?://[^\"]+)\"", html)) | set(srcs)
        if item.get("source_url"):
            urls.add(item["source_url"])
        if cover:
            urls.add(cover)
        for url in sorted(urls):
            try:
                req = urllib.request.Request(url, method="HEAD",
                                             headers={"User-Agent": "Mozilla/5.0"})
                urllib.request.urlopen(req, timeout=8)
            except Exception as exc:
                warnings.append(f"链接可能不可达：{url}（{exc.__class__.__name__}）")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--check-links", action="store_true")
    args = parser.parse_args()
    item = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = check(item, args.check_links)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
