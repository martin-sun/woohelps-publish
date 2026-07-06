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

# 城市坐标与元信息
CITIES: dict[str, dict] = {
    # ON 安大略 (8)
    "toronto": {"eng_name": "Toronto", "name": "多伦多", "lat": 43.6532, "lng": -79.3832, "radius": "50km"},
    "ottawa": {"eng_name": "Ottawa", "name": "渥太華", "lat": 45.4215, "lng": -75.6972, "radius": "50km"},
    "mississauga": {"eng_name": "Mississauga", "name": "密西沙加", "lat": 43.5890, "lng": -79.6441, "radius": "50km"},
    "markham": {"eng_name": "Markham", "name": "萬錦市", "lat": 43.8561, "lng": -79.3370, "radius": "50km"},
    "richmond-hill": {"eng_name": "Richmond Hill", "name": "列治文山", "lat": 43.8828, "lng": -79.4403, "radius": "50km"},
    "vaughan": {"eng_name": "Vaughan", "name": "沃汉", "lat": 43.8361, "lng": -79.4984, "radius": "50km"},
    "london": {"eng_name": "London", "name": "伦敦", "lat": 42.9849, "lng": -81.2453, "radius": "50km"},
    "oakville": {"eng_name": "Oakville", "name": "奥克维尔", "lat": 43.4675, "lng": -79.6877, "radius": "50km"},
    # BC 不列颠哥伦比亚 (5)
    "vancouver": {"eng_name": "Vancouver", "name": "溫哥華", "lat": 49.2827, "lng": -123.1207, "radius": "50km"},
    "burnaby": {"eng_name": "Burnaby", "name": "本那比", "lat": 49.2488, "lng": -122.9805, "radius": "50km"},
    "richmond": {"eng_name": "Richmond", "name": "列治文", "lat": 49.1614, "lng": -123.1376, "radius": "50km"},
    "surrey": {"eng_name": "Surrey", "name": "素里", "lat": 49.1913, "lng": -122.8490, "radius": "50km"},
    "coquitlam": {"eng_name": "Coquitlam", "name": "高貴林", "lat": 49.2838, "lng": -122.7932, "radius": "50km"},
    # QC 魁北克 (2)
    "montreal": {"eng_name": "Montreal", "name": "蒙特利尔", "lat": 45.5017, "lng": -73.5673, "radius": "50km"},
    "brossard": {"eng_name": "Brossard", "name": "寶樂沙", "lat": 45.4507, "lng": -73.4684, "radius": "50km"},
    # AB 阿尔伯塔 (2)
    "calgary": {"eng_name": "Calgary", "name": "卡尔加里", "lat": 51.0447, "lng": -114.0719, "radius": "50km"},
    "edmonton": {"eng_name": "Edmonton", "name": "埃德蒙顿", "lat": 53.5461, "lng": -113.4938, "radius": "50km"},
    # SK 萨斯喀彻温 (2)
    "saskatoon": {"eng_name": "Saskatoon", "name": "萨斯卡通", "lat": 52.1332, "lng": -106.6700, "radius": "50km"},
    "regina": {"eng_name": "Regina", "name": "里贾纳", "lat": 50.4452, "lng": -104.6189, "radius": "50km"},
    # MB 曼尼托巴 (1)
    "winnipeg": {"eng_name": "Winnipeg", "name": "温尼伯", "lat": 49.8954, "lng": -97.1385, "radius": "50km"},
    # NS 新斯科舍 (1)
    "halifax": {"eng_name": "Halifax", "name": "哈利法克斯", "lat": 44.6488, "lng": -63.5752, "radius": "50km"},
}


@lru_cache
def get_settings() -> Settings:
    return Settings()
