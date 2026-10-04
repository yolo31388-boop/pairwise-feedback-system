"""
玩家反馈与bug报告系统 - 核心模块

功能：
- 反馈提交：实际发送到服务器、内容校验、提交冷却
- 截图：截图捕获、标注（画圈/箭头/方框/文字）、自动附加到反馈
- 反馈分类：自动分类、优先级、玩家标签
- 反馈状态：状态流转、处理通知、历史记录
- 反馈奖励：按优先级分级奖励、反作弊（重复反馈/奖励上限）
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import re
import time


@dataclass
class Config:
    cooldown_seconds: float = 30.0
    min_content_length: int = 4
    max_content_length: int = 2000
    max_rewards_per_player: int = 5
    processing_delay: float = 1.0
    resolve_delay: float = 5.0


# 分类关键词
_BUG_KEYWORDS = ("bug", "崩溃", "闪退", "卡死", "错误", "报错", "异常",
                 "crash", "error", "黑屏", "掉线", "无法登录", "丢失")
_SUGGESTION_KEYWORDS = ("建议", "希望", "可以", "能不能", "suggest",
                        "feature", "优化", "改进")
# 严重度关键词 -> 提升为 critical
_CRITICAL_KEYWORDS = ("崩溃", "闪退", "卡死", "crash", "无法登录",
                      "数据丢失", "丢失", "黑屏")

# 分类 -> 基础优先级
_CATEGORY_PRIORITY = {
    "bug": "high",
    "suggestion": "low",
    "other": "normal",
}
# 优先级数值（用于比较）
PRIORITY_ORDER = {"low": 1, "normal": 2, "high": 3, "critical": 4}
# 优先级 -> 奖励（游戏币）
REWARD_TIERS = {"low": 5, "normal": 20, "high": 50, "critical": 100}

# 反馈状态流转
STATUS_SUBMITTED = "submitted"
STATUS_PROCESSING = "processing"
STATUS_RESOLVED = "resolved"

# 支持的截图标注类型
ANNOTATION_TYPES = ("circle", "arrow", "box", "text")


def _normalize_content(content: str) -> str:
    """归一化反馈内容，用于重复检测。"""
    return re.sub(r"\s+", " ", content.strip().lower())


class FeedbackSystem:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._cfg = Config(**{k: v for k, v in self.config.items()
                              if k in Config.__dataclass_fields__})
        self._state = {}
        self._history = []
        # 内部时钟（由 update(dt) 推进，保证行为确定可测）
        self._clock = 0.0
        # 模拟服务器收件箱：submit 实际发送到这里
        self._server_inbox: List[Dict] = []
        # 玩家维度数据
        self._last_submit_time: Dict[str, float] = {}
        self._seen_contents: Dict[str, set] = {}
        self._rewarded_count: Dict[str, int] = {}
        self._notifications: Dict[str, List[Dict]] = {}
        # 待附加的截图（截图后自动附加到下一条反馈）
        self._pending_screenshot: Optional[Dict] = None
        self._feedback_seq = 0
        self._screenshot_seq = 0

    # ------------------------------------------------------------------
    # 框架接口
    # ------------------------------------------------------------------
    def update(self, dt: float):
        """推进内部时钟并驱动反馈状态流转，状态变化时产生通知。"""
        self._clock += dt
        for record in self._history:
            elapsed = self._clock - record["submitted_at"]
            old_status = record["status"]
            if elapsed >= self._cfg.resolve_delay:
                record["status"] = STATUS_RESOLVED
            elif elapsed >= self._cfg.processing_delay:
                record["status"] = STATUS_PROCESSING
            if record["status"] != old_status:
                self._notify(record["player_id"], record)

    def reset(self):
        self._state = {}
        self._history = []
        self._clock = 0.0
        self._server_inbox = []
        self._last_submit_time = {}
        self._seen_contents = {}
        self._rewarded_count = {}
        self._notifications = {}
        self._pending_screenshot = None
        self._feedback_seq = 0
        self._screenshot_seq = 0

    # ------------------------------------------------------------------
    # 反馈提交
    # ------------------------------------------------------------------
    def submit_feedback(self, player_id: str, content: str,
                        category: Optional[str] = None,
                        tags: Optional[List[str]] = None,
                        attach_screenshot: bool = True) -> Dict:
        """提交反馈：校验内容 -> 冷却检查 -> 分类/优先级 -> 发送到服务器。"""
        # 1. 内容校验：空反馈、过短、过长都拒绝
        if not isinstance(content, str) or not content.strip():
            return {"ok": False, "error": "empty_content"}
        content = content.strip()
        if len(content) < self._cfg.min_content_length:
            return {"ok": False, "error": "content_too_short"}
        if len(content) > self._cfg.max_content_length:
            return {"ok": False, "error": "content_too_long"}

        # 2. 提交冷却：防止玩家无限刷反馈
        last = self._last_submit_time.get(player_id)
        if last is not None and self._clock - last < self._cfg.cooldown_seconds:
            retry_after = self._cfg.cooldown_seconds - (self._clock - last)
            return {"ok": False, "error": "cooldown",
                    "retry_after": round(retry_after, 3)}

        # 3. 分类 / 优先级 / 标签
        category = category or self.categorize(content)
        priority = self._priority_for(content, category)
        tags = list(tags) if tags else []

        # 4. 反作弊：重复内容检测 + 奖励上限
        normalized = _normalize_content(content)
        seen = self._seen_contents.setdefault(player_id, set())
        duplicate = normalized in seen
        seen.add(normalized)
        rewarded_so_far = self._rewarded_count.get(player_id, 0)
        reward_capped = rewarded_so_far >= self._cfg.max_rewards_per_player
        if duplicate or reward_capped:
            reward = 0
        else:
            reward = REWARD_TIERS[priority]
            self._rewarded_count[player_id] = rewarded_so_far + 1

        # 5. 截图自动附加
        screenshot = None
        if attach_screenshot and self._pending_screenshot is not None:
            screenshot = self._pending_screenshot
            self._pending_screenshot = None

        # 6. 生成记录并实际发送到服务器
        self._feedback_seq += 1
        record = {
            "id": "FB-%04d" % self._feedback_seq,
            "player_id": player_id,
            "content": content,
            "category": category,
            "priority": priority,
            "tags": tags,
            "screenshot": screenshot,
            "status": STATUS_SUBMITTED,
            "submitted_at": self._clock,
            "reward": reward,
            "duplicate": duplicate,
        }
        self._server_inbox.append(record)  # 实际发送
        self._history.append(record)
        self._last_submit_time[player_id] = self._clock
        return {"ok": True, "feedback_id": record["id"], "category": category,
                "priority": priority, "reward": reward, "duplicate": duplicate}

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------
    def screenshot(self, annotations: Optional[List[Dict]] = None) -> Dict:
        """截取当前游戏画面（模拟），支持标注，截图自动附加到下一条反馈。"""
        validated = []
        for ann in annotations or []:
            validated.append(self._validate_annotation(ann))
        self._screenshot_seq += 1
        shot = {
            "id": "SS-%04d" % self._screenshot_seq,
            "width": 1920,
            "height": 1080,
            "format": "png",
            "data": "<simulated-framebuffer-%d>" % self._screenshot_seq,
            "annotations": validated,
            "taken_at": self._clock,
        }
        self._pending_screenshot = shot
        return shot

    def _validate_annotation(self, ann: Dict) -> Dict:
        if not isinstance(ann, dict):
            raise ValueError("annotation must be a dict")
        ann_type = ann.get("type")
        if ann_type not in ANNOTATION_TYPES:
            raise ValueError("unsupported annotation type: %r" % (ann_type,))
        for key in ("x", "y"):
            if key not in ann or not isinstance(ann[key], (int, float)):
                raise ValueError("annotation missing coordinate: %s" % key)
        return dict(ann)

    # ------------------------------------------------------------------
    # 反馈分类
    # ------------------------------------------------------------------
    def categorize(self, content: str) -> str:
        """根据内容关键词自动分类：bug / suggestion / other。"""
        text = content.lower()
        if any(kw in text for kw in _BUG_KEYWORDS):
            return "bug"
        if any(kw in text for kw in _SUGGESTION_KEYWORDS):
            return "suggestion"
        return "other"

    def _priority_for(self, content: str, category: str) -> str:
        """确定优先级：含严重关键词的反馈提升为 critical。"""
        text = content.lower()
        if any(kw in text for kw in _CRITICAL_KEYWORDS):
            return "critical"
        return _CATEGORY_PRIORITY.get(category, "normal")

    # ------------------------------------------------------------------
    # 反馈状态
    # ------------------------------------------------------------------
    def get_status(self, feedback_id: str) -> Optional[str]:
        for record in self._history:
            if record["id"] == feedback_id:
                return record["status"]
        return None

    def get_history(self, player_id: str) -> List[Dict]:
        """玩家自己的历史反馈（含状态），按提交时间排序。"""
        return [dict(r) for r in self._history
                if r["player_id"] == player_id]

    def get_notifications(self, player_id: str) -> List[Dict]:
        """玩家收到的处理进度通知。"""
        return list(self._notifications.get(player_id, []))

    def _notify(self, player_id: str, record: Dict):
        self._notifications.setdefault(player_id, []).append({
            "feedback_id": record["id"],
            "status": record["status"],
            "message": "您的反馈 %s 状态已更新为 %s" % (
                record["id"], record["status"]),
        })

    # ------------------------------------------------------------------
    # 反馈奖励
    # ------------------------------------------------------------------
    def get_reward(self, player_id: str) -> int:
        """玩家累计获得的反馈奖励（重复/超限反馈不计）。"""
        return sum(r["reward"] for r in self._history
                   if r["player_id"] == player_id)
