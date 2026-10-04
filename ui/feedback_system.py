"""
玩家反馈与bug报告系统 - 核心模块

功能：
- 反馈提交：实际发送到服务器、内容校验、提交冷却
- 截图：截图捕获、画圈标注、提交时自动附加
- 反馈分类：自动/手动分类、优先级、标签
- 反馈状态：状态流转、处理通知、历史记录
- 反馈奖励：按优先级分级奖励、反作弊（去重/限频）
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math
import time


# 分类 -> 优先级（数值越大优先级越高）
CATEGORY_PRIORITY = {
    "bug": 3,
    "complaint": 2,
    "suggestion": 1,
    "other": 0,
}

# 优先级 -> 基础奖励
REWARD_BY_PRIORITY = {
    3: 100,
    2: 50,
    1: 20,
    0: 0,
}

# 反馈状态流转
STATUS_FLOW = ("pending", "processing", "resolved")

# 内容关键词 -> 自动分类
_CATEGORY_KEYWORDS = {
    "bug": ("bug", "崩溃", "闪退", "卡住", "报错", "黑屏", "漏洞"),
    "complaint": ("投诉", "举报", "外挂", "骗子", "骂人"),
    "suggestion": ("建议", "希望", "可以", "优化", "改进"),
}


@dataclass
class Config:
    cooldown: float = 60.0          # 同一玩家提交冷却（秒）
    max_rewards_per_day: int = 5    # 同一玩家每日奖励上限（反作弊）
    min_content_length: int = 1     # 反馈内容最小长度


class FeedbackSystem:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._cooldown = float(self.config.get("cooldown", Config.cooldown))
        self._max_rewards_per_day = int(
            self.config.get("max_rewards_per_day", Config.max_rewards_per_day)
        )
        self._min_content_length = int(
            self.config.get("min_content_length", Config.min_content_length)
        )

        self._state = {}
        self._history: List[Dict] = []          # 全部反馈记录
        self._feedback_by_id: Dict[str, Dict] = {}
        self._sent_to_server: List[Dict] = []   # 已发送到服务器的反馈
        self._screenshots: Dict[str, Dict] = {}
        self._player_screenshots: Dict[str, List[str]] = {}
        self._last_submit_time: Dict[str, float] = {}
        self._notifications: Dict[str, List[Dict]] = {}
        self._rewards: Dict[str, List[Dict]] = {}   # player_id -> 奖励记录
        self._next_feedback_id = 1
        self._next_screenshot_id = 1

    # ------------------------------------------------------------------
    # 反馈提交
    # ------------------------------------------------------------------
    def submit_feedback(
        self,
        player_id: str,
        content: str,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict:
        """提交反馈：校验内容 -> 冷却检查 -> 反作弊 -> 发送服务器 -> 奖励。"""
        # 1. 内容校验：空反馈不允许提交
        text = (content or "").strip()
        if len(text) < self._min_content_length:
            return {"success": False, "error": "empty_content"}

        # 2. 提交冷却：防止无限刷反馈
        now = time.time()
        last = self._last_submit_time.get(player_id)
        if last is not None and now - last < self._cooldown:
            return {
                "success": False,
                "error": "cooldown",
                "retry_after": self._cooldown - (now - last),
            }

        # 3. 反作弊：同一玩家重复内容视为无效反馈
        is_duplicate = any(
            r["player_id"] == player_id and r["content"] == text
            for r in self._history
        )

        # 4. 分类、优先级、标签
        category = category or self._detect_category(text)
        if category not in CATEGORY_PRIORITY:
            category = "other"
        priority = CATEGORY_PRIORITY[category]
        tags = list(tags or [])

        feedback_id = "FB-%d" % self._next_feedback_id
        self._next_feedback_id += 1

        # 5. 截图自动附加：取该玩家最近一张未附加的截图
        screenshot_id = self._latest_unattached_screenshot(player_id)

        record = {
            "id": feedback_id,
            "player_id": player_id,
            "content": text,
            "category": category,
            "priority": priority,
            "tags": tags,
            "screenshot_id": screenshot_id,
            "status": "pending",
            "duplicate": is_duplicate,
            "timestamp": now,
        }

        # 6. 实际发送到服务器（模拟网络发送，失败则不记账）
        if not self._send_to_server(record):
            return {"success": False, "error": "send_failed"}

        if screenshot_id is not None:
            self._screenshots[screenshot_id]["attached_to"] = feedback_id

        self._last_submit_time[player_id] = now
        self._history.append(record)
        self._feedback_by_id[feedback_id] = record

        # 7. 反馈奖励（分级 + 反作弊）
        reward = self._grant_reward(record)

        return {
            "success": True,
            "feedback_id": feedback_id,
            "category": category,
            "priority": priority,
            "tags": tags,
            "screenshot_id": screenshot_id,
            "reward": reward,
        }

    def _send_to_server(self, record: Dict) -> bool:
        """把反馈实际发送到服务器（此处为模拟实现）。"""
        self._sent_to_server.append(record)
        return True

    @property
    def sent_count(self) -> int:
        return len(self._sent_to_server)

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------
    def screenshot(
        self,
        player_id: str,
        annotations: Optional[List[Dict]] = None,
    ) -> Dict:
        """截图，可选地直接带上画圈标注。"""
        screenshot_id = "SS-%d" % self._next_screenshot_id
        self._next_screenshot_id += 1
        shot = {
            "id": screenshot_id,
            "player_id": player_id,
            "timestamp": time.time(),
            "data": "screenshot:%s" % screenshot_id,  # 模拟图像数据
            "annotations": [],
            "attached_to": None,
        }
        self._screenshots[screenshot_id] = shot
        self._player_screenshots.setdefault(player_id, []).append(screenshot_id)
        for ann in annotations or []:
            self.annotate(screenshot_id, **ann)
        return shot

    def annotate(
        self,
        screenshot_id: str,
        x: float,
        y: float,
        radius: float = 20.0,
        label: str = "",
    ) -> Dict:
        """在截图上画圈标注 bug 位置。"""
        shot = self._screenshots.get(screenshot_id)
        if shot is None:
            raise KeyError("screenshot not found: %s" % screenshot_id)
        annotation = {
            "type": "circle",
            "x": x,
            "y": y,
            "radius": radius,
            "label": label,
        }
        shot["annotations"].append(annotation)
        return annotation

    def get_screenshot(self, screenshot_id: str) -> Optional[Dict]:
        return self._screenshots.get(screenshot_id)

    def _latest_unattached_screenshot(self, player_id: str) -> Optional[str]:
        for sid in reversed(self._player_screenshots.get(player_id, [])):
            if self._screenshots[sid]["attached_to"] is None:
                return sid
        return None

    # ------------------------------------------------------------------
    # 反馈分类
    # ------------------------------------------------------------------
    def feedback_category(self, feedback_id: str) -> Dict:
        """返回反馈的分类信息：类别、优先级、标签。"""
        record = self._feedback_by_id.get(feedback_id)
        if record is None:
            raise KeyError("feedback not found: %s" % feedback_id)
        return {
            "category": record["category"],
            "priority": record["priority"],
            "tags": list(record["tags"]),
        }

    def add_tags(self, feedback_id: str, tags: List[str]) -> List[str]:
        """给反馈添加标签。"""
        record = self._feedback_by_id.get(feedback_id)
        if record is None:
            raise KeyError("feedback not found: %s" % feedback_id)
        for tag in tags:
            if tag not in record["tags"]:
                record["tags"].append(tag)
        return list(record["tags"])

    def list_by_category(self, category: str) -> List[Dict]:
        """按分类查看反馈，不再全部混在一起。"""
        return [r for r in self._history if r["category"] == category]

    def _detect_category(self, content: str) -> str:
        lowered = content.lower()
        for category, keywords in _CATEGORY_KEYWORDS.items():
            if any(k in lowered for k in keywords):
                return category
        return "other"

    # ------------------------------------------------------------------
    # 反馈状态
    # ------------------------------------------------------------------
    def feedback_status(self, feedback_id: str) -> Dict:
        """查询反馈处理状态。"""
        record = self._feedback_by_id.get(feedback_id)
        if record is None:
            raise KeyError("feedback not found: %s" % feedback_id)
        return {"feedback_id": feedback_id, "status": record["status"]}

    def update_status(self, feedback_id: str, status: str) -> Dict:
        """更新处理状态，并给玩家发送通知。"""
        record = self._feedback_by_id.get(feedback_id)
        if record is None:
            raise KeyError("feedback not found: %s" % feedback_id)
        if status not in STATUS_FLOW:
            raise ValueError("invalid status: %s" % status)
        record["status"] = status
        notification = {
            "feedback_id": feedback_id,
            "status": status,
            "message": "你的反馈 %s 状态已更新为 %s" % (feedback_id, status),
            "timestamp": time.time(),
        }
        self._notifications.setdefault(record["player_id"], []).append(notification)
        return notification

    def get_notifications(self, player_id: str) -> List[Dict]:
        """玩家收到的处理通知。"""
        return list(self._notifications.get(player_id, []))

    def get_history(self, player_id: str) -> List[Dict]:
        """玩家自己的历史反馈记录。"""
        return [r for r in self._history if r["player_id"] == player_id]

    # ------------------------------------------------------------------
    # 反馈奖励
    # ------------------------------------------------------------------
    def feedback_reward(self, player_id: str) -> Dict:
        """玩家累计奖励情况。"""
        rewards = self._rewards.get(player_id, [])
        return {
            "player_id": player_id,
            "total": sum(r["amount"] for r in rewards),
            "count": len(rewards),
            "records": list(rewards),
        }

    def _grant_reward(self, record: Dict) -> int:
        """按优先级分级奖励；重复反馈与超限玩家不给奖励（反作弊）。"""
        player_id = record["player_id"]
        if record["duplicate"]:
            return 0
        today_rewards = self._rewards.get(player_id, [])
        if len(today_rewards) >= self._max_rewards_per_day:
            return 0
        amount = REWARD_BY_PRIORITY.get(record["priority"], 0)
        if amount <= 0:
            return 0
        self._rewards.setdefault(player_id, []).append({
            "feedback_id": record["id"],
            "amount": amount,
            "timestamp": record["timestamp"],
        })
        return amount

    # ------------------------------------------------------------------
    # 框架接口
    # ------------------------------------------------------------------
    def update(self, dt: float):
        pass

    def reset(self):
        self._state = {}
        self._history = []
        self._feedback_by_id = {}
        self._sent_to_server = []
        self._screenshots = {}
        self._player_screenshots = {}
        self._last_submit_time = {}
        self._notifications = {}
        self._rewards = {}
        self._next_feedback_id = 1
        self._next_screenshot_id = 1
