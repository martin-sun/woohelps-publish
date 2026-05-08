import hashlib
import re

from src.models.activity import ProcessedActivity


def _normalize_text(text: str) -> str:
    """归一化文本用于 hash 比较：小写、去除多余空白和标点"""
    text = text.lower().strip()
    text = re.sub(r'[,\.\-#]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def compute_html_hash(html: str) -> str:
    return hashlib.sha256(html.encode()).hexdigest()[:32]


def compute_content_hash(activity: ProcessedActivity) -> str:
    """基于标题+开始时间+地址生成内容哈希"""
    title = _normalize_text(activity.title_en)
    start = activity.start_time_utc.isoformat() if activity.start_time_utc else ""
    address = _normalize_text(activity.address or "")
    key = f"{title}|{start}|{address}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]
