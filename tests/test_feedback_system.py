"""
玩家反馈与bug报告系统 - 验收测试
运行方式：python -m pytest tests/test_feedback_system.py -q
共 13 个测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.feedback_system import (
    FeedbackSystem,
    PRIORITY_ORDER,
    REWARD_TIERS,
    STATUS_SUBMITTED,
    STATUS_PROCESSING,
    STATUS_RESOLVED,
)

COOLDOWN = 30.0


class TestFeedbackSystem:
    def setup_method(self):
        self.system = FeedbackSystem()

    def _submit_ok(self, player="p1", content="游戏闪退无法登录", **kwargs):
        result = self.system.submit_feedback(player, content, **kwargs)
        assert result["ok"], result
        return result

    # 1. 提交实际发送到服务器
    def test_case_01(self):
        result = self._submit_ok(content="角色卡在墙里出不来")
        assert len(self.system._server_inbox) == 1
        sent = self.system._server_inbox[0]
        assert sent["id"] == result["feedback_id"]
        assert sent["content"] == "角色卡在墙里出不来"
        assert sent["player_id"] == "p1"

    # 2. 空反馈/纯空白反馈被拒绝
    def test_case_02(self):
        for bad in ("", "   ", "\n\t ", "ab"):
            result = self.system.submit_feedback("p1", bad)
            assert not result["ok"]
        assert self.system._server_inbox == []

    # 3. 提交冷却：冷却期内重复提交被拒绝，冷却结束后可提交
    def test_case_03(self):
        assert self.system.submit_feedback("p1", "第一个反馈内容")["ok"]
        result = self.system.submit_feedback("p1", "第二个反馈内容")
        assert not result["ok"]
        assert result["error"] == "cooldown"
        assert result["retry_after"] > 0
        self.system.update(COOLDOWN)
        assert self.system.submit_feedback("p1", "第二个反馈内容")["ok"]
        # 冷却按玩家隔离
        assert self.system.submit_feedback("p2", "另一个玩家的反馈")["ok"]

    # 4. 截图功能可用
    def test_case_04(self):
        shot = self.system.screenshot()
        assert shot["id"].startswith("SS-")
        assert shot["width"] > 0 and shot["height"] > 0
        assert shot["data"]
        assert shot["annotations"] == []

    # 5. 截图支持标注（画圈标记bug位置），非法标注被拒绝
    def test_case_05(self):
        shot = self.system.screenshot(annotations=[
            {"type": "circle", "x": 100, "y": 200, "radius": 30},
            {"type": "arrow", "x": 50, "y": 60, "to_x": 100, "to_y": 200},
        ])
        assert len(shot["annotations"]) == 2
        assert shot["annotations"][0]["type"] == "circle"
        with pytest.raises(ValueError):
            self.system.screenshot(annotations=[{"type": "laser", "x": 1, "y": 2}])
        with pytest.raises(ValueError):
            self.system.screenshot(annotations=[{"type": "circle"}])

    # 6. 截图自动附加到下一条反馈，附加后清空
    def test_case_06(self):
        shot = self.system.screenshot()
        result = self._submit_ok(content="这里显示错位了")
        record = self.system._server_inbox[0]
        assert record["screenshot"] is not None
        assert record["screenshot"]["id"] == shot["id"]
        # 已附加的截图不会重复附加到后续反馈
        self.system.update(COOLDOWN)
        self._submit_ok(content="第二条反馈内容")
        assert self.system._server_inbox[1]["screenshot"] is None

    # 7. 反馈自动分类：bug / 建议 / 其他
    def test_case_07(self):
        assert self.system.categorize("游戏一直闪退报错") == "bug"
        assert self.system.categorize("建议增加自动寻路功能") == "suggestion"
        assert self.system.categorize("今天天气不错") == "other"
        r1 = self._submit_ok(content="游戏一直闪退报错")
        assert r1["category"] == "bug"
        self.system.update(COOLDOWN)
        r2 = self._submit_ok(content="建议增加自动寻路功能")
        assert r2["category"] == "suggestion"

    # 8. 分类有优先级：bug 高于建议，严重bug为 critical
    def test_case_08(self):
        bug = self._submit_ok(content="发现一个显示错误的bug")
        self.system.update(COOLDOWN)
        suggestion = self._submit_ok(content="建议优化一下界面")
        self.system.update(COOLDOWN)
        critical = self._submit_ok(content="游戏崩溃导致存档数据丢失")
        assert PRIORITY_ORDER[bug["priority"]] > PRIORITY_ORDER[suggestion["priority"]]
        assert critical["priority"] == "critical"

    # 9. 反馈支持玩家自定义标签
    def test_case_09(self):
        self._submit_ok(content="副本里怪物卡住不动",
                        tags=["副本", "怪物AI"])
        record = self.system._server_inbox[0]
        assert record["tags"] == ["副本", "怪物AI"]

    # 10. 反馈状态随处理推进：submitted -> processing -> resolved
    def test_case_10(self):
        result = self._submit_ok(content="任务无法提交的问题")
        fid = result["feedback_id"]
        assert self.system.get_status(fid) == STATUS_SUBMITTED
        self.system.update(2.0)
        assert self.system.get_status(fid) == STATUS_PROCESSING
        self.system.update(10.0)
        assert self.system.get_status(fid) == STATUS_RESOLVED
        assert self.system.get_status("FB-9999") is None

    # 11. 状态变化时玩家收到通知
    def test_case_11(self):
        result = self._submit_ok(content="商店价格显示错误")
        assert self.system.get_notifications("p1") == []
        self.system.update(2.0)
        notes = self.system.get_notifications("p1")
        assert len(notes) == 1
        assert notes[0]["feedback_id"] == result["feedback_id"]
        assert notes[0]["status"] == STATUS_PROCESSING
        self.system.update(10.0)
        notes = self.system.get_notifications("p1")
        assert len(notes) == 2
        assert notes[-1]["status"] == STATUS_RESOLVED

    # 12. 玩家可查看自己的历史反馈
    def test_case_12(self):
        self._submit_ok(player="p1", content="第一条反馈内容")
        self.system.update(COOLDOWN)
        self._submit_ok(player="p1", content="第二条反馈内容")
        self._submit_ok(player="p2", content="其他玩家的反馈")
        history = self.system.get_history("p1")
        assert len(history) == 2
        assert [h["content"] for h in history] == ["第一条反馈内容", "第二条反馈内容"]
        assert all(h["player_id"] == "p1" for h in history)
        assert all("status" in h for h in history)

    # 13. 奖励分级 + 反作弊：严重bug奖励更高，重复反馈无奖励
    def test_case_13(self):
        critical = self._submit_ok(content="游戏崩溃导致存档数据丢失")
        self.system.update(COOLDOWN)
        suggestion = self._submit_ok(content="建议优化一下界面")
        assert critical["reward"] == REWARD_TIERS["critical"]
        assert suggestion["reward"] == REWARD_TIERS["low"]
        assert critical["reward"] > suggestion["reward"]
        # 反作弊：重复内容刷反馈不给奖励
        self.system.update(COOLDOWN)
        dup = self._submit_ok(content="游戏崩溃导致存档数据丢失")
        assert dup["duplicate"]
        assert dup["reward"] == 0
        assert self.system.get_reward("p1") == critical["reward"] + suggestion["reward"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
