# -*- coding: utf-8 -*-
"""编排层（§4.7 / §4.8）：多轮焦点、分级、门禁、生成 + 重试、落库。

行为对齐 routers.ts L439-637（agent.chat）。差异只在「生成」这一步：
原实现调用 invokeLLM，这里交给可插拔生成器 + 三层降级重试（engine/retry.py）。
"""
from __future__ import annotations

import re
import time

from . import config, knowledge, rules, store, templates
from .generator import GenContext, build_summary, get_generator
from .retry import run_pipeline

# 进程内会话记忆（routers.ts L175 / L450）：上限 30 条，进程重启后由 messages 表恢复
_SESSION_MEMORY: dict[str, list[dict]] = {}

_IDENTITY_FIELDS = ("name", "age", "breed", "sex", "neutered")
_STABLE_FIELDS = ("diet", "medicalHistory", "allergies", "dailyHabits")
_DEVICE_ALERT_PATTERN = r"下降|增加|异常|减少|无尿|尿量为0|只尿出几滴|少尿"


def _history(session_id: str) -> list[dict]:
    history = _SESSION_MEMORY.get(session_id)
    if history is None:
        rows = store.list_messages(session_id)
        history = [{"role": row["role"], "content": row["content"]} for row in rows]
        _SESSION_MEMORY[session_id] = history
    return history


def _trace_id() -> str:
    return f"web-{int(time.time() * 1000)}-{next(_trace_seq)}"


_trace_seq = iter(range(1, 10 ** 9))


# ---------------------------------------------------------------- 后台状态抽取
def _background_updates(session_id: str, latest_text: str, nickname: str | None,
                        device_trend: str, profile: dict) -> dict:
    """routers.ts L250-297：候选层抽取 + 确认门禁。"""
    update = {
        "profile_candidates": [], "confirmed_profile_updates": [], "memory_candidates": [],
        "reminder_candidates": [], "created_reminders": [], "device_alerts": [], "device_missing": [],
    }

    # 本轮新抽取的候选（去重后只保留新增）
    existing = {(c["field"], c["value"]) for c in store.list_profile_candidates(session_id)}
    new_candidates = rules.extract_profile_candidates(latest_text, nickname, profile)
    for candidate in new_candidates:
        candidate_id = store.upsert_profile_candidate(session_id, candidate)
        candidate["id"] = candidate_id
        if (candidate["field"], candidate["value"]) not in existing:
            update["profile_candidates"].append(candidate)

    # 自然确认词 / 明确声明 → 身份类候选写档（SKILL.md 原则 6）
    # 注意：确认在“创建本轮候选”之后，所以首次说出名字也能立即确认。
    explicit_name = rules.is_explicit_name_claim(latest_text)
    if rules.is_natural_confirmation(latest_text):
        # 自然确认词：确认所有待确认的身份字段
        for candidate in store.list_profile_candidates(session_id):
            if candidate["status"] == "candidate" and candidate["field"] in _IDENTITY_FIELDS:
                store.confirm_profile_candidate(session_id, candidate["id"])
                update["confirmed_profile_updates"].append(
                    {"field": candidate["field"], "value": candidate["value"]})
    elif explicit_name:
        # 明确说出名字：只自动确认 name，不越权确认年龄/品种等其他字段
        for candidate in store.list_profile_candidates(session_id):
            if candidate["status"] == "candidate" and candidate["field"] == "name":
                store.confirm_profile_candidate(session_id, candidate["id"])
                update["confirmed_profile_updates"].append(
                    {"field": candidate["field"], "value": candidate["value"]})

    # 昵称记忆候选（routers.ts L276-280）
    # 如果本轮已经把 nickname 写入 profile candidate，就不再重复生成记忆候选，
    # 避免和 profile pending question 同时追问同一个名字。
    has_name_candidate = any(c["field"] == "name" for c in update["profile_candidates"])
    if nickname and not rules.has_explicit_profile_claim(latest_text) and not has_name_candidate:
        content = templates.MEMORY_NICKNAME_TEMPLATE.format(name=nickname)
        before = {m["content"] for m in store.list_memories(session_id)}
        memory = store.propose_memory(session_id, content, "pet_identity")
        if content not in before:
            update["memory_candidates"].append(memory)

    # 稳定信息（饮食/病史/过敏/习惯）进记忆候选（routers.ts L281-289）
    for candidate in update["profile_candidates"]:
        if candidate["field"] in _STABLE_FIELDS:
            content = f"{candidate['field']}：{candidate['value']}"
            before = {m["content"] for m in store.list_memories(session_id)}
            memory = store.propose_memory(session_id, content, candidate["field"])
            if content not in before:
                update["memory_candidates"].append(memory)

    # 提醒候选：只登记不创建，三要素齐全才建任务（routers.ts L290-294）
    if re.search(templates.REMINDER_TRIGGERS, latest_text or ""):
        details = rules.parse_reminder_details(latest_text)
        update["reminder_candidates"].append({
            "title": details["content"] or (latest_text or "")[:80],
            "instruction": templates.REMINDER_CANDIDATE_INSTRUCTION,
            "reason": "检测到用户明确表达了提醒或观察意图，尚未自动创建任务",
            "missing_fields": details["missing_fields"],
            "time": details["time"],
            "timezone": details["timezone"],
        })

    if device_trend and re.search(_DEVICE_ALERT_PATTERN, device_trend):
        update["device_alerts"] = [device_trend]
    return update


def _device_missing(events: list[dict]) -> list[str]:
    observed = {event.get("type") for event in events}
    return [label for etype, label in templates.DEVICE_MISSING_LABELS.items() if etype not in observed]


def _join(items: list[str], sep: str) -> str:
    return sep.join(items)


# ---------------------------------------------------------------- 主流程
def handle_chat(payload: dict) -> dict:
    session_id = (payload.get("sessionId") or "").strip() or f"web-session-{int(time.time() * 1000)}"
    store.ensure_session(session_id)

    incoming = [m for m in (payload.get("messages") or []) if m.get("content")]
    history = _history(session_id)

    # 落库并拼接历史（routers.ts L448-452）
    user_texts = [m["content"] for m in incoming if m.get("role") == "user"]
    if user_texts:
        latest_text = user_texts[-1]
        store.append_message(session_id, "user", latest_text)
        history.append({"role": "user", "content": latest_text})
    else:
        latest_text = history[-1]["content"] if history else ""
    history = history[-config.SESSION_MEMORY_LIMIT:]
    _SESSION_MEMORY[session_id] = history

    prior_user_text = next((m["content"] for m in reversed(history[:-1]) if m["role"] == "user"), "")

    # 省略式补充绑定到上一轮主题（routers.ts L455 / SKILL.md 原则 4）
    if rules.is_elliptical_follow_up(latest_text) and prior_user_text:
        focus = f"{prior_user_text}\n用户补充/修正：{latest_text}"
    else:
        focus = latest_text

    # 档案与昵称（routers.ts L460-466）
    profile = store.get_profile(session_id)
    if payload.get("catProfile"):
        profile.update({k: v for k, v in payload["catProfile"].items() if v})
    mentioned_nickname = rules.extract_pet_nickname(
        " ".join(m["content"] for m in history if m["role"] == "user"))
    preferred_name = mentioned_nickname or profile.get("name") or None

    # 设备上下文（routers.ts L469-477）
    if payload.get("deviceEvents"):
        for event in payload["deviceEvents"][:20]:
            store.ingest_device_event(session_id, event)
    events = store.list_device_events(session_id)
    device_trend = rules.summarize_device_trend(events)

    # 情景解析（不随检索档位变化）
    context = rules.context_of(focus)
    flags = rules.detect_e0(focus)

    base_ctx = GenContext(
        focus=focus,
        latest_text=latest_text,
        context=context,
        flags=flags,
        pet_name=preferred_name,
        profile=profile,
        onset=rules.detect_onset(focus, context),
        timeline=rules.detect_timeline(focus),
        severity=rules.detect_severity(focus, flags),
        user_goal=rules.detect_user_goal(latest_text),
        behavior_change=rules.detect_behavior_change(focus),
        device_trend=device_trend,
    )
    # normalized_symptoms 依赖首次检索结果，先做一次 R0 预检索填充
    preview = knowledge.retrieve(focus, "R0", flags)
    base_ctx.normalized_symptoms = rules.detect_normalized_symptoms(focus, preview)
    base_ctx.missing_info = rules.detect_missing_info(context, rules.decide_urgency(flags, preview, context),
                                                      bool(profile.get("name")))

    trace_id = _trace_id()
    pipeline = run_pipeline(base_ctx, focus, flags, get_generator(), trace_id)
    draft = pipeline["draft"]
    evidence = pipeline["evidence"]
    retrieved = pipeline["retrieved"]
    review = pipeline["review"]

    # 后台状态（routers.ts L526）
    background = _background_updates(session_id, latest_text, mentioned_nickname, device_trend, profile)
    background["device_missing"] = _device_missing(events)

    # 昵称替换（routers.ts L508-514）：不再自动追加“我可以继续叫它吗”，
    # 避免和 profile pending 问题重复；由后端根据是否明确声明决定确认/追问。
    if mentioned_nickname and (not profile.get("name") or mentioned_nickname != profile.get("name")):
        draft.answer = rules.replace_pet_name(draft.answer, mentioned_nickname, profile.get("name"))
        draft.clarification_questions = [rules.replace_pet_name(q, mentioned_nickname, profile.get("name"))
                                         for q in draft.clarification_questions]
        # 上下文推断出名字时，若答案里还没提到，先温柔地点一下名字，再进入正题
        if (mentioned_nickname not in draft.answer
                and not rules.is_explicit_name_claim(latest_text)
                and not draft.answer.startswith(mentioned_nickname)):
            draft.answer = f"{mentioned_nickname}怎么啦？{draft.answer}"

    # 提醒确认门禁（routers.ts L544-565）
    user_turns = [m["content"] for m in history if m["role"] == "user"]
    previous_user_text = user_turns[-2] if len(user_turns) >= 2 else ""
    previous_reminder_text = next(
        (text for text in reversed(user_turns[:-1]) if re.search(templates.REMINDER_TRIGGERS, text)),
        previous_user_text)

    created_reminder = None
    if rules.is_reminder_confirmation(latest_text) and re.search(templates.REMINDER_TRIGGERS, previous_reminder_text):
        details = rules.parse_reminder_details(f"{previous_reminder_text} {latest_text}")
        if details["missing_fields"]:
            background["reminder_candidates"] = [{
                "title": details["content"] or previous_reminder_text[:80],
                "instruction": templates.REMINDER_PENDING_INSTRUCTION,
                "reason": "确认消息仍缺少结构化提醒字段",
                "missing_fields": details["missing_fields"],
                "time": details["time"], "timezone": details["timezone"],
            }]
        else:
            created_reminder = store.create_reminder(session_id, {
                "type": "observation", "title": details["content"],
                "instruction": templates.REMINDER_CREATED_INSTRUCTION,
                "schedule": details["time"], "timezone": details["timezone"],
            })
            background["created_reminders"] = [created_reminder]
            background["reminder_candidates"] = []

    # 设备缺失 / 异常追问（routers.ts L531-543、L609-620）
    # 仅在用户已描述具体健康表现（有伴随症状 / 红旗 / 突然）时才追问设备数据，
    # 避免“我的猫叫 X”这种纯介绍场景下突兀追问。
    has_health_context = bool(flags or context.get("has_companion") or context.get("sudden"))
    if draft.urgency_level != "E0" and has_health_context:
        if config.ASK_DEVICE_MISSING and background["device_missing"]:
            missing = background["device_missing"]
            question = templates.DEVICE_MISSING_QUESTION.format(
                joined_and=_join(missing, "和"), joined_dun=_join(missing, "、"))
            draft.clarification_questions = [question, *[q for q in draft.clarification_questions if q != question]]
            draft.missing_info = list(dict.fromkeys(
                [*draft.missing_info, *[f"{m}情况" for m in missing]]))
            draft.next_action = templates.NEXT_ACTION_CLARIFY
        if background["device_alerts"]:
            question = templates.DEVICE_ALERT_QUESTION.format(trend=background["device_alerts"][0])
            draft.clarification_questions = [question, *[q for q in draft.clarification_questions if q != question]]
            draft.missing_info = list(dict.fromkeys([*draft.missing_info, "设备异常趋势是否真实、何时开始"]))
            draft.next_action = templates.NEXT_ACTION_CLARIFY

    # 提醒 / 档案 / 记忆三类门禁文案（routers.ts L566-635）
    if created_reminder:
        draft.answer = templates.REMINDER_CREATED_ANSWER.format(
            title=created_reminder["title"], schedule=created_reminder["schedule"],
            timezone=created_reminder["timezone"])
        draft.clarification_questions = []
        draft.next_action = templates.NEXT_ACTION_ANSWER
        review = {**review, "status": "approved", "issues": []}
    else:
        pending_reminder = next((r for r in background["reminder_candidates"] if r.get("missing_fields")), None)
        if pending_reminder:
            question = templates.REMINDER_MISSING_QUESTION.format(
                fields=_join(pending_reminder["missing_fields"], "、"))
            draft.clarification_questions = [question, *[q for q in draft.clarification_questions if q != question]]
        elif background["reminder_candidates"] and not any(
                re.search(r"提醒|时间|时区", q) for q in draft.clarification_questions):
            draft.clarification_questions = [templates.REMINDER_SCHEDULE_QUESTION, *draft.clarification_questions]

        if background["confirmed_profile_updates"]:
            confirmed_name = next((u for u in background["confirmed_profile_updates"] if u["field"] == "name"), None)
            if confirmed_name and rules.is_explicit_name_claim(latest_text):
                # 用户明确说出名字：直接温暖确认并引导进入正题
                draft.answer = templates.NICKNAME_WARM_CONFIRMED.format(name=confirmed_name["value"])
            else:
                items = _join([f"{templates.FIELD_LABELS.get(u['field'], u['field'])}“{u['value']}”"
                               for u in background["confirmed_profile_updates"]], "、")
                draft.answer = templates.PROFILE_CONFIRMED_ANSWER.format(items=items)
            draft.next_action = templates.NEXT_ACTION_ANSWER
        # 注意：档案/记忆候选仍会在 _background_updates 中持久化，
        # 但不再作为「下一步你可以问我」的确认问题推给用户，避免打断对话。

    # 落库：assistant 消息 + 每次 attempt 的审核 trace（§4.6）
    message_id = store.append_message(session_id, "assistant", draft.answer)
    for record in review["attempts"]:
        store.log_review(session_id, trace_id, {**record, "generator": review["generator"]}, message_id)

    response = draft.to_response()
    response.update({
        "session_id": session_id,
        "recognized_pet_name": mentioned_nickname,
        "device_context": {"events": events, "trend_summary": device_trend},
        "background_updates": background,
        "evidence": evidence,
        "diagnosis_boundary": draft.diagnosis_boundary,
        "can_do": draft.can_do,
        "must_not": draft.must_not,
        "answer_summary": build_summary(draft.urgency_level, context, flags),
        "review": review,
    })
    response["retrieved_knowledge_ids"] = [e["id"] for e in retrieved]
    return response


def bootstrap(session_id: str) -> dict:
    """一次性返回刷新恢复所需的全部状态（§5）。"""
    store.ensure_session(session_id)
    return {
        "session_id": session_id,
        "welcomed": store.is_welcomed(session_id),
        "profile": store.get_profile(session_id),
        "profile_candidates": store.list_profile_candidates(session_id),
        "memories": store.list_memories(session_id),
        "reminders": store.list_reminders(session_id),
        "device_events": store.list_device_events(session_id),
        "messages": store.list_messages(session_id),
        "knowledge_base_count": len(knowledge.KB_ALL),
        "verified_source_count": len(knowledge.VERIFIED_SOURCE_IDS),
    }


def reset_for_tests(session_id: str):
    """routers.ts L192-194"""
    _SESSION_MEMORY.pop(session_id, None)
    store.reset_session(session_id)
