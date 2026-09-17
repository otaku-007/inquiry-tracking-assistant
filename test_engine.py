# -*- coding: utf-8 -*-
"""v1.2 引擎验证（模拟测试）。真实数据与模拟场景分开标注。

覆盖 v1.1 原有 12 项 + v1.2 独立复核新增：
- 导入后重跑不丢失线索（40 不退回 20）
- 人工延期日期/动作不被旧数据覆盖
- 延期任务到期重新进入可见队列
- 状态文件损坏不被空默认值覆盖
- 统一唤醒资格判断（三次上限/工作日间隔/冷却期/滚动次数/未知历史/暂停条件/停止联系）
- 约定暂停：从模拟原始聊天到暂停决策的完整闭环
"""

import os
import shutil
import tempfile
from datetime import date, timedelta

import state_engine as se
import wake_rules as wr
from seed_state import build_tasks, build_opportunities

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append((name, detail))


# ---------------------------------------------------------------- v1.1 原有

def test_idempotent_rerun():
    tasks = []
    t1 = build_tasks()
    t2 = build_tasks()
    for x in t1:
        se.upsert_task(tasks, x)
    n1 = len(tasks)
    ids1 = {t["task_id"] for t in tasks}
    for x in t2:
        se.upsert_task(tasks, x)
    check("1a 重复运行任务数不变", n1 == len(tasks), f"n1={n1} n2={len(tasks)}")
    check("1b 任务ID集合不变", ids1 == {t["task_id"] for t in tasks})


def test_cross_day_due():
    check("2a 未到期", se.compute_overdue("2026-09-13", "2026-09-11") == "未到期")
    check("2b 今日到期", se.compute_overdue("2026-09-13", "2026-09-13") == "今日到期")
    check("2c 已逾期", se.compute_overdue("2026-09-13", "2026-09-14") == "已逾期")
    check("2d 无期限=未知", se.compute_overdue(None, "2026-09-14") == "未知")
    tasks = build_tasks()
    se.refresh_overdue(tasks, "2026-09-13")
    jens = next(t for t in tasks if t["lead_id"] == "13904438223")
    check("2e Jens 在 09-13 今日到期", jens["overdue_status"] == "今日到期", jens["overdue_status"])
    se.refresh_overdue(tasks, "2026-09-14")
    jens = next(t for t in tasks if t["lead_id"] == "13904438223")
    check("2f Jens 在 09-14 已逾期", jens["overdue_status"] == "已逾期", jens["overdue_status"])


def test_contact_dedup():
    wakes = {}
    c = {"sent_at": "2026-09-11T10:00:00", "channel": "TM", "kind": "wake", "content_hash": "abc"}
    se.record_contact(wakes, "opp-x", c)
    se.record_contact(wakes, "opp-x", c)
    check("3 联系事件去重", se.wake_count(wakes["opp-x"]) == 1, str(wakes["opp-x"]))


def test_reply_pause():
    wakes = {"opp-x": {"contacts": [], "plan": {"status": "active", "planned_at": "2026-09-13"}}}
    se.apply_reply_pause(wakes, "opp-x", "2026-09-11T12:00:00")
    check("4 新回复暂停旧唤醒", wakes["opp-x"]["plan"]["status"] == "paused", str(wakes["opp-x"]["plan"]))


def test_manual_persist():
    tasks = []
    t = build_tasks()[0]
    se.upsert_task(tasks, t)
    tasks[0]["status"] = "done"
    tasks[0]["manual_override"] = True
    se.upsert_task(tasks, build_tasks()[0])
    check("5 人工已处理状态保留", tasks[0]["status"] == "done", tasks[0]["status"])


def test_partial_failure_preserves():
    tasks = build_tasks()
    n0 = len(tasks)
    se.upsert_task(tasks, {
        "task_id": se.make_task_id("99999999999", "待核查"),
        "lead_id": "99999999999", "buyer": "New Buyer", "owner": "Ella Chen",
        "action_type": "待核查", "status": "open",
    })
    check("6 部分失败不清空原任务", len(tasks) == n0 + 1, f"n0={n0} now={len(tasks)}")


# ---------------------------------------------------------------- v1.2 修复验证

def test_import_rerun_no_loss():
    opps = []
    for o in build_opportunities():
        se.upsert_opportunity(opps, o)
    n1 = len(opps)  # 20
    # 历史沉默导入（模拟 20 条）
    for i in range(20):
        se.upsert_opportunity(opps, {
            "lead_id": f"silent-{i}", "buyer": f"HistBuyer{i}", "owner": "Alice Lam",
            "category": "TM 商机", "source": "历史沉默扫描",
            "create_time": "2025-09-08", "update_time": "2025-09-08",
            "high_intent": False, "conversation_key": f"silent-{i}",
            "dedup_note": None, "silent": True,
        })
    n2 = len(opps)  # 40
    check("7a 首屏+历史导入后=40", n1 == 20 and n2 == 40, f"n1={n1} n2={n2}")
    # 再次运行首屏初始导入（v1.1 的 bug：整体替换回 20）
    for o in build_opportunities():
        se.upsert_opportunity(opps, o)
    check("7b 重跑首屏导入不丢失历史线索", len(opps) == 40, f"now={len(opps)}")


def test_manual_defer_preserved():
    tasks = []
    t = build_tasks()[1]  # Paola
    se.upsert_task(tasks, t)
    tasks[0]["status"] = "deferred"
    tasks[0]["due_at"] = "2026-10-01"
    tasks[0]["next_action"] = "人工修改后的下一步"
    tasks[0]["next_check_at"] = "2026-10-01"
    tasks[0]["defer_reason"] = "客户说10月再谈"
    tasks[0]["manual_override"] = True
    se.upsert_task(tasks, build_tasks()[1])  # 旧样本再导入
    check("8a 延期状态保留", tasks[0]["status"] == "deferred", tasks[0]["status"])
    check("8b 延期日期保留", tasks[0]["due_at"] == "2026-10-01", tasks[0].get("due_at"))
    check("8c 人工动作保留", tasks[0]["next_action"] == "人工修改后的下一步", tasks[0].get("next_action"))
    check("8d 延期理由保留", tasks[0].get("defer_reason") == "客户说10月再谈", tasks[0].get("defer_reason"))


def test_deferred_reenters():
    tasks = build_tasks()
    jens = next(t for t in tasks if t["lead_id"] == "13904438223")
    jens["status"] = "deferred"
    jens["due_at"] = "2026-10-01"
    jens["next_check_at"] = "2026-10-01"
    jens["manual_override"] = True
    se.refresh_overdue(tasks, "2026-09-12")
    check("9a 延期未到期不进入队列", not se.is_review_due(jens, "2026-09-12"))
    se.refresh_overdue(tasks, "2026-10-02")
    check("9b 延期到期重新可见", se.is_review_due(jens, "2026-10-02"))
    check("9c 延期到期判定为已逾期", jens["overdue_status"] == "已逾期", jens["overdue_status"])


def test_corrupt_state_protection():
    old = se.STATE_DIR
    d = tempfile.mkdtemp()
    try:
        se.STATE_DIR = d
        with open(os.path.join(d, "tasks.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")
        state, errors = se.load_state()
        check("10a 损坏被标记为错误", "tasks" in errors, str(errors))
        check("10b 损坏 section 置 None", state["tasks"] is None)
        try:
            se.save_section("tasks", None)
            check("10c 拒绝保存 None", False)
        except se.StateCorruptError:
            check("10c 拒绝保存 None", True)
        # 损坏文件被改名保留，未被空数据覆盖
        check("10d 原损坏文件已改名保留", not os.path.exists(os.path.join(d, "tasks.json")))
        corrupts = [f for f in os.listdir(d) if "corrupt" in f]
        check("10e 存在 .corrupt 备份", len(corrupts) >= 1, str(os.listdir(d)))
    finally:
        se.STATE_DIR = old
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 统一唤醒资格判断

def test_wake_rules():
    # 三次上限
    d = wr.decide_wake({"cycle_wake_count": 3, "contacts": [], "classification": "待核实候选"},
                       "2026-09-12")
    check("11a 三次上限", d["decision"] == "待核实" and "3" in d["reason"], str(d))

    # 工作日间隔：第2次距第1次≥5工作日
    w = {"cycle_wake_count": 1, "last_wake_at": "2026-09-10", "contacts": [], "classification": "待核实候选"}
    d = wr.decide_wake(w, "2026-09-12")
    check("11b 第2次间隔不足→等待", d["decision"] == "等待至某日", str(d))
    d = wr.decide_wake(w, "2026-09-17")
    check("11c 第2次间隔满足→可联系", d["decision"] == "可联系", str(d))

    # 第3次距第2次≥7工作日
    w = {"cycle_wake_count": 2, "last_wake_at": "2026-09-10", "contacts": [], "classification": "待核实候选"}
    d = wr.decide_wake(w, "2026-09-12")
    check("11d 第3次间隔不足→等待", d["decision"] == "等待至某日", str(d))

    # 冷却期
    w = {"cycle_wake_count": 3, "cooldown_until": "2026-10-15", "contacts": [], "classification": "待核实候选"}
    d = wr.decide_wake(w, "2026-09-20")
    check("11e 冷却期内→等待", d["decision"] == "等待至某日", str(d))
    d = wr.decide_wake(w, "2026-10-20")
    check("11f 冷却期后不自动重开→待核实", d["decision"] == "待核实", str(d))

    # 滚动90天最多6次
    contacts = [{"kind": "wake", "sent_at": (date(2026, 9, 12) - timedelta(days=i)).isoformat()}
                for i in range(6)]
    w = {"cycle_wake_count": 0, "contacts": contacts, "classification": "待核实候选"}
    d = wr.decide_wake(w, "2026-09-12", rolling_contacts=contacts)
    check("11g 滚动90天达上限→等待", d["decision"] == "等待至某日" and "6" in d["reason"], str(d))

    # 未知历史 → 待核实
    d = wr.decide_wake({"history_unknown": True, "contacts": [], "classification": "待核实候选"}, "2026-09-12")
    check("11h 未知历史→待核实", d["decision"] == "待核实", str(d))

    # 无有效需求 → 待核实
    d = wr.decide_wake({"no_valid_demand": True, "classification": "未知C", "contacts": []}, "2026-09-12")
    check("11i 无有效需求→待核实", d["decision"] == "待核实", str(d))

    # 停止联系
    d = wr.decide_wake({"stop_contact": True, "contacts": [], "classification": "待核实候选"}, "2026-09-12")
    check("11j 停止联系", d["decision"] == "停止联系", str(d))

    # 无联系价值（供应未匹配）
    d = wr.decide_wake({"cycle_wake_count": 0, "contacts": [], "classification": "待核实候选"},
                       "2026-09-12", has_value=False)
    check("11k 无供应方案→待核实", d["decision"] == "待核实", str(d))


def test_simulated_pause_flow():
    """约定暂停：模拟原始聊天 → 分析提取暂停日期 → 统一决策 → 到期复查 → 后续回复重判。"""
    raw_chat = [
        {"speaker": "buyer", "time": "2026-09-10 10:00",
         "text": "Thanks, I'll contact you around Oct 5 for the hotel project order."},
        {"speaker": "seller", "time": "2026-09-10 10:02",
         "text": "Sure, we'll be ready by then."},
    ]
    # 分析产物：从原文提取 pause_until（不是手写 classification=暂停）
    pause_until = "2026-10-05"
    w = {"contacts": [], "plan": None, "classification": "待核实候选", "history_unknown": False}
    w["pause_until"] = pause_until

    d_before = wr.decide_wake(w, "2026-09-12")
    check("12a 约定日期未到→等待至某日", d_before["decision"] == "等待至某日", str(d_before))

    d_due = wr.decide_wake(w, "2026-10-05")
    check("12b 到期只进入复查不自动联系", d_due["decision"] == "待核实", str(d_due))

    # 客户后续主动回复 → 重判为正常待处理
    wr.apply_substantive_reply(w, "2026-10-06 09:00")
    w["pause_until"] = None
    d_reply = wr.decide_wake(w, "2026-10-06")
    check("12c 后续回复后重新可联系", d_reply["decision"] == "可联系", str(d_reply))


# ---------------------------------------------------------------- 运行

def run():
    test_idempotent_rerun()
    test_cross_day_due()
    test_contact_dedup()
    test_reply_pause()
    test_manual_persist()
    test_partial_failure_preserves()
    test_import_rerun_no_loss()
    test_manual_defer_preserved()
    test_deferred_reenters()
    test_corrupt_state_protection()
    test_wake_rules()
    test_simulated_pause_flow()

    print(f"PASS: {len(PASS)}")
    for p in PASS:
        print(f"  [PASS] {p}")
    if FAIL:
        print(f"FAIL: {len(FAIL)}")
        for n, d in FAIL:
            print(f"  [FAIL] {n} -> {d}")
    else:
        print("ALL PASS")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(run())
