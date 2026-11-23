import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.config.settings import get_settings
from src.publisher.woohelps import WoohelpsPublisher

async def main():
    settings = get_settings()
    out = []
    async with WoohelpsPublisher(base_url=settings.WOOHELPS_API_URL, login_session=settings.WOOHELPS_LOGIN_SESSION) as pub:
        for aid in range(5044, 5235):
            try:
                r = await pub.client.get(
                    settings.WOOHELPS_API_URL + "/api/applet/activity/info/get",
                    params={"activity_id": aid},
                    headers={"LOGIN-SESSION": settings.WOOHELPS_LOGIN_SESSION},
                )
                info = r.json()
                ad = info.get("data") or info
                if ad.get("name"):
                    out.append(f"{aid} | {ad.get('name')} | {ad.get('start_time')} | {(ad.get('address') or '')[:60]}")
            except Exception as e:
                out.append(f"{aid} | ERROR {type(e).__name__}")
    Path("/tmp/published_acts_5044_5234.txt").write_text("\n".join(out), encoding="utf-8")
    print("total named:", len(out))

asyncio.run(main())
