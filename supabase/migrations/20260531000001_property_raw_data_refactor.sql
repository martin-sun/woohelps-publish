-- 房源表结构化数据重构
-- 前提：properties 表已清空（老数据无 raw_data，无法展示新结构化区块）

-- 1. properties 表：添加 raw_data JSONB
ALTER TABLE properties ADD COLUMN IF NOT EXISTS raw_data JSONB;
CREATE INDEX IF NOT EXISTS idx_properties_raw_data ON properties USING GIN (raw_data);

-- 2. properties 表：删除所有可从 raw_data 派生的旧详细字段
ALTER TABLE properties
    DROP COLUMN IF EXISTS living_area,
    DROP COLUMN IF EXISTS lot_size,
    DROP COLUMN IF EXISTS year_built,
    DROP COLUMN IF EXISTS stories,
    DROP COLUMN IF EXISTS garage,
    DROP COLUMN IF EXISTS features,
    DROP COLUMN IF EXISTS parking,
    DROP COLUMN IF EXISTS neighborhood;

-- 3. property_candidates 表：添加 raw_data（详情页提取的结构化数据），并清理旧字段
ALTER TABLE property_candidates
    ADD COLUMN IF NOT EXISTS raw_data JSONB;

ALTER TABLE property_candidates
    DROP COLUMN IF EXISTS living_area,
    DROP COLUMN IF EXISTS lot_size,
    DROP COLUMN IF EXISTS year_built,
    DROP COLUMN IF EXISTS stories,
    DROP COLUMN IF EXISTS features,
    DROP COLUMN IF EXISTS parking;
