# -*- coding: utf-8 -*-
"""每日询盘跟进 v1.2 —— 状态持久化与幂等任务引擎。

本模块只负责「持久化与合并」，不负责业务判断。要点（对应 v1.1 独立复核）：

1. 安全读取：区分「文件不存在」与「文件存在但损坏」。
   损坏时备份原文件并抛 StateCorruptError，绝不返回空默认值，
   避免后续 save_section 用空数据覆盖历史记录。
2. 商机按 lead_id 幂等合并（upsert），不再整体替换列表。
3. 任务按 task_id 幂等合并；人工覆盖字段（manual_override）在无新信号时保留。
4. 跨日到期：due_at/next_check_at 与 as_of 比较，延期/等待任务到期后重新进入可见队列。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
BACKUP_DIR = os.path.join(STATE_DIR, "backups")

SECTIONS = ("opportunities", "tasks", "wakes", "checkpoints")
SECTION_DEFAULTS: Dict[str, Any] = {
    "opportunities": [],
    "tasks": [],
    "wakes": {},
    "checkpoints": {"runs": [], "coverage": None},
}

# 人工覆盖字段：一旦人工修改（manual_override=True），无新信号时不覆盖。
MANUAL_FIELDS = (
    "status", "due_at", "next_action", "next_check_at",
    "defer_reason", "deferred_at", "manual_note", "waiting_reason",
)
# 幂等/不可变字段
IMMUTABLE_FIELDS = ("task_id", "created_at")

# 终态：不再进入日报（除非人工重开）
TERMINAL_STATUS = ("done", "cancelled", "closed")


class StateCorruptError(Exception):
    """状态文件存在但不可读或损坏。"""


def stable_id(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _path(section: str) -> str:
    return os.path.join(STATE_DIR, section + ".json")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 备份

def backup_state(tag: Optional[str] = None) -> str:
    """把当前 state/ 下所有 json 复制到 backups/<tag>/，返回备份目录。

    备份目录始终跟随当前 STATE_DIR（支持测试/演示的临时副本）。
    """
    ts = tag or datetime.now().strftime("%Y-%m-%dT%H%M%S")
    dest = os.path.join(STATE_DIR, "backups", ts)
    os.makedirs(dest, exist_ok=True)
    for section in SECTIONS:
        p = _path(section)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dest, section + ".json"))
    return dest


def _backup_corrupt(p: str) -> str:
    """把损坏文件改名保留，避免被覆盖。"""
    dest = p + ".corrupt-" + datetime.now().strftime("%Y%m%dT%H%M%S")
    try:
        os.replace(p, dest)
    except OSError:
        pass
    return dest


# ---------------------------------------------------------------- 读取/保存

def _load(section: str, default: Any) -> Tuple[Any, str]:
    p = _path(section)
    if not os.path.exists(p):
        return default, "missing"
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f), "ok"
    except (json.JSONDecodeError, OSError) as e:
        _backup_corrupt(p)
        raise StateCorruptError(f"state/{section}.json 损坏：{e}") from e


def load_state() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """返回 (state, errors)。损坏的 section 置 None 并记录错误，绝不返回空默认值。"""
    state: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for section, default in SECTION_DEFAULTS.items():
        try:
            data, _ = _load(section, default)
            state[section] = data
        except StateCorruptError as e:
            state[section] = None
            errors[section] = str(e)
    return state, errors


def save_section(section: str, data: Any) -> None:
    if section not in SECTIONS:
        raise ValueError(f"unknown section: {section}")
    if data is None:
        raise StateCorruptError(
            f"拒绝保存 {section}：状态未成功读取，避免用空数据覆盖原文件。"
        )
    os.makedirs(STATE_DIR, exist_ok=True)
    p = _path(section)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)  # 原子替换：写失败不破坏原文件


# ---------------------------------------------------------------- 商机

def upsert_opportunity(opps: List[dict], opp: dict) -> Tuple[dict, bool]:
    """按 lead_id 幂等合并。新值不为空才覆盖已有非空值；保留 dedup_note/silent 标注。"""
    lid = opp["lead_id"]
    for i, o in enumerate(opps):
        if o.get("lead_id") == lid:
            merged = dict(o)
            for k, v in opp.items():
                if k == "lead_id":
                    continue
                if v is None and o.get(k):
                    continue  # 不覆盖已有非空值
                merged[k] = v
            opps[i] = merged
            return merged, False
    opps.append(dict(opp))
    return opps[-1], True


# ---------------------------------------------------------------- 任务

def make_task_id(lead_id: str, action_type: str) -> str:
    return stable_id("task", lead_id, action_type)


def upsert_task(tasks: List[dict], task: dict, new_signal: bool = False) -> Tuple[dict, bool]:
    """幂等写入任务。

    - 新任务：追加。
    - 已存在：更新可变字段；若已有 manual_override 且无新信号，保留人工字段。
    - new_signal=True（如客户新回复）时，允许覆盖人工字段并清除 manual_override。
    """
    tid = task["task_id"]
    for i, t in enumerate(tasks):
        if t.get("task_id") == tid:
            merged = dict(t)
            manual = bool(t.get("manual_override")) and not new_signal
            for k, v in task.items():
                if k in IMMUTABLE_FIELDS:
                    continue
                if manual and k in MANUAL_FIELDS:
                    continue  # 保留人工覆盖
                merged[k] = v
            if new_signal:
                merged["manual_override"] = False
            merged["updated_at"] = task.get("updated_at", _now_iso())
            tasks[i] = merged
            return merged, False
    task.setdefault("created_at", _now_iso())
    task.setdefault("updated_at", _now_iso())
    task.setdefault("status", "open")
    tasks.append(task)
    return task, True


# ---------------------------------------------------------------- 到期

def compute_overdue(due_at: Optional[str], as_of: str) -> str:
    """due_at 为空返回 未知；否则 未到期/今日到期/已逾期。"""
    if not due_at:
        return "未知"
    d = due_at[:10]
    if d < as_of:
        return "已逾期"
    if d == as_of:
        return "今日到期"
    return "未到期"


def task_check_date(t: dict) -> Optional[str]:
    """任务复查锚点：due_at 与 next_check_at 取较早者。"""
    ds = [d for d in (t.get("due_at"), t.get("next_check_at")) if d]
    return min(ds) if ds else None


def is_review_due(t: dict, as_of: str) -> bool:
    """是否应进入当日可见队列：
    - 终态：不进入
    - open：始终可见
    - deferred / waiting_*：复查时间已到才可见
    """
    if t.get("status") in TERMINAL_STATUS:
        return False
    if t.get("status") == "open":
        return True
    cd = task_check_date(t)
    return cd is not None and cd <= as_of


def refresh_overdue(tasks: List[dict], as_of: str) -> List[dict]:
    """对非终态任务重算 overdue_status 与 review_due（不改变人工状态）。"""
    for t in tasks:
        if t.get("status") in TERMINAL_STATUS:
            continue
        t["overdue_status"] = compute_overdue(t.get("due_at"), as_of)
        t["review_due"] = is_review_due(t, as_of)
    return tasks


# ---------------------------------------------------------------- 唤醒/联系

def apply_reply_pause(wakes: Dict[str, Any], opp_key: str, reply_time: str) -> Dict[str, Any]:
    """客户新回复 -> 暂停该客户旧唤醒计划（不清空历史联系次数）。"""
    w = wakes.setdefault(opp_key, {"contacts": [], "plan": None})
    if w.get("plan") and w["plan"].get("status") in ("planned", "active"):
        w["plan"] = {
            "status": "paused",
            "paused_at": reply_time,
            "pause_reason": "客户新回复",
            "paused_from": w["plan"].get("status"),
        }
    return wakes


def record_contact(wakes: Dict[str, Any], opp_key: str, contact: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """记录实际主动联系，按 event_id 去重；重复运行不增加联系次数。"""
    w = wakes.setdefault(opp_key, {"contacts": [], "plan": None, "history_unknown": False})
    ev = stable_id("contact", opp_key, contact.get("sent_at", ""), contact.get("channel", ""),
                   contact.get("content_hash", ""))
    if any(c.get("event_id") == ev for c in w["contacts"]):
        return wakes, False
    rec = dict(contact)
    rec["event_id"] = ev
    w["contacts"].append(rec)
    return wakes, True


def wake_count(w: Dict[str, Any]) -> int:
    return len([c for c in w.get("contacts", []) if c.get("kind") == "wake"])


# ---------------------------------------------------------------- 检查点

def checkpoint_run(checkpoints: Dict[str, Any], run_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    """记录一次运行（覆盖基线 / 重复运行审计）。"""
    rec = {"run_id": run_id, "at": _now_iso()}
    rec.update(meta)
    runs = checkpoints.setdefault("runs", [])
    if not any(r.get("run_id") == run_id for r in runs):
        runs.append(rec)
    if meta.get("coverage"):
        checkpoints["coverage"] = meta["coverage"]
    return checkpoints


if __name__ == "__main__":
    print("state_engine loaded. STATE_DIR =", STATE_DIR)
