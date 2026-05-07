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

    async def publish_activity(self, activity: ProcessedActivity) -> dict:
        """发布活动到海外新生活平台"""
        data = {
            "name": activity.title_zh,
            "description": activity.description_zh,
            "html": activity.html_zh,
            "city_id": activity.city_id,
            "start_time": activity.start_time.strftime("%Y-%m-%d %H:%M"),
            "end_time": activity.end_time.strftime("%Y-%m-%d %H:%M"),
            "address": activity.address,
            "img": activity.image_url or "",
            "imgs": json.dumps(activity.image_urls),
            "fee_type": 1 if activity.is_free else 2,
            "fee": activity.price or 0,
            "enroll_type": 1,     # 不需要报名
            "remind_type": 1,     # 不提醒
            "groupon_type": 1,    # 非团购
            "period": 1,          # 一次性
            "type": activity.activity_type,
        }
        headers = {"LOGIN_SESSION": self.login_session}
        response = await self.client.post(
            f"{self.base_url}/api/applet/activity/release/",
            data=data,
            headers=headers,
        )
        return response.json()
```

## 错误处理

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
