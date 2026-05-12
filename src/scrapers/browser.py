from playwright.async_api import Browser, Playwright

from src.config.settings import Settings


async def launch_browser(p: Playwright, settings: Settings) -> Browser:
    if settings.BROWSER_WS_ENDPOINT:
        return await p.chromium.connect_over_cdp(settings.BROWSER_WS_ENDPOINT)
    return await p.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
