# -*- coding: utf-8 -*-
"""二次审核门禁（§4.5）与保守兜底。

来源：routers.ts L142-155（MAX_REVIEW_ATTEMPTS / reviewResponse）
扩展：否定语境遮蔽层 —— 原实现的 prohibited_action 是无语境子串匹配，
      会把知识库 must_not 原文（如「不自行给药」）误判为违规（已知偏差 3）。
"""
from __future__ import annotations

import re

from . import knowledge, templates
from .generator import Draft, GenContext

PROHIBITED_PATTERNS = ("自行催吐", "自行给药", "人用药", "强行灌食")
NEGATION_HINTS = ("不要", "别", "切勿", "禁止", "避免", "不应", "不能", "不可", "不得", "不")

# 结构性失败：可确定性修复，不消耗重试次数（§4.6 失败映射表）
STRUCTURAL_ISSUES = ("e0_without_escalation", "missing_diagnosis_boundary", "prohibited_action")

# 分句边界：句末标点 + 逗号/冒号。顿号「、」**不**作为边界——
# 「不要自行给药、催吐或强行灌食灌水」是否定辖域内的并列宾语，须整段被覆盖。
_CLAUSE_BREAK = ("。", "；", "！", "？", "\n", "，", ",", "：", ":", ";")
_CLAUSE_MAX_BACK = 40


def _clause_prefix(answer: str, idx: int) -> str:
    """取 idx 之前、最近一个分句边界之后的片段（最多回看 _CLAUSE_MAX_BACK 字）。

    旧实现只回看 4 字符，跨顿号并列结构时否定词落窗外，导致
    「不要自行给药、催吐或强行灌食灌水」中的「强行灌食」被误判为违规。
    """
    start = max(0, idx - _CLAUSE_MAX_BACK)
    span = answer[start:idx]
    cut = 0
    for i, ch in enumerate(span):
        if ch in _CLAUSE_BREAK:
            cut = i + 1
    return span[cut:]


def prohibited_hits(answer: str) -> list[str]:
    """返回未被否定辖域覆盖的违规词形。

    否定辖域口径：禁忌词所在分句内、其前方出现否定词 → 视为被辖域覆盖。
    「不要自行给药、催吐或强行灌食灌水」全部遮蔽；
    「不要自行给药，建议催吐」中的「催吐」在逗号后新分句 → 判为真违规。
    """
    hits: list[str] = []
    for pattern in PROHIBITED_PATTERNS:
        start = 0
        while True:
            idx = answer.find(pattern, start)
            if idx < 0:
                break
            prefix = _clause_prefix(answer, idx)
            if not any(hint in prefix for hint in NEGATION_HINTS):
                hits.append(pattern)
                break
            start = idx + len(pattern)
    return hits


def review_response(response: dict) -> dict:
    """routers.ts L144-155 逐条移植 + 否定语境遮蔽。"""
    issues: list[str] = []
    answer = response.get("answer", "") or ""
    urgency = response.get("urgency_level", "")
    evidence = response.get("evidence") or []

    if not response.get("diagnosis_boundary"):
        issues.append("missing_diagnosis_boundary")
    if len(evidence) == 0:
        issues.append("missing_evidence")
    if not knowledge.evidence_has_excerpt(evidence):
        issues.append("missing_evidence_excerpt")

    evidence_text = knowledge.evidence_text(evidence)

    if urgency == "E0" and not re.search(r"急诊|立即联系|动物医院", answer):
        issues.append("e0_without_escalation")
    if urgency == "E0" and not re.search(r"升级|急诊|无尿|呼吸|抽搐|中毒|出血", evidence_text):
        issues.append("judgment_not_supported")
    if urgency == "E1" and re.search(r"尽快联系兽医|尽快就医|联系兽医", answer) \
            and not re.search(r"尽快|升级|拒食|疼痛|异常", evidence_text):
        issues.append("judgment_not_supported")
    if prohibited_hits(answer):
        issues.append("prohibited_action")

    return {"passed": not issues, "issues": issues}


# ---------------------------------------------------------------- 结构性硬修复
def apply_structural_fixes(draft: Draft, issues: list[str], flags: list[str]) -> list[str]:
    """就地修复可确定性修复的问题，返回实际执行的修复名。不消耗重试次数。"""
    applied: list[str] = []

    if "e0_without_escalation" in issues:
        draft.answer = templates.E0_COVER_ANSWER.format(flags="、".join(flags) or "急症表现")
        draft.next_action = templates.NEXT_ACTION_BY_URGENCY["E0"]
        if templates.E0_COVER_QUESTION not in draft.clarification_questions:
            draft.clarification_questions = [templates.E0_COVER_QUESTION, *draft.clarification_questions]
        for note in templates.E0_SAFETY_NOTES:
            if note not in draft.safety_notes:
                draft.safety_notes.append(note)
        applied.append("e0_without_escalation")

    if "missing_diagnosis_boundary" in issues:
        draft.diagnosis_boundary = templates.DIAGNOSIS_BOUNDARY_TOP
        applied.append("missing_diagnosis_boundary")

    if "prohibited_action" in issues:
        for pattern in prohibited_hits(draft.answer):
            draft.answer = draft.answer.replace(pattern, "")
            if pattern not in draft.must_not:
                draft.must_not.insert(0, f"不要{pattern}")
        draft.answer = re.sub(r"[，、]{2,}", "，", draft.answer).strip()
        applied.append("prohibited_action")

    return applied


# ---------------------------------------------------------------- 保守兜底
def conservative_fallback(ctx: GenContext, evidence_ids: list[str]) -> Draft:
    """§4.6 终止条件：非空回答 + 追问 + 安全注意 + 就医升级提示，且保留已检索到的出处。"""
    return Draft(
        answer=templates.CONSERVATIVE_FALLBACK_ANSWER,
        next_action=templates.NEXT_ACTION_CLARIFY,
        urgency_level=ctx.urgency if ctx.urgency == "E0" else "E3",
        clarification_questions=list(templates.CONSERVATIVE_FALLBACK_QUESTIONS),
        intent_summary="本轮证据不足以支撑分级，已按保守路径返回追问与安全注意。",
        normalized_symptoms=ctx.normalized_symptoms,
        evidence_source_ids=evidence_ids,
        user_goal=ctx.user_goal,
        timeline=ctx.timeline,
        onset=ctx.onset,
        severity=ctx.severity,
        companion_symptoms=list(ctx.context.get("companions") or []),
        behavior_change=ctx.behavior_change,
        missing_info=list(dict.fromkeys([*ctx.missing_info, "症状名称", "开始时间", "是否突然", "伴随表现"])),
        safety_notes=list(templates.BASE_SAFETY_NOTES),
        can_do=list(dict.fromkeys(sum([e.get("can_do") or [] for e in ctx.retrieved], [])))[:5],
        must_not=list(dict.fromkeys(sum([e.get("must_not") or [] for e in ctx.retrieved], [])))[:6],
        diagnosis_boundary=templates.DIAGNOSIS_BOUNDARY_TOP,
        assert_level="A2",
    )
