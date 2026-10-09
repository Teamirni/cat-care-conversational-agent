# -*- coding: utf-8 -*-
"""路径、端口与开关。全部可通过环境变量覆盖，默认配置保证离线可跑。

对应计划 §2 / §3。所有医学内容均来自 data/ 下的权威文件，本文件不含任何医学常量。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"

KB_PATH = DATA_DIR / "knowledge_base_v1.jsonl"
FALLBACK_KB_PATH = DATA_DIR / "fallback_entries.jsonl"
SOURCE_FACTS_PATH = DATA_DIR / "source_facts.json"
SOURCE_REGISTRY_PATH = DATA_DIR / "source-registry.md"

# 权威文件的 sha256，启动时校验，防止知识库被误改（§3）
EXPECTED_KB_SHA256 = "d3fd851a1ce20ceec775243cb58416d5865cbecf4ceb1283ab319c810982cd8b"
EXPECTED_SOURCE_FACTS_SHA256 = "3c69c8524930e9720d30a0b45cc43966c1bded3f8ee4f8eb1bc03daecfa680d7"

# 持久化
DB_PATH = Path(os.environ.get("CAT_AGENT_DB", str(BASE_DIR / "data" / "cat_agent.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# HTTP
HOST = os.environ.get("CAT_AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CAT_AGENT_PORT", "8787"))
PORT_FALLBACK_TRIES = 10  # 绑定失败自动 +1，最多试 10 次（§5）

# 生成器（§4.4）：rule = 离线规则引擎（默认）；llm = OpenAI 兼容端点（可选）
GENERATOR = os.environ.get("CAT_AGENT_GENERATOR", "rule").strip().lower()
LLM_BASE_URL = os.environ.get("CAT_AGENT_LLM_BASE_URL", "").rstrip("/")
LLM_MODEL = os.environ.get("CAT_AGENT_LLM_MODEL", "")
LLM_API_KEY = os.environ.get("CAT_AGENT_LLM_API_KEY", "")
LLM_TIMEOUT_SEC = float(os.environ.get("CAT_AGENT_LLM_TIMEOUT", "20"))

# 审核与重试（§4.6）：首生成 1 次 + 重试 2 次 = 上限 3
MAX_REVIEW_ATTEMPTS = 2
MAX_ATTEMPTS = MAX_REVIEW_ATTEMPTS + 1

# 检索档位参数（§4.6 检索档 R0/R1/R2）
RETRIEVE_TOP_K = {"R0": 5, "R1": 8, "R2": 8}
RETRIEVE_LEVELS = ("R0", "R1", "R2")
EVIDENCE_LEVELS = ("E0", "E1", "E2")
ASSERT_LEVELS = ("A0", "A1", "A2")

# 行为开关
SESSION_MEMORY_LIMIT = 30          # 进程内会话记忆条数（routers.ts L450）
EVIDENCE_MAX = 8                    # 证据卡上限（阶段5 server.py L134）
ASK_DEVICE_MISSING = True           # 忠实 routers.ts L531-543：缺失观测追问
REMINDER_CONTENT_CLEANUP = False    # 忠实保留原文清洗残留（已知偏差 5）
DEFAULT_TIMEZONE = "Asia/Shanghai"

# 兜底条目：检索为空时注入（§4.2）
FALLBACK_ENTRY_ID = "KB-017"


def generator_mode() -> str:
    """返回实际生效的生成器。llm 缺配置时回落 rule（§4.4）。"""
    if GENERATOR == "llm" and LLM_BASE_URL and LLM_MODEL:
        return "llm"
    return "rule"
