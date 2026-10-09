# -*- coding: utf-8 -*-
"""`sync_to_skill.ps1` 的辅助脚本：把「本地 Demo 与知识库位置」段追加到 SKILL.md。

直接用 Python 写 UTF-8 文件，绕开 Windows PowerShell 5.1 here-string 的 ANSI 编码坑。
"""
from __future__ import annotations
import sys
import os
import pathlib

SKILL_MD = pathlib.Path(sys.argv[1]).resolve()

MARKER = "## 本地 Demo 与知识库位置"

APPEND = """

## 本地 Demo 与知识库位置

本 Skill 之外，本项目同时维护一份**可独立运行的 Python 复原 Demo**：

- 位置：`D:\\工作文件\\猫咪健康Agent_复原Demo_2026-08-24\\`
- 入口：`python server.py` → `http://127.0.0.1:8787`
- 知识库：`data/knowledge_base_v1.jsonl`（14 条权威 sha256 锁定）+ `data/fallback_entries.jsonl`（3 条兜底）
- 来源表：`data/source_facts.json`（16 条 verified sources）
- 验收：`python tests/smoke.py` → `PASS 86/86`

详见 `README.md` 与 `docs/验收报告.md`。

回到本 Skill 的工作流：

- 使用 Skill 时若 demo 已运行，**推荐先调用本地 demo** 测试业务可达性，再决定是否启用 LLMGenerator。
- LLMGenerator 走 OpenAI 兼容端点（`CAT_AGENT_LLM_BASE_URL` / `MODEL` / `API_KEY`），
  缺配置时自动回落 `RuleGenerator`。
- 行为验收标准（8 条原则、5 步流程、8 项交付前检查）未改动，仍以本文件上述章节为准。
"""

if not SKILL_MD.exists():
    print(f"SKILL.md missing: {SKILL_MD}", file=sys.stderr)
    sys.exit(1)

text = SKILL_MD.read_text(encoding="utf-8")
anchor = "8. E0 场景明确要求及时就医。"
idx_end = text.rfind(anchor)
if idx_end < 0:
    print(f"anchor not found: {anchor!r}", file=sys.stderr)
    sys.exit(1)

body = text[: idx_end + len(anchor)].rstrip() + "\n"
new_content = body + APPEND

# 幂等保证：若已含 marker，先剥离旧段
if MARKER in new_content:
    # 这里直接保留新写内容（因为我们基于 anchor 重建，已不会有旧段残留）
    pass

SKILL_MD.write_text(new_content, encoding="utf-8")
print(f"OK, size = {SKILL_MD.stat().st_size}, anchor+marker preserved")
