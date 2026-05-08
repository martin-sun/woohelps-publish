# 海外新生活 API 发布模块设计

## API 信息

- **发布端点**: `POST /api/applet/activity/release/`
- **认证方式**: `LOGIN_SESSION` header
  - 方式 1: 使用 `WEBSITE_PUBLIC_TOKEN`（user_id=1，管理员）
  - 方式 2: 使用 Redis 中存储的用户 token

## 请求参数

```python
# POST form-data 参数
PUBLISH_PARAMS = {
    # === 必填字段 ===
    "name": str,                  # 活动名（中文）
    "city_id": int,               # 平台城市 ID
    "start_time": "YYYY-MM-DD HH:MM",  # UTC 时间
    "end_time": "YYYY-MM-DD HH:MM",    # UTC 时间

    # === 内容字段 ===
    "description": str,           # 活动描述（中文摘要）
    "html": str,                  # 富文本内容（中文 HTML）
    "address": str,               # 活动地点
    "img": str,                   # 封面图片 URL
    "imgs": str,                  # JSON 数组，图片列表

    # === 可选字段（有默认值）===
    "city_area_id": 0,            # 城市区域 ID（0 = 不指定）
    "remind_type": 1,             # 提醒类型（1=不提醒）
    "fee_type": 1,                # 费用类型（1=免费）
    "fee": 0,                     # 费用金额
    "enroll_type": 1,             # 报名类型（1=不需要报名）
    "groupon_type": 1,            # 团购类型（1=否）
    "need_enroll_amount": 1,      # 需要报名人数
    "need_enroll_phone": 1,       # 需要报名电话
    "need_enroll_groupon_amount": 1,
    "need_enroll_comment": 1,
    "customer_fields": "",
    "limit": 0,
}
```

## 响应格式

```json
{
    "errcode": 0,
    "errmsg": "ok",
    "activity_id": 12345
}
```

错误响应：
```json
{
    "errcode": 201,
    "errmsg": "开始时间不能晚于结束时间"
}
```

## 审核流程

发布后平台会自动触发审核（`perform_sync_moderation`）：
1. **同步审核**: 基于智谱 GLM 的内容审核，检查活动名称、描述、图片
2. **异步审核**: 后台进一步审核
3. 活动默认状态为 `STATUS_ACTIVE = 1`（等待开始）
4. 只有 `moderation_status = 'approved'` 的活动才会对用户可见

这意味着我们的系统不需要自己做审核后台，平台的审核系统会自动过滤不当内容。

## 发布客户端设计

```python
class WoohelpsPublisher:
    def __init__(self, base_url: str, login_session: str):
        self.base_url = base_url
        self.login_session = login_session
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def publish_activity(self, activity: ProcessedActivity) -> dict:
        """发布活动到海外新生活平台"""
        data = {
            "name": activity.title_zh,
            "description": activity.description_zh,
            "html": activity.html_zh,
            "city_id": activity.city_id,
            "start_time": activity.start_time_utc.strftime("%Y-%m-%d %H:%M"),
            "end_time": activity.end_time_utc.strftime("%Y-%m-%d %H:%M"),
            "address": activity.address,
            "img": activity.image_url or "",
            "imgs": json.dumps(activity.image_urls),
            "fee_type": 1 if activity.fee_parsed_free else 2,
            "fee": activity.fee_amount,  # 已解析的 float，解析失败为 0
            "enroll_type": 1,     # 不需要报名
            "remind_type": 1,     # 不提醒
            "groupon_type": 1,    # 非团购
        }
        headers = {"LOGIN_SESSION": self.login_session}
        response = await self.client.post(
            f"{self.base_url}/api/applet/activity/release/",
            data=data,
            headers=headers,
        )
        return response.json()
```

### 字段说明：`type` 和 `period`

后端 `release_activity` **不会**从请求中读取 `type` 和 `period` 字段（参考 `reference/overseas-new-life/overseas_api/src/apps/content/views/activity.py:464-509`）。所有已发布活动的类型默认为 `1`（一般活动），周期默认为一次性。AI 分类结果仅存储在本地数据库用于后续分析，不影响发布。

如需在发布时指定活动类型，需要先修改后端 `release_activity` 接口增加对 `type` 字段的读取。

### 字段说明：`fee` 价格解析

后端 `release_activity` 对 `fee` 执行 `float(request.POST.get("fee", 0))`，非数字字符串会导致 500 错误。但爬虫拿到的 `price` 是原始字符串（如 `"Free"`、`"$10-$20"`、`"$5"`），必须预先解析：

```python
import re

def parse_fee_amount(price: str | None) -> tuple[float, bool]:
    """解析价格字符串，返回 (金额, 是否免费)。

    - "Free" / "" / None → (0.0, True)
    - "$5" → (5.0, False)
    - "$10-$20" → (10.0, False)  取最低价
    - 解析失败 → (0.0, False)，原始价格应追加到 description/html
    """
    if not price:
        return 0.0, True
    price = price.strip()
    if price.lower() in ("free", "free!"):
        return 0.0, True
    # 匹配金额数字
    numbers = re.findall(r"\$(\d+(?:\.\d+)?)", price)
    if numbers:
        return float(numbers[0]), False
    # 兜底：尝试提取任意数字
    numbers = re.findall(r"(\d+(?:\.\d+)?)", price)
    if numbers:
        return float(numbers[0]), False
    # 无法解析 → 传 0，原始价格由调用方追加到描述中
    return 0.0, False
```

**`is_free` 来源规则**: 发布时的 `fee_type` 和 `fee` 必须统一从 `parse_fee_amount` 的结果派生，而不是使用爬虫原始的 `is_free` 字段。原因：爬虫对 `is_free` 的判断逻辑各不相同（有的看 `"Free"` 文本，有的看 `cost` 字段是否为空），可能与价格解析结果矛盾。处理流程：

1. 爬虫写入 `price` 原始字符串和初步 `is_free`
2. 存储阶段调用 `parse_fee_amount(price)` → 得到 `(fee_amount, fee_parsed_free)`
3. 将 `fee_amount` 和 `fee_parsed_free` 写入数据库，覆盖爬虫的 `is_free`
4. 发布时使用 `fee_parsed_free` 决定 `fee_type`，使用 `fee_amount` 作为 `fee`

| 错误码 | 含义 | 处理策略 |
|--------|------|---------|
| 0 | 成功 | 记录 activity_id |
| 101 | 参数错误 | 记录日志，跳过该活动 |
| 201 | 业务错误（时间等） | 记录日志，跳过该活动 |
| 500 | 系统错误 | 重试 3 次 |

## 城市映射 — 自动获取

城市 ID 不需要手动配置。启动时通过平台 API 自动获取映射关系。

**获取热门城市 API**: `GET /api/applet/city/hot/get/`
- 返回所有热门城市的 `id`、`name`、`eng_name`
- 使用 `LOGIN_SESSION` header 认证

**按英文名搜索 API**: `GET /api/applet/city/eng/search/?eng_name=Toronto`
- 精确匹配城市英文名
- 返回 `city_id`、`city_name`、`city_eng_name`

**启动时自动映射流程**:
```python
async def fetch_city_mapping(client, base_url, login_session):
    """启动时调用平台 API 获取城市 ID 映射"""
    resp = await client.get(
        f"{base_url}/api/applet/city/hot/get/",
        headers={"LOGIN_SESSION": login_session},
    )
    # 遍历返回的城市列表，建立 eng_name → city_id 映射
    # 例如: {"Toronto": 1, "Vancouver": 2, ...}
```

**城市配置**（只需维护英文名和坐标）:
```python
CITIES = {
    "toronto":    {"eng_name": "Toronto",     "lat": 43.6532,  "lng": -79.3832,  "radius": "50km"},
    "vancouver":  {"eng_name": "Vancouver",   "lat": 49.2827,  "lng": -123.1207, "radius": "50km"},
    "montreal":   {"eng_name": "Montreal",    "lat": 45.5017,  "lng": -73.5673,  "radius": "50km"},
    "calgary":    {"eng_name": "Calgary",     "lat": 51.0447,  "lng": -114.0719, "radius": "50km"},
    "edmonton":   {"eng_name": "Edmonton",    "lat": 53.5461,  "lng": -113.4938, "radius": "50km"},
    "ottawa":     {"eng_name": "Ottawa",      "lat": 45.4215,  "lng": -75.6972,  "radius": "50km"},
    "winnipeg":   {"eng_name": "Winnipeg",    "lat": 49.8954,  "lng": -97.1385,  "radius": "50km"},
    "saskatoon":  {"eng_name": "Saskatoon",   "lat": 52.1332,  "lng": -106.6700, "radius": "50km"},
    "regina":     {"eng_name": "Regina",      "lat": 50.4452,  "lng": -104.6189, "radius": "50km"},
    "moncton":    {"eng_name": "Moncton",     "lat": 46.0878,  "lng": -64.7782,  "radius": "50km"},
}
```

`woohelps_city_id` 在运行时自动从 API 获取并缓存。

## 时区转换

平台 API 要求 UTC 时间（后端按 UTC 解析）。爬虫抓取的活动时间是城市本地时间，必须在发布前转换为 UTC。

### 城市 IANA 时区映射

```python
from zoneinfo import ZoneInfo

CITY_TIMEZONES = {
    "toronto":    ZoneInfo("America/Toronto"),      # UTC-5 (EST) / UTC-4 (EDT)
    "vancouver":  ZoneInfo("America/Vancouver"),    # UTC-8 (PST) / UTC-7 (PDT)
    "montreal":   ZoneInfo("America/Toronto"),      # 同 Toronto
    "calgary":    ZoneInfo("America/Edmonton"),     # UTC-7 (MST) / UTC-6 (MDT)
    "edmonton":   ZoneInfo("America/Edmonton"),     # 同 Calgary
    "ottawa":     ZoneInfo("America/Toronto"),      # 同 Toronto
    "winnipeg":   ZoneInfo("America/Winnipeg"),     # UTC-6 (CST) / UTC-5 (CDT)
    "saskatoon":  ZoneInfo("America/Regina"),       # UTC-6 (CST, 不使用夏令时)
    "regina":     ZoneInfo("America/Regina"),       # 同 Saskatoon
    "moncton":    ZoneInfo("America/Moncton"),      # UTC-4 (AST) / UTC-3 (ADT)
}
```

### 转换逻辑

所有爬虫抓取的本地时间（naive datetime）必须在存入数据库前标注时区并转换为 UTC：

```python
from datetime import datetime, timezone

def local_to_utc(local_dt: datetime, city_slug: str) -> datetime:
    """将城市本地时间转为 UTC naive datetime（供平台 API 使用）。

    输入: naive datetime（无时区信息，代表城市当地时间）
    输出: naive datetime（UTC，用于 strftime 后传给平台 API）
    """
    tz = CITY_TIMEZONES[city_slug]
    # 标注为本地时区
    aware = local_dt.replace(tzinfo=tz)
    # 转为 UTC 并剥离时区信息
    utc_dt = aware.astimezone(timezone.utc).replace(tzinfo=None)
    return utc_dt
```

### 注意事项

- **夏令时**: `zoneinfo` 自动处理 DST 切换，无需手动判断
- **Saskatoon/Regina**: 使用 `America/Regina` 时区，该地区不使用夏令时
- **爬虫侧**: 爬虫应存储原始本地时间（`start_time_local`），在 AI 处理或发布前统一转换
- **数据库**: 存储两列 — `start_time`（UTC）用于去重和排序，`start_time_local` 用于本地显示和调试
