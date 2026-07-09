-- 扩展 scrape_tasks 表，支持房产抓取任务
ALTER TABLE scrape_tasks
    ADD COLUMN IF NOT EXISTS agent_id INTEGER,
    ADD COLUMN IF NOT EXISTS params TEXT NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS new_candidates INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS updated_candidates INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS delisted_candidates INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS failed_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS scraper_version TEXT,
    ADD COLUMN IF NOT EXISTS ip_address TEXT,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 目标地产经纪表
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,

    -- 来源标识
    source TEXT NOT NULL DEFAULT 'realtorca',
    agent_id TEXT NOT NULL,                     -- realtor.ca IndividualId (如 2061436)

    -- 经纪信息
    name TEXT NOT NULL,                         -- 英文原名
    name_zh TEXT,                               -- 中文名（如有）
    brokerage TEXT,                             -- 所属经纪公司
    phone TEXT,
    email TEXT,
    website TEXT,

    -- 覆盖城市/省份
    city_slugs TEXT NOT NULL DEFAULT '[]',      -- JSON 数组 ["saskatoon", "toronto"]
    province_code TEXT,                         -- sk/on/bc/...

    -- 管理字段
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(source, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(is_active);

-- 房源候选表
CREATE TABLE IF NOT EXISTS property_candidates (
    id SERIAL PRIMARY KEY,

    city_slug TEXT NOT NULL,
    agent_id INTEGER REFERENCES agents(id),
    source TEXT NOT NULL DEFAULT 'realtorca',
    source_id TEXT NOT NULL,                    -- realtor.ca 房源 Id (如 29758682)
    source_url TEXT NOT NULL,                   -- 详情页 URL
    mls_number TEXT,                            -- MLS 号码

    -- API 返回的原始摘要（列表阶段即已知）
    title TEXT,                                 -- 地址作为标题
    price TEXT,
    price_numeric NUMERIC,
    previous_price_numeric NUMERIC,             -- 用于价格变动检测
    property_type TEXT,
    bedrooms TEXT,
    bathrooms TEXT,
    living_area TEXT,
    lot_size TEXT,
    address TEXT,
    postal_code TEXT,
    latitude NUMERIC,
    longitude NUMERIC,

    -- 图片
    photo_urls TEXT NOT NULL DEFAULT '[]',      -- JSON 数组，来自 API Property.Photo

    -- 开放日
    open_house TEXT NOT NULL DEFAULT '[]',      -- JSON 数组，来自 API OpenHouse

    -- 建筑与设施（列表阶段即可获取）
    year_built TEXT,
    stories TEXT,
    features TEXT,                              -- LandscapeFeatures 等（JSON 或文本）
    parking TEXT,                               -- 停车信息摘要
    description_en TEXT,                        -- 详情页抓取的英文描述

    -- 房源生命周期（相对于该经纪的视图）
    listing_status TEXT NOT NULL DEFAULT 'active',
                                                -- active / price_changed / sold / delisted
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- 最近一次在 API 中见到该房源
    miss_count INTEGER NOT NULL DEFAULT 0,      -- 连续未在 API 中见到的次数
    history_log TEXT NOT NULL DEFAULT '[]',     -- 变更历史

    -- 人工审核
    human_status TEXT NOT NULL DEFAULT 'pending',  -- pending / selected
    fetched_detail BOOLEAN NOT NULL DEFAULT FALSE, -- 是否已抓取详情页描述
    fetch_error TEXT,                           -- 详情页抓取失败原因
    property_id INTEGER,                        -- 关联到 properties.id（当选中时）

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(source, source_id, agent_id)         -- 同一房源可被不同经纪各自追踪（co-listing）
);

CREATE INDEX IF NOT EXISTS idx_property_candidates_agent ON property_candidates(agent_id);
CREATE INDEX IF NOT EXISTS idx_property_candidates_status ON property_candidates(listing_status);
CREATE INDEX IF NOT EXISTS idx_property_candidates_human ON property_candidates(human_status);
CREATE INDEX IF NOT EXISTS idx_property_candidates_source ON property_candidates(source, source_id);

-- 房源主表
CREATE TABLE IF NOT EXISTS properties (
    id SERIAL PRIMARY KEY,

    source TEXT NOT NULL DEFAULT 'realtorca',
    source_id TEXT NOT NULL,                    -- 房源 Id
    source_url TEXT NOT NULL,
    city_slug TEXT NOT NULL,
    agent_id INTEGER REFERENCES agents(id),     -- 主要关联经纪

    -- 标题
    title_en TEXT NOT NULL,                     -- 英文标题（地址）
    title_zh TEXT NOT NULL,                     -- 中文标题

    -- 房产核心字段
    price TEXT NOT NULL,
    price_numeric NUMERIC,
    mls_number TEXT,
    property_type TEXT,

    -- 房间信息
    bedrooms TEXT,
    bathrooms TEXT,

    -- 面积
    living_area TEXT,
    lot_size TEXT,

    -- 建筑信息
    year_built TEXT,
    stories TEXT,
    garage TEXT,

    -- 地址
    address TEXT NOT NULL,
    neighborhood TEXT,
    postal_code TEXT,
    latitude NUMERIC,
    longitude NUMERIC,

    -- 中文内容
    description_zh TEXT,                        -- 中文摘要（200字内）
    content_zh TEXT NOT NULL DEFAULT '',        -- 中文正文（500-1000字）
    highlights TEXT NOT NULL DEFAULT '[]',      -- 亮点列表 JSON

    -- 开放日
    open_house TEXT NOT NULL DEFAULT '[]',      -- JSON 数组

    -- 图片
    image_url TEXT,
    image_urls TEXT NOT NULL DEFAULT '[]',

    -- 经纪信息（冗余存储）
    agent_name TEXT,
    agent_brokerage TEXT,
    agent_phone TEXT,

    -- 房源生命周期与发布状态
    status TEXT NOT NULL DEFAULT 'pending',
                                                -- pending / ai_processing / review / published
                                                -- / price_changed / delisted
    last_scraped_at TIMESTAMPTZ,                -- 最近一次成功爬取时间
    delisted_at TIMESTAMPTZ,                    -- 标记为下架时间

    platform_property_id INTEGER,
    publish_error TEXT,

    content_hash TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(source, source_id)                   -- 同一房源在平台只发布一次
);

CREATE INDEX IF NOT EXISTS idx_properties_city ON properties(city_slug);
CREATE INDEX IF NOT EXISTS idx_properties_status ON properties(status);
CREATE INDEX IF NOT EXISTS idx_properties_agent ON properties(agent_id);
