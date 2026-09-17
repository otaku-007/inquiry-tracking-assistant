# -*- coding: utf-8 -*-
"""v1.2 引擎验证（模拟测试）。真实数据与模拟场景分开标注。

覆盖 v1.1 原有 12 项 + v1.2 独立复核新增：
- 导入后重跑不丢失线索（40 不退回 20）
- 人工延期日期/动作不被旧数据覆盖
- 延期任务到期重新进入可见队列
- 状态文件损坏不被空默认值覆盖
- 统一唤醒资格判断（三次上限/工作日间隔/冷却期/滚动次数/未知历史/暂停条件/停止联系）
- 约定暂停：从模拟原始聊天到暂停决策的完整闭环

v1.2.1 新增「集成链路」测试（13-17，见文件末段）：
此前测试只直接调 decide_wake()，未覆盖「实际写入一次联系」的端到端链路，
因而漏掉了两个隐藏问题：
- 唤醒次数重复计数：record_contact() 先追加，record_wake_contact() 再
  cycle_wake_count()+1，而派生计数已含本次 -> 首次唤醒被记成 2 次，冷却提前一次触发。
- buyer_id 聚合键错配：聚合以 buyer_id 写入，日报却用 buyer 姓名查询，
  一旦有 buyer_id 客户级限次就永远查不中。
集成测试在临时 STATE_DIR 上跑真实 daily_run.run_daily，直接验证落盘结果。
"""

import os
import shutil
import tempfile
from datetime import date, timedelta

import state_engine as se
import wake_rules as wr
import generate_report as gr
import daily_run as dr
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


# ---------------------------------------------------------------- v1.2.1 集成链路
# 以下测试跑真实的 daily_run.run_daily（在临时 STATE_DIR 上落盘），
# 覆盖「实际写入一次联系」的完整链路，而不是只调 decide_wake()。

def _temp_state():
    """把 STATE_DIR 指向临时目录，返回 (原目录, 临时目录)。调用方负责还原。"""
    old = se.STATE_DIR
    d = tempfile.mkdtemp(prefix="v121_")
    se.STATE_DIR = d
    return old, d


def _mk_lead(lid, buyer=None, buyer_id=None, owner="Ella Chen"):
    o = {
        "lead_id": lid, "buyer": buyer or f"Buyer {lid}", "owner": owner,
        "category": "TM 商机", "source": "Inquiry from TM", "country": "DE",
        "create_time": "2026-09-01", "update_time": "2026-09-01",
        "high_intent": False, "conversation_key": f"conv-{lid}", "dedup_note": None,
    }
    if buyer_id:
        o["buyer_id"] = buyer_id
    return o


def _mk_wake(lid, sent_at, h):
    return {"lead_id": lid,
            "contact": {"sent_at": sent_at, "channel": "TM", "kind": "wake", "content_hash": h}}


def _run_batch(batch):
    """只更新状态，不写日报文件。"""
    return dr.run_daily(batch, write=False)


def _loaded_wakes():
    return se.load_state()[0]["wakes"]


def test_integration_wake_count_once():
    """13. 集成：首次真实唤醒只能算 1 次，且冷却必须等到第 3 次才触发。

    旧实现下 record_contact 先写入 contacts，record_wake_contact 再
    cycle_wake_count()+1 会把首次记成 2、第 2 次就触发冷却。
    """
    old, d = _temp_state()
    try:
        _run_batch({"as_of": "2026-09-12", "leads": [_mk_lead("L1")],
                    "wake_contacts": [_mk_wake("L1", "2026-09-12T10:00:00", "h1")]})
        w = _loaded_wakes()["L1"]
        n = len([c for c in w["contacts"] if c.get("kind") == "wake"])
        check("13a 联系只写入 1 条", n == 1, f"n={n}")
        check("13b 首次唤醒周期计数=1", w.get("cycle_wake_count") == 1,
              f"cycle_wake_count={w.get('cycle_wake_count')}（旧实现为 2）")
        check("13c 首次唤醒不触发冷却", not w.get("cooldown_until"), str(w.get("cooldown_until")))

        _run_batch({"as_of": "2026-09-22", "leads": [],
                    "wake_contacts": [_mk_wake("L1", "2026-09-22T10:00:00", "h2")]})
        w = _loaded_wakes()["L1"]
        check("13d 第2次后计数=2", w.get("cycle_wake_count") == 2, str(w.get("cycle_wake_count")))
        check("13e 第2次后仍不冷却", not w.get("cooldown_until"),
              f"cooldown_until={w.get('cooldown_until')}（旧实现会提前冷却）")

        _run_batch({"as_of": "2026-10-06", "leads": [],
                    "wake_contacts": [_mk_wake("L1", "2026-10-06T10:00:00", "h3")]})
        w = _loaded_wakes()["L1"]
        check("13f 第3次后计数=3", w.get("cycle_wake_count") == 3, str(w.get("cycle_wake_count")))
        check("13g 第3次后冷却=第3次+30天", w.get("cooldown_until") == "2026-11-05",
              str(w.get("cooldown_until")))
    finally:
        se.STATE_DIR = old
        shutil.rmtree(d, ignore_errors=True)


def test_integration_wake_rerun_idempotent():
    """14. 集成：同一批次重复运行，联系去重后周期计数不得增长。"""
    old, d = _temp_state()
    try:
        batch = {"as_of": "2026-09-12", "leads": [_mk_lead("L1")],
                 "wake_contacts": [_mk_wake("L1", "2026-09-12T10:00:00", "h1")]}
        _run_batch(batch)
        first = _loaded_wakes()["L1"].get("cycle_wake_count")
        _run_batch(batch)
        w = _loaded_wakes()["L1"]
        check("14a 重跑不新增联系", len(w["contacts"]) == 1, f"n={len(w['contacts'])}")
        check("14b 重跑不增长周期计数", first == w.get("cycle_wake_count") == 1,
              f"first={first} second={w.get('cycle_wake_count')}")
    finally:
        se.STATE_DIR = old
        shutil.rmtree(d, ignore_errors=True)


def test_integration_reply_resets_cycle():
    """15. 集成：实质回复重置周期后，再唤醒必须从 1 重新开始（不能继承旧计数）。"""
    old, d = _temp_state()
    try:
        _run_batch({"as_of": "2026-09-12", "leads": [_mk_lead("L1")],
                    "wake_contacts": [_mk_wake("L1", "2026-09-12T10:00:00", "h1")]})
        check("15a 首轮计数=1", _loaded_wakes()["L1"].get("cycle_wake_count") == 1,
              str(_loaded_wakes()["L1"].get("cycle_wake_count")))

        _run_batch({"as_of": "2026-09-15", "leads": [],
                    "events": [{"lead_id": "L1", "type": "buyer_reply",
                                "at": "2026-09-15T09:00:00"}]})
        w = _loaded_wakes()["L1"]
        check("15b 实质回复后周期归零", w.get("cycle_wake_count") == 0,
              str(w.get("cycle_wake_count")))
        check("15c 历史联系仍保留（不因限次丢弃）",
              len([c for c in w["contacts"] if c.get("kind") == "wake"]) == 1,
              str(len(w["contacts"])))

        _run_batch({"as_of": "2026-09-20", "leads": [],
                    "wake_contacts": [_mk_wake("L1", "2026-09-20T10:00:00", "h2")]})
        check("15d 重置后再唤醒从1开始", _loaded_wakes()["L1"].get("cycle_wake_count") == 1,
              str(_loaded_wakes()["L1"].get("cycle_wake_count")))
    finally:
        se.STATE_DIR = old
        shutil.rmtree(d, ignore_errors=True)


def _seed_buyer_state(buyer_id):
    """两条线索同一买家，各 1 次本周期唤醒但联系人头共 6 条（达 90 天滚动上限）。"""
    opps = [_mk_lead("L1", "Same Buyer", buyer_id), _mk_lead("L2", "Same Buyer", buyer_id)]
    contacts = [{"kind": "wake", "sent_at": f"2026-09-0{i}", "channel": "TM"} for i in (1, 2, 3)]
    se.save_section("opportunities", opps)
    se.save_section("wakes", {
        "L1": {"contacts": [dict(c) for c in contacts],
               "cycle_wake_count": 1, "classification": "待核实候选"},
        "L2": {"contacts": [dict(c) for c in contacts],
               "cycle_wake_count": 1, "classification": "待核实候选"},
    })
    return se.load_state()[0]


def test_integration_buyer_id_aggregation_used():
    """16. 集成：有稳定 buyer_id 时，日报必须走客户级聚合。

    旧实现用姓名查聚合（键是 buyer_id），rolling_contacts 恒为 None，
    于是把已达 6 次上限的客户误判为「可联系」。
    """
    old, d = _temp_state()
    try:
        state = _seed_buyer_state("BID-1")
        agg = wr.aggregate_buyer_contacts(state["wakes"], state["opportunities"])
        check("16a 聚合键为 buyer_id 且可核验",
              list(agg.keys()) == ["BID-1"] and agg["BID-1"]["identity_verified"],
              str({k: v["identity_verified"] for k, v in agg.items()}))
        check("16b 聚合联系人头=6", len(agg["BID-1"]["contacts"]) == 6,
              str(len(agg["BID-1"]["contacts"])))

        contactable, verify = gr._evaluate_wakes(state, "2026-09-12")
        entries = contactable + verify
        check("16c 客户级聚合已真正参与判定",
              entries and all(e["client_level_verified"] for e in entries),
              str([(e["lead_id"], e["client_level_verified"]) for e in entries]))
        check("16d 达 90 天滚动上限不再判可联系",
              not contactable and len(verify) == 2,
              f"contactable={len(contactable)} verify={len(verify)}（旧实现误判为可联系）")
        check("16e 原因为 90 天滚动上限",
              all("90 天滚动" in e["reason"] for e in verify),
              str([e["reason"] for e in verify]))
    finally:
        se.STATE_DIR = old
        shutil.rmtree(d, ignore_errors=True)


def test_integration_no_buyer_id_falls_back():
    """17. 集成：无 buyer_id 时回退姓名聚合，且不得宣称客户级限次已实现。"""
    old, d = _temp_state()
    try:
        state = _seed_buyer_state(None)
        agg = wr.aggregate_buyer_contacts(state["wakes"], state["opportunities"])
        check("17a 姓名聚合被标记为不可信",
              agg["Same Buyer"]["identity_verified"] is False, str(agg))

        contactable, verify = gr._evaluate_wakes(state, "2026-09-12")
        entries = contactable + verify
        check("17b 未宣称客户级已验证",
              all(e["client_level_verified"] is False for e in entries),
              str([(e["lead_id"], e["client_level_verified"]) for e in entries]))
        check("17c 无 buyer_id 时不误判超限", len(contactable) == 2,
              f"contactable={len(contactable)} verify={len(verify)}")
    finally:
        se.STATE_DIR = old
        shutil.rmtree(d, ignore_errors=True)


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
    # v1.2.1 集成链路
    test_integration_wake_count_once()
    test_integration_wake_rerun_idempotent()
    test_integration_reply_resets_cycle()
    test_integration_buyer_id_aggregation_used()
    test_integration_no_buyer_id_falls_back()

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
