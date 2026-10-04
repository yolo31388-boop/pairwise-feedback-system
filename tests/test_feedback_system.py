"""
玩家反馈与bug报告系统 - 验收测试
运行方式：python -m pytest tests/test_feedback_system.py -q
共 13 个测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.feedback_system import FeedbackSystem


class TestFeedbackSystem:
    def setup_method(self):
        # 默认关闭冷却便于多场景提交；冷却专项测试单独构造带冷却的实例
        self.system = FeedbackSystem({"cooldown": 0})

    # ---------------- 反馈提交 ----------------

    def test_case_01(self):
        """提交反馈实际发送到服务器，而不只是设置标志位"""
        result = self.system.submit_feedback("p1", "打怪的时候角色卡在墙里了")
        assert result["success"] is True
        assert self.system.sent_count == 1
        sent = self.system._sent_to_server[0]
        assert sent["id"] == result["feedback_id"]
        assert sent["content"] == "打怪的时候角色卡在墙里了"

    def test_case_02(self):
        """空反馈（空串/纯空白）不允许提交"""
        assert self.system.submit_feedback("p2", "")["success"] is False
        assert self.system.submit_feedback("p2", "   ")["success"] is False
        assert self.system.sent_count == 0

    def test_case_03(self):
        """提交冷却：冷却期内连续提交被拒绝"""
        system = FeedbackSystem({"cooldown": 60})
        assert system.submit_feedback("p3", "第一次反馈")["success"] is True
        second = system.submit_feedback("p3", "冷却期内第二次反馈")
        assert second["success"] is False
        assert second["error"] == "cooldown"
        assert system.sent_count == 1

    # ---------------- 截图 ----------------

    def test_case_04(self):
        """截图可以正常捕获"""
        shot = self.system.screenshot("p4")
        assert shot["id"] is not None
        assert shot["player_id"] == "p4"
        assert shot["data"]
        assert self.system.get_screenshot(shot["id"]) is shot

    def test_case_05(self):
        """截图支持画圈标注 bug 位置"""
        shot = self.system.screenshot("p5")
        self.system.annotate(shot["id"], x=120, y=80, radius=25, label="bug在这")
        fetched = self.system.get_screenshot(shot["id"])
        assert len(fetched["annotations"]) == 1
        ann = fetched["annotations"][0]
        assert ann["type"] == "circle"
        assert (ann["x"], ann["y"]) == (120, 80)
        assert ann["label"] == "bug在这"

    def test_case_06(self):
        """提交反馈时最近的截图自动附加，无需手动选择"""
        shot = self.system.screenshot("p6")
        self.system.annotate(shot["id"], x=10, y=10)
        result = self.system.submit_feedback("p6", "截图里这个位置显示异常")
        assert result["screenshot_id"] == shot["id"]
        assert self.system.get_screenshot(shot["id"])["attached_to"] == result["feedback_id"]

    # ---------------- 反馈分类 ----------------

    def test_case_07(self):
        """反馈按分类归档，不再全部混在一起"""
        bug = self.system.submit_feedback("p7a", "游戏闪退，有bug")
        suggestion = self.system.submit_feedback("p7b", "建议增加背包排序功能")
        bugs = self.system.list_by_category("bug")
        suggestions = self.system.list_by_category("suggestion")
        assert [r["id"] for r in bugs] == [bug["feedback_id"]]
        assert [r["id"] for r in suggestions] == [suggestion["feedback_id"]]

    def test_case_08(self):
        """bug 反馈优先级高于建议反馈"""
        bug = self.system.submit_feedback("p8a", "战斗结算时崩溃了")
        suggestion = self.system.submit_feedback("p8b", "希望能出个夜间模式")
        assert bug["category"] == "bug"
        assert suggestion["category"] == "suggestion"
        assert bug["priority"] > suggestion["priority"]

    def test_case_09(self):
        """反馈支持标签"""
        result = self.system.submit_feedback("p9", "副本boss血量显示错误", tags=["pve"])
        tags = self.system.add_tags(result["feedback_id"], ["ui", "pve"])
        assert "ui" in tags and "pve" in tags
        assert tags.count("pve") == 1  # 标签不重复
        assert "ui" in self.system.feedback_category(result["feedback_id"])["tags"]

    # ---------------- 反馈状态 ----------------

    def test_case_10(self):
        """状态更新后玩家能收到通知，不会石沉大海"""
        result = self.system.submit_feedback("p10", "充值后钻石没到账")
        fid = result["feedback_id"]
        assert self.system.feedback_status(fid)["status"] == "pending"
        self.system.update_status(fid, "processing")
        self.system.update_status(fid, "resolved")
        notes = self.system.get_notifications("p10")
        assert [n["status"] for n in notes] == ["processing", "resolved"]
        assert self.system.feedback_status(fid)["status"] == "resolved"

    def test_case_11(self):
        """玩家可以查看自己的历史反馈"""
        self.system.submit_feedback("p11a", "第一个问题")
        self.system.submit_feedback("p11b", "别人的反馈")
        self.system.submit_feedback("p11a", "第二个问题")
        history = self.system.get_history("p11a")
        assert len(history) == 2
        assert all(r["player_id"] == "p11a" for r in history)
        assert [r["content"] for r in history] == ["第一个问题", "第二个问题"]

    # ---------------- 反馈奖励 ----------------

    def test_case_12(self):
        """奖励按优先级分级：严重 bug 奖励高于普通建议"""
        bug = self.system.submit_feedback("p12a", "严重bug：数据丢失，账号回档")
        suggestion = self.system.submit_feedback("p12b", "建议登录界面换个音乐")
        assert bug["reward"] > suggestion["reward"]
        assert bug["reward"] > 0
        rewards = self.system.feedback_reward("p12a")
        assert rewards["total"] == bug["reward"]

    def test_case_13(self):
        """反作弊：重复/刷量反馈拿不到奖励"""
        system = FeedbackSystem({"cooldown": 0})
        first = system.submit_feedback("p13", "又遇到bug了，同样的问题再说一遍")
        assert first["reward"] > 0
        duplicate = system.submit_feedback("p13", "又遇到bug了，同样的问题再说一遍")
        assert duplicate["success"] is True
        assert duplicate["reward"] == 0
        assert system.feedback_reward("p13")["count"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
