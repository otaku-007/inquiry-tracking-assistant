# -*- coding: utf-8 -*-
"""统一唤醒资格判断（v1.2 试行配置）。

把「联系频率限制」和「是否允许联系」分开：
- decide_wake() 只输出统一决策：可联系 / 等待至某日 / 待核实 / 停止联系，
  每项带原因与下次检查时间。
- 联系记录（contacts）永远保留真实历史，不因限次而丢弃。

试行参数来自《Accio-每日询盘跟进执行指令-讨论稿》第四节，属保守初值，非行业定律：
- 一个持续无实质回复的唤醒周期最多 3 次实际主动联系。
- 第 2 次距第 1 次 ≥5 个团队工作日；第 3 次距第 2 次 ≥7 个团队工作日。
- 第 3 次后仍无实质回复：暂停主动唤醒 ≥30 个自然日，再评估（不自动重开，需新事实/新方案）。
- 同一买家滚动 90 个自然日无实质回复的主动唤醒合计最多 6 次（跨项目/账号/渠道合并）。

工作日按周一~周五；节假日历未配置（属待补业务配置）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

WAKE_CONFIG: Dict[str, Any] = {
    "cycle_max": 3,
    "interval_2nd_workdays": 5,
    "interval_3rd_workdays": 7,
    "cooldown_days_after_3rd": 30,
    "rolling_window_days": 90,
    "rolling_max": 6,
    "silence_workdays": 5,
}

DECISION_CONTACT = "可联系"
DECISION_WAIT = "等待至某日"
DECISION_VERIFY = "待核实"
DECISION_STOP = "停止联系"


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def add_workdays(d: date, n: int) -> date:
    """d 之后第 n 个工作日（n>0）。周一~周五；节假日未配置。"""
    cur = d
    remaining = abs(n)
    step = 1 if n >= 0 else -1
    while remaining > 0:
        cur += timedelta(days=step)
        if cur.weekday() < 5:  # Mon=0 .. Fri=4
            remaining -= 1
    return cur


def workdays_between(d1: date, d2: date) -> int:
    """d1 之后到 d2 之间（不含 d1）的工作日数。"""
    cnt = 0
    cur = d1
    while cur < d2:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            cnt += 1
    return cnt


def wake_contacts(w: Dict[str, Any]) -> List[dict]:
    return [c for c in w.get("contacts", []) if c.get("kind") == "wake"]


def cycle_wake_count(w: Dict[str, Any]) -> int:
    """当前唤醒周期已发生的主动唤醒次数。优先用显式记录，否则按 contacts 推算。"""
    if "cycle_wake_count" in w:
        return int(w["cycle_wake_count"] or 0)
    return len(wake_contacts(w))


def _within_rolling(sent_at: Optional[str], today: date, window_days: int) -> bool:
    d = parse_date(sent_at)
    if not d:
        return False
    return (today - d).days < window_days


def record_wake_contact(w: Dict[str, Any], contact: Dict[str, Any]) -> Dict[str, Any]:
    """记录一次实际主动唤醒，并推进周期计数/冷却。调用方需先用 state_engine.record_contact 去重。"""
    cfg = WAKE_CONFIG
    cwc = cycle_wake_count(w) + 1
    w["cycle_wake_count"] = cwc
    w["last_wake_at"] = str(contact.get("sent_at", ""))[:10]
    if cwc >= cfg["cycle_max"]:
        d = parse_date(contact.get("sent_at"))
        if d:
            w["cooldown_until"] = (d + timedelta(days=cfg["cooldown_days_after_3rd"])).isoformat()
    return w


def apply_substantive_reply(w: Dict[str, Any], reply_at: str) -> Dict[str, Any]:
    """客户实质回复 -> 重置唤醒周期，清除冷却（正常处理不受暂停主动唤醒限制）。"""
    w["cycle_wake_count"] = 0
    w["cooldown_until"] = None
    w["last_substantive_reply_at"] = str(reply_at)[:10]
    return w


def _decide(decision: str, reason: str, next_check_at: Optional[str],
            w: Dict[str, Any], client_level_verified: bool = False) -> Dict[str, Any]:
    return {
        "decision": decision,
        "reason": reason,
        "next_check_at": next_check_at,
        "cycle_wake_count": cycle_wake_count(w),
        "history_unknown": bool(w.get("history_unknown")),
        "stop_contact": bool(w.get("stop_contact")),
        "client_level_verified": client_level_verified,
    }


def decide_wake(w: Dict[str, Any], as_of: str,
                config: Optional[Dict[str, Any]] = None,
                rolling_contacts: Optional[List[dict]] = None,
                has_value: bool = True) -> Dict[str, Any]:
    """统一唤醒资格判断。

    参数：
      w                该询盘的唤醒记录（含 contacts/classification/history_unknown 等）
      as_of            运行日期 YYYY-MM-DD
      config           可选覆盖 WAKE_CONFIG
      rolling_contacts 该客户跨询盘/账号/渠道聚合后的唤醒联系（仅 buyer_id 可核实时传入）
      has_value        是否具备真实联系理由（供应匹配/新方案）；False 时保持待核实

    返回决策 dict（decision/reason/next_check_at/...）。
    """
    cfg: Dict[str, Any] = {**WAKE_CONFIG, **(config or {})}
    today = parse_date(as_of)
    if today is None:
        today = date.today()

    # 客户级限次是否可信：仅当调用方传入已验证聚合联系时成立
    client_level_verified = rolling_contacts is not None

    # 0) 停止联系
    if w.get("stop_contact"):
        return _decide(DECISION_STOP, "客户要求停止联系或明确拒绝", None, w, client_level_verified)

    # 1) 历史次数未知 -> 待核实，不默认为零
    if w.get("history_unknown"):
        return _decide(DECISION_VERIFY, "历史联系次数未知，需先核实（不默认为零）", None, w, client_level_verified)

    # 2) 分类未知 / 无有效需求 -> 待核实
    cls = str(w.get("classification") or "")
    if cls.startswith("未知") or w.get("no_valid_demand"):
        return _decide(DECISION_VERIFY, "缺少有效需求证据，维持待核实", None, w, client_level_verified)

    # 3) 约定暂停（客户指定日期再联系）
    pu = parse_date(w.get("pause_until"))
    if pu and today < pu:
        return _decide(DECISION_WAIT, f"约定暂停至 {pu.isoformat()}", pu.isoformat(), w, client_level_verified)
    if pu and today >= pu:
        return _decide(DECISION_VERIFY, "约定暂停已到期，进入复查（不自动联系）", None, w, client_level_verified)

    # 4) 冷却期（第 3 次后 30 自然日）
    cu = parse_date(w.get("cooldown_until"))
    if cu and today < cu:
        return _decide(DECISION_WAIT, f"已满周期上限，冷却至 {cu.isoformat()}", cu.isoformat(), w, client_level_verified)
    if cu and today >= cu:
        # 冷却结束不自动重开，需新事实/新方案
        return _decide(DECISION_VERIFY, "冷却期已过，需新事实/新方案才可重开", None, w, client_level_verified)

    # 5) 周期次数上限 + 工作日间隔
    cwc = cycle_wake_count(w)
    lw = parse_date(w.get("last_wake_at"))
    if cwc >= cfg["cycle_max"]:
        return _decide(DECISION_VERIFY, f"本周期已达 {cfg['cycle_max']} 次上限", None, w, client_level_verified)
    if cwc == 1 and lw:
        need = add_workdays(lw, int(cfg["interval_2nd_workdays"]))
        if today < need:
            return _decide(DECISION_WAIT,
                           f"第 2 次需距第 1 次 ≥{cfg['interval_2nd_workdays']} 个工作日",
                           need.isoformat(), w, client_level_verified)
    if cwc == 2 and lw:
        need = add_workdays(lw, int(cfg["interval_3rd_workdays"]))
        if today < need:
            return _decide(DECISION_WAIT,
                           f"第 3 次需距第 2 次 ≥{cfg['interval_3rd_workdays']} 个工作日",
                           need.isoformat(), w, client_level_verified)

    # 6) 滚动 90 天最多 6 次（跨项目/账号/渠道合并）
    rc: List[dict] = rolling_contacts if rolling_contacts is not None else wake_contacts(w)
    rcnt = sum(1 for c in rc if _within_rolling(c.get("sent_at"), today, int(cfg["rolling_window_days"])))
    if rcnt >= cfg["rolling_max"]:
        return _decide(DECISION_WAIT,
                       f"90 天滚动已达 {cfg['rolling_max']} 次上限",
                       None, w, client_level_verified)

    # 7) 联系价值 / 供应匹配
    if not has_value:
        return _decide(DECISION_VERIFY, "暂无有效供应方案或联系价值，保留观察", None, w, client_level_verified)

    return _decide(DECISION_CONTACT, f"满足条件，可联系（本周期第 {cwc + 1} 次）", None, w, client_level_verified)


def aggregate_buyer_contacts(wakes: Dict[str, Any],
                             opportunities: List[dict]) -> Dict[str, Dict[str, Any]]:
    """按稳定 buyer_id 聚合唤醒联系（缺 buyer_id 时按姓名，identity_verified=False）。

    仅在 buyer_id 存在时才可信；姓名聚合不能宣称客户级限次已实现。
    """
    from collections import defaultdict
    by_buyer: Dict[str, List[dict]] = defaultdict(list)
    for o in opportunities:
        bid = o.get("buyer_id") or o.get("buyer")
        verified = bool(o.get("buyer_id"))
        w = wakes.get(o.get("lead_id")) or {}
        by_buyer[bid].append({
            "contacts": list(w.get("contacts", [])),
            "verified": verified,
            "lead_id": o.get("lead_id"),
        })
    agg: Dict[str, Dict[str, Any]] = {}
    for bid, items in by_buyer.items():
        verified = bool(items) and all(it["verified"] for it in items)
        contacts: List[dict] = []
        for it in items:
            contacts.extend(it["contacts"])
        agg[bid] = {"contacts": contacts, "identity_verified": verified}
    return agg


if __name__ == "__main__":
    # 简单自检：打印配置
    print("WAKE_CONFIG =", WAKE_CONFIG)
