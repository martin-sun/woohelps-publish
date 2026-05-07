# 一期实施计划

## 目标

完成 TodoCanada/FamilyFunCanada/DiscoverSaskatoon 活动抓取 → AI 翻译处理 → 通过 API 发布到海外新生活平台的核心流程。

## 实施步骤

### Step 1: 项目初始化
- [ ] 初始化 Python 项目（pyproject.toml）
- [ ] 配置依赖管理（uv 或 pip）
- [ ] 创建项目目录结构
- [ ] 编写 `.env.example` 配置模板
- [ ] 配置 pydantic-settings

**依赖包**:
```
httpx>=0.27
anthropic>=0.40
pydantic>=2.0
pydantic-settings>=2.0
apscheduler>=3.10
loguru>=0.7
aiosqlite>=0.20
playwright>=1.40
beautifulsoup4>=4.12
bleach>=6.0
lxml>=5.0
```

### Step 2: 配置模块 (`src/config/settings.py`)
- [ ] pydantic-settings 配置类
- [ ] 环境变量加载
- [ ] 城市坐标映射配置
- [ ] 城市与平台 ID 映射配置

**配置项**:
```
KIMI_API_KEY=
KIMI_BASE_URL=https://api.kimi.com/coding/
KIMI_MODEL=kimi-k2.6
WOOHELPS_API_URL=https://www.woohelps.com
WOOHELPS_LOGIN_SESSION=
DB_PATH=./data/activities.db
LOG_LEVEL=INFO
```

### Step 3: 数据模型 (`src/models/`)
- [ ] RawActivity 数据类（原始活动）
- [ ] ProcessedActivity 数据类（AI 处理后）
- [ ] 城市配置模型

### Step 4: 存储模块 (`src/storage/`)
- [ ] SQLite 数据库初始化（建表）
- [ ] 活动 CRUD 操作
- [ ] 去重查询
- [ ] 抓取日志记录

### Step 5: 多源爬虫 (`src/scrapers/`)
- [ ] Spike: 验证各数据源选择器和反爬机制（详见 [02-scraper-design.md](./02-scraper-design.md)）
- [ ] 爬虫基类 + RawActivity 数据结构
- [ ] TodoCanada 爬虫（Playwright）
- [ ] FamilyFunCanada 爬虫（WordPress REST API）
- [ ] DiscoverSaskatoon 爬虫（Playwright）
- [ ] 爬虫注册与调度逻辑
- [ ] 错误处理和重试
- [ ] 数据映射（各源 → RawActivity）

### Step 6: AI 处理引擎 (`src/ai/`)
- [ ] Kimi API 客户端封装（Anthropic 兼容协议）
- [ ] 翻译+摘要功能
- [ ] 活动分类功能（一期仅本地存储，不发布到平台）
- [ ] 质量评估功能
- [ ] 批量处理逻辑

### Step 7: 发布模块 (`src/publisher/woohelps.py`)
- [ ] 海外新生活 API 客户端
- [ ] 活动发布接口
- [ ] 错误处理和重试

### Step 8: 去重模块 (`src/dedup/`)
- [ ] source + source_id 精确去重
- [ ] content_hash 内容去重

### Step 9: 主流程 (`src/main.py`)
- [ ] 编排整个流程（抓取 → AI → 去重 → 发布）
- [ ] APScheduler 定时任务
- [ ] 命令行参数（手动触发/定时运行）
- [ ] 日志配置

### Step 10: 测试 & 验证
- [ ] 单元测试（各模块）
- [ ] 集成测试（端到端流程）
- [ ] 手动验证（先发布 1-2 个城市）

## 文件清单

```
src/
├── config/
│   ├── __init__.py
│   └── settings.py          # Step 2
├── models/
│   ├── __init__.py
│   └── activity.py          # Step 3
├── storage/
│   ├── __init__.py
│   └── db.py                # Step 4
├── scrapers/
│   ├── __init__.py
│   ├── base.py              # Step 5 - 爬虫基类 + RawActivity
│   ├── todocanada.py        # Step 5 - Playwright
│   ├── familyfun.py         # Step 5 - REST API
│   └── saskatoon.py         # Step 5 - Playwright
├── ai/
│   ├── __init__.py
│   ├── client.py            # Step 6 - Kimi API 封装
│   ├── translator.py        # Step 6
│   ├── classifier.py        # Step 6
│   └── quality.py           # Step 6
├── publisher/
│   ├── __init__.py
│   └── woohelps.py          # Step 7
├── dedup/
│   ├── __init__.py
│   └── deduplicator.py      # Step 8
└── main.py                  # Step 9
```

## 验证方法

1. **Spike 验证**: 在实现爬虫前，对每个数据源完成选择器验证、反爬测试、页面采样（详见 [02-scraper-design.md](./02-scraper-design.md) 的 Spike 验证清单）
2. **抓取验证**: 运行爬虫，确认能获取到多伦多的活动数据
3. **AI 处理验证**: 检查翻译和摘要质量
4. **HTML 清理验证**: 确认清理后的 HTML 不含 script/iframe，外链安全，来源标注正确
5. **时区验证**: 对比本地时间和发布的 UTC 时间，确认转换正确（尤其注意夏令时边界）
6. **发布验证**: 先在测试环境发布 1-2 个活动到平台，检查：
   - 活动是否出现在平台上
   - 中文内容是否正确
   - 时间、地点是否准确
   - 图片是否正常显示
7. **审核验证**: 确认平台审核流程正常工作

## 时间估算

| 步骤 | 预估时间 |
|------|---------|
| Step 0: Spike 验证（选择器/反爬） | 1-2 天 |
| Step 1-2: 项目初始化 + 配置 | 0.5 天 |
| Step 3-4: 模型 + 存储 | 0.5 天 |
| Step 5: 多源爬虫 | 2 天 |
| Step 6: AI 处理引擎 + HTML 清理 | 1 天 |
| Step 7-8: 发布 + 去重 | 0.5 天 |
| Step 9: 主流程编排 | 0.5 天 |
| Step 10: 测试验证 | 1 天 |
| **合计** | **约 7-8 天** |
