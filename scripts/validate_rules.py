#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则库校验。

检查规则是否符合 references/rules/SCHEMA.md 定义的结构，并运行每条规则的
内嵌测试样例。修改规则库后必须运行本脚本，校验不通过不得提交。

用法：
    python3 scripts/validate_rules.py
    python3 scripts/validate_rules.py --quiet     只输出错误
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rule_engine as eng

REQUIRED_FIELDS = ("rule_id", "title", "category", "status", "severity",
                   "verdict", "basis", "detect", "suggestion")

ID_PREFIX_BY_FILE = {
    "banned-expressions.json": "BAN",
    "performance-display.json": "PERF",
    "mandatory-elements.json": "REQ",
    "format-presentation.json": "FMT",
}

DETECT_REQUIRED_KEYS = {
    "keyword": ["patterns"],
    "required": ["any_of"],
    "conditional_required": [],
    "numeric": ["extract", "assert"],
    "manual": ["when"],
}


class Report(object):
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, rule_id, message):
        self.errors.append((rule_id, message))

    def warn(self, rule_id, message):
        self.warnings.append((rule_id, message))

    @property
    def ok(self):
        return not self.errors


def check_patterns_compile(patterns, rule_id, where, report):
    for pat in patterns or []:
        if not isinstance(pat, str):
            report.error(rule_id, "%s 中存在非字符串模式：%r" % (where, pat))
            continue
        try:
            re.compile(pat)
        except re.error as exc:
            report.error(rule_id, "%s 中正则无法编译 %r：%s" % (where, pat, exc))


def validate_detect(rule, report):
    rule_id = rule.get("rule_id", "?")
    detect = rule.get("detect")
    if not isinstance(detect, dict):
        report.error(rule_id, "detect 必须是对象")
        return

    dtype = detect.get("type")
    if dtype not in eng.VALID_DETECT_TYPE:
        report.error(rule_id, "detect.type 取值非法：%r，应为 %s" % (
            dtype, "/".join(eng.VALID_DETECT_TYPE)))
        return

    for key in DETECT_REQUIRED_KEYS.get(dtype, []):
        if not detect.get(key):
            report.error(rule_id, "detect.type 为 %s 时必须提供 %s" % (dtype, key))

    if dtype == "keyword":
        check_patterns_compile(detect.get("patterns"), rule_id, "detect.patterns", report)

    elif dtype == "required":
        check_patterns_compile(detect.get("any_of"), rule_id, "detect.any_of", report)

    elif dtype == "conditional_required":
        check_patterns_compile(detect.get("when"), rule_id, "detect.when", report)
        has_any = detect.get("require_any")
        has_all = detect.get("require_all")
        if not has_any and not has_all:
            report.error(rule_id, "conditional_required 必须提供 require_any 或 require_all")
        if has_any and has_all:
            report.error(rule_id, "require_any 与 require_all 不可同时使用")
        check_patterns_compile(has_any, rule_id, "detect.require_any", report)
        for i, item in enumerate(has_all or []):
            if not isinstance(item, dict) or not item.get("name"):
                report.error(rule_id, "require_all[%d] 缺少 name" % i)
            check_patterns_compile(
                (item or {}).get("any_of"), rule_id,
                "detect.require_all[%d].any_of" % i, report)

    elif dtype == "numeric":
        extract = detect.get("extract") or {}
        check_patterns_compile(extract.get("patterns"), rule_id,
                               "detect.extract.patterns", report)
        group = extract.get("group", 1)
        for pat in extract.get("patterns") or []:
            try:
                if re.compile(pat).groups < group:
                    report.error(rule_id,
                                 "正则 %r 的捕获分组数少于 extract.group=%s" % (pat, group))
            except re.error:
                pass
        assertion = detect.get("assert") or {}
        if assertion.get("op") not in ("<", "<=", ">", ">=", "==", "!="):
            report.error(rule_id, "detect.assert.op 取值非法：%r" % assertion.get("op"))
        if not isinstance(assertion.get("value"), (int, float)):
            report.error(rule_id, "detect.assert.value 必须是数值")
        on_missing = detect.get("on_missing")
        if on_missing and on_missing not in ("skip", "manual"):
            report.error(rule_id, "detect.on_missing 取值非法：%r" % on_missing)

    elif dtype == "manual":
        check_patterns_compile(detect.get("when"), rule_id, "detect.when", report)


def validate_scope(rule, report):
    rule_id = rule.get("rule_id", "?")
    scope = rule.get("scope")
    if scope is None:
        return
    if not isinstance(scope, dict):
        report.error(rule_id, "scope 必须是对象")
        return

    known = {
        "products": eng.VALID_PRODUCTS,
        "audience": eng.VALID_AUDIENCE,
        "media": eng.VALID_MEDIA,
        "institutions": eng.VALID_INSTITUTIONS,
    }
    for key, values in scope.items():
        if key not in known:
            report.error(rule_id, "scope 含未知维度 %r，应为 %s" % (
                key, "/".join(known.keys())))
            continue
        if not isinstance(values, list) or not values:
            report.error(rule_id, "scope.%s 必须是非空数组" % key)
            continue
        for v in values:
            if v not in known[key]:
                report.error(rule_id, "scope.%s 含非法取值 %r，应为 %s" % (
                    key, v, "/".join(known[key])))


def validate_legal_basis(rule, legal_ref_ids, report):
    rule_id = rule.get("rule_id", "?")
    basis = rule.get("basis")
    entries = rule.get("legal_basis", [])

    if basis == "industry_practice":
        if entries:
            report.error(rule_id,
                         "basis 为 industry_practice 时 legal_basis 必须为空。"
                         "给行业惯例挂条款号会在合规核查时失分")
        return

    if not entries:
        report.error(rule_id, "basis 为 statutory 时必须提供至少一条 legal_basis")
        return

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            report.error(rule_id, "legal_basis[%d] 必须是对象" % i)
            continue
        for field in ("ref_id", "law", "clause", "excerpt"):
            if not entry.get(field):
                report.error(rule_id, "legal_basis[%d] 缺少 %s" % (i, field))
        ref_id = entry.get("ref_id")
        if ref_id and legal_ref_ids and ref_id not in legal_ref_ids:
            report.error(rule_id,
                         "legal_basis[%d].ref_id=%s 在 legal-index.md 中不存在。"
                         "请先在法条索引中登记该条款" % (i, ref_id))
        law = entry.get("law") or ""
        if law and not (law.startswith("《") or law.startswith("中基协")
                        or law.startswith("中证协")):
            report.warn(rule_id, "legal_basis[%d].law 建议使用法规全称并带书名号：%r" % (i, law))


def validate_rule(rule, filename, seen_ids, legal_ref_ids, report):
    rule_id = rule.get("rule_id", "?")

    for field in REQUIRED_FIELDS:
        if rule.get(field) in (None, "", []):
            report.error(rule_id, "缺少必填字段 %s" % field)

    expected_prefix = ID_PREFIX_BY_FILE.get(filename)
    if expected_prefix:
        if not re.match(r"^%s-\d{3}$" % expected_prefix, rule_id or ""):
            report.error(rule_id, "rule_id 格式应为 %s-三位数字（文件 %s）" % (
                expected_prefix, filename))
    if rule_id in seen_ids:
        report.error(rule_id, "rule_id 重复，已在 %s 中出现" % seen_ids[rule_id])
    else:
        seen_ids[rule_id] = filename

    if rule.get("status") not in eng.VALID_STATUS:
        report.error(rule_id, "status 取值非法：%r" % rule.get("status"))
    if rule.get("severity") not in eng.VALID_SEVERITY:
        report.error(rule_id, "severity 取值非法：%r" % rule.get("severity"))
    if rule.get("verdict") not in eng.VALID_VERDICT:
        report.error(rule_id, "verdict 取值非法：%r" % rule.get("verdict"))
    if rule.get("basis") not in eng.VALID_BASIS:
        report.error(rule_id, "basis 取值非法：%r" % rule.get("basis"))

    if rule.get("status") == "pending":
        eff = rule.get("effective_date")
        if not eff:
            report.error(rule_id, "status 为 pending 时必须提供 effective_date")
        elif not re.match(r"^\d{4}-\d{2}-\d{2}$", str(eff)):
            report.error(rule_id, "effective_date 格式应为 YYYY-MM-DD：%r" % eff)
    elif rule.get("effective_date") and rule.get("status") == "active":
        report.warn(rule_id, "status 为 active 时不应保留 effective_date")

    if rule.get("basis") == "industry_practice" and rule.get("verdict") == "violation":
        report.error(rule_id,
                     "basis 为 industry_practice 时 verdict 不得为 violation，"
                     "无条款依据不能认定违规")

    validate_detect(rule, report)
    validate_scope(rule, report)
    validate_legal_basis(rule, legal_ref_ids, report)

    if not rule.get("tests"):
        report.warn(rule_id, "未提供 tests，建议补充测试样例以便后续迭代防回归")


def run_tests(rule, report):
    """运行内嵌测试样例，返回 (通过数, 失败数)。"""
    tests = rule.get("tests") or {}
    rule_id = rule.get("rule_id", "?")
    passed = failed = 0

    for sample in tests.get("match", []):
        findings = eng.evaluate_rule(rule, sample)
        if findings:
            passed += 1
        else:
            failed += 1
            report.error(rule_id, "tests.match 未命中：%r" % sample)

    for sample in tests.get("no_match", []):
        findings = eng.evaluate_rule(rule, sample)
        if findings:
            failed += 1
            hit = findings[0].get("matched_text") or "(要素缺失)"
            report.error(rule_id, "tests.no_match 意外命中「%s」：%r" % (hit, sample))
        else:
            passed += 1

    return passed, failed


def check_docs_coverage(root, rule_ids, report):
    """
    检查 README 的规则清单是否覆盖全部规则。

    该清单是手写的，新增规则时极易漏同步——理财产品那三条规则就曾整批
    缺席，而文档正是面试与交付时最先被读到的部分。此处只做存在性检查，
    成本极低但足以捕获遗漏。
    """
    path = os.path.join(root, "README.md")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    missing = [rid for rid in rule_ids if rid and rid not in content]
    if missing:
        report.warn("-", "README.md 未收录以下规则，请同步 §3.5 规则清单：%s"
                    % "、".join(missing))


def main():
    parser = argparse.ArgumentParser(description="规则库校验")
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    args = parser.parse_args()

    root = eng.default_root()
    if not root:
        sys.stderr.write("未找到 references/rules 目录\n")
        return 2

    report = Report()
    legal_ref_ids = eng.load_legal_ref_ids(root)
    if not legal_ref_ids:
        report.warn("-", "未能从 legal-index.md 读取任何条目编号，跳过 ref_id 校验")

    seen_ids = {}
    rule_count = 0
    file_count = 0
    tests_passed = tests_failed = 0
    rules_without_tests = 0

    try:
        rule_files = eng.load_rule_files(root)
        shared_groups = eng.load_shared_patterns(root)
    except ValueError as exc:
        sys.stderr.write("规则文件 JSON 解析失败：%s\n" % exc)
        return 2

    for filename, data in rule_files:
        file_count += 1
        rules = data.get("rules")
        if not isinstance(rules, list):
            report.error(filename, "缺少 rules 数组")
            continue
        for rule in rules:
            rule_count += 1
            # 先展开共享模式引用，后续的正则编译检查与内嵌测试才作用于
            # 扫描时真正使用的模式，而不是 "@组名" 这个字面量
            missing = []
            eng.expand_pattern_refs(rule, shared_groups, missing)
            for name in sorted(set(missing)):
                report.error(rule.get("rule_id", "?"),
                             "引用了未定义的共享模式组 @%s（见 %s）" % (
                                 name, eng.SHARED_PATTERNS_FILE))
            validate_rule(rule, filename, seen_ids, legal_ref_ids, report)
            if rule.get("tests"):
                p, f = run_tests(rule, report)
                tests_passed += p
                tests_failed += f
            else:
                rules_without_tests += 1

    check_docs_coverage(root, list(seen_ids.keys()), report)

    if not args.quiet:
        print("规则文件 %d 个，规则 %d 条" % (file_count, rule_count))
        print("法条索引条目 %d 个" % len(legal_ref_ids))
        print("内嵌测试 %d 通过，%d 失败（%d 条规则未提供测试）" % (
            tests_passed, tests_failed, rules_without_tests))
        print("")

    if report.warnings and not args.quiet:
        print("警告 %d 项：" % len(report.warnings))
        for rule_id, msg in report.warnings:
            print("  [%s] %s" % (rule_id, msg))
        print("")

    if report.errors:
        print("错误 %d 项：" % len(report.errors))
        for rule_id, msg in report.errors:
            print("  [%s] %s" % (rule_id, msg))
        print("")
        print("校验未通过。")
        return 1

    if not args.quiet:
        print("校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
