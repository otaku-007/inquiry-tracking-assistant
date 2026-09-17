# -*- coding: utf-8 -*-
"""把历史沉默客户识别结果写入 state/（机会 + 唤醒候选）。幂等合并，不整体替换。

来源：2026-09-12 浏览器子代理只读扫描（第 96/95 页 + 4 条详情）。

v1.2 修正：
- 历史候选阶段统一为「待核实」，不再直接标「该唤醒」。
- 每条保留各自已知事实，不再用同一句 history_note 覆盖所有客户。
- Marina 的列表 update_time 与详情最后消息时间不一致，单独记录待回查。
"""

import state_engine as se

SILENT_LEADS = [
    # (inquiry_id, buyer, owner, country, category, create, update)
    ("139000001041", "Fonda Arnaud Nanfack", "Alice Lam", "cm", "TM 商机", "2025-09-08", "2025-09-08"),
    ("139000001047", "Buyer Pi", "Bin Cai", "bf", "TM 商机", "2025-09-08", "2025-09-08"),
    ("139000001051", "Buyer Chi", "Alice Lam", "us", "TM 商机", "2025-09-07", "2025-09-07"),
    ("139000001045", "Miguel Arnaldo Molina Riveros", "Alan Au", "pe", "TM 商机", "2025-09-07", "2025-09-07"),
    ("139000001046", "fabiana donatti", "Alice Lam", "br", "TM 商机", "2025-09-07", "2025-09-07"),
    ("139000001049", "Buyer Rho", "Alice Lam", "gh", "TM 商机", "2025-09-07", "2025-09-07"),
    ("139000001052", "Buyer Psi", "Alice Lam", "sk", "询盘商机", "2025-09-07", "2025-09-07"),
    ("139000001048", "Buyer Sigma", "Alice Lam", "gb", "询盘商机", "2025-09-07", "2025-09-07"),
    ("139000001060", "Buyer Sigma", "Alice Lam", "gb", "询盘商机", "2025-09-07", "2025-09-07"),
    ("139000001042", "Desire Kibelushi", "Alice Lam", "zr", "TM 商机", "2025-09-06", "2025-09-07"),
    ("139000001043", "Demetra Popa", "Alice Lam", "it", "TM 商机", "2025-08-12", "2025-09-07"),
    ("139000001053", "Buyer Omega", "Alice Lam", "gb", "TM 商机", "2025-09-06", "2025-09-06"),
    ("139000001054", "Buyer A2", "Alan Au", "de", "询盘商机", "2025-09-08", "2025-09-08"),
    ("139000001058", "Kawira Njeru", "Alice Lam", "de", "询盘商机", "2025-09-08", "2025-09-08"),
    ("139000001050", "Buyer Tau", "Alice Lam", "us", "询盘商机", "2025-09-08", "2025-09-08"),
    ("139000001055", "Buyer B2", "Alice Lam", "ph", "TM 商机", "2025-09-07", "2025-09-08"),
    ("139000001057", "Ali Akbar", "Alice Lam", "ca", "询盘商机", "2025-09-08", "2025-09-08"),
    ("139000001059", "Atika El inani", "Alice Lam", "us", "询盘商机", "2025-09-08", "2025-09-08"),
    ("139000001044", "Buyer C2", "Alice Lam", "de", "TM 商机", "2025-09-08", "2025-09-08"),
    ("139000001056", "Buyer C2", "Alice Lam", "de", "询盘商机", "2025-09-08", "2025-09-08"),
]

# 4 条详情判定：inquiry_id -> 逐条已知事实（不再统一成一句）
DETAILS = {
    "139000001053": {  # Buyer Omega
        "classification": "待核实候选",
        "last_msg_speaker": "seller",
        "last_msg_time": "2025-09-06 15:54",
        "history_unknown": True,
        "history_note": "产品已停产，需先核实是否有匹配替代款；无供应方案则缺乏唤醒理由",
        "wake_value_pending": "核实替代款供应",
    },
    "139000001041": {  # Fonda Arnaud Nanfack
        "classification": "待核实候选",
        "last_msg_speaker": "seller",
        "last_msg_time": "2025-09-14 02:06",
        "history_unknown": True,
        "history_note": "索要目录，收到资料后沉默；需核实实际采购需求、历史联系及新联系价值",
        "wake_value_pending": "核实采购需求与历史联系",
    },
    "139000001047": {  # Buyer Pi —— 时间字段不一致，单独记录待回查
        "classification": "待核实候选",
        "last_msg_speaker": "seller",
        "last_msg_time": "2026-04-26 03:34",
        "list_update_time": "2025-09-08",
        "time_discrepancy_note": "列表 update_time(2025-09-08) 与详情最后消息时间(2026-04-26)不一致：字段语义待回查，不能据此判定一年未聊天",
        "history_unknown": True,
        "history_note": "买家索目录后消失，卖家多次模板跟进无果；需核实次数与有效新方案，不能因久未回复再追加模板",
        "wake_value_pending": "核实次数与有效新方案",
    },
    "139000001045": {  # Miguel Arnaldo Molina Riveros
        "classification": "未知C",
        "last_msg_speaker": "seller",
        "last_msg_time": "2025-09-07 09:04",
        "history_unknown": True,
        "no_valid_demand": True,
        "history_note": "会话仅 3 条（Hi+转交通知），无买家有效发言，无法判定需求",
    },
}


def main():
    state, errors = se.load_state()
    if errors:
        print(f"[WARN] 状态读取失败，跳过对应 section 写入：{errors}")

    opps = state["opportunities"] if state["opportunities"] is not None else []
    existing_ids = {o.get("lead_id") for o in opps}
    added = 0
    for (iid, buyer, owner, country, cat, ct, ut) in SILENT_LEADS:
        if iid in existing_ids:
            continue
        se.upsert_opportunity(opps, {
            "lead_id": iid, "buyer": buyer, "owner": owner, "country": country,
            "category": cat, "source": "历史沉默扫描", "create_time": ct,
            "update_time": ut, "high_intent": False,
            "conversation_key": f"silent-{iid}", "dedup_note": None,
            "silent": True,
        })
        added += 1

    # 唤醒状态：历史联系次数无法核实 → history_unknown=True，不按零
    wakes = state["wakes"] if state["wakes"] is not None else {}
    for iid, d in DETAILS.items():
        w = wakes.setdefault(iid, {"contacts": [], "plan": None})
        w["history_unknown"] = d.get("history_unknown", True)
        w["classification"] = d["classification"]
        w["last_msg_speaker"] = d["last_msg_speaker"]
        w["last_msg_time"] = d["last_msg_time"]
        w["history_note"] = d["history_note"]
        if d.get("no_valid_demand"):
            w["no_valid_demand"] = True
        if d.get("wake_value_pending"):
            w["wake_value_pending"] = d["wake_value_pending"]
        if d.get("time_discrepancy_note"):
            w["list_update_time"] = d.get("list_update_time")
            w["time_discrepancy_note"] = d["time_discrepancy_note"]

    if state["opportunities"] is not None:
        se.save_section("opportunities", opps)
    if state["wakes"] is not None:
        se.save_section("wakes", wakes)

    n_silent = sum(1 for o in opps if o.get("silent"))
    print(f"silent leads added={added}, total silent={n_silent}, wake records={len(wakes)}")
    verify = sum(1 for w in wakes.values() if w.get("classification", "").startswith("待核实"))
    unknown = sum(1 for w in wakes.values() if w.get("classification", "").startswith("未知"))
    print(f"待核实候选={verify} 未知C={unknown}")


if __name__ == "__main__":
    main()
