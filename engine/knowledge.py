# -*- coding: utf-8 -*-
"""知识库加载、口语归一化、检索与证据构建。

行为规格来源：`Agent独立交付包_猫咪健康对话Agent/source/server/routers.ts`
  - retrieveKnowledge  L31-39
  - buildEvidence      L72-80
  - evidenceCatalog    L41-58（本项目改用 source_facts.json 作为唯一来源真源）
  - detectE0 红旗词表  L105-108

医学内容一律来自 data/ 下的权威文件，本文件不新编任何症状、动作、来源或 URL。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import config

SPLIT = re.compile(r"[、，,或/]+")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


class KnowledgeBaseError(RuntimeError):
    pass


# ---------------------------------------------------------------- 加载
_AUTHORITATIVE = _load_jsonl(config.KB_PATH)
_FALLBACK = _load_jsonl(config.FALLBACK_KB_PATH) if config.FALLBACK_KB_PATH.exists() else []

# 兜底条目补齐 diagnosis_boundary（文本取自 routers.ts L523 顶层常量，非新编）
_TOP_LEVEL_BOUNDARY = "以上内容用于风险分流、养护沟通和下一步行动建议，不能替代执业兽医诊断。"
for _e in _FALLBACK:
    _e.setdefault("diagnosis_boundary", _TOP_LEVEL_BOUNDARY)
    _e.setdefault("source_version", "2026-08-22")
    _e.setdefault("status", "reviewed")

AUTHORITATIVE_ENTRIES: list[dict] = _AUTHORITATIVE
FALLBACK_ENTRIES: list[dict] = _FALLBACK
KB_ALL: list[dict] = _AUTHORITATIVE + _FALLBACK

_BY_ID = {e["id"]: e for e in KB_ALL}

SOURCE_FACTS: list[dict] = json.loads(config.SOURCE_FACTS_PATH.read_text(encoding="utf-8"))
VERIFIED_SOURCE_IDS: set[str] = {f["source_id"] for f in SOURCE_FACTS}
FACT_BY_ID: dict[str, dict] = {f["source_id"]: f for f in SOURCE_FACTS}


def get_entry(entry_id: str) -> dict | None:
    return _BY_ID.get(entry_id)


# ---------------------------------------------------------------- 词表
def _raw_terms(entry: dict, fields=("topic", "symptoms", "can_do")) -> list[str]:
    """切分后的术语列表，字段口径与 routers.ts L35 一致（topic+symptoms+can_do，不含 must_not）。"""
    terms: list[str] = []
    for field in fields:
        value = entry.get(field)
        items = value if isinstance(value, list) else ([value] if value else [])
        for raw in items:
            for piece in SPLIT.split(str(raw)):
                piece = piece.strip()
                if len(piece) > 1:
                    terms.append(piece)
    return terms


def build_vocab(entries=None) -> dict[str, set[str]]:
    vocab: dict[str, set[str]] = {}
    for entry in entries if entries is not None else KB_ALL:
        for term in _raw_terms(entry):
            vocab.setdefault(term, set()).add(entry["id"])
    return vocab


VOCAB = build_vocab(AUTHORITATIVE_ENTRIES)


# ---------------------------------------------------------------- 别名（三层）
# 第 1 层：routers.ts L33 原样照抄
ROUTERS_ALIASES_VERBATIM: dict[str, str] = {
    "只尿出几滴": "少量尿滴",
    "尿不出来": "无尿",
    "进猫砂盆": "进出猫砂盆",
    "不吃饭": "食欲不振",
    "不怎么吃": "食欲下降",
    "不太愿意理我": "行为或意识改变",
    "不愿意理我": "行为或意识改变",
    "不理人": "行为或意识改变",
    "心情不好": "精神下降",
    "精神不好": "精神下降",
    "情绪低落": "精神下降",
}
# 这 4 个目标词在知识库中不存在，属原始实现的死别名（已实测）
ROUTERS_DEAD_ALIASES = ("食欲不振", "进出猫砂盆", "精神下降", "行为或意识改变")

# 第 2 层：死别名重定向到库内真实术语
CANONICAL_FIX: dict[str, str] = {
    "食欲不振": "食欲下降",
    "进出猫砂盆": "反复进出猫砂盆",
    "精神下降": "嗜睡",
    "行为或意识改变": "意识改变",
}

# 第 3 层：口语归一化扩展（目标词 100% 取自知识库现有术语，启动自检强校验）
EXTRA_ALIASES: dict[str, str] = {
    # 排尿
    "尿几滴": "少量尿滴",
    "只尿一点": "少量尿滴",
    "尿一点点": "少量尿滴",
    "尿不出": "无尿",
    "没有尿": "无尿",
    "没尿": "无尿",
    "老跑厕所": "反复进出猫砂盆",
    "总去猫砂盆": "反复进出猫砂盆",
    "一直去猫砂盆": "反复进出猫砂盆",
    "一直进猫砂盆": "反复进出猫砂盆",
    "一直蹲厕所": "反复进出猫砂盆",
    "蹲猫砂盆": "反复进出猫砂盆",
    "频繁上厕所": "反复进出猫砂盆",
    "尿血": "血尿",
    "尿里有血": "血尿",
    "尿的时候叫": "排尿痛叫",
    "尿痛": "排尿痛叫",
    "舔生殖器": "频繁舔生殖区",
    "舔屁股": "频繁舔生殖区",
    # 呼吸
    "张嘴呼吸": "张口呼吸",
    "张着嘴呼吸": "张口呼吸",
    "张嘴喘": "张口呼吸",
    "伸长脖子": "伸长头颈",
    "伸着脖子": "伸长头颈",
    # 消化
    "吐了": "反复呕吐",
    "吐得很厉害": "反复呕吐",
    "一直吐": "反复呕吐",
    "拉稀": "腹泻",
    "拉肚子": "腹泻",
    "稀便": "腹泻",
    "拉血": "便血",
    "大便带血": "便血",
    "吐血": "呕血",
    # 精神与状态
    "没精神": "嗜睡",
    "精神差": "嗜睡",
    "精神沉郁": "嗜睡",
    "蔫了": "嗜睡",
    "没力气": "虚弱",
    "没劲": "虚弱",
    "浑身无力": "虚弱",
    "叫不醒": "持续反应差",
    "没什么反应": "持续反应差",
    # 食欲
    "吃不下去": "食欲差",
    "吃不下": "食欲差",
    "没胃口": "食欲下降",
    "吃得少": "食欲下降",
    "食欲不好": "食欲下降",
    "不爱吃": "食欲下降",
    "不太爱吃": "食欲下降",
    "一口不吃": "完全拒食",
    "完全不吃": "完全拒食",
    "什么都不吃": "完全拒食",
    # 神经与眼
    "抽筋": "抽搐",
    "痉挛": "抽搐",
    "抽动": "抽搐",
    "晕倒": "倒地",
    "昏倒": "倒地",
    "倒下了": "倒地",
    "晕过去了": "失去意识",
    "叫它没反应": "持续反应差",
    "站不稳": "步态不稳",
    "走路不稳": "步态不稳",
    "走路摇晃": "步态不稳",
    "歪头": "头歪",
    "头偏向": "头歪",
    "眼屎": "异常眼分泌物",
    "眼睛有分泌物": "异常眼分泌物",
    "眼屎多": "异常眼分泌物",
    "眯着眼睛": "眯眼",
    "突然看不见": "突然视力下降",
    "看不见了": "突然视力下降",
    # 外伤与出血
    "被车撞": "车祸",
    "撞到车": "车祸",
    "出车祸": "车祸",
    "流血不止": "大量持续出血",
    "一直流血": "大量持续出血",
    "大量流血": "大量持续出血",
    "摔下来": "坠落",
    "从高处摔下": "坠落",
    "掉下楼": "坠落",
    "烫伤": "烧伤",
    # 中毒
    "误食": "疑似中毒",
    "偷吃了": "疑似中毒",
    # 皮肤被毛
    "掉毛": "脱毛",
    "毛发掉落": "脱毛",
    "秃斑": "脱毛",
    "秃了": "脱毛",
    "一直抓": "持续瘙痒",
    "一直挠": "持续瘙痒",
    "抓挠": "持续瘙痒",
    "红肿": "红斑",
    # 排便
    "拉不出": "排便困难",
    "拉不出来": "排便困难",
    "不拉屎": "排便困难",
    "大便干": "干硬小粪便",
    # 代谢与体重
    "喝水变多": "饮水增加",
    "喝水多": "饮水增加",
    "尿多": "排尿增加",
    "尿量变多": "排尿增加",
    "排尿变多": "排尿增加",
    "瘦了": "体重下降",
    "变瘦": "体重下降",
    # 口腔
    "流口水": "流涎",
    "嘴巴臭": "口臭",
}


def _apply(text: str, mapping: dict[str, str]) -> str:
    for key in sorted(mapping, key=len, reverse=True):
        if key in text:
            text = text.replace(key, mapping[key])
    return text


def normalize_query(text: str, level: str = "R0") -> str:
    """按检索档位归一化查询文本。R0 只开原实现别名+修正层，R1/R2 追加口语扩展层。"""
    out = (text or "").lower()
    out = _apply(out, ROUTERS_ALIASES_VERBATIM)
    out = _apply(out, CANONICAL_FIX)
    if level in ("R1", "R2"):
        out = _apply(out, EXTRA_ALIASES)
    return out


# ---------------------------------------------------------------- 红旗强制召回
# flag -> {entries: [...], sources: [...]}，sources 必须落在 entries 的 source_ids 并集内（启动自检）
FLAG_EVIDENCE_HINTS: dict[str, dict] = {
    "张口呼吸": {"entries": ["KB-001"], "sources": ["S05", "S06", "S10", "S13"]},
    "呼吸困难": {"entries": ["KB-001"], "sources": ["S05", "S06", "S10", "S13"]},
    "呼吸费力": {"entries": ["KB-001"], "sources": ["S05", "S06", "S10", "S13"]},
    "无尿": {"entries": ["KB-002"], "sources": ["S01", "S05", "S10"]},
    "尿不出来": {"entries": ["KB-002"], "sources": ["S01", "S05", "S10"]},
    "只尿出几滴": {"entries": ["KB-002"], "sources": ["S01", "S05", "S10"]},
    "倒地": {"entries": ["KB-004"], "sources": ["S04", "S05", "S12"]},
    "失去意识": {"entries": ["KB-004"], "sources": ["S04", "S05", "S12"]},
    "昏迷": {"entries": ["KB-004"], "sources": ["S04", "S05", "S12"]},
    "持续抽搐": {"entries": ["KB-004"], "sources": ["S04", "S05", "S12"]},
    "中毒": {"entries": ["KB-003"], "sources": ["S02", "S03", "S05"]},
    "吃了百合": {"entries": ["KB-003"], "sources": ["S02", "S03", "S05"]},
    "误食百合": {"entries": ["KB-003"], "sources": ["S02", "S03", "S05"]},
    "大量出血": {"entries": ["KB-005"], "sources": ["S03", "S04", "S05"]},
    "车祸": {"entries": ["KB-005"], "sources": ["S03", "S04", "S05"]},
    "严重外伤": {"entries": ["KB-005"], "sources": ["S03", "S04", "S05"]},
}

# 原始资料自带的引用偏差，登记不改（已知偏差 1）
KNOWN_CITATION_DEVIATIONS = {
    "KB-011": "条目引用 S07，而权威来源表中 S07 = VCA《Feline Upper Respiratory Infection》，"
              "与便秘主题不符。此为原始资料自带偏差，不改 jsonl，仅登记。",
}


# ---------------------------------------------------------------- 检索
def retrieve(query: str, level: str = "R0", flags: list[str] | None = None) -> list[dict]:
    """按档位检索。R0/R1 只查权威 14 条；R2 追加兜底条目。

    红旗强制召回对所有档位生效：命中急症红旗时必须立刻拿到对应知识条目，
    否则证据卡会与主题不符（SKILL.md §2：证据不支持红旗升级时先修正检索，不能直接交付）。
    """
    expanded = normalize_query(query, level)
    pool = KB_ALL if level == "R2" else AUTHORITATIVE_ENTRIES
    top_k = config.RETRIEVE_TOP_K.get(level, 5)

    scored: list[tuple[int, dict]] = []
    for entry in pool:
        score = sum(1 for term in _raw_terms(entry) if term.lower() in expanded)
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda item: -item[0])  # 稳定排序，保持知识库原有顺序
    hits = [entry for _, entry in scored[:top_k]]

    if flags:
        for flag in flags:
            for entry_id in FLAG_EVIDENCE_HINTS.get(flag, {}).get("entries", []):
                forced = get_entry(entry_id)
                if forced is not None and all(h["id"] != entry_id for h in hits):
                    hits.append(forced)

    if not hits:
        fallback = get_entry(config.FALLBACK_ENTRY_ID)
        if fallback is not None:
            hits = [fallback]
    return hits


def grounded_source_ids(retrieved: list[dict]) -> list[str]:
    """已检索条目所绑定且已核验的来源 id（routers.ts L458）。"""
    ids: list[str] = []
    for entry in retrieved:
        for sid in entry.get("source_ids") or []:
            if sid in VERIFIED_SOURCE_IDS and sid not in ids:
                ids.append(sid)
    return ids


# ---------------------------------------------------------------- 证据
def build_evidence(source_ids: list[str], retrieved: list[dict]) -> list[dict]:
    """构建证据卡。以 source_facts.json 为唯一来源元数据真源（routers.ts L72-80）。"""
    evidence: list[dict] = []
    for sid in source_ids:
        if sid not in VERIFIED_SOURCE_IDS:
            continue
        fact = FACT_BY_ID.get(sid)
        if not fact:
            continue
        matches = [e for e in retrieved if sid in (e.get("source_ids") or [])]
        evidence.append({
            "source_id": sid,
            "organization": fact["organization"],
            "title": fact["title"],
            "url": fact["url"],
            "collapsed_by_default": True,
            "checked_at": fact["checked_at"],
            "supports": [e.get("topic", "") for e in matches][:3],
            "excerpts": [
                {
                    "knowledge_id": e["id"],
                    "chunk_id": f"{sid}:{e['id']}:reviewed-fact",
                    "excerpt": " ".join(fact.get("facts") or []),
                    "boundary": e.get("diagnosis_boundary") or "需要结合兽医检查，不能据此确诊",
                    "source_fact_id": sid,
                }
                for e in matches
            ][:3],
        })
    return evidence


def evidence_text(evidence: list[dict]) -> str:
    """把证据摘录与边界拼成文本，供审核器判断是否支持结论（routers.ts L149）。"""
    parts: list[str] = []
    for item in evidence:
        for excerpt in item.get("excerpts") or []:
            parts.append(f"{excerpt.get('excerpt', '')} {excerpt.get('boundary', '')}")
    return " ".join(parts)


def evidence_has_excerpt(evidence: list[dict]) -> bool:
    """routers.ts L148：evidence.some(条目缺 excerpts)。空列表返回 True，避免与 missing_evidence 重复计数。"""
    return all(isinstance(item.get("excerpts"), list) and item["excerpts"] for item in evidence)


# ---------------------------------------------------------------- 启动自检
def self_check(verbose: bool = False) -> list[str]:
    """3 项启动自检。返回警告列表；致命问题直接抛 KnowledgeBaseError。"""
    warnings: list[str] = []

    # 1. 权威文件 sha256
    kb_hash = _sha256(config.KB_PATH)
    if kb_hash != config.EXPECTED_KB_SHA256:
        raise KnowledgeBaseError(
            f"knowledge_base_v1.jsonl 校验失败：期望 {config.EXPECTED_KB_SHA256[:12]}…，实际 {kb_hash[:12]}…"
        )
    sf_hash = _sha256(config.SOURCE_FACTS_PATH)
    if sf_hash != config.EXPECTED_SOURCE_FACTS_SHA256:
        raise KnowledgeBaseError(
            f"source_facts.json 校验失败：期望 {config.EXPECTED_SOURCE_FACTS_SHA256[:12]}…，实际 {sf_hash[:12]}…"
        )

    # 2. 条目完整性
    if len(KB_ALL) != 17:
        raise KnowledgeBaseError(f"知识条目数应为 17（14 权威 + 3 兜底），实际 {len(KB_ALL)}")
    for entry in KB_ALL:
        if not entry.get("diagnosis_boundary"):
            raise KnowledgeBaseError(f"条目 {entry.get('id')} 缺少 diagnosis_boundary")

    # 3a. 死别名提醒（不阻断）
    for dead in ROUTERS_DEAD_ALIASES:
        if dead not in VOCAB:
            warnings.append(f"routers.ts 死别名（已由修正层处理）：{dead}")

    # 3b. 修正层与扩展层目标词必须真实存在于知识库，否则映射无效
    for name, mapping in (("CANONICAL_FIX", CANONICAL_FIX), ("EXTRA_ALIASES", EXTRA_ALIASES)):
        for src, dst in mapping.items():
            if dst not in VOCAB:
                raise KnowledgeBaseError(f"{name} 目标词不在知识库词表中：{src} -> {dst}")

    # 3c. 红旗强制召回的来源必须落在对应条目的 source_ids 并集内
    for flag, hint in FLAG_EVIDENCE_HINTS.items():
        allowed: set[str] = set()
        for entry_id in hint["entries"]:
            entry = get_entry(entry_id)
            if entry is None:
                raise KnowledgeBaseError(f"FLAG_EVIDENCE_HINTS 指向不存在的条目：{flag} -> {entry_id}")
            allowed |= set(entry.get("source_ids") or [])
        for sid in hint["sources"]:
            if sid not in allowed:
                raise KnowledgeBaseError(f"红旗强制召回来源越界：{flag} -> {sid}")
            if sid not in VERIFIED_SOURCE_IDS:
                raise KnowledgeBaseError(f"红旗强制召回来源未核验：{flag} -> {sid}")

    # 3d. 条目引用的来源必须全部已核验
    for entry in KB_ALL:
        dangling = [sid for sid in (entry.get("source_ids") or []) if sid not in VERIFIED_SOURCE_IDS]
        if dangling:
            raise KnowledgeBaseError(f"条目 {entry['id']} 引用了未核验来源：{dangling}")

    if verbose:
        print(f"[knowledge] 权威条目 {len(AUTHORITATIVE_ENTRIES)} 条，兜底 {len(FALLBACK_ENTRIES)} 条，"
              f"已核验来源 {len(VERIFIED_SOURCE_IDS)} 条，词表 {len(VOCAB)} 词")
        for warn in warnings:
            print(f"[knowledge] WARN {warn}")
    return warnings


if __name__ == "__main__":
    self_check(verbose=True)
