# -*- coding: utf-8 -*-
"""三层降级重试（§4.6，核心）。

为什么不能照抄原状态机：规则引擎是确定性函数，同输入重跑「重新生成」输出一字不差，
第二轮必然再次失败。原设计的补救主线本是 `retrieve_again`（重检索），
这里把它拆成三个可独立变化的层，每层有强度档位 —— 每一次重试都产生真实 diff：

  检索档 R0 → R1 → R2   命中条目池与别名扩展层逐档放宽
  证据档 E0 → E1 → E2   证据子集逐档变宽（top1 → top3 → 全部 + 补充来源）
  断言档 A0 → A1 → A2   结论强度逐档减弱（judgment → observation → uncertain）

审核失败 → 动作映射见 `plan_next`。结构性失败（E0 缺升级 / 缺边界 / 禁忌词形）
由 review.apply_structural_fixes 就地修复，**不消耗重试次数**。
"""
from __future__ import annotations

import time

from . import config, knowledge, rules
from .generator import Draft, GenContext
from .review import (STRUCTURAL_ISSUES, apply_structural_fixes, conservative_fallback,
                     review_response)

# E2 档的兜底补充来源：实测其 facts 含「异常」，可支撑 E1 的 judgment_not_supported 检查
SUPPLEMENTARY_SOURCE_IDS = ("S09", "S03", "S12")


def next_level(levels: tuple[str, ...], current: str) -> str | None:
    try:
        idx = levels.index(current)
    except ValueError:
        return None
    return levels[idx + 1] if idx + 1 < len(levels) else None


def select_evidence_ids(retrieved: list[dict], level: str) -> list[str]:
    """按证据档选择来源子集。档位越高证据集越宽，保证每次升档 evidence_ids 真的变化。"""
    if level == "E0":
        pool = retrieved[:1]
    elif level == "E1":
        pool = retrieved[:3]
    else:
        pool = retrieved

    ids: list[str] = []
    for entry in pool:
        for sid in entry.get("source_ids") or []:
            if sid in knowledge.VERIFIED_SOURCE_IDS and sid not in ids:
                ids.append(sid)
    if level == "E2":
        for sid in SUPPLEMENTARY_SOURCE_IDS:
            if sid in knowledge.VERIFIED_SOURCE_IDS and sid not in ids:
                ids.append(sid)
    return ids[: config.EVIDENCE_MAX]


def plan_next(issues: list[str], levels: dict) -> dict | None:
    """审核失败 → 下一组档位。返回 None 表示无路可走，应走保守兜底。"""
    result = dict(levels)
    # 只命中兜底条目 = 本档没真正检索到知识，优先放宽检索档（这类问题换证据子集无意义）
    if "low_confidence_retrieval" in issues:
        nxt = next_level(config.RETRIEVE_LEVELS, result["retrieve"])
        if nxt:
            result["retrieve"] = nxt
            return result
        return None
    if "missing_evidence" in issues or "missing_evidence_excerpt" in issues:
        nxt = next_level(config.RETRIEVE_LEVELS, result["retrieve"])
        if nxt:
            result["retrieve"] = nxt
            return result
        nxt = next_level(config.EVIDENCE_LEVELS, result["evidence"])
        if nxt:
            result["evidence"] = nxt
            return result
        return None
    if "judgment_not_supported" in issues:
        nxt = next_level(config.EVIDENCE_LEVELS, result["evidence"])
        if nxt:
            result["evidence"] = nxt
            return result
        nxt = next_level(config.ASSERT_LEVELS, result["assert"])
        if nxt:
            result["assert"] = nxt
            return result
        return None
    return None


def _with_levels(ctx: GenContext, retrieved: list[dict], evidence_ids: list[str], assert_level: str) -> GenContext:
    clone = GenContext(**ctx.__dict__)
    clone.retrieved = retrieved
    clone.evidence_ids = list(evidence_ids)
    clone.assert_level = assert_level
    return clone


def run_pipeline(base_ctx: GenContext, focus: str, flags: list[str],
                 generator, trace_id: str = "") -> dict:
    """执行「首生成 + 最多 2 次降级重试 + 保守兜底」，返回 draft / evidence / review。"""
    levels = {"retrieve": "R0", "evidence": "E0", "assert": "A0"}
    attempts: list[dict] = []
    attempt = 0
    retrieved: list[dict] = []
    evidence_ids: list[str] = []
    draft: Draft | None = None

    while attempt < config.MAX_ATTEMPTS:
        attempt += 1
        started = time.perf_counter()

        retrieved = knowledge.retrieve(focus, levels["retrieve"], flags)
        candidate_ids = select_evidence_ids(retrieved, levels["evidence"])
        ctx = _with_levels(base_ctx, retrieved, candidate_ids, levels["assert"])
        # 风险等级随检索档位变化：R 放宽后命中条目可能改变分级（§4.3 决策表）
        ctx.urgency = rules.decide_urgency(flags, retrieved, base_ctx.context)
        draft = generator.generate(ctx)

        final_ids = draft.evidence_source_ids or candidate_ids
        evidence = knowledge.build_evidence(final_ids, retrieved)

        result = review_response({
            "answer": draft.answer,
            "urgency_level": draft.urgency_level,
            "evidence": evidence,
            "diagnosis_boundary": draft.diagnosis_boundary,
        })

        # 只命中兜底条目视为本档检索失败，触发检索档升档（§4.6 检索档）
        if retrieved and all(e["id"] == config.FALLBACK_ENTRY_ID for e in retrieved) \
                and "low_confidence_retrieval" not in result["issues"]:
            result["issues"].append("low_confidence_retrieval")
            result["passed"] = False

        structural: list[str] = []
        if not result["passed"]:
            structural_issues = [i for i in result["issues"] if i in STRUCTURAL_ISSUES]
            if structural_issues:
                structural = apply_structural_fixes(draft, structural_issues, flags)
                evidence = knowledge.build_evidence(draft.evidence_source_ids or final_ids, retrieved)
                result = review_response({
                    "answer": draft.answer,
                    "urgency_level": draft.urgency_level,
                    "evidence": evidence,
                    "diagnosis_boundary": draft.diagnosis_boundary,
                })

        attempts.append({
            "attempt": attempt,
            "retrieve": levels["retrieve"],
            "evidence": levels["evidence"],
            "assert": levels["assert"],
            "kb": [e["id"] for e in retrieved],
            "evidence_ids": list(final_ids),
            "issues": list(result["issues"]),
            "structural_fixes": structural,
            "status": "approved" if result["passed"] else "rejected",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        })

        if result["passed"]:
            return {
                "draft": draft,
                "evidence": evidence,
                "retrieved": retrieved,
                "review": {
                    "status": "approved",
                    "attempt": attempt,
                    "issues": [],
                    "trace_id": trace_id,
                    "generator": getattr(generator, "name", "rule"),
                    "levels": dict(levels),
                    "attempts": attempts,
                },
            }

        nxt = plan_next(result["issues"], levels)
        if nxt is None:
            break
        levels = nxt

    fallback_ids = draft.evidence_source_ids if draft else evidence_ids
    fallback = conservative_fallback(base_ctx, list(fallback_ids))
    evidence = knowledge.build_evidence(fallback_ids, retrieved)
    return {
        "draft": fallback,
        "evidence": evidence,
        "retrieved": retrieved,
        "review": {
            "status": "fallback",
            "attempt": attempt,
            "issues": attempts[-1]["issues"] if attempts else [],
            "trace_id": trace_id,
            "generator": getattr(generator, "name", "rule"),
            "levels": dict(levels),
            "attempts": attempts,
        },
    }
