-- 活动主表
CREATE TABLE IF NOT EXISTS activities (
    id SERIAL PRIMARY KEY,

    -- 来源信息
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    city_slug TEXT NOT NULL,

    -- 标题
    title_en TEXT NOT NULL,
    title_zh TEXT NOT NULL,

    -- 中文处理后的数据
    description_zh TEXT NOT NULL,
    content_zh TEXT NOT NULL DEFAULT '',

    -- 时间和地点
    start_time TEXT,
    end_time TEXT,
    timezone TEXT,
    address TEXT NOT NULL DEFAULT '',
    venue_name TEXT,

    -- 图片
    image_url TEXT,
    image_urls TEXT NOT NULL DEFAULT '[]',

    -- 活动属性
    price TEXT,
    is_free BOOLEAN NOT NULL DEFAULT TRUE,
    fee_amount DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    fee_parsed_free BOOLEAN NOT NULL DEFAULT TRUE,
    activity_type INTEGER NOT NULL DEFAULT 1,

    -- AI 处理结果
    highlights TEXT NOT NULL DEFAULT '[]',

    -- 发布状态
    status TEXT NOT NULL DEFAULT 'pending',
    platform_activity_id INTEGER,
    publish_error TEXT,

    -- 去重
    content_hash TEXT,

    -- 元数据
    created_at TEXT NOT NULL DEFAULT NOW(),
    updated_at TEXT NOT NULL DEFAULT NOW(),

    UNIQUE(source, source_id)
);

-- 已处理页面（去重用）
CREATE TABLE IF NOT EXISTS processed_pages (
    id SERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    html_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    activity_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT NOW(),
    updated_at TEXT NOT NULL DEFAULT NOW(),

    UNIQUE(source, source_url)
);

-- 抓取任务
CREATE TABLE IF NOT EXISTS scrape_tasks (
    id SERIAL PRIMARY KEY,
    task_type TEXT NOT NULL DEFAULT 'discover',
    city_slugs TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'running',
    total_fetched INTEGER NOT NULL DEFAULT 0,
    total_new INTEGER NOT NULL DEFAULT 0,
    total_skipped INTEGER NOT NULL DEFAULT 0,
    current_city TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT NOW(),
    completed_at TEXT
);

-- 候选活动（人工审核流程）
CREATE TABLE IF NOT EXISTS candidate_activities (
    id SERIAL PRIMARY KEY,
    city_slug TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    title_zh TEXT DEFAULT '',
    event_date TEXT,
    address TEXT DEFAULT '',
    price TEXT DEFAULT '',
    description TEXT DEFAULT '',
    description_zh TEXT DEFAULT '',
    ai_worth_fetching BOOLEAN,
    ai_reason TEXT,
    human_status TEXT NOT NULL DEFAULT 'pending',
    fetched_detail BOOLEAN NOT NULL DEFAULT FALSE,
    activity_id INTEGER,
    created_at TEXT NOT NULL DEFAULT NOW(),
    UNIQUE(source, source_url)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_activities_city ON activities(city_slug);
CREATE INDEX IF NOT EXISTS idx_activities_status ON activities(status);
CREATE INDEX IF NOT EXISTS idx_activities_start_time ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_activities_content_hash ON activities(city_slug, content_hash);
CREATE INDEX IF NOT EXISTS idx_processed_pages_source ON processed_pages(source);
CREATE INDEX IF NOT EXISTS idx_candidates_city ON candidate_activities(city_slug);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidate_activities(human_status);
CREATE INDEX IF NOT EXISTS idx_candidates_source ON candidate_activities(source);
