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
    KIMI_MODEL: str = "kimi-k2.6"
    KIMI_MAX_TOKENS: int = 8192

    # 海外新生活平台 API
    WOOHELPS_API_URL: str = "https://www.woohelps.com"
    WOOHELPS_LOGIN_SESSION: str

    # 数据库
    DB_PATH: str = "./data/activities.db"

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
}

# 城市坐标与元信息
CITIES: dict[str, dict] = {
    "toronto": {"eng_name": "Toronto", "lat": 43.6532, "lng": -79.3832, "radius": "50km"},
    "vancouver": {"eng_name": "Vancouver", "lat": 49.2827, "lng": -123.1207, "radius": "50km"},
    "montreal": {"eng_name": "Montreal", "lat": 45.5017, "lng": -73.5673, "radius": "50km"},
    "calgary": {"eng_name": "Calgary", "lat": 51.0447, "lng": -114.0719, "radius": "50km"},
    "edmonton": {"eng_name": "Edmonton", "lat": 53.5461, "lng": -113.4938, "radius": "50km"},
    "ottawa": {"eng_name": "Ottawa", "lat": 45.4215, "lng": -75.6972, "radius": "50km"},
    "winnipeg": {"eng_name": "Winnipeg", "lat": 49.8954, "lng": -97.1385, "radius": "50km"},
    "saskatoon": {"eng_name": "Saskatoon", "lat": 52.1332, "lng": -106.6700, "radius": "50km"},
    "regina": {"eng_name": "Regina", "lat": 50.4452, "lng": -104.6189, "radius": "50km"},
    "moncton": {"eng_name": "Moncton", "lat": 46.0878, "lng": -64.7782, "radius": "50km"},
}


@lru_cache
def get_settings() -> Settings:
    return Settings()
