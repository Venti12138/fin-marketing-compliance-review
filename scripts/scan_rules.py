#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
营销材料确定性规则扫描。

检测逻辑位于 rule_engine.py，本脚本只负责命令行参数、上下文推断与结果渲染。
语义研判由调用方（Agent）在本脚本输出的基础上进行。

用法：
    python3 scan_rules.py --file 材料.txt --product public_fund
    python3 scan_rules.py --file 材料.txt --product private_fund --audience specific
    python3 scan_rules.py --file 材料.txt --product public_fund --media online --json
"""

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rule_engine as eng

SEVERITY_CN = {"red": "红", "orange": "橙", "yellow": "黄"}

# 材料文本的最小长度。低于此值视为文档解析失败而非「一份很短的材料」。
# 仅作用于 --file；--text 用于调试短句，不受限制。
MIN_TEXT_LENGTH = 50

# 每条规则最多输出的命中明细数。超出部分只计数不列明细，防止长材料
# 的输出体积超出调用方上下文预算。设为 0 表示不限制。
DEFAULT_MAX_PER_RULE = 10


def cap_findings_per_rule(findings, max_per_rule):
    """
    限制每条规则输出的命中明细数量，返回 (保留的 findings, 被截断的计数)。

    长材料会让输出体积失控：一份 4 万字的路演稿可产生近千条 findings，
    JSON 输出达数百 KB，远超调用方（Agent）的上下文预算。同一条规则在
    同一份材料里命中几十次时，前若干处已足够说明问题。

    统计口径不受影响——summary 基于全量 findings 计算，截断只作用于明细
    列表，且在 summary.findings_truncated 中逐规则说明省略了多少处。
    """
    if not max_per_rule or max_per_rule <= 0:
        return findings, {}

    totals = {}
    for f in findings:
        totals[f["rule_id"]] = totals.get(f["rule_id"], 0) + 1

    kept = []
    shown = {}
    truncated = {}
    for f in findings:
        rid = f["rule_id"]
        n = shown.get(rid, 0)
        if n < max_per_rule:
            shown[rid] = n + 1
            if totals[rid] > max_per_rule:
                f["occurrences"] = totals[rid]
            kept.append(f)
        else:
            truncated[rid] = totals[rid] - max_per_rule
    return kept, truncated


def scan_text(text, product=None, audience=None, media=None, institution=None,
              include_pending=True, root=None, source_encoding=None,
              max_per_rule=DEFAULT_MAX_PER_RULE):
    root = root or eng.default_root()
    if not root:
        raise RuntimeError("未找到 references/rules 目录，请在 skill 根目录下运行。")

    rules = eng.load_rules(root)
    context = {
        "product": product,
        "audience": audience,
        "media": media,
        "institution": institution,
    }
    findings, stats = eng.evaluate_all(text, rules, context, include_pending)

    # 统计先于截断：报告中的数字必须反映真实命中数，截断只影响明细列表
    summary = summarize(findings)
    stats["text_length"] = len(text)
    kept, truncated = cap_findings_per_rule(findings, max_per_rule)
    if truncated:
        summary["findings_shown"] = len(kept)
        summary["findings_truncated"] = truncated

    return {
        "scanned_at": date.today().isoformat(),
        "rule_set": eng.rule_set_signature(root),
        "source_encoding": source_encoding,
        "context": context,
        "stats": stats,
        "summary": summary,
        "findings": kept,
    }


def summarize(findings):
    """
    汇总统计。

    by_verdict_severity 提供判定与级别的交叉计数，active / pending 分列，
    供报告直接引用，避免调用方自行交叉统计出错或将未生效项重复计入总数。
    """
    by_verdict = {}
    by_severity = {}
    by_category = {}
    by_verdict_severity = {}
    pending = 0

    for f in findings:
        by_verdict[f["verdict"]] = by_verdict.get(f["verdict"], 0) + 1
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
        by_category[f["category"]] = by_category.get(f["category"], 0) + 1
        cross = by_verdict_severity.setdefault(f["verdict"], {})
        cross[f["severity"]] = cross.get(f["severity"], 0) + 1
        if f["status"] == "pending":
            pending += 1

    return {
        "total": len(findings),
        "active_total": len(findings) - pending,
        "pending_total": pending,
        "by_verdict": by_verdict,
        "by_severity": by_severity,
        "by_verdict_severity": by_verdict_severity,
        "by_category": by_category,
        "pending_rules_hit": pending,
    }


def render_text(result):
    out = []
    ctx = result["context"]
    s = result["summary"]

    out.append("=" * 70)
    out.append("确定性规则扫描结果")
    out.append("=" * 70)
    out.append("产品类型：%s    受众范围：%s" % (
        ctx.get("product") or "未指定", ctx.get("audience") or "未指定"))
    out.append("材料载体：%s    机构类型：%s" % (
        ctx.get("media") or "未指定", ctx.get("institution") or "未指定"))
    out.append("适用规则：%d / %d 条" % (
        result["stats"]["rules_applied"], result["stats"]["rules_total"]))
    rs = result.get("rule_set") or {}
    if rs.get("fingerprint"):
        out.append("规则库指纹：%s    扫描日期：%s" % (
            rs["fingerprint"], result.get("scanned_at", "")))
    if result.get("source_encoding") and result["source_encoding"] != "utf-8-sig":
        out.append("材料编码：%s（已自动识别）" % result["source_encoding"])
    out.append("")

    pattern_errors = result["stats"].get("pattern_errors") or []
    if pattern_errors:
        out.append("!" * 70)
        out.append("警告：%d 个正则模式无法编译，对应检查项已失效" % len(pattern_errors))
        for e in pattern_errors:
            out.append("    %s %s：%s（%s）" % (
                e["rule_id"], e["where"], e["pattern"], e["error"]))
        out.append("本次扫描结果不完整，请修正规则库后重新扫描。")
        out.append("!" * 70)
        out.append("")
    out.append("共 %d 项发现：" % s["total"])
    for verdict in ("violation", "evidence_required", "manual_review", "advisory"):
        count = s["by_verdict"].get(verdict)
        if count:
            out.append("    %s %d" % (eng.VERDICT_LABEL[verdict], count))
    if s["pending_rules_hit"]:
        out.append("    （其中 %d 项命中尚未生效的规则，单独列出）" % s["pending_rules_hit"])
    out.append("")

    active = [f for f in result["findings"] if f["status"] == "active"]
    pending = [f for f in result["findings"] if f["status"] == "pending"]

    if active:
        out.append("-" * 70)
        out.append("现行有效规则")
        out.append("-" * 70)
        for i, f in enumerate(active, 1):
            out.extend(render_finding(i, f))

    if pending:
        out.append("-" * 70)
        out.append("尚未生效的规则（提前排查）")
        out.append("-" * 70)
        for i, f in enumerate(pending, 1):
            out.extend(render_finding(i, f))

    skipped = result["stats"].get("skipped_rules") or []
    if skipped:
        out.append("")
        out.append("-" * 70)
        out.append("本次未应用的规则（%d 条）" % len(skipped))
        out.append("-" * 70)
        for item in skipped:
            out.append("    %-10s %s（%s）" % (
                item["rule_id"], item["title"], item["reason"]))

    out.append("")
    out.append("-" * 70)
    out.append("本结果由确定性规则扫描产生，供合规人员复核使用，不构成合规结论。")
    out.append("标记为「需人工研判」的项须结合上下文确认，最终判定权在合规人员。")
    return "\n".join(out)


def render_finding(index, f):
    lines = []
    lines.append("[%d] %s ｜ %s ｜ %s级 ｜ %s" % (
        index,
        eng.VERDICT_LABEL.get(f["verdict"], f["verdict"]),
        f["title"],
        SEVERITY_CN.get(f["severity"], f["severity"]),
        f["rule_id"],
    ))
    if f.get("matched_text"):
        lines.append("    命中：%s（第 %s 行）" % (f["matched_text"], f["line"]))
    if f.get("excerpt"):
        lines.append("    原文：%s" % f["excerpt"])

    if f.get("legal_basis"):
        for basis in f["legal_basis"]:
            lines.append("    依据：%s%s" % (basis.get("law", ""), basis.get("clause", "")))
            if basis.get("excerpt"):
                lines.append("          「%s」" % basis["excerpt"])
    elif f.get("basis") == "industry_practice":
        lines.append("    依据：无现行条款依据（行业惯例）")

    if f.get("effective_date"):
        lines.append("    生效：%s 起施行" % f["effective_date"])
    if f.get("enforcement_case"):
        lines.append("    案例：%s" % f["enforcement_case"])
    if f.get("suggestion"):
        lines.append("    建议：%s" % f["suggestion"])
    if f.get("engine_note"):
        lines.append("    说明：%s" % f["engine_note"])
    lines.append("")
    return lines


def main():
    parser = argparse.ArgumentParser(description="金融营销材料确定性规则扫描")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="待扫描的纯文本文件路径")
    src.add_argument("--text", help="直接传入待扫描文本")
    parser.add_argument("--product", default=None, choices=list(eng.VALID_PRODUCTS),
                        help="产品类型")
    parser.add_argument("--audience", default=None, choices=list(eng.VALID_AUDIENCE),
                        help="受众范围：public 面向不特定对象，specific 面向特定对象")
    parser.add_argument("--media", default=None, choices=list(eng.VALID_MEDIA),
                        help="材料载体")
    parser.add_argument("--institution", default=None,
                        choices=list(eng.VALID_INSTITUTIONS), help="机构类型")
    parser.add_argument("--exclude-pending", action="store_true",
                        help="排除尚未生效的规则")
    parser.add_argument("--max-per-rule", type=int, default=DEFAULT_MAX_PER_RULE,
                        help="每条规则最多输出的命中明细数，0 为不限制"
                             "（默认 %d，统计数字不受影响）" % DEFAULT_MAX_PER_RULE)
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    encoding = None
    if args.file:
        if not os.path.isfile(args.file):
            sys.stderr.write("文件不存在：%s\n" % args.file)
            return 2
        try:
            text, encoding = eng.read_text_file(args.file)
        except OSError as exc:
            sys.stderr.write("文件读取失败：%s\n" % exc)
            return 2
        # 空文件通常意味着 PDF/DOCX 抽取失败。此时若继续扫描，required 类规则
        # 会因「未检出必备要素」而报出一批红级违规，产出一份语气笃定却毫无依据
        # 的报告——这比直接失败危险得多。
        if len(text.strip()) < MIN_TEXT_LENGTH:
            sys.stderr.write(
                "材料内容为空或过短（%d 字符，下限 %d）：%s\n"
                "若原件为 PDF/DOCX/PPTX，请确认文本抽取是否成功。\n"
                % (len(text.strip()), MIN_TEXT_LENGTH, args.file))
            return 3
    else:
        text = args.text

    try:
        result = scan_text(
            text,
            product=args.product,
            audience=args.audience,
            media=args.media,
            institution=args.institution,
            include_pending=not args.exclude_pending,
            source_encoding=encoding,
            max_per_rule=args.max_per_rule,
        )
    except (RuntimeError, ValueError) as exc:
        sys.stderr.write("扫描失败：%s\n" % exc)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
