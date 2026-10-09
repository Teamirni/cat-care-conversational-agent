# 猫咪健康对话 Agent — 接口契约

> 本文档为 `D:\工作文件\猫咪健康Agent_复原Demo_2026-08-24\server.py` 的对外 HTTP 协议。
> 所有响应均统一为 `application/json; charset=utf-8`，错误对象使用统一 envelope。

## 通用约定

| 项              | 约定                                                                 |
| --------------- | -------------------------------------------------------------------- |
| 编码            | UTF-8；请求头 `Content-Type: application/json`                       |
| CORS            | 任意 Origin（本地 demo）                                              |
| 错误对象        | `{ "error": { "code": "snake_case", "message": "…", "details": {} } }` |
| 时区            | 内部统一 `Asia/Shanghai`                                              |
| 端口            | 默认 8787，绑定失败自动 +1，最多 10 次                               |
| 健康检查        | `GET /api/v1/health` 返回 `ok:true` 即视为可用                       |

## 端点

### `GET /`

返回 `web/index.html` 单文件前端（包含 `?debug=1` 重试 trace 入口）。

### `GET /api/v1/health`

健康检查。

**响应 200**

```json
{
  "ok": true,
  "generator": "rule",
  "kb_sha256": "d3fd851a1ce20ceec775243cb58416d5865cbecf4ceb1283ab319c810982cd8b",
  "knowledge_count": 17,
  "verified_source_count": 16,
  "fallback_entry": "KB-017",
  "uptime_unix": 1788000000,
  "app_version": "1.0.0"
}
```

### `GET /api/v1/bootstrap?sessionId=xxx`

一次性返回刷新恢复所需的全部状态（profile / candidates / memories / reminders / device_events / messages）。

| 字段                | 类型 | 说明 |
| ------------------- | ---- | ---- |
| `session_id`        | string | 由调用方提供（前端 localStorage：`cat-agent-session-id`） |
| `welcomed`          | bool   | 是否首次欢迎语已发 |
| `profile`           | object | 已确认的猫咪档案 KV 集 |
| `profile_candidates`| array  | profile 候选（候选生命周期 candidate → confirmed \| deleted） |
| `memories`          | array  | 已确认的长期记忆 |
| `reminders`         | array  | 提醒（含 `timezone` / `schedule` / `instruction` / `status`） |
| `device_events`     | array  | 智能猫窝设备事件趋势（可选） |
| `messages`          | array  | 当前会话历史（按 id 倒序） |
| `knowledge_base_count`     | number | 当前 KB 大小 |
| `verified_source_count`    | number | 当前 verified sources 数 |

### `POST /api/v1/chat`

主对话入口。

**请求体**

```jsonc
{
  "sessionId": "user-1",                     // 推荐：前端从 localStorage 复用；缺省则自动生成 web-session-<ts>
  "messages": [                              // 多轮消息；只关心最后一条 user 文本
    { "role": "user", "content": "我家小白今天吐了" }
  ],
  "context": {                                // 可选：上游上下文（不再使用；以 store 为准）
    "pet_name": "小白"
  }
}
```

**响应 200**

```jsonc
{
  // ① 核心答案
  "answer": "可能与急性胃肠刺激有关…",
  "urgency_level": "E2",              // E0 / E1 / E2 / E3
  "next_action": "建议带猫咪到兽医处进一步评估",
  "can_do": ["维持环境通风", "少量多次饮水"],   // 独立字段，不进 answer
  "must_not": ["不自行给药", "不自行催吐"],     // 独立字段，不进 answer
  "diagnosis_boundary": "本助手判断仅基于知识库内容，不构成最终诊断",

  // ② 理解
  "intent_summary": "用户描述小白呕吐",
  "normalized_symptoms": ["反复呕吐"],
  "onset": "today",
  "timeline": "today",
  "severity": "moderate",
  "behavior_change": false,
  "user_goal": "ask_advice",
  "companion_symptoms": [],
  "missing_info": [],

  // ③ 证据
  "evidence": [
    {
      "source_id": "S05",
      "organization": "VCA Animal Hospitals",
      "title": "Emergencies in Cats",
      "url": "https://vcahospitals.com/know-your-pet/emergencies-in-cats",
      "checked_at": "2026-08-22",
      "supports": ["呕吐与腹泻"],
      "excerpts": [
        {
          "knowledge_id": "KB-007",
          "chunk_id": "S05:KB-007:reviewed-fact",
          "excerpt": "持续或反复呕吐腹泻、抽搐、严重出血…",
          "boundary": "可能与中毒、梗阻或感染等多种原因有关",
          "source_fact_id": "S05"
        }
      ],
      "collapsed_by_default": true
    }
  ],
  "retrieved_knowledge_ids": ["KB-007"],

  // ④ 记忆门禁
  "session_id": "user-1",
  "recognized_pet_name": "小白",
  "device_context": null,
  "background_updates": {
    "pet_name_candidate": null,
    "pet_age_candidate": null,
    "pet_breed_candidate": null,
    "device_event_candidate": null,
    "reminder_candidate": null,
    "memory_candidate": null,
    "message_logged": true
  },

  // ⑤ 追问
  "clarification_questions": [
    "呕吐物的颜色？是否含血？",
    "今天是第几次呕吐？"
  ],
  "safety_notes": "若出现精神下降、张口呼吸或抽搐请立即就医。",

  // ⑥ 重试 trace
  "review": {
    "status": "approved",           // approved | rejected_conservative_fallback
    "attempt": 2,                   // 1..MAX_ATTEMPTS (3)
    "issues": [],
    "trace_id": "web-1788000097835-1",
    "generator": "rule",
    "levels": { "retrieve": "R1", "evidence": "E0", "assert": "A0" },
    "attempts": [
      {
        "attempt": 1,
        "retrieve": "R0", "evidence": "E0", "assert": "A0",
        "kb": ["KB-017"], "evidence_ids": [],
        "issues": ["low_confidence_retrieval"],
        "structural_fixes": [],
        "status": "rejected",
        "elapsed_ms": 12
      },
      {
        "attempt": 2,
        "retrieve": "R1", "evidence": "E0", "assert": "A0",
        "kb": ["KB-007", "KB-008", "KB-012"], "evidence_ids": ["S05", "S10"],
        "issues": [],
        "structural_fixes": [],
        "status": "approved",
        "elapsed_ms": 8
      }
    ]
  }
}
```

**响应 4xx / 5xx**

```json
{
  "error": {
    "code": "bad_request",
    "message": "missing sessionId",
    "details": {}
  }
}
```

### `POST /api/v1/reset`

清空某会话的全部内存与持久化（开发用）。

```jsonc
// 请求
{ "sessionId": "user-1" }
// 响应
{ "ok": true, "session_id": "user-1" }
```

### `GET /api/v1/attempts/<trace_id>`

按 trace_id 反查全部 attempts（debug 面板回放用）。

**响应 200**

```jsonc
{
  "trace_id": "web-1788000097835-1",
  "attempts": [
    { "session_id": "user-1", "attempt": 1, "retrieve_level": "R0", "evidence_level": "E0", "assert_level": "A0", "kb_hits": "[\"KB-017\"]", "evidence_ids": "[]", "issues": "[\"low_confidence_retrieval\"]", "status": "rejected", "elapsed_ms": 12, "created_at": "2026-08-29T18:00:00Z" },
    { "session_id": "user-1", "attempt": 2, "retrieve_level": "R1", "evidence_level": "E0", "assert_level": "A0", "kb_hits": "[\"KB-007\"]", "evidence_ids": "[\"S05\",\"S10\"]", "issues": "[]", "status": "approved", "elapsed_ms": 8,  "created_at": "2026-08-29T18:00:00Z" }
  ]
}
```

> 注：JSON 字符串字段（`kb_hits` / `evidence_ids` / `issues`）由前端 `JSON.parse` 后渲染。

## 数据 Schema

### Reminder

| 字段         | 类型   | 说明 |
| ------------ | ------ | ---- |
| `id`         | string | 服务端生成（`reminder-<ts>-<seq>`） |
| `type`       | string | 默认 `observation` |
| `title`      | string | 标题（≤24 字） |
| `instruction`| string | 内容 |
| `schedule`   | string | HH:MM |
| `timezone`   | string | IANA 名，默认 `Asia/Shanghai` |
| `status`     | enum   | `active` / `paused` / `completed` / `deleted` |

### Evidence

| 字段                 | 类型   | 说明 |
| -------------------- | ------ | ---- |
| `source_id`          | string | S01…S16 之一，必在 `VERIFIED_SOURCE_IDS` |
| `organization`       | string | 机构（如 VCA / AAHA / Cornell / ASPCA …） |
| `title`              | string | 文献标题（中文或英文） |
| `url`                | string | 原始 URL，必须可访问 |
| `checked_at`         | string | 核验日期 ISO 8601 |
| `supports`           | array  | 该证据支持的主题列表 |
| `excerpts`           | array  | 摘录，按 `chunk_id` 复用 |
| `collapsed_by_default` | bool | 默认折叠，点击展开 |

### Review attempt

| 字段                | 类型   | 说明 |
| ------------------- | ------ | ---- |
| `attempt`           | int    | 序号（1..MAX_ATTEMPTS） |
| `retrieve`          | enum   | `R0` / `R1` / `R2` |
| `evidence`          | enum   | `E0` / `E1` / `E2` |
| `assert`            | enum   | `A0` / `A1` / `A2` |
| `kb`                | array  | 本次命中的 KB id 列表 |
| `evidence_ids`      | array  | 实际绑定的 source_id 列表 |
| `issues`            | array  | 审核发现的问题；空 = PASS |
| `structural_fixes`  | array  | 结构性修复记录（不消耗重试） |
| `status`            | enum   | `approved` / `rejected` |
| `elapsed_ms`        | int    | 单 attempt 耗时 |

## 三层降级重试（核心）

```
attempt 1: R0 default top5 + E0 top1 + A0 judgment          →  rejected
attempt 2: R1 aliases expanded top8 + E0 top1 + A0           →  approved
                      ↑
        low_confidence_retrieval / missing_diagnosis_boundary / judgment_not_supported
```

失败映射具体见 `engine/retry.py` 的 `plan_next()`。终止条件：

- 任一 attempt 通过 → 立即返回
- 三次尝试均失败 → 进入 `conservative_fallback`（保留已检索出处 + 安全注意 + 就医升级提示，
  并显式声明「无法基于可靠证据分级」）
