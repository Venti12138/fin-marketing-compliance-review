#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
样本基线回归。

规则库的内嵌测试（validate_rules.py）保护的是单条规则的词表，覆盖不到
「多条规则共同作用在一份完整材料上」的结果。而合规审查工具最怕的失效恰恰
在这一层：某次调宽正则修好了一个漏报，同时让另一条规则在合规材料上误报，
单规则测试全绿，只有整份材料跑一遍才看得出来。

本脚本对 examples/ 下的 8 份样本按固定上下文重跑，逐项比对期望值。三份
合规/边界样本的 violation 期望值为 0，是误报的硬守门线——它们比违规样本
更重要，因为把合规材料判成违规会直接摧毁合规人员对工具的信任。

规则库变更导致基线合理变化时，用 --update 重新生成期望值并在提交信息中
说明原因；不要为了让 CI 变绿而随手更新。

用法：
    python3 scripts/check_baselines.py            # 校验，不一致则退出码 1
    python3 scripts/check_baselines.py --verbose  # 附带规则覆盖统计
    python3 scripts/check_baselines.py --update   # 重新生成本文件中的期望值
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (文件名, product, audience, media, institution, 中文名)
# 上下文四维必须显式给全（media 对私募样本为 None，因其材料载体不影响结论），
# 缺省会导致带该维度限定的规则被跳过，基线数字失去意义。
SAMPLES = [
    ("sample-public-fund-violating.md",    "public_fund",  "public",   "online", "fund_manager",      "公募违规版"),
    ("sample-public-fund-compliant.md",    "public_fund",  "public",   "online", "fund_manager",      "公募合规版"),
    ("sample-public-fund-edge-cases.md",   "public_fund",  "public",   "online", "fund_manager",      "公募边界表述版"),
    ("sample-public-fund-video-script.md", "public_fund",  "public",   "video",  "fund_manager",      "短视频脚本"),
    ("sample-private-fund-violating.md",   "private_fund", "specific", None,     "fund_manager",      "私募违规版"),
    ("sample-private-fund-compliant.md",   "private_fund", "specific", None,     "fund_manager",      "私募合规版"),
    ("sample-securities-firm-promo.md",    "public_fund",  "public",   "online", "securities_firm",   "券商推广材料"),
    ("sample-wealth-mgmt-violating.md",    "wealth_mgmt",  "public",   "online", "wealth_subsidiary", "理财产品违规版"),
]

FIELDS = ("applied", "total", "violation", "evidence_required",
          "manual_review", "advisory", "pending")

FIELD_CN = {
    "applied": "适用规则", "total": "发现", "violation": "违规",
    "evidence_required": "补证", "manual_review": "研判",
    "advisory": "建议", "pending": "未生效",
}

# 期望基线。修改规则库后如确认变化合理，用 --update 重新生成。
BASELINE = {
    "sample-public-fund-violating.md":    (32, 30, 17, 5, 6, 2, 2),
    "sample-public-fund-compliant.md":    (32,  6,  0, 0, 5, 1, 0),
    "sample-public-fund-edge-cases.md":   (32,  6,  0, 0, 5, 1, 0),
    "sample-public-fund-video-script.md": (31, 13,  6, 1, 4, 2, 0),
    "sample-private-fund-violating.md":   (16, 20, 10, 0, 8, 2, 0),
    "sample-private-fund-compliant.md":   (16,  2,  0, 0, 1, 1, 0),
    "sample-securities-firm-promo.md":    (33, 16,  7, 4, 2, 3, 1),
    "sample-wealth-mgmt-violating.md":    ( 7,  6,  6, 0, 0, 0, 1),
}

# 这三份材料本身合规，violation 必须恒为 0。它们存在的唯一目的就是
# 在每次规则调整后证明「没有把合规的说成违规」。
ZERO_VIOLATION_SAMPLES = (
    "sample-public-fund-compliant.md",
    "sample-public-fund-edge-cases.md",
    "sample-private-fund-compliant.md",
)


def scan(sample):
    filename, product, audience, media, institution, _ = sample
    cmd = [sys.executable, os.path.join(HERE, "scan_rules.py"),
           "--file", os.path.join(ROOT, "examples", filename),
           "--product", product, "--audience", audience,
           "--institution", institution, "--json"]
    if media:
        cmd += ["--media", media]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != 0:
        raise RuntimeError("扫描失败（退出码 %d）：%s\n%s"
                           % (proc.returncode, filename, proc.stderr.strip()))
    return json.loads(proc.stdout)


def measure(result):
    summary, stats = result["summary"], result["stats"]
    by_verdict = summary["by_verdict"]
    return (
        stats["rules_applied"],
        summary["total"],
        by_verdict.get("violation", 0),
        by_verdict.get("evidence_required", 0),
        by_verdict.get("manual_review", 0),
        by_verdict.get("advisory", 0),
        summary["pending_total"],
    )


def main():
    parser = argparse.ArgumentParser(description="样本基线回归")
    parser.add_argument("--update", action="store_true",
                        help="重新生成本文件中的 BASELINE 期望值")
    parser.add_argument("--verbose", action="store_true",
                        help="附带规则覆盖统计")
    args = parser.parse_args()

    actual = {}
    covered = set()
    for sample in SAMPLES:
        try:
            result = scan(sample)
        except RuntimeError as exc:
            sys.stderr.write("%s\n" % exc)
            return 2
        actual[sample[0]] = measure(result)
        covered |= {f["rule_id"] for f in result["findings"]}

    if args.update:
        return rewrite_baseline(actual)

    header = "%-16s" % "样本" + "".join("%-9s" % FIELD_CN[f] for f in FIELDS)
    print(header)
    print("-" * len(header))
    failures = []
    for sample in SAMPLES:
        filename, label = sample[0], sample[5]
        got = actual[filename]
        want = BASELINE.get(filename)
        print("%-18s" % label + "".join("%-11d" % v for v in got))
        if want is None:
            failures.append("%s：期望基线缺失，请运行 --update" % label)
            continue
        for i, field in enumerate(FIELDS):
            if got[i] != want[i]:
                failures.append("%s 的「%s」：期望 %d，实际 %d"
                                % (label, FIELD_CN[field], want[i], got[i]))

    for filename in ZERO_VIOLATION_SAMPLES:
        violations = actual[filename][FIELDS.index("violation")]
        if violations:
            failures.append("误报守门线失守：%s 本身合规，却报出 %d 项 violation"
                            % (filename, violations))

    if args.verbose:
        all_ids = set()
        for name, data in load_rule_ids():
            all_ids |= data
        print("\n样本集覆盖规则 %d / %d 条" % (len(covered), len(all_ids)))
        untriggered = sorted(all_ids - covered)
        if untriggered:
            print("从未被任何样本触发：%s" % "、".join(untriggered))

    if failures:
        print("\n基线不一致，共 %d 处：" % len(failures))
        for item in failures:
            print("  - %s" % item)
        print("\n若确认变化合理，运行 python3 scripts/check_baselines.py --update "
              "更新期望值，并在提交信息中说明原因。")
        return 1

    print("\n基线一致，%d 份样本全部通过。" % len(SAMPLES))
    return 0


def load_rule_ids():
    sys.path.insert(0, HERE)
    import rule_engine as eng
    for name, data in eng.load_rule_files(eng.default_root()):
        yield name, {r.get("rule_id") for r in data.get("rules", [])}


def rewrite_baseline(actual):
    """把实测值写回本文件的 BASELINE 常量。"""
    path = os.path.abspath(__file__)
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()

    lines = ["BASELINE = {"]
    width = max(len(s[0]) for s in SAMPLES) + 2
    for sample in SAMPLES:
        filename = sample[0]
        values = ", ".join("%2d" % v for v in actual[filename])
        lines.append('    %-*s (%s),' % (width, '"%s":' % filename, values))
    lines.append("}")
    block = "\n".join(lines)

    start = source.index("BASELINE = {")
    end = source.index("\n}", start) + 2
    with open(path, "w", encoding="utf-8") as f:
        f.write(source[:start] + block + source[end:])
    print("已更新 BASELINE，请检查 diff 并在提交信息中说明变化原因。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
