# 规则库编写规范

本文件定义规则的数据结构与编写约定。新增或修改规则前请先读本文件，改完运行校验脚本。

```bash
python3 scripts/validate_rules.py
```

校验不通过的规则库不得提交。

---

## 一、文件划分

规则按**检查什么**划分为四个文件，不按法规来源划分（一条规则可能有多个法规依据）。

| 文件 | 判断标准 | rule_id 前缀 |
|---|---|---|
| `banned-expressions.json` | 材料里**不该出现**的表述 | `BAN-` |
| `performance-display.json` | 业绩数据**怎么展示**才合规 | `PERF-` |
| `mandatory-elements.json` | 材料里**必须出现**的要素 | `REQ-` |
| `format-presentation.json` | 内容**怎么排版呈现** | `FMT-` |

新增规则时按这个顺序自问：

1. 这条规则是禁止某种说法吗 → `banned-expressions`
2. 是关于业绩数字的展示方式吗 → `performance-display`
3. 是要求材料必须包含某内容吗 → `mandatory-elements`
4. 是关于字号、位置、时长、篇幅的吗 → `format-presentation`

边界情形：

- "展示业绩必须同时登载业绩比较基准"→ `performance-display`（业绩展示方式），不是 `mandatory-elements`
- "业绩与基准必须在同一位置"→ `format-presentation`（位置要求）
- "登载业绩必须声明过往业绩不预示未来"→ `mandatory-elements`（必须出现的要素）

下划线开头的文件不是规则文件，不参与规则加载：

| 文件 | 用途 |
|---|---|
| `_shared-patterns.json` | 跨规则复用的模式组，见下节 |

### 共享模式组

多条规则检查同一类表述、却因产品类型不同而依据不同法条时，**词表必须共享，不得各自维护副本**。在 `_shared-patterns.json` 的 `pattern_groups` 中定义，规则里用 `"@组名"` 引用，加载时展开：

```json
"patterns": ["@guaranteed_return_core", "避险"]
```

引用可出现在 `patterns`、`any_of`、`when`、`require_any`、`require_all[].any_of`、`extract.patterns`、`exclude_context.patterns` 任意位置，可与普通模式混用。引用了未定义的组会直接报错，不会静默跳过。

**为什么必须这样做**：公募、私募、理财三条保本规则曾各自维护一份词表，结果单边演进——私募规则少了「本金安全」「没有风险」等 6 个表述，而这些恰恰落在位阶更高的《私募投资基金监督管理条例》射程内；更糟的是规则注释写着「与公募规则的唯一差异是避险一词」，把错误固化了下来。按规则手写的内嵌测试查不出这类漂移，因为没人会想到给私募规则补一条「本金安全」的用例。**副本一旦存在就一定会漂移，唯一可靠的办法是让它们物理上共享同一份定义。**

判断是否该抽成共享组：如果两条规则的词表本该保持一致、只是适用场景不同，就抽；如果只是碰巧有重叠的词，不抽。

---

## 二、rule_id 约定

格式：`前缀-三位序号`，例如 `BAN-001`。

**rule_id 一经分配不得变更，也不得复用。** 评测集、报告、变更记录都通过它引用规则。

规则作废时不要删除条目，将 `status` 改为 `deprecated` 并在 `notes` 写明原因与替代规则。删除条目会导致历史报告无法追溯。

序号在文件内递增，不填补空缺。

---

## 三、字段定义

### 必填字段

| 字段 | 类型 | 取值 | 说明 |
|---|---|---|---|
| `rule_id` | string | 见上 | 全局唯一 |
| `title` | string | | 一句话说明规则，会出现在报告里 |
| `category` | string | 见附录 A | 报告分组用 |
| `status` | string | `active` / `pending` / `deprecated` | 规则生效状态 |
| `severity` | string | `red` / `orange` / `yellow` | 见下 |
| `verdict` | string | `violation` / `evidence_required` / `manual_review` / `advisory` | 命中后如何定性 |
| `basis` | string | `statutory` / `industry_practice` | 有无条款依据 |
| `detect` | object | 见第四节 | 检测逻辑 |
| `suggestion` | string | | 给业务人员的修改建议，要可操作 |

### 条件必填

| 字段 | 何时必填 |
|---|---|
| `effective_date` | `status` 为 `pending` 时必填，格式 `YYYY-MM-DD` |
| `legal_basis` | `basis` 为 `statutory` 时必填且非空 |

`basis` 为 `industry_practice` 时 `legal_basis` **必须为空数组**。给行业惯例挂条款号会在合规官核查时直接失分，校验脚本会拦截这种情况。

### 可选字段

| 字段 | 说明 |
|---|---|
| `scope` | 适用范围路由，见第五节。缺省表示不限制 |
| `enforcement_case` | 真实处罚案例，含决定书文号与监管原始措辞 |
| `notes` | 设计说明、已知边界、误报风险、与其他规则的关系 |
| `tests` | 内嵌测试样例，见第六节。强烈建议填写 |

### severity 取值标准

| 值 | 标准 |
|---|---|
| `red` | 依据为法律或行政法规，且有明确罚则 |
| `orange` | 依据为部门规章、证监会规范性文件或协会自律规则 |
| `yellow` | 无直接依据的建议项，或需人工确认的提示项 |

严重等级依据**法规位阶**判定，不依据主观危害程度。这样任何人加规则时判断标准一致。

### verdict 取值标准

| 值 | 含义 | 报告中呈现 |
|---|---|---|
| `violation` | 命中即违规 | 违规 |
| `evidence_required` | 法条限定"在未提供客观证据的情况下"才违规，引擎会检查附近有无数据出处，有则自动降级为提示 | 需补充证据 |
| `manual_review` | 需结合上下文或外部数据判断，引擎只标记位置 | 需人工研判 |
| `advisory` | 建议项，不构成违规认定 | 建议 |

选择依据是**法条怎么写的**，不是我们觉得多严重：

- 法条无附加条件 → `violation`
- 法条含"在未提供客观证据的情况下"等限定 → `evidence_required`
- 法条含"夸大""片面""误导"等需要判断的词 → `manual_review`
- 没有法条 → `advisory`

---

## 四、detect 结构

`detect.type` 有五种，互斥。

### 4.1 keyword — 关键词匹配

材料中出现指定表述即命中。

```json
"detect": {
  "type": "keyword",
  "patterns": ["保本", "保收益", "年化收益率\\s*(可达|不低于)"]
}
```

`patterns` 为正则表达式数组，Python `re` 语法。注意 JSON 中反斜杠要转义（`\\d` 而非 `\d`）。

同一规则内多个模式命中重叠文本时，引擎自动合并为一条，取最长匹配。所以"售罄"和"一日售罄"可以同时列出，不会重复报出。

可选的 `exclude_context` 用于排除误报：

```json
"detect": {
  "type": "keyword",
  "patterns": ["保本", "保收益"],
  "exclude_context": {
    "patterns": ["不(承诺|保证)", "非保本", "不保本"],
    "window": 12
  }
}
```

命中点**向前** `window` 个字符至命中结束的区间内，若匹配到任一排除模式，则跳过该命中。窗口向前取是因为否定词通常出现在被匹配词之前（"不承诺保本"）。

这是降误报的首选手段，尤其适用于两类情形：

- **否定语境**：`本基金不承诺保本` 不应命中"保本"
- **限定语境**：`风险等级为 R2，属于中低风险产品` 不应命中"低风险"

对于纯粹的子串包含问题（"中低风险"含"低风险"），优先用正则负向断言 `(?<![中较极])低风险` 解决，比 `exclude_context` 更精确、开销更小。

### 4.2 required — 无条件必备

材料中必须出现下列内容之一，否则命中。

```json
"detect": {
  "type": "required",
  "any_of": ["风险提示", "投资有风险", "投资需谨慎"]
}
```

### 4.3 conditional_required — 条件必备

材料中出现 `when` 所列内容时，才要求必须出现 `require_*` 所列内容。

单项检查，缺失报一条：

```json
"detect": {
  "type": "conditional_required",
  "when": ["经中国证监会注册", "证监会注册"],
  "require_any": ["注册并不代表", "并不表明.{0,20}(判断|推荐|保证)"]
}
```

分项检查，缺哪项报哪项：

```json
"detect": {
  "type": "conditional_required",
  "when": null,
  "require_all": [
    {"name": "推广材料标识", "any_of": ["推广材料", "宣传推介材料"]},
    {"name": "风险提示", "any_of": ["风险提示", "投资有风险"]},
    {"name": "适合对象", "any_of": ["适合.{0,10}投资者", "C[1-5].{0,6}以上"]}
  ]
}
```

`when` 为 `null` 表示无条件触发（等价于 `required`，但支持分项）。

### 4.4 numeric — 数值提取与判定

从材料中提取数字并判断，用于业绩区间等可计算规则。

```json
"detect": {
  "type": "numeric",
  "extract": {
    "patterns": ["近(\\d+)个月.{0,10}(收益|回报|涨幅)"],
    "group": 1,
    "unit": "month"
  },
  "assert": {"op": ">=", "value": 6},
  "on_missing": "skip"
}
```

- `group`：正则中捕获数字的分组序号
- `assert.op`：`>=` `>` `<=` `<` `==` `!=`，断言**不成立**时命中
- `on_missing`：材料中提取不到数值时的行为，`skip`（跳过）或 `manual`（转人工核验）

上例含义：材料中出现"近 N 个月收益"时，N 必须 `>= 6`，否则违规。

### 4.5 manual — 仅标记位置

命中触发词后只标记位置，判断交给语义研判层或合规人员。用于需要外部数据或上下文理解的规则。

```json
"detect": {
  "type": "manual",
  "when": ["基金经理.{0,20}(业绩|回报)", "代表作", "任职以来"]
}
```

---

## 五、scope 路由

四个维度，缺省表示该维度不限制。

```json
"scope": {
  "products": ["public_fund", "private_fund", "am_plan", "wealth_mgmt"],
  "audience": ["public", "specific"],
  "media": ["print", "online", "video", "audio"],
  "institutions": ["fund_manager", "securities_firm", "bank", "wealth_subsidiary"]
}
```

| 维度 | 取值 | 含义 |
|---|---|---|
| `products` | `public_fund` | 公开募集证券投资基金 |
| | `private_fund` | 私募投资基金 |
| | `am_plan` | 证券期货经营机构资产管理计划 |
| | `wealth_mgmt` | 理财公司理财产品 |
| `audience` | `public` | 面向不特定对象 |
| | `specific` | 面向特定对象 |
| `media` | `print` / `online` / `video` / `audio` | 材料载体 |
| `institutions` | 见上表 | 制作或使用材料的机构类型 |

**`audience` 与 `media` 是两个正交维度，不可混同。** 私募材料可以在线上投放（仍是特定对象），公募材料也可以印成海报。《金融产品网络营销管理办法》约束的是载体，《私募投资基金监督管理条例》约束的是受众。

**路由的谨慎原则**：规则限定了 `media` 或 `institutions`，但调用方未提供该信息时，该规则**不适用**。宁可漏报也不在信息不足时误报。`products` 与 `audience` 不受此限制。

同一违规行为在公募与私募下依据不同法条时，**应拆成两条规则**而非合并。例如"避险"一词在私募场景违规（依据中基协《私募投资基金募集行为管理办法》第二十四条第四项），在公募场景无依据（该词出自已废止的证监会公告〔2008〕2 号）。合并会导致引用错误法条。

---

## 六、legal_basis 结构

```json
"legal_basis": [
  {
    "ref_id": "N-AD-15",
    "law": "《公开募集证券投资基金宣传推介材料管理暂行规定》",
    "clause": "第十五条第（二）项",
    "excerpt": "违规使用安全、保证、承诺、保险、有保障、高收益、无风险等可能使投资人认为没有风险或者忽视风险的表述"
  }
]
```

| 字段 | 要求 |
|---|---|
| `ref_id` | 必须在 `references/legal-index.md` 中存在，校验脚本会检查 |
| `law` | 法规全称，带书名号 |
| `clause` | 精确到项，格式如"第十五条第（二）项" |
| `excerpt` | 条款原文摘录，不得转述或改写 |

多条依据按位阶从高到低排列：法律 → 行政法规 → 部门规章 → 规范性文件 → 自律规则。

**`excerpt` 必须是条款原文。** 报告会直接展示这段文字给合规人员核对，改写会导致核对失败。

---

## 七、tests 内嵌测试

```json
"tests": {
  "match": [
    "本产品保本保收益，请放心投资",
    "承诺最低收益 5%"
  ],
  "no_match": [
    "本基金不保本，可能发生亏损",
    "资金由银行安全存管"
  ]
}
```

- `match`：应当命中的文本，跑不出命中则校验失败
- `no_match`：不应命中的文本，用于锁定已知误报

每次调整正则后运行 `validate_rules.py`，可立即发现是否破坏了既有行为。

`no_match` 尤其重要。修正误报时，把误报文本加进 `no_match`，可防止后续改动重新引入该误报。

---

## 八、迭代操作指引

### 新增一条规则

1. 确认法条依据，在 `references/legal-index.md` 中新增条目（若尚未收录），标注核实状态与来源 URL
2. 按第一节判断放入哪个文件
3. 分配 rule_id，文件内序号递增
4. 按 schema 填写字段，`excerpt` 必须抄原文
5. 填 `tests`，至少一条 `match`
6. 运行 `python3 scripts/validate_rules.py`
7. 用样本材料跑一遍确认无误报

### 修改一条规则的正则

1. 先把当前误报或漏报的文本加进 `tests` 的对应数组
2. 改 `patterns`
3. 运行校验脚本，确认新旧用例全过

### 法规更新时

- 新法规发布但未生效：`status` 设为 `pending`，填 `effective_date`。引擎会在报告中单独分节呈现，不与现行规则混列
- 法规生效：`status` 改为 `active`，清空 `effective_date`
- 法规废止：`status` 改为 `deprecated`，在 `notes` 写明废止依据。**不要删除条目**
- 条款号变更：更新 `legal_basis`，同步修改 `legal-index.md`

### 降低误报的常见手法

按优先级：

1. 收窄正则，加限定上下文。例：`有保障` 改为 `(收益|本金|回报).{0,4}有保障`
2. 把 `verdict` 从 `violation` 降为 `manual_review`，让人工确认
3. 用 `scope` 限制适用范围
4. 拆成两条规则，分别处理高置信与低置信情形

不要用扩充 `no_match` 的方式掩盖正则本身的问题，`no_match` 是回归防护，不是过滤器。

---

## 附录 A：category 取值

`category` 用于报告分组，同类规则应使用相同取值。

| 取值 | 含义 |
|---|---|
| `guaranteed_return` | 保本保收益与变相承诺 |
| `unwarranted_claim` | 无依据的褒扬表述 |
| `absolute_claim` | 绝对化用语 |
| `sales_pressure` | 饥饿营销与销售诱导 |
| `endorsement` | 推荐性文字与代言 |
| `disparagement` | 诋毁贬低同业 |
| `false_endorsement` | 利用监管备案等增信 |
| `inducement` | 诱导性用语 |
| `performance_window` | 业绩展示区间 |
| `benchmark` | 业绩比较基准 |
| `data_source` | 数据来源与出处 |
| `manager_performance` | 基金经理业绩 |
| `third_party_rating` | 第三方评价与榜单 |
| `hypothetical_performance` | 模拟与回测业绩 |
| `comparison` | 业绩比较方法 |
| `required_declaration` | 法定声明 |
| `risk_disclosure` | 风险揭示 |
| `suitability_labeling` | 适当性与客户等级标注 |
| `special_product` | 特殊品种专门揭示 |
| `formatting` | 形式与版式 |
| `industry_practice` | 行业惯例项 |

新增取值需同步更新本表与报告模板的分组配置。
