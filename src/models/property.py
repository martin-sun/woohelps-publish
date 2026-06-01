from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Agent:
    """目标地产经纪"""
    id: int | None = None
    source: str = "realtorca"
    agent_id: str = ""                          # realtor.ca IndividualId
    name: str = ""
    name_zh: str | None = None
    brokerage: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    city_slugs: list[str] = field(default_factory=list)
    province_code: str | None = None
    is_active: bool = True
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class PropertyCandidate:
    """房源候选 — API 列表阶段即已知的数据"""
    id: int | None = None
    city_slug: str = ""
    agent_id: int | None = None
    source: str = "realtorca"
    source_id: str = ""                         # realtor.ca 房源 Id
    source_url: str = ""
    mls_number: str | None = None

    # 原始摘要（API 基础字段，用于列表筛选/展示）
    title: str | None = None
    price: str | None = None
    price_numeric: float | None = None
    previous_price_numeric: float | None = None
    property_type: str | None = None
    bedrooms: str | None = None
    bathrooms: str | None = None
    address: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    # 图片与开放日
    photo_urls: list[str] = field(default_factory=list)
    open_house: list[dict] = field(default_factory=list)

    # 详情页描述
    description_en: str | None = None

    # 详情页结构化数据（从 HTML 提取，传递到 AI 处理环节）
    raw_data: dict = field(default_factory=dict)

    # 生命周期
    listing_status: str = "active"              # active / price_changed / sold / delisted
    last_seen_at: datetime | None = None
    miss_count: int = 0
    history_log: list[dict] = field(default_factory=list)

    # 人工审核
    human_status: str = "pending"               # pending / selected / rejected
    fetched_detail: bool = False
    fetch_error: str | None = None
    property_id: int | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Property:
    """房源主表 — AI 处理后待发布/已发布的内容"""
    id: int | None = None
    source: str = "realtorca"
    source_id: str = ""
    source_url: str = ""
    city_slug: str = ""
    agent_id: int | None = None

    # 标题
    title_en: str = ""
    title_zh: str = ""

    # 房产核心字段
    price: str = ""
    price_numeric: float | None = None
    mls_number: str | None = None
    property_type: str | None = None

    # 房间信息（API 基础字段，列表筛选必需）
    bedrooms: str | None = None
    bathrooms: str | None = None

    # 地址
    address: str = ""
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    # 中文内容
    description_zh: str | None = None
    content_zh: str = ""
    highlights: list[str] = field(default_factory=list)

    # 开放日
    open_house: list[dict] = field(default_factory=list)

    # 图片
    image_url: str | None = None
    image_urls: list[str] = field(default_factory=list)

    # 经纪信息（冗余存储）
    agent_name: str | None = None
    agent_brokerage: str | None = None
    agent_phone: str | None = None

    # 状态
    status: str = "pending"                     # pending / ai_processing / review / published / price_changed / delisted
    last_scraped_at: datetime | None = None
    delisted_at: datetime | None = None
    platform_property_id: int | None = None
    publish_error: str | None = None

    content_hash: str | None = None

    # 详情页完整结构化数据（JSONB）
    raw_data: dict = field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None
