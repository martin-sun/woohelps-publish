from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawPage:
    """爬虫输出 — 一个详情页的原始内容"""
    source: str                    # 数据来源标识 (todocanada/familyfuncanada/discoversaskatoon)
    source_url: str                # 详情页 URL
    raw_html: str                  # 详情页完整 HTML
    city_slug: str                 # 城市 slug (toronto/vancouver/...)
    image_url: str | None = None   # 页面主图 (og:image)


@dataclass
class ProcessedActivity:
    """AI 处理后的活动数据"""
    # 来源信息
    source: str
    source_id: str                 # 确定性 ID: source_url#<hash(title|datetime|address)>[:8]
    source_url: str
    city_slug: str

    # 标题
    title_en: str
    title_zh: str

    # 内容
    description_zh: str            # 中文摘要 (200字以内)
    html_zh: str                   # 翻译后的中文 HTML

    # 地点
    address: str
    venue_name: str | None = None

    # 费用
    price: str | None = None       # 费用信息原文
    is_free: bool = True
    fee_amount: float = 0.0        # 解析后的金额
    fee_parsed_free: bool = True   # 解析后是否免费

    # 时间 (UTC naive datetime)
    start_time_utc: datetime | None = None
    end_time_utc: datetime | None = None

    # 时区信息
    timezone: str | None = None    # IANA 时区 (如 America/Toronto)

    # 其他
    image_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    activity_type: int = 1         # 1=活动 2=招聘 3=促销 4=美食 5=教育 6=参政

    # 去重
    content_hash: str | None = None

    # 发布状态
    status: str = "pending"        # pending/published/failed/skipped
    platform_activity_id: int | None = None
    publish_error: str | None = None
