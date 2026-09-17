# -*- coding: utf-8 -*-
"""每日询盘跟进 —— 日常运行入口（v1.2）。

与样本初始化（seed_state.py / seed_silent.py）分开：本入口不整体替换商机库，
只按稳定标识合并「本次新抓取 + 分析结果」，并生成当日日报。

数据流：新抓取结果 → 分析产物(leads/tasks/events) → 更新状态 → 生成当日日报。
所有计数、日期、覆盖范围来自本次运行，不需要人工修改种子代码。

输入 batch 结构（示例见 demo()）：
{
  "as_of": "2026-09-12",            # 运行日期
  "cutoff_time": "12:15",           # 数据截止时间
  "coverage": {...},                # 本次覆盖范围（来源/页/行/详情复核数/后台总数/缺口）
  "leads": [ {...lead...} ],        # 本次发现的线索（按 lead_id 合并）
  "tasks": [ {...task..., "new_signal": bool} ],  # 分析产出的任务（new_signal=True 表示有新入站信号）
  "events": [ {...} ],              # 会话事件（新回复/停止联系/约定暂停）
  "wake_contacts": [ {"lead_id":..., "contact":{...}} ],  # 本次实际主动唤醒（去重记账）
}
"""

import os
from datetime import date, datetime

import state_engine as se
import wake_rules as wr
import generate_report as gr

WORKDIR = os.path.dirname(os.path.abspath(__file__))


def _apply_events(wakes, events):
    """把会话事件应用到唤醒记录，返回 wakes。"""
    for ev in events or []:
        lid = ev.get("lead_id")
        if not lid:
            continue
        w = wakes.setdefault(lid, {"contacts": [], "plan": None})
        typ = ev.get("type")
        if typ == "buyer_reply":
            # 客户新回复：暂停旧唤醒计划 + 重置唤醒周期（正常处理不受暂停限制）
            se.apply_reply_pause(wakes, lid, ev.get("at", ""))
            wr.apply_substantive_reply(w, ev.get("at", ""))
            w["pause_until"] = None
            w["stop_contact"] = False
        elif typ == "stop_contact":
            w["stop_contact"] = True
        elif typ == "pause_until":
            w["pause_until"] = ev.get("at")
        elif typ == "clear_pause":
            w["pause_until"] = None
    return wakes


def _record_wake_contacts(wakes, wake_contacts):
    for item in wake_contacts or []:
        lid = item.get("lead_id")
        contact = item.get("contact") or {}
        if not lid:
            continue
        w = wakes.setdefault(lid, {"contacts": [], "plan": None})
        # prior 必须在写入 contacts 之前取。cycle_wake_count() 在缺显式计数时会按
        # contacts 派生，若追加后再取，得到的已含本次，再加 1 会多算一次。
        prior = wr.cycle_wake_count(w)
        _, added = se.record_contact(wakes, lid, contact)
        if added and contact.get("kind") == "wake":
            wr.record_wake_contact(w, contact, prior_count=prior)
    return wakes


def run_daily(batch, write=True, report_path=None):
    """执行一次日常运行。返回 (report_text, report_path, meta)。

    report_path 为 None 时按 as_of 自动命名；否则用指定路径。
    """
    as_of = batch.get("as_of") or date.today().isoformat()
    cutoff_time = batch.get("cutoff_time") or datetime.now().strftime("%H:%M")
    run_id = batch.get("run_id") or se.stable_id("run", as_of, cutoff_time, str(len(batch.get("leads") or [])))

    # 1) 修改前备份现有状态
    backup_dir = se.backup_state()

    # 2) 安全读取（损坏 section 置 None 并记录错误）
    state, errors = se.load_state()

    # 3) 合并线索（按 lead_id，不整体替换）
    opps = state["opportunities"] if state["opportunities"] is not None else []
    new_opp = 0
    for lead in batch.get("leads") or []:
        _, is_new = se.upsert_opportunity(opps, lead)
        if is_new:
            new_opp += 1

    # 4) 合并任务（new_signal=True 的 lead 允许覆盖人工字段并重开）
    tasks = state["tasks"] if state["tasks"] is not None else []
    new_task = 0
    for task in batch.get("tasks") or []:
        new_signal = bool(task.pop("new_signal", False))
        _, is_new = se.upsert_task(tasks, task, new_signal=new_signal)
        if is_new:
            new_task += 1

    # 5) 应用事件 + 唤醒记账
    wakes = state["wakes"] if state["wakes"] is not None else {}
    wakes = _apply_events(wakes, batch.get("events"))
    wakes = _record_wake_contacts(wakes, batch.get("wake_contacts"))

    # 6) 刷新到期状态
    se.refresh_overdue(tasks, as_of)

    # 7) 检查点：记录本次运行 + 覆盖范围（不覆盖失败来源）
    checkpoints = state["checkpoints"] if state["checkpoints"] is not None else {"runs": [], "coverage": None}
    checkpoints.setdefault("runs", [])
    checkpoints["runs"].append({
        "run_id": run_id, "at": se._now_iso(),
        "kind": "daily", "as_of": as_of,
        "new_opportunities": new_opp, "new_tasks": new_task,
    })
    if batch.get("coverage"):
        checkpoints["coverage"] = batch["coverage"]

    # 8) 保存（跳过读取失败的 section，避免空数据覆盖）
    saved, skipped = [], []
    if state["opportunities"] is not None:
        se.save_section("opportunities", opps); saved.append("opportunities")
    else:
        skipped.append("opportunities")
    if state["tasks"] is not None:
        se.save_section("tasks", tasks); saved.append("tasks")
    else:
        skipped.append("tasks")
    if state["wakes"] is not None:
        se.save_section("wakes", wakes); saved.append("wakes")
    else:
        skipped.append("wakes")
    if state["checkpoints"] is not None:
        se.save_section("checkpoints", checkpoints); saved.append("checkpoints")
    else:
        skipped.append("checkpoints")

    # 9) 生成日报（刷新后的状态）
    coverage = batch.get("coverage") or checkpoints.get("coverage") or {}
    report = gr.render(state, as_of, cutoff_time, coverage, run_id=run_id)
    if report_path is None:
        report_path = os.path.join(WORKDIR, f"每日询盘跟进日报-{as_of}.md")
    if write:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)

    meta = {
        "as_of": as_of, "cutoff_time": cutoff_time, "run_id": run_id,
        "backup_dir": backup_dir, "new_opportunities": new_opp, "new_tasks": new_task,
        "saved": saved, "skipped": skipped, "load_errors": errors,
    }
    return report, report_path, meta


def demo():
    """演示：一条新输入 → 更新状态 → 当日日报（不修改种子代码）。

    在真实状态的临时副本上运行，避免污染真实 state/；日报写到独立演示文件。
    """
    import shutil
    import tempfile

    old_dir = se.STATE_DIR
    tmp = tempfile.mkdtemp(prefix="daily_demo_")
    try:
        for f in os.listdir(old_dir):
            if f.endswith(".json"):
                shutil.copy2(os.path.join(old_dir, f), os.path.join(tmp, f))
        se.STATE_DIR = tmp
        return _demo_inner(os.path.join(WORKDIR, "每日询盘跟进日报-演示-2026-09-12.md"))
    finally:
        se.STATE_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def _demo_inner(report_path):
    batch = {
        "as_of": "2026-09-12",
        "cutoff_time": "12:15",
        "coverage": {
            "source": "消息中心「全部」第 1 页 + 历史第 95/96 页（只读）",
            "pages_read": "第 1、95、96 页",
            "rows_read": 41,
            "detail_reviewed": 17,
            "total_hint": "后台显示约 1912 条（96 页，未全量扫描）",
            "cutoff_time": "2026-09-12 12:15",
            "note": "仅抽样页，未全量扫描；列表覆盖与详情分析分层。",
        },
        "leads": [
            {
                "lead_id": "13999999999", "buyer": "Demo New Buyer", "owner": "Ella Chen",
                "category": "TM 商机", "source": "Inquiry from TM", "country": "Germany",
                "create_time": "2026-09-12", "update_time": "2026-09-12 11:00",
                "high_intent": True, "conversation_key": "demo-new", "dedup_note": None,
            },
        ],
        "tasks": [
            # 新线索的任务
            {
                "task_id": se.make_task_id("13999999999", "待回复"),
                "lead_id": "13999999999", "buyer": "Demo New Buyer", "owner": "Ella Chen",
                "action_type": "待回复",
                "evidence": [{"speaker": "buyer", "time": "09-12 11:00",
                              "text_original": "Can you ship to Hamburg with DDP?",
                              "translation": "（译文）能 DDP 发货到汉堡吗？", "is_judgment": False}],
                "trigger_reason": "买家询问 DDP 发货到汉堡",
                "blocker": "未答复", "responsible": "Ella Chen",
                "preconditions": [], "due_at": None,
                "next_action": "核实汉堡 DDP 到门价并回复",
                "completion_criteria": "DDP 到门价答复发出",
                "next_check_at": "2026-09-13", "status": "open",
            },
            # 旧会话变化：Buyer Gamma 有新回复 -> 重开（new_signal=True）
            {
                "task_id": se.make_task_id("13900000103", "待核查"),
                "lead_id": "13900000103", "buyer": "Buyer Gamma", "owner": "Ella Chen",
                "action_type": "待核查",
                "evidence": [{"speaker": "buyer", "time": "09-12 10:20",
                              "text_original": "Ok send me the sea freight option",
                              "translation": "（译文）好，发海运方案给我", "is_judgment": False}],
                "trigger_reason": "买家新回复，要求海运方案",
                "blocker": "待发海运方案", "responsible": "Ella Chen",
                "preconditions": [], "due_at": None,
                "next_action": "整理海运方案发买家",
                "completion_criteria": "海运方案发出",
                "next_check_at": "2026-09-13", "status": "open", "new_signal": True,
            },
        ],
        "events": [
            # 约定暂停：模拟客户明确下月某日联系
            {"lead_id": "13900000119", "type": "pause_until", "at": "2026-10-05"},
        ],
        "wake_contacts": [],
    }
    report, path, meta = run_daily(batch, report_path=report_path)
    print(f"[daily_run] as_of={meta['as_of']} run_id={meta['run_id']}")
    print(f"[daily_run] new_opportunities={meta['new_opportunities']} new_tasks={meta['new_tasks']}")
    print(f"[daily_run] saved={meta['saved']} skipped={meta['skipped']} load_errors={meta['load_errors']}")
    print(f"[daily_run] backup_dir={meta['backup_dir']}")
    print(f"[daily_run] report -> {path}")
    print()
    print(report)
    return meta


if __name__ == "__main__":
    demo()
