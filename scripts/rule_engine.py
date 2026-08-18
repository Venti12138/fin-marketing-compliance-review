#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则加载与检测核心。

scan_rules.py（扫描）与 validate_rules.py（校验）共用本模块，
以保证内嵌测试样例验证的逻辑与实际扫描逻辑完全一致。

仅使用标准库，兼容 Python 3.9。
"""

import hashlib
import json
import os
import re

RULES_SUBDIR = os.path.join("references", "rules")
LEGAL_INDEX = os.path.join("references", "legal-index.md")

VALID_STATUS = ("active", "pending", "deprecated")
VALID_SEVERITY = ("red", "orange", "yellow")
VALID_VERDICT = ("violation", "evidence_required", "manual_review", "advisory")
VALID_BASIS = ("statutory", "industry_practice")
VALID_DETECT_TYPE = ("keyword", "required", "conditional_required", "numeric", "manual")

VALID_PRODUCTS = ("public_fund", "private_fund", "am_plan", "wealth_mgmt")
VALID_AUDIENCE = ("public", "specific")
VALID_MEDIA = ("print", "online", "video", "audio")
VALID_INSTITUTIONS = ("fund_manager", "securities_firm", "bank", "wealth_subsidiary")

# 判定为「已提供客观证据」的标志，用于 evidence_required 规则的降级
EVIDENCE_MARKERS = [
    r"数据来源", r"资料来源", r"来源[:：]", r"统计区间", r"数据截至",
    r"截至\s*\d{4}\s*年", r"Wind", r"银河证券", r"晨星", r"海通证券",
    r"基金定期报告", r"基金年报", r"基金季报",
]

SEVERITY_ORDER = {"red": 0, "orange": 1, "yellow": 2}

VERDICT_LABEL = {
    "violation": "违规",
    "evidence_required": "需补充证据",
    "manual_review": "需人工研判",
    "advisory": "建议",
}


def find_skill_root(start):
    """从给定路径向上查找含 references/rules 的目录。"""
    cur = os.path.abspath(start)
    for _ in range(5):
        if os.path.isdir(os.path.join(cur, RULES_SUBDIR)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def default_root():
    return find_skill_root(os.path.dirname(os.path.abspath(__file__)))


SHARED_PATTERNS_FILE = "_shared-patterns.json"
PATTERN_REF = "@"


def load_rule_files(root):
    """返回 [(文件名, 数据)]，跳过 SCHEMA.md 与下划线开头的非规则文件。"""
    rules_dir = os.path.join(root, RULES_SUBDIR)
    out = []
    if not os.path.isdir(rules_dir):
        return out
    for name in sorted(os.listdir(rules_dir)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        path = os.path.join(rules_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            try:
                out.append((name, json.load(f)))
            except ValueError as exc:
                # 附上文件名与行列位置，否则规则库变大后无从定位是哪个文件写坏了
                raise ValueError("规则文件 %s 解析失败：%s" % (name, exc))
    return out


def rule_set_signature(root):
    """
    规则库指纹，用于把一份审查报告钉死到产生它的那一版规则库。

    合规报告需存档备查。若干月后被问及某项认定的依据，必须能取回当时的
    规则与法条原文重新复算。fingerprint 覆盖全部规则文件与法条索引：
    法条原文改动同样会改变报告内容，因此 legal-index.md 也须纳入。
    """
    entries = []
    digest = hashlib.sha256()

    for name, data in load_rule_files(root):
        path = os.path.join(root, RULES_SUBDIR, name)
        with open(path, "rb") as f:
            raw = f.read()
        sha = hashlib.sha256(raw).hexdigest()
        digest.update(name.encode("utf-8"))
        digest.update(sha.encode("ascii"))
        entries.append({
            "file": name,
            "rules": len(data.get("rules", [])),
            "schema_version": data.get("schema_version"),
            "sha256": sha[:16],
        })

    shared_path = os.path.join(root, RULES_SUBDIR, SHARED_PATTERNS_FILE)
    if os.path.isfile(shared_path):
        with open(shared_path, "rb") as f:
            shared_raw = f.read()
        shared_sha = hashlib.sha256(shared_raw).hexdigest()
        digest.update(shared_sha.encode("ascii"))
        entries.append({
            "file": SHARED_PATTERNS_FILE,
            "rules": 0,
            "schema_version": json.loads(shared_raw.decode("utf-8")).get("schema_version"),
            "sha256": shared_sha[:16],
        })

    legal_path = os.path.join(root, LEGAL_INDEX)
    legal_sha = None
    if os.path.isfile(legal_path):
        with open(legal_path, "rb") as f:
            legal_sha = hashlib.sha256(f.read()).hexdigest()
        digest.update(legal_sha.encode("ascii"))

    return {
        "fingerprint": digest.hexdigest()[:16],
        "rules_total": sum(e["rules"] for e in entries),
        "legal_index_sha256": legal_sha[:16] if legal_sha else None,
        "sources": entries,
    }


def load_shared_patterns(root):
    """加载 _shared-patterns.json 中定义的共享模式组。"""
    path = os.path.join(root, RULES_SUBDIR, SHARED_PATTERNS_FILE)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as exc:
            raise ValueError("%s 解析失败：%s" % (SHARED_PATTERNS_FILE, exc))
    return {name: group.get("patterns") or []
            for name, group in (data.get("pattern_groups") or {}).items()}


def _expand_refs(items, groups, missing):
    if not items:
        return items
    out = []
    changed = False
    for item in items:
        if isinstance(item, str) and item.startswith(PATTERN_REF):
            changed = True
            name = item[len(PATTERN_REF):]
            if name in groups:
                out.extend(groups[name])
            else:
                missing.append(name)
        else:
            out.append(item)
    return out if changed else items


def expand_pattern_refs(rule, groups, missing):
    """
    就地把 "@组名" 展开为共享模式组的内容。

    多条规则检查同一类表述、但因产品类型不同而依据不同法条时，词表本身
    高度重合。此前公募、私募、理财三条保本规则各自维护一份副本，结果是
    副本单边演进：私募规则少了「本金安全」「没有风险」等 6 个表述，而这
    些恰恰落在位阶更高的《私募投资基金监督管理条例》射程内，注释却写着
    「与公募规则的唯一差异是避险一词」。按规则手写的内嵌测试查不出这类
    漂移，只有让词表物理上共享同一份定义才能根除。
    """
    detect = rule.get("detect")
    if not isinstance(detect, dict):
        return

    for key in ("patterns", "any_of", "when", "require_any"):
        if detect.get(key):
            detect[key] = _expand_refs(detect[key], groups, missing)

    exclude = detect.get("exclude_context")
    if isinstance(exclude, dict) and exclude.get("patterns"):
        exclude["patterns"] = _expand_refs(exclude["patterns"], groups, missing)

    for item in detect.get("require_all") or []:
        if isinstance(item, dict) and item.get("any_of"):
            item["any_of"] = _expand_refs(item["any_of"], groups, missing)

    extract = detect.get("extract")
    if isinstance(extract, dict) and extract.get("patterns"):
        extract["patterns"] = _expand_refs(extract["patterns"], groups, missing)


def load_rules(root):
    """展平所有规则，展开共享模式引用。"""
    groups = load_shared_patterns(root)
    missing = []
    rules = []
    for filename, data in load_rule_files(root):
        for rule in data.get("rules", []):
            rule["_file"] = filename
            expand_pattern_refs(rule, groups, missing)
            rules.append(rule)
    if missing:
        # 引用不存在的组会让规则少查一部分内容，属于不可见的漏报，必须立即失败
        raise ValueError("规则引用了未定义的共享模式组：%s（见 %s）" % (
            "、".join(sorted(set(missing))), SHARED_PATTERNS_FILE))
    return rules


def load_legal_ref_ids(root):
    """从 legal-index.md 抽取所有条目编号，用于校验 legal_basis 引用。"""
    path = os.path.join(root, LEGAL_INDEX)
    ref_ids = set()
    if not os.path.isfile(path):
        return ref_ids
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^###\s+([A-Z][A-Z0-9\-]*[A-Z0-9])\s", line)
            if m:
                ref_ids.add(m.group(1))
    return ref_ids


def compile_patterns(patterns):
    """返回 [(原始串, 已编译)]，编译失败的条目会被跳过。

    此处静默跳过是为了让单个坏正则不至于中断整次扫描，但「悄悄少查一项」
    在合规场景同样危险。因此扫描入口会用 collect_pattern_errors 预先扫出
    所有无法编译的模式并计入 stats.pattern_errors，由报告显式告知用户。
    """
    out = []
    for pat in patterns or []:
        try:
            out.append((pat, re.compile(pat)))
        except re.error:
            continue
    return out


def iter_rule_patterns(rule):
    """产出规则中所有正则模式，形如 (字段路径, 模式串)。"""
    detect = rule.get("detect") or {}

    for key in ("patterns", "any_of", "when", "require_any"):
        for pat in detect.get(key) or []:
            yield "detect.%s" % key, pat

    exclude = detect.get("exclude_context") or {}
    for pat in exclude.get("patterns") or []:
        yield "detect.exclude_context.patterns", pat

    for i, item in enumerate(detect.get("require_all") or []):
        for pat in (item or {}).get("any_of") or []:
            yield "detect.require_all[%d].any_of" % i, pat

    for pat in (detect.get("extract") or {}).get("patterns") or []:
        yield "detect.extract.patterns", pat


def collect_pattern_errors(rules):
    """
    找出所有无法编译的正则。

    一条规则的某个模式写坏时，该模式会被静默丢弃，规则看似仍在工作却少查了
    一部分内容。用户据此认为「已检查」是最坏的结果，故须在扫描结果中显式报出。
    """
    errors = []
    for rule in rules:
        for where, pat in iter_rule_patterns(rule):
            if not isinstance(pat, str):
                errors.append({
                    "rule_id": rule.get("rule_id"),
                    "where": where,
                    "pattern": repr(pat),
                    "error": "模式必须是字符串",
                })
                continue
            try:
                re.compile(pat)
            except re.error as exc:
                errors.append({
                    "rule_id": rule.get("rule_id"),
                    "where": where,
                    "pattern": pat,
                    "error": str(exc),
                })
    return errors


# 国内金融机构存量材料大量为 GBK/GB18030 编码，仅按 UTF-8 读取会直接崩溃。
# gb18030 是 GBK/GB2312 的超集且几乎能解码任意字节序列，必须排在 UTF-8 之后，
# 否则会把 UTF-8 文本误解码成乱码。utf-8-sig 可同时处理带 BOM 与不带 BOM 的文本。
TEXT_ENCODINGS = ("utf-8-sig", "gb18030", "utf-16")


def read_text_file(path):
    """读取材料文件，返回 (文本, 实际编码)。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    # 全部失败时以替换字符兜底，避免因个别坏字节导致整份材料无法审查
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"


# (scope 字段, context 字段, 中文名)
#
# 四个维度一律「缺失即跳过」：宁可漏报也不在信息不足时误报。
#
# 产品类型尤其不能放行。若未提供 product 就应用带 products 限定的规则，
# 私募与理财的专属规则会落到公募材料上，导致向公募材料引用《私募证券投资
# 基金运作指引》这类不适用的条款——错误的法条引用比漏报危险得多，合规人员
# 拿它去举证会直接失分。跳过的规则会连同原因出现在 stats.skipped_rules 中，
# 由报告向用户说明哪些检查项未被覆盖。
SCOPE_DIMENSIONS = (
    ("products", "product", "产品类型"),
    ("audience", "audience", "受众范围"),
    ("media", "media", "材料载体"),
    ("institutions", "institution", "机构类型"),
)


def scope_skip_reason(rule, context):
    """
    按 products / audience / media / institutions 四维路由。

    规则适用时返回 None，否则返回中文跳过原因，供报告说明哪些检查项未被应用。
    """
    scope = rule.get("scope") or {}
    for key, ctx_key, label in SCOPE_DIMENSIONS:
        allowed = scope.get(key)
        if not allowed:
            continue
        value = context.get(ctx_key)
        if not value:
            return "%s未提供" % label
        if value not in allowed:
            return "%s不匹配" % label
    return None


def scope_matches(rule, context):
    return scope_skip_reason(rule, context) is None


COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def mask_comments(text):
    """
    将 HTML/Markdown 注释替换为等长空格。

    注释不会出现在最终发布物中，对其扫描只会产生噪音——起草阶段的批注里
    引用违禁词原文（如「此处不得出现保本表述」）是常见写法，会被误判为违规。
    用等长空格替换而非删除，保证所有字符偏移量不变，行号与原文片段仍然准确。
    """
    return COMMENT_RE.sub(lambda m: " " * len(m.group(0)), text)


def build_line_index(text):
    lines = []
    offset = 0
    for i, line in enumerate(text.split("\n"), start=1):
        lines.append((i, offset))
        offset += len(line) + 1
    return lines


def line_of(line_index, pos):
    lo, hi = 0, len(line_index) - 1
    result = 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if line_index[mid][1] <= pos:
            result = line_index[mid][0]
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def make_excerpt(text, start, end, window=30):
    left = max(0, start - window)
    right = min(len(text), end + window)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    return prefix + text[left:right].replace("\n", " ").strip() + suffix


def has_evidence_nearby(text, pos, radius=200):
    segment = text[max(0, pos - radius):min(len(text), pos + radius)]
    for marker in EVIDENCE_MARKERS:
        if re.search(marker, segment):
            return True
    return False


def dedupe_overlapping(hits):
    """
    同一规则内重叠命中合并为一条，保留最长匹配。

    例：「售罄」与「一日售罄」同属饥饿营销规则且区间重叠，应作为一处报出，
    否则同一句话被重复计数，虚增发现数并干扰评测指标。

    实现为「按长度降序贪心保留」：长匹配优先入选，后续与任一已选区间重叠者丢弃。
    早期实现按起点排序、只与首个重叠项比较后即 break，会因中间区间搭桥而误删
    互不重叠的命中——(0,6) (4,12) (10,30) 三处只剩 (10,30)，材料开头的违规
    凭空消失。此处必须与全部已选区间比较，不能提前退出。
    """
    ordered = sorted(hits, key=lambda h: (-(h[1] - h[0]), h[0]))
    kept = []
    for hit in ordered:
        start, end = hit[0], hit[1]
        if any(start < k[1] and end > k[0] for k in kept):
            continue
        kept.append(hit)
    return sorted(kept, key=lambda h: h[0])


def _base_finding(rule, verdict, note=""):
    return {
        "rule_id": rule.get("rule_id"),
        "title": rule.get("title"),
        "category": rule.get("category"),
        "verdict": verdict,
        "severity": rule.get("severity", "yellow"),
        "basis": rule.get("basis", "statutory"),
        "status": rule.get("status", "active"),
        "effective_date": rule.get("effective_date"),
        "matched_text": None,
        "line": None,
        "excerpt": None,
        "legal_basis": rule.get("legal_basis", []),
        "enforcement_case": rule.get("enforcement_case"),
        "suggestion": rule.get("suggestion", ""),
        "engine_note": note,
    }


def is_excluded(text, start, end, exclude):
    """
    判断命中是否落在排除上下文中。

    命中点前 window 字符至命中结束的区间内匹配到排除模式即视为误报跳过。
    否定表述（「不承诺保本」「非保本浮动收益」）的否定词通常出现在被匹配词之前，
    故向前取窗口、向后只到命中结束。
    """
    if not exclude:
        return False
    window = exclude.get("window", 12)
    segment = text[max(0, start - window):end]
    for _, regex in compile_patterns(exclude.get("patterns")):
        if regex.search(segment):
            return True
    return False


def _eval_keyword(rule, text, line_index):
    detect = rule["detect"]
    verdict = rule.get("verdict", "violation")
    exclude = detect.get("exclude_context")
    findings = []

    raw_hits = []
    for raw, regex in compile_patterns(detect.get("patterns")):
        for m in regex.finditer(text):
            if is_excluded(text, m.start(), m.end(), exclude):
                continue
            raw_hits.append((m.start(), m.end(), raw, m.group(0)))

    # 重叠命中的合并统一由 evaluate_rule 出口处理，此处不再单独去重
    for start, end, raw, matched in raw_hits:
        actual = verdict
        note = ""
        if verdict == "evidence_required":
            if has_evidence_nearby(text, start):
                actual = "advisory"
                note = "命中该表述，但附近已检出数据出处标注，降级为提示。仍需确认所提供证据是否充分支撑该表述。"
            else:
                note = "法条限定为「在未提供客观证据的情况下」才构成违规。材料中未检出数据出处标注，需补充客观证据或删除该表述。"
        elif verdict == "manual_review":
            note = "该规则需结合上下文判断，本项为候选标记，须由合规人员确认。"

        f = _base_finding(rule, actual, note)
        f["matched_text"] = matched
        f["line"] = line_of(line_index, start)
        f["excerpt"] = make_excerpt(text, start, end)
        f["_span"] = (start, end)
        findings.append(f)
    return findings


def _first_hit(text, patterns):
    for _, regex in compile_patterns(patterns):
        m = regex.search(text)
        if m:
            return m
    return None


def _eval_required(rule, text, line_index):
    detect = rule["detect"]
    if _first_hit(text, detect.get("any_of")):
        return []
    verdict = rule.get("verdict", "violation")
    note = ""
    if rule.get("basis") == "industry_practice":
        note = "本项属行业惯例，无现行条款依据，输出为建议不构成违规认定。"
    return [_base_finding(rule, verdict, note)]


def _eval_conditional_required(rule, text, line_index):
    detect = rule["detect"]
    findings = []

    when = detect.get("when")
    trigger = None
    if when:
        trigger = _first_hit(text, when)
        if not trigger:
            return []

    verdict = rule.get("verdict", "violation")
    trigger_note = ""
    if trigger:
        trigger_note = "触发条件命中：材料中出现「%s」。" % trigger.group(0)

    require_any = detect.get("require_any")
    if require_any:
        if _first_hit(text, require_any):
            return []
        f = _base_finding(rule, verdict, trigger_note)
        if trigger:
            f["line"] = line_of(line_index, trigger.start())
            f["excerpt"] = make_excerpt(text, trigger.start(), trigger.end())
        findings.append(f)
        return findings

    for item in detect.get("require_all") or []:
        if _first_hit(text, item.get("any_of")):
            continue
        note = trigger_note
        if rule.get("basis") == "industry_practice":
            note += "本项属行业惯例，无现行条款依据，输出为建议不构成违规认定。"
        f = _base_finding(rule, verdict, note)
        f["title"] = "%s（缺少：%s）" % (rule.get("title"), item.get("name", ""))
        if trigger:
            f["line"] = line_of(line_index, trigger.start())
            f["excerpt"] = make_excerpt(text, trigger.start(), trigger.end())
        findings.append(f)
    return findings


_OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _eval_numeric(rule, text, line_index):
    detect = rule["detect"]
    extract = detect.get("extract") or {}
    assertion = detect.get("assert") or {}
    group = extract.get("group", 1)
    op = _OPS.get(assertion.get("op", ">="))
    threshold = assertion.get("value")
    findings = []

    matches = []
    for _, regex in compile_patterns(extract.get("patterns")):
        for m in regex.finditer(text):
            try:
                value = float(m.group(group))
            except (ValueError, IndexError, TypeError):
                continue
            matches.append((m, value))

    if not matches:
        if detect.get("on_missing") == "manual":
            f = _base_finding(rule, "manual_review",
                              "材料中未提取到可判定的数值，需人工核验。")
            findings.append(f)
        return findings

    verdict = rule.get("verdict", "violation")
    for m, value in matches:
        if op is None or threshold is None:
            continue
        if op(value, threshold):
            continue

        f = _base_finding(
            rule, verdict,
            "提取值 %s%s，不满足 %s %s 的要求。" % (
                _fmt_num(value), extract.get("unit", ""),
                assertion.get("op"), _fmt_num(threshold)))
        f["matched_text"] = m.group(0)
        f["line"] = line_of(line_index, m.start())
        f["excerpt"] = make_excerpt(text, m.start(), m.end())
        f["_span"] = m.span()
        findings.append(f)
    return findings


def _fmt_num(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _eval_manual(rule, text, line_index):
    detect = rule["detect"]
    m = _first_hit(text, detect.get("when"))
    if not m:
        return []
    f = _base_finding(
        rule, rule.get("verdict", "manual_review"),
        "该规则需结合外部数据或上下文判断，扫描仅标记触发位置，须由合规人员核验。")
    f["matched_text"] = m.group(0)
    f["line"] = line_of(line_index, m.start())
    f["excerpt"] = make_excerpt(text, m.start(), m.end())
    return [f]


_EVALUATORS = {
    "keyword": _eval_keyword,
    "required": _eval_required,
    "conditional_required": _eval_conditional_required,
    "numeric": _eval_numeric,
    "manual": _eval_manual,
}


def _dedupe_findings(findings):
    """
    合并同一规则内位置重叠的命中，统一在检测器出口处理。

    早期只有 _eval_keyword 自行去重，_eval_numeric 漏了，导致 PERF-001 的
    「近 N 个月」与「最近 N 个月」两个模式对同一处文本各报一次，同一违规被
    双倍计数。而报告里的发现总数、交叉计数都直接取自扫描结果，虚增的数字会
    原样写进交给合规人员的报告里。

    放在出口而非各检测器内，是为了让后续新增的检测器自动获得该保证。
    只有携带 _span 的 finding 参与合并：conditional_required 的多条缺失项
    共享同一触发位置却代表不同缺失要素，不可合并，故不标记 _span。
    """
    indexed = [(i, f) for i, f in enumerate(findings) if f.get("_span")]
    if len(indexed) < 2:
        return findings
    hits = [(f["_span"][0], f["_span"][1], i, None) for i, f in indexed]
    kept = {h[2] for h in dedupe_overlapping(hits)}
    return [f for i, f in enumerate(findings)
            if not f.get("_span") or i in kept]


def evaluate_rule(rule, text, line_index=None):
    """对单条规则求值，返回 findings 列表。不做 scope 路由。"""
    if line_index is None:
        line_index = build_line_index(text)
    detect = rule.get("detect") or {}
    evaluator = _EVALUATORS.get(detect.get("type"))
    if not evaluator:
        return []
    findings = _dedupe_findings(evaluator(rule, text, line_index))
    for f in findings:
        f.pop("_span", None)
    return findings


def evaluate_all(text, rules, context=None, include_pending=True):
    """按上下文路由后逐条求值，返回 (findings, stats)。"""
    context = context or {}
    text = mask_comments(text)
    line_index = build_line_index(text)
    findings = []
    applied = 0
    skipped = []

    for rule in rules:
        if rule.get("status") == "deprecated":
            continue
        if rule.get("status") == "pending" and not include_pending:
            continue
        reason = scope_skip_reason(rule, context)
        if reason:
            skipped.append({
                "rule_id": rule["rule_id"],
                "title": rule["title"],
                "reason": reason,
            })
            continue
        applied += 1
        findings.extend(evaluate_rule(rule, text, line_index))

    findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f["severity"], 9),
        0 if f["status"] == "active" else 1,
        f["line"] if f["line"] is not None else 10 ** 6,
    ))

    skipped_by_reason = {}
    for item in skipped:
        skipped_by_reason[item["reason"]] = skipped_by_reason.get(item["reason"], 0) + 1

    stats = {
        "rules_total": len(rules),
        "rules_applied": applied,
        "rules_skipped": len(skipped),
        "skipped_by_reason": skipped_by_reason,
        "skipped_rules": skipped,
        "pattern_errors": collect_pattern_errors(rules),
    }
    return findings, stats
