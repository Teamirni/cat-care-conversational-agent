# cat-care-conversational-agent

> 面向猫咪主人的多轮健康与养护对话 Agent Skill —— **先安全、后解释；不猜病，先分流。**

A multi-turn conversational agent skill for cat owners. It understands colloquial symptom descriptions, performs an emergency (E0) triage first, grounds every recommendation in an auditable RAG evidence chain, and **never** turns a one-line message into a disease diagnosis.

---

## 它解决什么问题

宠物对话类产品的最大风险不是"答错病名"，而是**把一句话改写成疾病诊断**。本 Skill 把对话约束成一条可审计的流水线：

```
口语表达标准化 → E0 红旗预筛查 → 情景化追问与风险分流 → 权威 RAG 检索 → 结构化生成 → 二次审核门禁
```

## 核心设计

| 设计点 | 做法 |
|---|---|
| **安全优先** | 每一轮先查 E0 红旗（呼吸困难、无尿、持续抽搐、疑似中毒、误食百合、大量出血等）→ 立即升级急诊，不等待继续问答 |
| **四级分流** | E0 急诊 / E1 尽快就医 / E2 观察 + 明确升级条件 / E3 追问最少必要事实 |
| **口语情景化** | "掉毛很多" ≠ "**突然**掉毛很多"；短句"突然的 / 现在呢"绑定上一轮主题，而不是开启新问题 |
| **证据可审计** | 证据结果必须保存 `source_id`、机构、原始 URL、核验日期、`knowledge_id`、事实摘录与诊断边界；**没有证据就降低断言强度并追问，不许凭常识补写来源** |
| **写入门禁** | 昵称 / 年龄 / 病史 / 过敏史等只进候选层，用户明确确认后才写入档案；提醒必须结构化确认（内容 + 时间 + 时区），缺任一字段继续追问 |
| **设备数据定位** | 饮水 / 进食 / 排尿等设备读数是**上下文，不是诊断**；异常趋势先向用户确认"是否确实变化、何时开始" |

证据来源限定为权威兽医资源：Cornell Feline Health Center · VCA Animal Hospitals · Merck Veterinary Manual。

## 仓库结构

```
cat-care-conversational-agent/
├── SKILL.md                      # Agent 能力定义：总体原则 + 每轮处理流程 + 输出契约
├── references/
│   ├── knowledge_base_v1.jsonl   # 结构化知识条目（症状 / 同义词 / 就医等级 / 禁止动作）
│   ├── fallback_entries.jsonl    # 无证据命中时的降级条目
│   ├── source-registry.md        # 来源登记：机构 / URL / 核验日期
│   └── source_facts.json         # 已复核的来源事实
└── templates/
    └── api-contract.md           # 对话接口契约模板
```

## 使用方式

把整个目录作为一个 Agent Skill 安装（`SKILL.md` 位于根目录），Agent 加载后即按 SKILL.md 定义的原则与流程运行；也可仅把 SKILL.md 作为提示词工程参考。

## 边界

**本 Skill 不替代执业兽医诊断。** 所有输出只包含风险等级、信息缺口与行动建议，并在涉及就医判断时明确提示线下就诊。

## 作者

翁彥斌（Tearimi）· 产品设计 → AI 产品方向
设计动机与完整复盘见我的 [GitHub 主页](https://github.com/Teamirni)。
