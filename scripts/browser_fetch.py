"""新闻线浏览器兜底工具：用项目 stealth Playwright 打开官网、原文页与图片。

四家官网（CBC / CTV / Financial Post / Daily Hive）对普通程序抓取屏蔽，
文本抓取打不开、图片直链有时返 403，搜索索引又有延迟和关键词盲区。
本工具复用 src/scrapers/browser.py 的 stealth 浏览器，作为新闻线
扫站发现、读原文、取主图、取封面字节的标准兜底。

用法：
    uv run python scripts/browser_fetch.py sweep --out <目录>
    uv run python scripts/browser_fetch.py article --url <原文URL> --out <目录>
    uv run python scripts/browser_fetch.py image --url <图片URL> --out <文件> [--referer <来源页URL>]

sweep:
    依次打开四家官网首页与重点版块页（含纵深版块），从页面 DOM 提取
    文章链接（标题 + URL），按来源/版块汇总去重后写 <目录>/sweep.json，
    stdout 打印一行 JSON 摘要（各来源条数、总条数、没打开的版块）。

article:
    打开页面，提取正文纯文本与主图地址（优先 og:image，其次页面内最大图）。
    正文写入 <目录>/article.txt，元信息写入 <目录>/meta.json，
    stdout 打印一行 JSON 摘要（标题、正文字数、主图 URL）。

image:
    用 stealth 浏览器上下文请求图片并把字节存到 <文件>；直接请求失败时，
    若给了 --referer，先打开来源页建立语境后在同一上下文内重试。
    stdout 打印一行 JSON（字节数、content-type、文件路径）。

退出码 0 表示成功；失败时 stdout 打印 {"ok": false, "error": ...} 并以 1 退出。
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

# ── sweep 版块清单：（来源，版块名，URL）。打不开的跳过并在结果里注明。──
SWEEP_PAGES = [
    ("CBC News", "首页", "https://www.cbc.ca/"),
    ("CBC News", "政治", "https://www.cbc.ca/news/politics"),
    ("CBC News", "加拿大", "https://www.cbc.ca/news/canada"),
    ("CBC News", "商业", "https://www.cbc.ca/news/business"),
    ("CBC News", "消费者", "https://www.cbc.ca/news/consumer"),
    ("CTV News", "首页", "https://www.ctvnews.ca/"),
    ("CTV News", "全国", "https://www.ctvnews.ca/canada"),
    ("CTV News", "消费者维权", "https://www.ctvnews.ca/consumer"),
    ("Financial Post", "首页", "https://financialpost.com/"),
    ("Financial Post", "新闻", "https://financialpost.com/news"),
    ("Financial Post", "个人理财", "https://financialpost.com/personal-finance"),
    ("Financial Post", "Posthaste", "https://financialpost.com/news/posthaste"),
    ("Daily Hive Canada", "加拿大首页", "https://dailyhive.com/canada"),
    ("Daily Hive Canada", "加拿大新闻列表", "https://dailyhive.com/canada/news"),
]

EXTRACT_JS = r"""
() => {
  const metaContent = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.getAttribute('content') || '').trim() : '';
  };
  const ogImage = metaContent('meta[property="og:image"]') || metaContent('meta[name="twitter:image"]');
  const title = metaContent('meta[property="og:title"]') || document.title || '';
  const description = metaContent('meta[property="og:description"]') || metaContent('meta[name="description"]') || '';

  // 正文：常见正文容器里取 innerText 最长的一块
  const candidates = document.querySelectorAll(
    'article, main, [itemprop="articleBody"], [data-testid="story-body"], .story, .article-body, .l-article'
  );
  let text = '';
  for (const el of candidates) {
    const t = (el.innerText || '').trim();
    if (t.length > text.length) text = t;
  }
  if (text.length < 800) {
    const bodyText = (document.body ? document.body.innerText : '') || '';
    if (bodyText.trim().length > text.length) text = bodyText.trim();
  }

  // 页面内最大图（按自然尺寸面积，过滤小图标）
  let largest = '', bestArea = 0;
  for (const img of document.images) {
    const w = img.naturalWidth || 0, h = img.naturalHeight || 0;
    const src = img.currentSrc || img.src || '';
    if (src && w >= 300 && h >= 180 && w * h > bestArea) {
      largest = src; bestArea = w * h;
    }
  }
  return { title, description, og_image: ogImage, largest_image: largest, text };
}
"""

EXTRACT_LINKS_JS = r"""
() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href || '';
    if (!href.startsWith('http')) continue;
    let text = (a.innerText || a.getAttribute('aria-label') || a.title || '').trim().replace(/\s+/g, ' ');
    out.push({ url: href, title: text });
  }
  return out;
}
"""

_BAD_PARTS = (
    "/video", "/live", "/author", "/tag/", "/topics", "newsletter",
    "/photos", "/galleries", "signup", "/podcasts", "/radio", "/tv/",
)
_GENERIC_TITLES = {"read more", "learn more", "watch", "listen", "more", "see more"}


def _norm_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")


def is_article_url(source: str, url: str) -> bool:
    u = _norm_url(url)
    low = u.lower()
    if any(b in low for b in _BAD_PARTS):
        return False
    if "cbc.ca" in low:
        # CBC 文章 URL 形如 /news/<版块>/<slug>-1.7xxxxxxx
        return bool(re.search(r"cbc\.ca/news/.+-\d+\.\d+$", low)) or bool(
            re.search(r"cbc\.ca/(news|player)/[a-z-]+/[a-z0-9-]{12,}$", low)
        )
    if "ctvnews.ca" in low:
        return "/article/" in low or bool(re.search(r"-\d+\.\d+$", low))
    if "financialpost.com" in low:
        path = low.split("financialpost.com", 1)[1]
        if path in ("/news", "/personal-finance", "/news/posthaste"):
            return False
        return len(path) > 30 and path.count("-") >= 2
    if "dailyhive.com" in low:
        return bool(re.search(r"dailyhive\.com/[a-z-]+/[a-z0-9-]{12,}$", low))
    return False


async def cmd_sweep(out_dir: str) -> int:
    settings = get_settings()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items: dict[str, dict] = {}
    pages_info = []
    by_source: dict[str, int] = {}
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings)
        page = await ctx.new_page()
        try:
            for source, section, url in SWEEP_PAGES:
                info = {"source": source, "section": section, "url": url,
                        "status": None, "links": 0, "new_items": 0, "error": ""}
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    info["status"] = resp.status if resp else None
                    if resp is None or resp.status >= 400:
                        info["error"] = f"http {info['status']}"
                        pages_info.append(info)
                        continue
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    try:
                        await page.mouse.wheel(0, 4000)
                        await page.wait_for_timeout(800)
                    except Exception:
                        pass
                    raw_links = await page.evaluate(EXTRACT_LINKS_JS)
                    info["links"] = len(raw_links)
                    for link in raw_links:
                        title = (link.get("title") or "").strip()
                        href = link.get("url") or ""
                        if len(title) < 15 or title.lower() in _GENERIC_TITLES:
                            continue
                        if not is_article_url(source, href):
                            continue
                        key = _norm_url(href)
                        if key in items:
                            continue
                        items[key] = {"source": source, "section": section,
                                      "title": title, "url": key}
                        info["new_items"] += 1
                except Exception as e:
                    info["error"] = f"{type(e).__name__}: {e}"[:200]
                pages_info.append(info)
                await asyncio.sleep(1)
        finally:
            await ctx.close()
            await browser.close()

    item_list = sorted(items.values(), key=lambda x: (x["source"], x["title"]))
    for it in item_list:
        by_source[it["source"]] = by_source.get(it["source"], 0) + 1
    failed_pages = [f'{pi["source"]}/{pi["section"]}' for pi in pages_info if pi["error"]]
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(item_list),
        "by_source": by_source,
        "pages": pages_info,
        "items": item_list,
    }
    (out / "sweep.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "total": len(item_list),
        "by_source": by_source,
        "pages_failed": failed_pages,
        "sweep_json": str(out / "sweep.json"),
    }, ensure_ascii=False))
    return 0


async def cmd_article(url: str, out_dir: str) -> int:
    settings = get_settings()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings)
        page = await ctx.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            status = resp.status if resp else None
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            # 触发懒加载图片/正文
            try:
                await page.mouse.wheel(0, 3000)
                await page.wait_for_timeout(1200)
                await page.mouse.wheel(0, -3000)
                await page.wait_for_timeout(600)
            except Exception:
                pass
            data = await page.evaluate(EXTRACT_JS)
            final_url = page.url
        finally:
            await ctx.close()
            await browser.close()

    text = (data.get("text") or "").strip()
    og_image = data.get("og_image") or ""
    largest = data.get("largest_image") or ""
    if og_image:
        og_image = urljoin(final_url, og_image)
    if largest:
        largest = urljoin(final_url, largest)
    main_image = og_image or largest

    (out / "article.txt").write_text(text, encoding="utf-8")
    meta = {
        "url": url,
        "final_url": final_url,
        "status": status,
        "title": data.get("title") or "",
        "description": data.get("description") or "",
        "main_image": main_image,
        "og_image": og_image,
        "largest_image": largest,
        "text_chars": len(text),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = bool(text) and status is not None and status < 400
    print(json.dumps({
        "ok": ok,
        "status": status,
        "title": meta["title"],
        "text_chars": meta["text_chars"],
        "main_image": main_image,
        "article_txt": str(out / "article.txt"),
        "meta_json": str(out / "meta.json"),
    }, ensure_ascii=False))
    return 0 if ok else 1


async def _image_via_page(ctx, url: str):
    page = await ctx.new_page()
    try:
        resp = await page.goto(url, timeout=60000)
        if resp and resp.ok:
            body = await resp.body()
            return body, resp.headers.get("content-type"), "page.goto"
        return None, None, f"page.goto status {resp.status if resp else None}"
    finally:
        await page.close()


async def cmd_image(url: str, out_path: str, referer: str | None) -> int:
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings)
        try:
            body, ctype, how = await _image_via_page(ctx, url)
            if body is None and referer:
                # 先打开来源页建立 cookie/referer 语境，再在同上下文重试
                page = await ctx.new_page()
                try:
                    await page.goto(referer, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(1000)
                finally:
                    await page.close()
                r = await ctx.request.get(url, headers={"Referer": referer})
                if r.ok:
                    body = await r.body()
                    ctype = r.headers.get("content-type")
                    how = "context.request after referer"
                else:
                    body, ctype, how2 = await _image_via_page(ctx, url)
                    if body is not None:
                        how = how2 + " after referer"
        finally:
            await ctx.close()
            await browser.close()

    if not body:
        print(json.dumps({"ok": False, "error": "image fetch failed", "url": url}, ensure_ascii=False))
        return 1
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    print(json.dumps({
        "ok": True,
        "bytes": len(body),
        "content_type": ctype,
        "path": str(out),
        "via": how,
    }, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="新闻线 stealth 浏览器兜底工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("sweep", help="扫四家官网首页与重点版块，汇总文章链接")
    ps.add_argument("--out", required=True)

    pa = sub.add_parser("article", help="打开原文页，提取正文与主图")
    pa.add_argument("--url", required=True)
    pa.add_argument("--out", required=True)

    pi = sub.add_parser("image", help="用浏览器上下文取图片字节存文件")
    pi.add_argument("--url", required=True)
    pi.add_argument("--out", required=True)
    pi.add_argument("--referer", default=None)

    args = parser.parse_args()
    if args.cmd == "sweep":
        return asyncio.run(cmd_sweep(args.out))
    if args.cmd == "article":
        return asyncio.run(cmd_article(args.url, args.out))
    return asyncio.run(cmd_image(args.url, args.out, args.referer))


if __name__ == "__main__":
    raise SystemExit(main())
