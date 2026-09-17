# -*- coding: utf-8 -*-
"""v1.3 阶段 1.1 —— 商机沟通列表数据标准化（只读调试工具）。

输入：captures/v1.3/list_raw.json（subjectList.htm 接口原始行 + DOM 对照行）
输出：captures/v1.3/list_normalized.json

三段式结构（原始 Alibaba 字段与内部标准字段严格分离）：
  source.raw        原始接口行（原样保留，含嵌套 sender / latestMessage）
  normalized        内部标准字段（lead_id / buyer_id / conversation_id / ...）
  field_confidence  逐字段置信标注（verified / probable / unverified / unstable）

置信依据（captures/v1.3/field_validation.json，2026-09-17 实测）：
  - tradeId == feedbackId == requestNo，DOM/XHR/重复扫描一致
      → lead_id: verified
  - sender.accountId 两次扫描一致，Buyer Zeta 三条询盘（13900000106 /
    13900000107 / 13900000108）同值 10000000001 → buyer_id: verified（客户级）
  - secTradeId 每次响应重新签发（同一 tradeId 两次抓取全变）
      → conversation_id 不可用：normalized 置 null，confidence: unstable
  - ownerName DOM vs XHR 60/60 一致 → owner: verified
  - countryName 与 DOM 国旗 title 前缀一致（个别行平台侧即缺失）
      → country: verified（缺失即 null，禁止猜）
  - lastestReplyTime == DOM「更新时间」== modifiedTime == readTime
      → list_update_time: verified；语义 = 最后活动时间（含已读动作），
        禁止用于推导客户沉默天数（规格 §4.3 冻结规则）
  - latestMessage.gmtChat 为最后一条聊天消息时间
      → last_message_time: verified（仅 page1 SSR 行含 latestMessage，
        page>=2 需补调 subjectListExtraInfo，见 field_validation notes）
  - latestMessage.sendType "rec"=买家发 / "send"=卖家发（内容佐证 2/2）
      → last_message_sender: probable（样本少，待详情页消息级验证）
  - hasUnread / unreadCount 结构稳定存在，但当前账号无未读样本
      → read_status / unread_count: probable

本模块不写生产 state（opportunities / tasks / wakes / checkpoints 均不动）。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(BASE_DIR, "captures", "v1.3")
DEFAULT_INPUT = os.path.join(CAPTURE_DIR, "list_raw.json")
DEFAULT_OUTPUT = os.path.join(CAPTURE_DIR, "list_normalized.json")

CST = timezone(timedelta(hours=8))  # 北京时间（规格 §1.1：统一北京时间存储展示）

# 置信级别
VERIFIED = "verified"      # 多源实测一致（XHR vs DOM vs 重复扫描）
PROBABLE = "probable"      # 结构存在、已有样本一致，但样本量不足
UNVERIFIED = "unverified"  # 无法确认，禁止用于业务判断
UNSTABLE = "unstable"      # 实测证明易变，不可作稳定键


def _ms_to_iso(ms: Any) -> Optional[str]:
    """epoch 毫秒 → 北京时间 ISO 字符串；空值/非法输入返回 None（禁止猜测）。"""
    if ms in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=CST).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _sender_dir(send_type: Any) -> Optional[str]:
    """latestMessage.sendType → 方向。rec=买家发，send=卖家发；其余 unknown。"""
    if send_type == "rec":
        return "buyer"
    if send_type == "send":
        return "seller"
    return None


def normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """单行原始接口数据 → 三段式标准结构。"""
    sender = raw.get("sender") or {}
    latest = raw.get("latestMessage") or {}
    trade_id = raw.get("tradeId")
    if trade_id is None:
        trade_id = raw.get("feedbackId")
    if trade_id is None:
        trade_id = raw.get("requestNo")

    account_id = sender.get("accountId")
    has_unread = raw.get("hasUnread")
    country = sender.get("countryName")

    normalized: Dict[str, Any] = {
        "lead_id": str(trade_id) if trade_id is not None else None,
        "buyer_id": str(account_id) if account_id is not None else None,
        # secTradeId 实测每次响应重签（field_validation v2），不可作会话稳定键；
        # 稳定会话键待阶段 1.3 详情页调查（候选：tradeId 本身 / 详情页消息上下文）
        "conversation_id": None,
        "buyer_name": sender.get("name"),
        "company": sender.get("companyName"),
        "country": country or None,
        "country_code": sender.get("countryCode") or None,
        "owner": raw.get("ownerName"),
        "owner_id": raw.get("ownerId"),
        "last_message_time": _ms_to_iso(latest.get("gmtChatLong") or latest.get("gmtChat")),
        "last_message_sender": _sender_dir(latest.get("sendType")),
        "read_status": ("unread" if has_unread else "read") if has_unread is not None else None,
        "unread_count": raw.get("unreadCount"),
        # 语义警告：最后活动时间（含已读动作），不是最后消息时间，禁止推导沉默天数
        "list_update_time": _ms_to_iso(raw.get("lastestReplyTime")),
        # url 内含易变 secTradeId 哈希段；仅 imInquiryId=<lead_id> 部分稳定
        "conversation_url": raw.get("url"),
        "inquiry_source": raw.get("source"),
        "subject": raw.get("subject"),
        "feedback_type": raw.get("feedbackType"),
    }

    confidence: Dict[str, str] = {
        "lead_id": VERIFIED if normalized["lead_id"] else UNVERIFIED,
        "buyer_id": VERIFIED if normalized["buyer_id"] else UNVERIFIED,
        "conversation_id": UNSTABLE,
        "buyer_name": VERIFIED if normalized["buyer_name"] else UNVERIFIED,
        "company": PROBABLE if normalized["company"] else UNVERIFIED,
        "country": VERIFIED if normalized["country"] else UNVERIFIED,
        "owner": VERIFIED if normalized["owner"] else UNVERIFIED,
        "last_message_time": VERIFIED if normalized["last_message_time"] else UNVERIFIED,
        "last_message_sender": PROBABLE if normalized["last_message_sender"] else UNVERIFIED,
        "read_status": PROBABLE,
        "unread_count": PROBABLE,
        "list_update_time": VERIFIED if normalized["list_update_time"] else UNVERIFIED,
        "conversation_url": PROBABLE if normalized["conversation_url"] else UNVERIFIED,
        "inquiry_source": VERIFIED if normalized["inquiry_source"] else UNVERIFIED,
    }

    return {
        "source": {
            "kind": "alibaba_message_subjectList",
            "raw": raw,
        },
        "normalized": normalized,
        "field_confidence": confidence,
    }


def dom_crosscheck(rows: List[Dict[str, Any]], dom_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """DOM 对照行 vs 标准化行：按 lead_id 匹配，比对 owner / buyer_name / country 前缀。"""
    by_lead = {}
    for r in rows:
        lid = r["normalized"]["lead_id"]
        if lid:
            by_lead[lid] = r["normalized"]

    matched = 0
    mismatches: List[Dict[str, Any]] = []
    for d in dom_rows:
        lid = str(d.get("trade_id") or d.get("feedback_id") or "")
        norm = by_lead.get(lid)
        if norm is None:
            mismatches.append({"lead_id": lid, "field": "_match", "dom": "MISSING_IN_XHR",
                               "xhr": None})
            continue
        matched += 1
        # 国旗 title 形如 "Germany [Local Time: ...]"，取 " [" 前的国家名
        dom_country = (d.get("country") or "").split(" [")[0].strip() or None
        for field, dom_val in (
            ("owner", d.get("owner")),
            ("buyer_name", d.get("buyer_name")),
            ("country", dom_country),
        ):
            xhr_val = norm.get(field)
            if (dom_val or None) != (xhr_val or None):
                mismatches.append({"lead_id": lid, "field": field,
                                   "dom": dom_val, "xhr": xhr_val})
    return {
        "dom_rows": len(dom_rows),
        "matched_by_lead_id": matched,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:20],
    }


def _field_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合全行置信 + 填充率。

    区分两件事：
    - 置信（机制）：仅在「有值的行」上评估——机制本身经 DOM/XHR/重复扫描验证
      即为 verified，个别行平台侧缺值不影响机制置信；
    - 填充率：有值行 / 总行数，缺值行如实暴露（禁止猜填充）。
    """
    if not rows:
        return {}
    keys = rows[0]["field_confidence"].keys()
    summary: Dict[str, Any] = {}
    for k in keys:
        present = [r["field_confidence"].get(k) for r in rows
                   if r["normalized"].get(k) is not None
                   or k in ("conversation_id",)]  # conversation_id 恒 null，单独评估
        levels = set(present) or {UNVERIFIED}
        if UNSTABLE in levels:
            level = UNSTABLE
        elif UNVERIFIED in levels:
            level = UNVERIFIED
        elif PROBABLE in levels:
            level = PROBABLE
        else:
            level = VERIFIED
        filled = sum(1 for r in rows if r["normalized"].get(k) is not None)
        summary[k] = {
            "confidence": level,
            "fill_rate": "%d/%d" % (filled, len(rows)),
        }
    return summary


def normalize_file(input_path: str, output_path: str) -> Dict[str, Any]:
    with open(input_path, "r", encoding="utf-8") as f:
        raw_doc = json.load(f)

    raw_rows = raw_doc.get("list_rows_raw") or []
    dom_rows = raw_doc.get("dom_rows") or []
    rows = [normalize_row(r) for r in raw_rows]

    out: Dict[str, Any] = {
        "schema": "v1_3_list_normalized_v1",
        "generated_at": datetime.now(tz=CST).isoformat(timespec="seconds"),
        "input": os.path.relpath(input_path, BASE_DIR),
        "captured_at": raw_doc.get("captured_at"),
        "row_count": len(rows),
        "rows": rows,
        "dom_crosscheck": dom_crosscheck(rows, dom_rows),
        "field_summary": _field_summary(rows),
        "semantics_notes": {
            "list_update_time": "最后活动时间（含已读动作）＝lastestReplyTime；"
                                "禁止用于推导客户沉默天数（规格 §4.3 冻结）",
            "conversation_id": "secTradeId 每次响应重新签发，实测不稳定；"
                               "normalized 恒为 null，稳定会话键待阶段 1.3",
            "last_message_sender": "sendType rec=买家 / send=卖家，2 样本内容佐证，"
                                   "待详情页消息级验证",
            "read_status": "hasUnread 结构稳定；当前账号无未读样本，映射未实测",
        },
    }

    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    input_path = argv[0] if len(argv) > 0 else DEFAULT_INPUT
    output_path = argv[1] if len(argv) > 1 else DEFAULT_OUTPUT

    if not os.path.isfile(input_path):
        print("输入不存在: %s" % input_path)
        return 2

    out = normalize_file(input_path, output_path)
    print("标准化完成: %d 行" % out["row_count"])
    print("输出: %s" % os.path.relpath(output_path, BASE_DIR))
    cc = out["dom_crosscheck"]
    print("DOM 对照: %d/%d 匹配, 不一致 %d 处" %
          (cc["matched_by_lead_id"], cc["dom_rows"], cc["mismatch_count"]))
    print("字段置信汇总（置信 / 填充率）:")
    for k, v in sorted(out["field_summary"].items()):
        print("  %-22s %-11s %s" % (k, v["confidence"], v["fill_rate"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
