import asyncio, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.config.settings import get_settings
from src.publisher.woohelps import WoohelpsPublisher

async def main():
    settings = get_settings()
    async with WoohelpsPublisher(base_url=settings.WOOHELPS_API_URL, login_session=settings.WOOHELPS_LOGIN_SESSION) as pub:
        for aid in range(5282, 5296):
            r = await pub.client.get(
                settings.WOOHELPS_API_URL + "/api/applet/activity/info/get",
                params={"activity_id": aid},
                headers={"LOGIN-SESSION": settings.WOOHELPS_LOGIN_SESSION},
            )
            info = r.json()
            ad = info.get("data") if isinstance(info.get("data"), dict) else info
            imgs = ad.get("imgs") or []
            print(aid, "|", (ad.get("name") or "")[:28], "| imgs:", len(imgs), "|", (imgs[0].split("/")[-1] if imgs else "NONE"))

if __name__ == "__main__":
    asyncio.run(main())
