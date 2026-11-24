import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.config.settings import get_settings
from src.publisher.woohelps import WoohelpsPublisher

async def main():
    settings = get_settings()
    out = []
    async with WoohelpsPublisher(base_url=settings.WOOHELPS_API_URL, login_session=settings.WOOHELPS_LOGIN_SESSION) as pub:
        for aid in range(5100, 5286):
            try:
                r = await pub.client.get(
                    settings.WOOHELPS_API_URL + "/api/applet/activity/info/get",
                    params={"activity_id": aid},
                    headers={"LOGIN-SESSION": settings.WOOHELPS_LOGIN_SESSION},
                )
                info = r.json()
                ad = info.get("data") if isinstance(info.get("data"), dict) else info
                if ad.get("name"):
                    out.append((aid, ad.get("name"), ad.get("start_time"), (ad.get("address") or "")[:60], (ad.get("city") or {}).get("eng_name")))
                else:
                    out.append((aid, "(empty)", "", "", ""))
            except Exception as e:
                out.append((aid, "ERROR " + type(e).__name__, "", "", ""))
    for row in out:
        print(row[0], "|", row[1], "|", row[2], "|", row[3], "|", row[4])

if __name__ == "__main__":
    asyncio.run(main())
