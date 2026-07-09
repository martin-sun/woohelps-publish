from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AI 引擎 (Kimi API, Anthropic 兼容协议)
    KIMI_API_KEY: str
    KIMI_BASE_URL: str = "https://api.kimi.com/coding/"
    KIMI_MODEL: str = "kimi-k2.7"
    KIMI_MAX_TOKENS: int = 8192

    # 海外新生活平台 API
    WOOHELPS_API_URL: str = "https://www.woohelps.com"
    WOOHELPS_LOGIN_SESSION: str
    WOOHELPS_USER_ID: str = "1"

    # 数据库
    DATABASE_URL: str = "postgresql://localhost/activities"

    # 浏览器（空值=本地启动 Chromium，非空=连接远程 CDP）
    BROWSER_WS_ENDPOINT: str = ""

    # 代理设置（用于绕过 WAF / 反爬，格式: http://host:port 或 socks5://host:port）
    PROXY_SERVER: str = ""
    PROXY_USERNAME: str = ""
    PROXY_PASSWORD: str = ""

    # 管理界面
    ADMIN_PASSWORD: str = ""  # 可选，设置后管理界面启用 Basic Auth

    # 日志
    LOG_LEVEL: str = "INFO"


# 城市 IANA 时区映射
CITY_TIMEZONES: dict[str, str] = {
    "toronto": "America/Toronto",
    "vancouver": "America/Vancouver",
    "montreal": "America/Toronto",
    "calgary": "America/Edmonton",
    "edmonton": "America/Edmonton",
    "ottawa": "America/Toronto",
    "winnipeg": "America/Winnipeg",
    "saskatoon": "America/Regina",
    "regina": "America/Regina",
    "moncton": "America/Moncton",
    "st-johns": "America/St_Johns",
    "halifax": "America/Halifax",
    "charlottetown": "America/Halifax",
    "fredericton": "America/Moncton",
    "kelowna": "America/Vancouver",
    "victoria": "America/Vancouver",
}

# 城市元信息：启动时从 v2 API 动态加载填充（见 src/config/city_loader.py）
# 结构: {slug: {"name": "多伦多", "eng_name": "Toronto", "province": "ON", "id": 1}}
CITIES: dict[str, dict] = {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
