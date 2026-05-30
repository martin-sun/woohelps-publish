from playwright.async_api import Browser, BrowserContext, Playwright

from src.config.settings import Settings, CITY_TIMEZONES

# ── Chromium 启动参数：禁用所有自动化暴露点 ──
STEALTH_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-accelerated-2d-canvas",
    "--disable-gpu",
    "--hide-scrollbars",
    "--disable-notifications",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--disable-extensions",
    "--disable-features=TranslateUI",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--safebrowsing-disable-auto-update",
    "--password-store=basic",
    "--use-mock-keychain",
    "--disable-features=InterestFeedContentSuggestions",
    "--disable-features=MediaRouter",
    "--disable-features=OptimizationHints",
    "--disable-features=PasswordManager",
    "--window-size=1920,1080",
    "--start-maximized",
]

# ── 页面初始化脚本：在网站任何 JS 执行前注入 ──
STEALTH_INIT_SCRIPT = """
(() => {
    // 1. 抹除 navigator.webdriver
    delete Object.getPrototypeOf(navigator).webdriver;
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        enumerable: false,
        configurable: true,
    });

    // 2. 伪造 window.chrome
    window.chrome = {
        app: { isInstalled: false },
        runtime: {
            OnInstalledReason: { CHROME_UPDATE: "chrome_update", SHARED_MODULE_UPDATE: "shared_module_update", INSTALL: "install", UPDATE: "update" },
            OnRestartRequiredReason: { APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic" },
            PlatformArch: { ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", MIPS64EL: "mips64el", MIPSel: "mipsel", X86_32: "x86-32", X86_64: "x86-64" },
            PlatformNaclArch: { ARM: "arm", MIPS: "mips", MIPS64: "mips64", MIPS64EL: "mips64el", MIPSel: "mipsel", MIPSel64: "mipsel64", X86_32: "x86-32", X86_64: "x86-64" },
            PlatformOs: { ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win" },
            RequestUpdateCheckStatus: { NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available" }
        },
    };

    // 3. 伪造 navigator.plugins（3个常见插件）
    function makeFakeMimeType(type, suffixes, description) {
        const m = Object.setPrototypeOf({}, MimeType.prototype);
        Object.defineProperty(m, 'type', { get: () => type });
        Object.defineProperty(m, 'suffixes', { get: () => suffixes });
        Object.defineProperty(m, 'description', { get: () => description });
        Object.defineProperty(m, 'enabledPlugin', { get: () => null });
        return m;
    }
    function makeFakePlugin(name, filename, description, version, mimes) {
        const p = Object.setPrototypeOf({}, Plugin.prototype);
        Object.defineProperty(p, 'name', { get: () => name });
        Object.defineProperty(p, 'filename', { get: () => filename });
        Object.defineProperty(p, 'description', { get: () => description });
        Object.defineProperty(p, 'version', { get: () => version });
        Object.defineProperty(p, 'length', { get: () => mimes.length });
        for (let i = 0; i < mimes.length; i++) {
            Object.defineProperty(p, String(i), { get: () => mimes[i] });
            Object.defineProperty(p, mimes[i].type, { get: () => mimes[i] });
        }
        Object.defineProperty(p, 'item', {
            value: function(idx) { return this[idx]; },
        });
        Object.defineProperty(p, 'namedItem', {
            value: function(name) { return this[name]; },
        });
        return p;
    }
    const pdfPlugin = makeFakePlugin(
        "Chrome PDF Plugin", "internal-pdf-viewer", "Portable Document Format", "undefined",
        [makeFakeMimeType("application/x-google-chrome-pdf", "pdf", "Portable Document Format")]
    );
    const pdfViewer = makeFakePlugin(
        "Chrome PDF Viewer", "mhjfbmdgcfjbbpaeojofohoefgiehjai", "", "undefined",
        [makeFakeMimeType("application/pdf", "pdf", "")]
    );
    const nativeClient = makeFakePlugin(
        "Native Client", "internal-nacl-plugin", "", "undefined",
        [
            makeFakeMimeType("application/x-nacl", "", ""),
            makeFakeMimeType("application/x-pnacl", "", ""),
        ]
    );
    const plugins = [pdfPlugin, pdfViewer, nativeClient];
    const pluginArray = Object.setPrototypeOf(plugins, PluginArray.prototype);
    Object.defineProperty(pluginArray, 'length', { get: () => plugins.length });
    Object.defineProperty(pluginArray, 'item', { value: idx => plugins[idx] });
    Object.defineProperty(pluginArray, 'namedItem', { value: name => plugins.find(p => p.name === name) });
    Object.defineProperty(pluginArray, 'refresh', { value: () => {} });
    Object.defineProperty(navigator, 'plugins', { get: () => pluginArray });

    // 4. 伪造 mimeTypes
    const allMimes = [...pdfPlugin, ...pdfViewer, ...nativeClient].filter(Boolean);
    const mimeTypeArray = Object.setPrototypeOf(allMimes, MimeTypeArray.prototype);
    Object.defineProperty(mimeTypeArray, 'length', { get: () => allMimes.length });
    Object.defineProperty(mimeTypeArray, 'item', { value: idx => allMimes[idx] });
    Object.defineProperty(mimeTypeArray, 'namedItem', { value: name => allMimes.find(m => m.type === name) });
    Object.defineProperty(mimeTypeArray, 'refresh', { value: () => {} });
    Object.defineProperty(navigator, 'mimeTypes', { get: () => mimeTypeArray });

    // 5. 伪造 navigator.languages
    Object.defineProperty(navigator, 'languages', { get: () => ['en-CA', 'en-US', 'en'] });

    // 6. Permissions API 补丁
    const originalQuery = window.navigator.permissions?.query;
    if (originalQuery) {
        window.navigator.permissions.query = (parameters) => {
            if (parameters.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission });
            }
            return originalQuery(parameters);
        };
    }

    // 7. WebGL vendor / renderer 伪装
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter(parameter);
    };

    // 8. 覆盖 console.debug 避免某些检测脚本通过 debug 频率判断自动化
    window.console.debug = () => {};

    // 9. 覆盖 Permissions.prototype.query 返回更多真实值
    try {
        const permProto = Permissions.prototype;
        const origQuery = permProto.query;
        permProto.query = async function(permissionDesc) {
            const name = typeof permissionDesc === 'string' ? permissionDesc : permissionDesc.name;
            const denyList = ['midi', 'midi-sysex', 'bluetooth', 'usb', 'serial', 'hid'];
            if (denyList.includes(name)) {
                return { state: 'prompt', onchange: null };
            }
            return origQuery.call(this, permissionDesc);
        };
    } catch (e) {}
})();
"""


async def launch_browser(p: Playwright, settings: Settings) -> Browser:
    """启动带 stealth 参数的 Chromium"""
    if settings.BROWSER_WS_ENDPOINT:
        return await p.chromium.connect_over_cdp(settings.BROWSER_WS_ENDPOINT)
    return await p.chromium.launch(
        headless=False,
        args=STEALTH_CHROMIUM_ARGS,
    )


def _build_proxy_config(settings: Settings) -> dict | None:
    if not settings.PROXY_SERVER:
        return None
    proxy = {"server": settings.PROXY_SERVER}
    if settings.PROXY_USERNAME:
        proxy["username"] = settings.PROXY_USERNAME
    if settings.PROXY_PASSWORD:
        proxy["password"] = settings.PROXY_PASSWORD
    return proxy


async def new_stealth_context(
    browser: Browser,
    settings: Settings,
    city_slug: str | None = None,
) -> BrowserContext:
    """创建一个带完整反检测配置的浏览器上下文"""
    timezone = CITY_TIMEZONES.get(city_slug, "America/Toronto") if city_slug else "America/Toronto"
    proxy = _build_proxy_config(settings)

    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        screen={"width": 1920, "height": 1080},
        locale="en-CA",
        timezone_id=timezone,
        permissions=[],
        bypass_csp=True,  # 禁用 CSP 以允许 stealth init script 在目标站点注入执行
        proxy=proxy,
        # 额外降低指纹独特性
        color_scheme="light",
        reduced_motion="no-preference",
    )

    # 在每一页 JS 执行前注入 stealth 脚本
    await context.add_init_script(STEALTH_INIT_SCRIPT)

    # 覆盖 navigator.webdriver（再保险一层，某些检测时机更早）
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
    """)

    return context
