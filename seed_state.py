# -*- coding: utf-8 -*-
"""v1.1 种子数据：把试跑已核验的线索与修正后的任务写入 state/。

- 20 条列表基线（第 1 页，2026-09-11 抓取）。
- 12 条详情核验 → 修正后任务（7 处判断修正）。
- 幂等：重复运行本脚本不会重复建任务（task_id 稳定）。
"""

import state_engine as se

LEADS = [
    # (inquiry_id, buyer, owner, category, source, country, create_time, update_time, high_intent, conversation_key)
    ("13900000101", "Buyer Alpha", "Ella Chen", "TM 商机", "Inquiry from TM", "Belgium", "2026-09-06", "2026-09-11 17:44", False, "jens"),
    ("13900000102", "Buyer Beta", "Ella Chen", "TM 商机", "Inquiry from TM", "Lithuania", "2026-08-01", "2026-09-11 17:40", True, "lina"),
    ("13900000103", "Buyer Gamma", "Ella Chen", "TM 商机", "Inquiry from TM", "Saudi Arabia", "2026-09-11", "2026-09-11 16:30", False, "sultana"),
    ("13900000104", "Buyer Delta", "Ella Chen", "TM 商机", "Inquiry from TM", "Poland", "2026-09-09", "2026-09-11 14:28", False, "josh"),
    ("13900000105", "Buyer Epsilon", "Alice Lam", "TM 商机", "Inquiry from TM", "United States", "2026-07-23", "2026-09-11 14:01", True, "jonathan"),
    ("13900000106", "Buyer Zeta", "Alice Lam", "TM 商机", "Inquiry from TM", "Rwanda", "2026-04-11", "2026-09-11 13:31", True, "nsabimana"),
    ("13900000107", "Buyer Zeta", "Alice Lam", "TM 商机", "Inquiry from TM", "Rwanda", "2026-06-12", "2026-09-11 13:31", True, "nsabimana"),
    ("13900000108", "Buyer Zeta", "Alice Lam", "TM 商机", "Inquiry from TM", "Rwanda", "2026-09-07", "2026-09-11 13:31", True, "nsabimana"),
    ("13900000109", "Buyer Eta", "Bin Cai", "TM 商机", "Inquiry from TM", "Australia", "2026-08-18", "2026-09-11 13:01", True, "hashim"),
    ("13900000110", "Buyer Eta", "Bin Cai", "TM 商机", "Inquiry from TM", "Australia", "2026-05-29", "2026-09-11 13:01", True, "hashim"),
    ("13900000133", "Buyer Upsilon", "Ella Chen", "询盘商机", "Inquiry from Product Details Page", "Colombia", "2026-09-06", "2026-09-11 09:27", True, "paola"),
    ("13900000111", "Buyer Theta", "Bin Cai", "TM 商机", "Inquiry from TM", "France", "2026-09-11", "2026-09-11 07:42", False, "yanis"),
    ("13900000112", "Buyer Iota", "Ella Chen", "TM 商机", "Inquiry from TM", "India", "2026-09-10", "2026-09-10", False, "lightcraft"),
    ("13900000113", "Buyer Kappa", "Ella Chen", "TM 商机", "Inquiry from TM", "Saudi Arabia", "2026-09-10", "2026-09-10", False, "rowaida"),
    ("13900000114", "Buyer Lambda", "Ella Chen", "TM 商机", "Inquiry from TM", "", "2026-09-02", "2026-09-10", False, "aparna"),
    ("13900000115", "Buyer Mu", "Ella Chen", "TM 商机", "Inquiry from TM", "Australia", "2026-09-05", "2026-09-10", True, "rozi"),
    ("13900000116", "Buyer Nu", "Ella Chen", "TM 商机", "Inquiry from TM", "Saudi Arabia", "2026-09-10", "2026-09-10", False, "ggff"),
    ("13900000134", "Buyer Phi", "Ella Chen", "询盘商机", "Inquiry from Product Details Page", "United Kingdom", "2026-09-07", "2026-09-09", False, "john"),
    ("13900000117", "Buyer Xi", "Ella Chen", "TM 商机", "Inquiry from TM", "United Arab Emirates", "2026-09-08", "2026-09-09", False, "ansab"),
    ("13900000118", "Buyer Omicron", "Ella Chen", "TM 商机", "Inquiry from TM", "United Kingdom", "2026-08-13", "2026-09-09", True, "dilek"),
]

def build_opportunities():
    opps = []
    for (iid, buyer, owner, cat, src, country, ct, ut, hi, ckey) in LEADS:
        opps.append({
            "lead_id": iid,
            "buyer": buyer,
            "owner": owner,
            "category": cat,
            "source": src,
            "country": country,
            "create_time": ct,
            "update_time": ut,
            "high_intent": hi,
            "conversation_key": ckey,
            "dedup_note": None,
        })
    # 标注疑似重复（同一会话，未确认是否同一采购项目）
    for o in opps:
        if o["conversation_key"] == "nsabimana":
            o["dedup_note"] = "疑似与 13900000106 同一会话（未确认是否同一采购项目）"
        if o["conversation_key"] == "hashim":
            o["dedup_note"] = "疑似与 13900000109 同一会话（未确认是否同一采购项目）"
    return opps


def ev(speaker, time, text_original, translation, judgment):
    return {"speaker": speaker, "time": time, "text_original": text_original,
            "translation": translation, "is_judgment": judgment}


def build_tasks():
    t = []
    as_of = "2026-09-11"

    # 1) Jens —— 承诺待兑现，未到期（两天内答复，due 09-13）
    t.append({
        "task_id": se.make_task_id("13900000101", "承诺待兑现"),
        "lead_id": "13900000101", "buyer": "Buyer Alpha", "owner": "Ella Chen",
        "action_type": "承诺待兑现",
        "evidence": [
            ev("buyer", "09-11 17:43", "for DDP sea the price should be much lower to be profitable",
               "（判断）买家要求 DDP 海运价必须低很多才有利可图", False),
            ev("seller", "09-11 17:42", "I will look for other freight forwarders... Or you can wait two days",
               "（译文）我会再找其他货代……或请你等两天", False),
        ],
        "trigger_reason": "买家嫌 206 美元/盏卖不动，要求更低 DDP 报价",
        "blocker": "货代报价未取得", "responsible": "Ella Chen",
        "preconditions": [],
        "due_at": "2026-09-13",  # 两天内答复，非今日逾期
        "next_action": "找多家货代比价，两天内给出 DDP 对比报价",
        "completion_criteria": "发出 DDP 对比报价 或 明确延期说明",
        "next_check_at": "2026-09-12",
        "status": "open",
    })

    # 2) Paola —— 承诺待兑现，无客户期限（不编造）
    t.append({
        "task_id": se.make_task_id("13900000133", "承诺待兑现"),
        "lead_id": "13900000133", "buyer": "Buyer Upsilon", "owner": "Ella Chen",
        "action_type": "承诺待兑现",
        "evidence": [
            ev("buyer", "09-10 22:59", "Hola, me podrias cotizar lampara de muestra a esta dirección 广州市白云区…",
               "（译文）你好，能否把样品灯报价发到这个广州地址", False),
            ev("seller", "09-11 09:27", "Por supuesto que sí", "（译文）当然可以", False),
        ],
        "trigger_reason": "买家明确要样品报价并给了收货地址",
        "blocker": "样品报价未出", "responsible": "Ella Chen",
        "preconditions": ["确认样品规格（颜色/数量）"],
        "due_at": None,  # 无客户期限，不编造；团队响应目标为内部 SLA
        "next_action": "确认样品规格后出样品报价（含到广州运费）",
        "completion_criteria": "样品报价发出",
        "next_check_at": "2026-09-12",
        "status": "open",
    })

    # 3) YANIS —— 前置条件未满足（缺地址，不催报价）
    t.append({
        "task_id": se.make_task_id("13900000111", "前置条件未满足"),
        "lead_id": "13900000111", "buyer": "Buyer Theta", "owner": "Bin Cai",
        "action_type": "前置条件未满足",
        "evidence": [
            ev("bot", "09-11 05:44", "Impossible de garantir l'exonération douanière…",
               "不能保证关税豁免（自动接待）", False),
            ev("buyer", "09-11 05:45", "je vais me renseigner auprès de la douane française, ensuite je reviens vers vous",
               "（译文）我先咨询法国海关，再回来找你", False),
            ev("seller", "09-11 07:41", "Veuillez me fournir l'adresse.", "（译文）请提供地址", False),
            ev("seller", "09-11 07:42", "Je vais calculer un prix incluant les frais de port pour vous.",
               "（译文）我来给你算含运费的价格", False),
        ],
        "trigger_reason": "买家询价，业务员承诺含运费报价但缺收货地址",
        "blocker": "收货地址/配置未收到（买家在查海关）", "responsible": "客户（等待提供地址）",
        "preconditions": ["收货地址", "数量/规格"],
        "due_at": None,
        "next_action": "等待客户地址；超时则精准补问地址与配置（不直接催报价）",
        "completion_criteria": "收到地址与配置（核价前置满足）",
        "next_check_at": "2026-09-12",
        "status": "open",
    })

    # 4) Lina —— 唛头确认（未闭环）
    t.append({
        "task_id": se.make_task_id("13900000102", "订单收尾待办"),
        "lead_id": "13900000102", "buyer": "Buyer Beta", "owner": "Ella Chen",
        "action_type": "订单收尾待办",
        "evidence": [
            ev("seller", "09-11 17:05", "The goods have been taken for wooden crating.",
               "（译文）货物已送去打木箱", False),
            ev("seller", "09-11 17:40", "Can I put your name on the outer carton, or do you need anything else...?",
               "（译文）我能在外箱印你的名字吗，还需要别的吗？", False),
            ev("buyer", "09-11 17:16", "Thats great! Thank you", "（译文）太好了，谢谢", False),
        ],
        "trigger_reason": "出货阶段需确认外箱唛头",
        "blocker": "唛头问题客户未答复", "responsible": "客户（待确认唛头）",
        "preconditions": [],
        "due_at": None,
        "next_action": "跟进外箱唛头确认，并核对出货节点",
        "completion_criteria": "唛头确认 + 出货节点核对完成（礼貌收尾≠闭环）",
        "next_check_at": "2026-09-12",
        "status": "open",
    })

    # 5) josh —— 认证异议核验（发文件≠解决）
    t.append({
        "task_id": se.make_task_id("13900000104", "高意向待推进"),
        "lead_id": "13900000104", "buyer": "Buyer Delta", "owner": "Ella Chen",
        "action_type": "高意向待推进",
        "evidence": [
            ev("buyer", "09-11 02:53", "质疑 listing 上 CE Certified / DOC 标识与实际不符",
               "（判断）买家对认证标识提出异议，需解决后才购买", True),
            ev("seller", "09-11 14:28", "Part of this file is for plaster lamps; it is written in the title...",
               "（译文）这份文件部分是石膏灯用的，标题里有写……", False),
        ],
        "trigger_reason": "买家认证异议未解决前不买",
        "blocker": "资料是否对应型号/问题待核验；买家未确认", "responsible": "Ella Chen",
        "preconditions": ["PDF 须对应买家质疑的型号"],
        "due_at": None,
        "next_action": "核验资料是否对应买家型号与问题，再安排买家确认",
        "completion_criteria": "买家确认认证异议解决（或明确下一步）",
        "next_check_at": "2026-09-12",
        "status": "open",
    })

    # 6) Jonathan —— 规格确认（完成标准=规格确认，非下单）
    t.append({
        "task_id": se.make_task_id("13900000105", "高意向待推进"),
        "lead_id": "13900000105", "buyer": "Buyer Epsilon", "owner": "Alice Lam",
        "action_type": "高意向待推进",
        "evidence": [
            ev("buyer", "09-11 13:44", "I will go with what you suggest... 3 small plates then",
               "（译文）就按你建议的……先 3 个小盘", False),
            ev("seller", "09-11 14:01", "Then I will provide you with 2.5-meter or 3-meter cables...",
               "（译文）那我给你配 2.5 米或 3 米的线……", False),
        ],
        "trigger_reason": "买家已选型，需确认线长规格",
        "blocker": "线长规格待客户确认", "responsible": "客户（待确认线长）",
        "preconditions": [],
        "due_at": None,
        "next_action": "等买家确认线长/规格",
        "completion_criteria": "规格（线长）确认（下单是后续商机目标，不混入本任务）",
        "next_check_at": "2026-09-12",
        "status": "open",
    })

    # 7) Buyer Gamma —— 运费异议（明确下一步）
    t.append({
        "task_id": se.make_task_id("13900000103", "待核查"),
        "lead_id": "13900000103", "buyer": "Buyer Gamma", "owner": "Ella Chen",
        "action_type": "待核查",
        "evidence": [
            ev("buyer", "09-11 16:28", "تكاليف الشحن مرتفعه جدًا جدًا", "（译文）运费非常非常高", False),
            ev("seller", "09-11 16:30", "تكلفة الشحن الخاصة بي تشمل التوصيل حتى الباب...",
               "（译文）我的运费含送货上门……", False),
        ],
        "trigger_reason": "运费异议",
        "blocker": "运费方案未被接受", "responsible": "Ella Chen",
        "preconditions": [],
        "due_at": None,
        "next_action": "解释运费构成、核验可行替代（海运/自提/货代）、确认接受条件",
        "completion_criteria": "买家接受运费方案或明确拒绝（有聊天依据）",
        "next_check_at": "2026-09-12",
        "status": "open",
    })

    # 8/9) Buyer Zeta、Hashim —— 重复线索核实（不删除、不合并）
    for lid, buyer, owner, ref in [
        ("13900000106", "Buyer Zeta", "Alice Lam", "13900000107/13900000108"),
        ("13900000109", "Buyer Eta", "Bin Cai", "13900000110"),
    ]:
        t.append({
            "task_id": se.make_task_id(lid, "重复线索核实"),
            "lead_id": lid, "buyer": buyer, "owner": owner,
            "action_type": "重复线索核实",
            "evidence": [
                ev("seller", "09-11 13:31", "You're welcome.", "（译文）不客气（礼貌收尾）", False),
            ] if buyer.startswith("Buyer Zeta") else [
                ev("buyer", "09-11 12:53", "Thanks", "（译文）谢谢", False),
            ],
            "trigger_reason": f"多条询盘 ID 疑似同一会话（{ref}）",
            "blocker": "未确认是否同一采购项目", "responsible": owner,
            "preconditions": [],
            "due_at": None,
            "next_action": "核实是否同一采购项目，避免重复联系；不自动合并/修改后台",
            "completion_criteria": "确认线索归属（合并/独立）",
            "next_check_at": "2026-09-12",
            "status": "open",
        })

    # 闭环会话（本次无动作，但保留记录；礼貌收尾不等同订单闭环）
    for lid, buyer, owner in [
        ("13900000107", "Buyer Zeta", "Alice Lam"),
        ("13900000108", "Buyer Zeta", "Alice Lam"),
        ("13900000110", "Buyer Eta", "Bin Cai"),
    ]:
        t.append({
            "task_id": se.make_task_id(lid, "本次会话已闭环"),
            "lead_id": lid, "buyer": buyer, "owner": owner,
            "action_type": "本次会话已闭环",
            "evidence": [],
            "trigger_reason": "疑似重复线索，主线索已记录",
            "blocker": None, "responsible": owner,
            "preconditions": [], "due_at": None,
            "next_action": "无（见主线索的重复线索核实任务）",
            "completion_criteria": "随主线索核实",
            "next_check_at": None,
            "status": "closed",
        })

    # 统一刷新逾期状态
    se.refresh_overdue(t, as_of)
    return t


def main():
    state, errors = se.load_state()
    if errors:
        print(f"[WARN] 状态读取失败，已跳过对应 section 的写入：{errors}")

    # 商机：按 lead_id 幂等合并，不再整体替换（修复 40→20 退回）
    opps = state["opportunities"] if state["opportunities"] is not None else []
    added_opp = 0
    for opp in build_opportunities():
        _, is_new = se.upsert_opportunity(opps, opp)
        if is_new:
            added_opp += 1

    tasks = state["tasks"] if state["tasks"] is not None else []
    created = 0
    for task in build_tasks():
        _, is_new = se.upsert_task(tasks, task)
        if is_new:
            created += 1

    # 样本回放记录（与日常运行分开）：仅追加运行记录，不覆盖本次运行的真实覆盖范围
    checkpoints = state["checkpoints"] if state["checkpoints"] is not None else {"runs": [], "coverage": None}
    checkpoints.setdefault("runs", [])
    checkpoints["runs"].append({
        "run_id": se.stable_id("sample", "page1", "2026-09-11"),
        "kind": "sample_replay",
        "at": se._now_iso(),
        "note": "首屏 20 条样本回放（非日常运行入口）",
    })

    if state["opportunities"] is not None:
        se.save_section("opportunities", opps)
    if state["tasks"] is not None:
        se.save_section("tasks", tasks)
    if state["checkpoints"] is not None:
        se.save_section("checkpoints", checkpoints)

    print(f"seeded: {len(opps)} leads (new_opp={added_opp}), {len(tasks)} tasks (new={created})")
    # 计数核对
    convs = len({o["conversation_key"] for o in opps})
    buyers = len({o["buyer"] for o in opps})
    open_tasks = sum(1 for x in tasks if x.get("status") == "open")
    print(f"原始线索={len(opps)} 独立会话={convs} 独立客户(按名,估计)={buyers} 待办={open_tasks}")


if __name__ == "__main__":
    main()
