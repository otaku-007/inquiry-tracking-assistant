# -*- coding: utf-8 -*-
"""从 state/ 生成当日日报（v1.2，动态日期/范围/计数）。

- 日报日期、数据截止、覆盖范围全部来自本次运行（meta），不写死。
- 生成前按 as_of 刷新到期状态（含延期/等待任务到期重新可见）。
- 负责人分组按 state/assignee_roles.json 的 sales 列表路由（不硬编码）。
- 历史沉默候选按统一唤醒资格判断分为「今天可联系」与「候选待核实」。

计数口径：
- 原始线索 = opportunities 行数（去重后记录数，非后台显示总数）
- 独立会话 = 去重 conversation_key
- 独立客户 = 去重 buyer（按名，估计）
- 商机 = 独立客户（未确认，同一买家可能多项目）
- 待办 = status=open 的任务数
- 到期复查 = deferred/waiting_* 且复查时间已到的任务数
"""

import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime

import state_engine as se
import wake_rules as wr

ROLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "assignee_roles.json")


def load_roles():
    try:
        with open(ROLES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"sales": [], "operations": []}


def _load_state():
    state, errors = se.load_state()
    return state, errors


def counts(state):
    opps = state["opportunities"] or []
    tasks = state["tasks"] or []
    leads = len(opps)
    convs = len({o.get("conversation_key") for o in opps})
    buyers = len({o.get("buyer") for o in opps})
    open_tasks = [t for t in tasks if t.get("status") == "open"]
    review_tasks = [t for t in tasks
                    if t.get("status") in ("deferred", "waiting_customer", "waiting_internal")
                    and t.get("review_due")]
    return {
        "leads": leads,
        "conversations": convs,
        "customers_by_name": buyers,
        "opportunities_estimate": buyers,
        "open_tasks": len(open_tasks),
        "review_tasks": len(review_tasks),
    }


def _visible_tasks(tasks):
    """待办（open）与到期复查（deferred/waiting 且 review_due）。"""
    open_tasks = [t for t in tasks if t.get("status") == "open"]
    review_tasks = [t for t in tasks
                    if t.get("status") in ("deferred", "waiting_customer", "waiting_internal")
                    and t.get("review_due")]
    return open_tasks, review_tasks


def action_type_counts(tasks):
    c = Counter()
    for t in tasks:
        if t.get("status") == "open":
            c[t["action_type"]] += 1
    return c


def _strip_tags(s):
    s = (s or "").strip()
    for pre in ("（判断）", "（译文）", "（原文）"):
        if s.startswith(pre):
            s = s[len(pre):].strip()
    return s


def fmt_evidence(ev):
    speaker_cn = {"buyer": "买家", "seller": "业务员", "bot": "机器人"}
    s = speaker_cn.get(ev["speaker"], ev["speaker"])
    t = ev["time"]
    orig = _strip_tags(ev.get("text_original"))
    trans = _strip_tags(ev.get("translation"))
    if ev.get("is_judgment"):
        return f"{t} {s}（判断）：{trans or orig}"
    if orig and trans and orig != trans:
        return f"{t} {s}（原文）：{orig} ｜（译文）：{trans}"
    if orig:
        return f"{t} {s}（原文）：{orig}"
    return f"{t} {s}（译文）：{trans}"


def _wake_has_value(w):
    if w.get("no_valid_demand"):
        return False
    if w.get("wake_value_pending"):
        return False
    return True


def _evaluate_wakes(state, as_of):
    """对每个唤醒记录做统一资格判断，返回 (contactable, verify) 两组。"""
    wakes = state["wakes"] or {}
    opps = state["opportunities"] or []
    buyer_agg = wr.aggregate_buyer_contacts(wakes, opps)
    contactable, verify = [], []
    for opp_key, w in wakes.items():
        # 客户级聚合：仅 buyer_id 可核实时传入（当前数据无 buyer_id，按姓名聚合不可信）
        buyer = next((o.get("buyer") for o in opps if o.get("lead_id") == opp_key), None)
        agg = buyer_agg.get(buyer) if buyer else None
        rolling = agg["contacts"] if agg and agg.get("identity_verified") else None
        d = wr.decide_wake(w, as_of, rolling_contacts=rolling, has_value=_wake_has_value(w))
        entry = {"lead_id": opp_key, "buyer": buyer, **d, "note": w.get("history_note"),
                 "classification": w.get("classification"),
                 "last_msg_time": w.get("last_msg_time")}
        if d["decision"] == wr.DECISION_CONTACT:
            contactable.append(entry)
        else:
            verify.append(entry)
    return contactable, verify


def render(state, as_of, cutoff_time, coverage, run_id=None):
    # 生成前刷新到期状态（含延期/等待到期重新可见）
    tasks = state["tasks"] or []
    se.refresh_overdue(tasks, as_of)

    c = counts(state)
    open_tasks, review_tasks = _visible_tasks(tasks)
    atc = action_type_counts(tasks)
    roles = load_roles()
    sales = roles.get("sales") or []

    contactable, verify = _evaluate_wakes(state, as_of)

    lines = []
    lines.append("# 每日询盘跟进日报（v1.2）")
    lines.append("")
    lines.append(f"> 数据截至：{as_of} {cutoff_time or ''}（北京时间）".rstrip())
    if run_id:
        lines.append(f"> 本次运行：{run_id}")
    lines.append("")
    if coverage:
        lines.append("## 覆盖范围（来自本次运行）")
        lines.append("")
        for k in ("source", "pages_read", "rows_read", "detail_reviewed", "cutoff_time", "note"):
            if coverage.get(k):
                lines.append(f"- {k}：{coverage[k]}")
        lines.append("")

    lines.append("## 一、统一计数（多标签可重叠，不直接相加）")
    lines.append("")
    lines.append(f"- 原始线索（去重后记录数）：{c['leads']}")
    lines.append(f"- 独立会话：{c['conversations']}（同名买家多条询盘按会话去重）")
    lines.append(f"- 独立客户（按名，估计）：{c['customers_by_name']}（同名≠合并，需 buyer_id 确认）")
    lines.append(f"- 商机（估计）：{c['opportunities_estimate']}（未确认，同一买家可能多项目）")
    lines.append(f"- 待办（本次需动作）：{c['open_tasks']}")
    lines.append(f"- 到期复查（延期/等待已到期）：{c['review_tasks']}")
    lines.append(f"- 历史候选·今天可联系：{len(contactable)}")
    lines.append(f"- 历史候选·待核实：{len(verify)}")
    lines.append("")
    lines.append("待办按行动类型分布：")
    if atc:
        for k, v in sorted(atc.items(), key=lambda x: -x[1]):
            lines.append(f"- {k}：{v}")
    else:
        lines.append("- （无）")
    lines.append("")
    lines.append(f"> 说明：待办数({c['open_tasks']}) ≠ 客户数({c['customers_by_name']})。"
                 f"待办来自已详情核验的会话；未深度核验的列表线索不产生任务。")
    lines.append("")

    # 按负责人行动卡
    lines.append("## 二、按负责人行动卡（待办）")
    lines.append("")
    groups = defaultdict(list)
    for t in open_tasks:
        groups[t["owner"]].append(t)

    owners = sorted(groups.keys(),
                    key=lambda x: sales.index(x) if x in sales else 999)
    for owner in owners:
        ts = groups[owner]
        lines.append(f"### {owner}（{len(ts)} 条待办）")
        lines.append("")
        for i, t in enumerate(ts, 1):
            lines.append(f"**{i}.【{t['action_type']}】{t['buyer']}**")
            lines.append(f"- 触发：{t.get('trigger_reason') or '—'}")
            lines.append(f"- 阻塞与责任方：{t.get('blocker') or '—'}（{t.get('responsible') or '—'}）")
            pre = "；".join(t.get("preconditions") or []) or "无"
            lines.append(f"- 前置条件：{pre}")
            due = t.get("due_at") or "无明确期限"
            lines.append(f"- 到期：{due}｜状态：{t.get('overdue_status') or '未知'}（无期限不默认逾期）")
            lines.append(f"- 下一步：{t.get('next_action') or '—'}")
            lines.append(f"- 完成标准：{t.get('completion_criteria') or '—'}")
            lines.append(f"- 下次检查：{t.get('next_check_at') or '—'}")
            lines.append("- 证据：")
            for ev in t.get("evidence", []):
                lines.append(f"  - {fmt_evidence(ev)}")
            lines.append("")

    # 到期复查队列（延期/等待已到期）
    lines.append("## 三、到期复查队列（延期/等待已到期）")
    lines.append("")
    if review_tasks:
        rgroups = defaultdict(list)
        for t in review_tasks:
            rgroups[t["owner"]].append(t)
        for owner in sorted(rgroups.keys(), key=lambda x: sales.index(x) if x in sales else 999):
            for t in rgroups[owner]:
                lines.append(f"- 【{t['action_type']}】{t['buyer']}（{owner}）｜"
                             f"复查时间 {t.get('next_check_at') or t.get('due_at') or '—'} 已到｜"
                             f"延期理由：{t.get('defer_reason') or '—'}")
    else:
        lines.append("- （无）")
    lines.append("")

    # 历史沉默客户候选
    lines.append("## 四、历史沉默客户候选（统一唤醒资格判断）")
    lines.append("")
    lines.append("### 今天可联系")
    lines.append("")
    if contactable:
        for e in contactable:
            lines.append(f"- {e['buyer']}（{e['lead_id']}）｜决策：{e['decision']}｜{e['reason']}")
    else:
        lines.append("- （无）")
    lines.append("")
    lines.append("### 候选待核实（历史次数未知/无有效需求/无供应方案/冷却期）")
    lines.append("")
    if verify:
        for e in verify:
            note = e["note"] or ""
            lines.append(f"- {e['buyer']}（{e['lead_id']}）｜{e['decision']}｜{e['reason']}｜{note}")
    else:
        lines.append("- （无）")
    lines.append("")

    # 运行统计与覆盖缺口
    lines.append("## 五、运行统计与覆盖缺口")
    lines.append("")
    if coverage:
        lines.append(f"- 覆盖来源：{coverage.get('source') or '—'}")
        lines.append(f"- 实际读取页：{coverage.get('pages_read') or '—'}")
        lines.append(f"- 读取行数（去重后）：{coverage.get('rows_read') or c['leads']}")
        lines.append(f"- 详情复核数：{coverage.get('detail_reviewed') or '—'}")
        lines.append(f"- 后台显示总线索：{coverage.get('total_hint') or '未记录'}")
        lines.append(f"- 覆盖缺口：{coverage.get('note') or '—'}")
    lines.append("")

    # 未验证限制（静态，随数据变化可再补）
    lines.append("## 六、仍未验证的限制")
    lines.append("")
    lines.append("- 客户级 90 天滚动限次需稳定 buyer_id 聚合；当前数据仅有姓名，不能宣称客户级限次已实现。")
    lines.append("- 列表「更新时间」字段语义待回查（见 Marina 案例），不能作为「最后聊天时间」判定沉默。")
    lines.append("- 节假日历、团队时区、日报接收人/渠道、运行时间均未配置；暂不启用推送。")
    lines.append("")
    return "\n".join(lines)


def main(as_of=None, cutoff_time=None, run_id=None):
    state, errors = _load_state()
    if errors:
        print(f"[WARN] 状态读取失败：{errors}")

    if as_of is None:
        as_of = date.today().isoformat()
    if cutoff_time is None:
        cutoff_time = datetime.now().strftime("%H:%M")
    coverage = (state["checkpoints"] or {}).get("coverage") or {}

    report = render(state, as_of, cutoff_time, coverage, run_id=run_id)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"每日询盘跟进日报-{as_of}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n\n--- written to {out}")
    return out


if __name__ == "__main__":
    main()
