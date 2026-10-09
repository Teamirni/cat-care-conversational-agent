# -*- coding: utf-8 -*-
"""可插拔生成器（§4.4）。

审核器与重试状态机只依赖 `Generator.generate(ctx) -> Draft` 契约，不关心背后是规则还是模型：
  - RuleGenerator  默认，离线，模板 + 槽位组装
  - LLMGenerator   可选，OpenAI 兼容端点；接上后同一套状态机原地升级为真正的「重新生成」闭环

切换：环境变量 CAT_AGENT_GENERATOR=rule|llm（默认 rule）。llm 缺配置或调用失败一律回落 rule。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict

from . import config, knowledge, rules, templates

# routers.ts L299-310 原文照抄，仅作为 LLM 模式的系统提示词
AGENT_SYSTEM_PROMPT = """你是“猫咪智能管家”的对话式 Agent。你的任务是理解猫主人自然口语，维护上下文，主动追问，而不是凭单句输入诊断。

你必须遵守：
1. 先理解用户诉求、症状、时间线、是否突然、程度、伴随表现、行为变化和可能暴露。
2. 如果信息不足，next_action 必须为 ask_clarification，并提出最少但关键的问题；不要用“信息不足”结束对话。
3. 如果命中呼吸困难、张口呼吸、少尿/无尿、倒地/意识改变、持续抽搐、疑似中毒或严重外伤，urgency_level 必须为 E0，next_action 必须为 recommend_vet_now。
4. 不做确定疾病诊断，不自行推荐人用药、处方药、催吐、强行灌食或灌水。
5. 普通有明确时间但无危险伴随表现的症状可先给 E2 的观察/预约检查建议；突然发生但缺少伴随表现时先追问；突然发生并伴随攻击性、疼痛、精神差、呕吐腹泻或皮肤损伤时至少 E1。
6. answer 要自然、像在连续聊天，不要输出机械表格；可以引用当前已检索来源的含义，但不得编造来源未支持的具体事实。
7. 宠物名称必须优先使用用户本轮明确说出的称呼，例如“小咪”“小白”“小黑”；档案中的另一个昵称只能作为背景，不能覆盖用户当前称呼。
8. 如果最新一条用户消息是“突然的”“刚才的”“是突然发生的”等省略式补充，必须把它绑定到上一条用户描述，更新突发性/时间线和风险分流，只回应变化部分，不要逐字复述上一轮结论。
9. 只输出符合 JSON Schema 的 JSON。"""

# routers.ts L82-103 responseSchema
RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "next_action": {"type": "string", "enum": ["ask_clarification", "answer_with_evidence", "recommend_vet_now"]},
        "urgency_level": {"type": "string", "enum": ["E0", "E1", "E2", "E3"]},
        "clarification_questions": {"type": "array", "items": {"type": "string"}},
        "intent_summary": {"type": "string"},
        "normalized_symptoms": {"type": "array", "items": {"type": "string"}},
        "evidence_source_ids": {
            "type": "array",
            "items": {"type": "string", "enum": ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08",
                                                "S09", "S10", "S11", "S12", "S13", "S14", "S15", "S16"]},
        },
        "user_goal": {"type": "string"},
        "timeline": {"type": "string"},
        "onset": {"type": "string", "enum": ["sudden", "gradual", "recurring", "ongoing", "unknown"]},
        "severity": {"type": "string"},
        "companion_symptoms": {"type": "array", "items": {"type": "string"}},
        "behavior_change": {"type": "array", "items": {"type": "string"}},
        "missing_info": {"type": "array", "items": {"type": "string"}},
        "safety_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "next_action", "urgency_level", "clarification_questions", "intent_summary",
                 "normalized_symptoms", "evidence_source_ids", "user_goal", "timeline", "onset", "severity",
                 "companion_symptoms", "behavior_change", "missing_info", "safety_notes"],
}

REQUIRED_FIELDS = tuple(RESPONSE_SCHEMA["required"])


@dataclass
class GenContext:
    """生成一次回答所需的全部输入。"""
    focus: str = ""
    latest_text: str = ""
    urgency: str = "E3"
    flags: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    retrieved: list[dict] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    assert_level: str = "A0"
    pet_name: str | None = None
    profile: dict = field(default_factory=dict)
    onset: str = "unknown"
    timeline: str = "unknown"
    severity: str = "unknown"
    user_goal: str = "describe_situation"
    normalized_symptoms: list[str] = field(default_factory=list)
    behavior_change: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    device_trend: str = ""
    device_missing: list[str] = field(default_factory=list)
    device_alerts: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


@dataclass
class Draft:
    answer: str = ""
    next_action: str = templates.NEXT_ACTION_CLARIFY
    urgency_level: str = "E3"
    clarification_questions: list[str] = field(default_factory=list)
    intent_summary: str = ""
    normalized_symptoms: list[str] = field(default_factory=list)
    evidence_source_ids: list[str] = field(default_factory=list)
    user_goal: str = "describe_situation"
    timeline: str = "unknown"
    onset: str = "unknown"
    severity: str = "unknown"
    companion_symptoms: list[str] = field(default_factory=list)
    behavior_change: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)
    can_do: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    diagnosis_boundary: str = templates.DIAGNOSIS_BOUNDARY_TOP
    assert_level: str = "A0"
    raw: dict | None = None

    def to_response(self) -> dict:
        data = asdict(self)
        data.pop("raw", None)
        return data


def build_summary(urgency: str, context: dict, flags: list[str]) -> str:
    """阶段5 server.py L121-129 的摘要映射与三种覆盖文案。"""
    if flags:
        return templates.URGENCY_SUMMARY["E0"]
    if context.get("sudden") and context.get("has_companion"):
        return templates.SUMMARY_SUDDEN_WITH_COMPANION
    if context.get("sudden"):
        return templates.SUMMARY_SUDDEN_ONLY
    if not context.get("time_known"):
        return templates.SUMMARY_NO_TIMELINE
    return templates.URGENCY_SUMMARY.get(urgency, templates.URGENCY_SUMMARY["E3"])


class RuleGenerator:
    """离线规则生成器：模板 + 槽位组装，输出确定性（同输入同输出）。"""

    name = "rule"

    def generate(self, ctx: GenContext) -> Draft:
        retrieved = ctx.retrieved
        top = retrieved[0] if retrieved else None
        topic = (top or {}).get("topic", "") or "当前描述的表现"
        summary = build_summary(ctx.urgency, ctx.context, ctx.flags)

        if ctx.flags:
            # routers.ts L515-521：E0 硬覆盖，文案原文照抄
            answer = templates.E0_COVER_ANSWER.format(flags="、".join(ctx.flags))
            questions = [templates.E0_COVER_QUESTION]
            evidence_ids = ctx.evidence_ids or list(templates.E0_DEFAULT_SOURCE_IDS)
            safety = list(templates.E0_SAFETY_NOTES)
        else:
            observed = "、".join(ctx.normalized_symptoms) if ctx.normalized_symptoms else ""
            if ctx.assert_level == "A2":
                answer = templates.ASSERT_A2_TEMPLATE.format(summary=summary)
            elif ctx.assert_level == "A1":
                answer = templates.ASSERT_A1_TEMPLATE.format(
                    observed=observed or "当前描述的表现", summary=summary)
            else:
                if observed:
                    answer = templates.ASSERT_A0_OBSERVED_TEMPLATE.format(
                        summary=summary, observed=observed,
                        action_hint=templates.ACTION_HINT_BY_LEVEL.get(ctx.urgency, ""),
                    )
                else:
                    answer = templates.ASSERT_A0_TEMPLATE.format(
                        summary=summary, topic=topic,
                        action_hint=templates.ACTION_HINT_BY_LEVEL.get(ctx.urgency, ""),
                    )
            questions = list(topic_questions_for(topic, ctx.context, top))
            evidence_ids = ctx.evidence_ids
            safety = list(templates.BASE_SAFETY_NOTES)

        can_do = list(dict.fromkeys(sum([e.get("can_do") or [] for e in retrieved], [])))[:5]
        must_not = list(dict.fromkeys(sum([e.get("must_not") or [] for e in retrieved], [])))[:6]

        return Draft(
            answer=answer,
            next_action=next_action_for(ctx.urgency, questions),
            urgency_level=ctx.urgency,
            clarification_questions=questions,
            intent_summary=f"用户描述了{topic}相关情况，当前按 {ctx.urgency} 处理。",
            normalized_symptoms=ctx.normalized_symptoms,
            evidence_source_ids=evidence_ids,
            user_goal=ctx.user_goal,
            timeline=ctx.timeline,
            onset=ctx.onset,
            severity=ctx.severity,
            companion_symptoms=list(ctx.context.get("companions") or []),
            behavior_change=ctx.behavior_change,
            missing_info=ctx.missing_info,
            safety_notes=safety,
            can_do=can_do,
            must_not=must_not,
            diagnosis_boundary=templates.DIAGNOSIS_BOUNDARY_TOP,
            assert_level=ctx.assert_level,
        )


def topic_questions_for(topic: str, context: dict, top: dict | None) -> list[str]:
    """阶段5 server.py L130：突发 / 无时间线 / 有伴随 → 情景追问；否则用条目自带问题。"""
    if context.get("sudden") or not context.get("time_known") or context.get("has_companion"):
        return templates.topic_questions(topic)
    return (top or {}).get("questions") or templates.topic_questions(topic)


def next_action_for(urgency: str, questions: list[str]) -> str:
    """routers.ts L87：E0 强制急诊；仍有关键追问则先追问；否则给带证据的回答。"""
    if urgency == "E0":
        return templates.NEXT_ACTION_BY_URGENCY["E0"]
    return templates.NEXT_ACTION_CLARIFY if questions else templates.NEXT_ACTION_ANSWER


class LLMGenerator:
    """OpenAI 兼容端点适配器（默认关闭）。失败一律回落 RuleGenerator，绝不把异常暴露给用户。"""

    name = "llm"

    def __init__(self, fallback: RuleGenerator):
        self._fallback = fallback

    def generate(self, ctx: GenContext) -> Draft:
        payload = {
            "model": config.LLM_MODEL,
            "messages": [
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": self._user_prompt(ctx)},
            ],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "cat_agent_turn", "strict": True, "schema": RESPONSE_SCHEMA}},
        }
        try:
            content = self._post(payload)
            parsed = json.loads(content)
            if not isinstance(parsed, dict) or any(f not in parsed for f in REQUIRED_FIELDS):
                return self._fallback.generate(ctx)
            draft = self._fallback.generate(ctx)
            for key in REQUIRED_FIELDS:
                setattr(draft, key, parsed[key])
            draft.raw = parsed
            draft.assert_level = ctx.assert_level
            return draft
        except Exception:
            return self._fallback.generate(ctx)

    @staticmethod
    def _user_prompt(ctx: GenContext) -> str:
        return (
            f"猫咪档案：{json.dumps(ctx.profile, ensure_ascii=False)}\n"
            f"本轮用户明确使用的宠物称呼：{ctx.pet_name or '未识别'}。\n\n"
            f"本轮需要重点处理的最新用户表达：{ctx.focus}\n"
            f"当前风险等级判定：{ctx.urgency}，断言强度档位：{ctx.assert_level}\n\n"
            f"已从审核知识库检索到的条目：{json.dumps(ctx.retrieved, ensure_ascii=False)}\n"
            f"可选 evidence_source_ids（只能从中选择）：{json.dumps(ctx.evidence_ids, ensure_ascii=False)}\n\n"
            "请返回结构化 JSON。只能从已检索条目或来源目录选择 evidence_source_ids；"
            "如果没有充分证据就继续追问，不要编造来源未支持的事实。"
        )

    @staticmethod
    def _post(payload: dict) -> str:
        request = urllib.request.Request(
            f"{config.LLM_BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {config.LLM_API_KEY}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=config.LLM_TIMEOUT_SEC) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]


_RULE = RuleGenerator()
_LLM = LLMGenerator(_RULE)


def get_generator(name: str | None = None):
    """取得生成器。llm 缺配置时静默回落 rule（计划 §4.4）。"""
    return _LLM if (name or config.generator_mode()) == "llm" and config.LLM_BASE_URL and config.LLM_MODEL else _RULE


__all__ = ["GenContext", "Draft", "RuleGenerator", "LLMGenerator", "get_generator",
           "build_summary", "next_action_for", "AGENT_SYSTEM_PROMPT", "RESPONSE_SCHEMA", "REQUIRED_FIELDS"]
