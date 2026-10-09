# 猫咪健康对话 Agent — 复原 Demo

> 把遗失的 React 前端从行为规格复原成可跑的 Python demo。  
> 严格对齐三份权威文档：  
> ① `routers.ts` (641 行 TypeScript)  
> ② SKILL.md（`cat-care-conversational-agent` 8 原则 / 5 步流程 / 8 项交付前检查）  
> ③ `knowledge_base_v1.jsonl` (14 条权威) + `source_facts.json` (16 条来源)

## 一行启动

```bash
python server.py
# → listening on http://127.0.0.1:8787
```

打开浏览器访问根路径即可对话；首页内置 `?debug=1` 入口展示重试 trace。

## 目录

```
猫咪健康Agent_复原Demo_2026-08-24/
├─ server.py              ← HTTP 入口（http.server 标准库）
├─ engine/
│  ├─ config.py           ← 路径/端口/开关
│  ├─ knowledge.py        ← KB 加载 + 三层别名 + 检索 + 证据
│  ├─ rules.py            ← E0 / 昵称 / 省略句 / 提醒门禁 / 分级
│  ├─ templates.py        ← E0 覆盖文案 + 摘要 + 追问（routers.ts L518-543 原文照抄）
│  ├─ generator.py        ← RuleGenerator（默认）/ LLMGenerator（OpenAI 兼容 adapter）
│  ├─ review.py           ← 6 项审核 + 否定语境遮蔽 + 结构修复
│  ├─ retry.py            ← 检索/证据/断言 三层降级（核心创新）
│  ├─ store.py            ← SQLite 持久化（8 张表 / WAL / 线程安全）
│  └─ chat.py             ← 编排层（多轮 / 焦距 / 档案门禁 / 提醒门禁）
├─ data/
│  ├─ knowledge_base_v1.jsonl    ← 14 条权威 KB（sha256 锁定）
│  ├─ source_facts.json          ← 16 条 verified 来源
│  ├─ source-registry.md         ← 注册表
│  └─ fallback_entries.jsonl     ← KB-015/016/017 兜底条目
├─ web/index.html         ← 单文件对话前端（含 ?debug=1 trace 面板）
├─ tests/smoke.py         ← 0 依赖冒烟（86 项断言）
├─ docs/验收报告.md       ← 与 e2e 对照表
└─ README.md              ← 本文件
```

## 关键概念

### 三层降级重试（无 LLM 下「agent 二次检索」的折中方案）

`engine/retry.py` 把原状态机的 `regenerated` 拆成三个**独立变化**的层：

| 档位        | 内容                                              | 何时升级        |
| ----------- | ------------------------------------------------- | --------------- |
| 检索 R0→R2  | R0 标准别名+top5；R1 别名扩展+top8；R2 启用兜底 | `low_confidence_retrieval`（只命中 KB-017）或 evidence 摘录关键词覆盖率过低 |
| 证据 E0→E2 | E0 top 条目 source_ids；E1 top 条目全部 source_ids；E2 其它已检索条目 source_ids，并补 S09/S03/S12 | 摘录关键词对 answer 的覆盖率不达标 |
| 断言 A0→A2 | A0 judgment「可能…建议」；A1 observation「观察到…需区分 A/B/C」；A2 uncertain「无法…建议」 | 副作用审核失败 |

每次重试必然产生真实 diff（不是函数重跑），且 `review.attempts` 落库供 `?debug=1` 面板回放。

### 审核器 + 否定语境遮蔽

`engine/review.py` 严格移植 routers.ts L157-173 的 6 项检查：

```
missing_diagnosis_boundary | 缺诊断边界
missing_evidence           | 缺证据
missing_evidence_excerpt   | 缺证据摘录
e0_without_escalation      | E0 但未升级
judgment_not_supported     | judgment 与证据摘录词不匹配
prohibited_action          | 含 must_not 原文
```

防御：审核模式是**无语境子串匹配**——会把 KB 原文里的「不自行给药」「不自行催吐」「不强行灌食」
误判为违规。`must_not` / `can_do` 作为**独立字段**返回（不进 `answer`），审核器额外加一层否定语境遮蔽
（`不要/别/切勿/禁止/避免/不应/不能` + 4 字窗口）。

### 可插拔生成器

```python
# 默认离线 rule（templates.py 模板槽位组装）
# 缺配置时自动回落；接上 LLM 后，编排层零改动即升级

generator_mode()  # "rule" | "llm"
```

LLMGenerator 走 `chat/completions` + `response_format=json_schema`，把 responseValidator 的 15 字段契约作
为 Schema 下发，超时/报错自动回落 RuleGenerator。

## 运行模式

### 离线默认（无需任何外部依赖）

```bash
python server.py   # http://127.0.0.1:8787
```

浏览器输入 `猫吃了百合` → E0 急症升级文案，证据卡显示 S02 百合毒性。
输入 `猫今天不太爱吃` → 触发 R0→R1 检索升档。
输入 `猫打喷嚏流鼻涕` → 触发 R0→R1→R2 三档升档，命中 KB-015 上呼吸道感染。

### 接入 LLM（OpenAI 兼容）

```powershell
$env:CAT_AGENT_GENERATOR = "llm"
$env:CAT_AGENT_LLM_BASE_URL = "https://your-endpoint/v1"
$env:CAT_AGENT_LLM_MODEL = "gpt-4o-mini"
$env:CAT_AGENT_LLM_API_KEY = "sk-..."
python server.py
```

回到对话窗口输入 `?debug=1`（或改 URL 参数），重试 trace 面板开启。

## 与原 react 前端的差异

| 维度         | 原版（已遗失）            | 本 demo                       |
| ------------ | -------------------------- | ----------------------------- |
| 后端实现   | TS + Drizzle ORM + SQLite  | Python 3.13 标准库 + sqlite3  |
| 前端       | React + Vite              | 单 HTML，零依赖，Vanilla JS   |
| 协议       | `/api/chat` 文本           | `/api/v1/*` JSON              |
| E0/E1/E2/E3 | 颜色块（红/橙/绿/灰）       | 同上                          |
| 证据卡     | 折叠列表                  | 同上                          |
| Trace 面板 | 无                         | `?debug=1` 入口才有回放面板   |

## 验收

```bash
python tests/smoke.py
# PASS 86/86   全部通过
```

详见 `docs/验收报告.md`。

## 已知偏差

详见 `docs/验收报告.md § 三` 与计划 §7（共 9 条），均不阻断主线。

## 同步到技能包

```powershell
powershell -ExecutionPolicy Bypass -File sync_to_skill.ps1
```

把以下资源拷进 `cat-care-conversational-agent` 技能包：

```
references/
├─ knowledge_base_v1.jsonl          ← 直接复制
├─ source_facts.json
├─ source-registry.md
└─ fallback_entries.jsonl

templates/
├─ e0_cover_example.md
└─ api-contract.md
```

并向 `SKILL.md` 末尾追加一节「本地 Demo 与知识库位置」（不动原 8 原则 / 5 步流程 / 8 项交付前检查）。
