# -*- coding: utf-8 -*-
"""全部回答文案常量。逐条标注来源，禁止新编医学内容。

来源：
  routers.ts L489-503  兜底 parsed
  routers.ts L512      昵称确认追问
  routers.ts L518-519  E0 覆盖文案
  routers.ts L520      E0 默认证据
  routers.ts L523      顶层诊断边界
  routers.ts L534      设备缺失追问
  routers.ts L539      设备异常趋势追问
  routers.ts L551/555/561/566/605  提醒相关
  routers.ts L624/629/633          档案与记忆门禁
  阶段5 server.py L87-96   context_questions（通用 + 主题追问）
  阶段5 server.py L121-129 分级摘要与三种覆盖文案
  SKILL.md §低风险居家建议边界 / 交付前检查 4
"""

# ---------------------------------------------------------------- 诊断边界
# routers.ts L523 顶层常量
DIAGNOSIS_BOUNDARY_TOP = "以上内容用于风险分流、养护沟通和下一步行动建议，不能替代执业兽医诊断。"
# routers.ts L78 摘录缺省边界
DEFAULT_EXCERPT_BOUNDARY = "需要结合兽医检查，不能据此确诊"

# ---------------------------------------------------------------- E0 覆盖
# routers.ts L518
E0_COVER_ANSWER = (
    "我注意到你提到了“{flags}”。这类表现需要优先按急症处理，"
    "请现在联系可接诊的动物医院或急诊兽医，并准备安全转运。不要等待聊天继续来观察变化。"
)
# routers.ts L519
E0_COVER_QUESTION = "在联系急诊的同时，猫咪现在是否仍有呼吸/意识/排尿异常？"
# routers.ts L520：E0 无证据时的默认来源
E0_DEFAULT_SOURCE_IDS = ["S01", "S03"]

# ---------------------------------------------------------------- 分级摘要
# 阶段5 server.py L121
URGENCY_SUMMARY = {
    "E0": "当前描述命中潜在急症信号，请现在联系可接诊的动物医院或急诊兽医，并准备安全转运。",
    "E1": "目前不建议仅在家观察，请尽快联系兽医安排评估。",
    "E2": "目前未命中已知急症红旗信号，但建议预约兽医检查并密切观察。",
    "E3": "目前还不能安全完成风险分级，但你可以先按下方的安全观察和禁忌事项处理，并补充关键信息；"
          "如存在任何红旗信号，请直接按急症处理。",
}
# 阶段5 server.py L122-127：三种覆盖文案
SUMMARY_SUDDEN_WITH_COMPANION = (
    "症状是突然出现，并伴随其他异常表现，建议尽快联系兽医评估；"
    "如果出现呼吸困难、倒地、持续抽搐、少尿/无尿或疑似中毒，应直接按 E0 急症处理。"
)
SUMMARY_SUDDEN_ONLY = (
    "症状是突然出现，但目前缺少伴随表现信息；"
    "不能直接套用普通症状等级，请先回答下面的问题，再根据情况重新判断。"
)
SUMMARY_NO_TIMELINE = (
    "目前缺少症状开始时间和变化趋势，暂时不能安全分级；"
    "请先补充下面的问题，并按安全观察和禁忌事项处理。"
)

# ---------------------------------------------------------------- 追问模板
# 阶段5 server.py L88 通用三问
COMMON_QUESTIONS = [
    "症状是突然出现还是逐渐加重？从什么时候开始？",
    "目前进食、饮水、排尿、排便和精神状态是否正常？",
    "是否还有呼吸困难、倒地、持续抽搐、疑似中毒或严重外伤？",
]
# 阶段5 server.py L89-94 主题追问
TOPIC_QUESTIONS = {
    "食欲": [
        "今天实际吃了多少，完全不吃持续了多久？",
        "是否伴随呕吐、腹泻、流口水、口腔疼痛或吞咽困难？",
        "是否出现突然攻击、躲藏、痛叫或不让触碰？",
    ],
    "掉毛": [
        "掉毛是全身还是局部？是否有秃斑、红疹、皮屑、结痂或抓破？",
        "近期是否更换食物、猫砂、洗护用品或生活环境？",
        "是否伴随精神差、食欲下降、呕吐腹泻或呼吸异常？",
    ],
    "呼吸": [
        "是否张口呼吸、腹部用力呼吸或牙龈发紫？",
        "症状是在休息时出现还是活动后出现？",
        "是否伴随咳嗽、鼻眼分泌物、倒地或意识异常？",
    ],
    "排尿": [
        "最近一次正常排尿是什么时候？现在是否确实有尿？",
        "是否反复进出猫砂盆、痛叫或舔舐生殖部位？",
        "猫是否为公猫，是否伴随呕吐、虚弱或倒地？",
    ],
}


def topic_questions(topic: str) -> list[str]:
    """阶段5 server.py L95：按主题选追问，无匹配则用通用三问。"""
    key = ("食欲" if ("食欲" in topic or "拒食" in topic)
           else "掉毛" if ("掉毛" in topic or "皮肤" in topic)
           else "呼吸" if "呼吸" in topic
           else "排尿" if "尿" in topic else None)
    return (TOPIC_QUESTIONS.get(key, []) + COMMON_QUESTIONS)[:5]


# ---------------------------------------------------------------- 安全注意
# SKILL.md 交付前检查 4 / §低风险居家建议边界
BASE_SAFETY_NOTES = [
    "不要自行给药、催吐、强行灌食或灌水。",
    "如出现呼吸困难、倒地、持续抽搐、疑似中毒或严重外伤，请立即联系急诊动物医院。",
]
E0_SAFETY_NOTES = [
    "不要等待聊天继续来观察变化。",
    "不要自行给药、催吐、强行灌食或灌水。",
    "如可能涉及误食，携带包装、标签或样品，并安全转运。",
]

# ---------------------------------------------------------------- 断言档文案（§4.6）
# A0 judgment：给出倾向性判断 + 行动建议
ASSERT_A0_TEMPLATE = (
    "{summary}从你描述的表现看，{topic}是需要重视的方向，{action_hint}"
    "以上判断基于已核验来源，不能替代执业兽医诊断。"
)
# A0 使用用户真实提到的症状，避免 topic bundle 里混入未提及的症状
ASSERT_A0_OBSERVED_TEMPLATE = (
    "{summary}从你提到的{observed}来看，这是需要重视的方向，{action_hint}"
    "以上判断基于已核验来源，不能替代执业兽医诊断。"
)
# A1 observation：只描述观察到的事实，不判断病因
ASSERT_A1_TEMPLATE = (
    "目前能确认的是你观察到的表现：{observed}。{summary}"
    "这些表现可能对应多种情况，需要先区分后再决定下一步。"
)
# A2 uncertain：明确无法分级
ASSERT_A2_TEMPLATE = (
    "目前检索到的证据不足以支撑分级判断，无法基于可靠证据分级。{summary}"
    "建议你先补充下面的关键信息，或直接联系兽医做当面评估。"
)
ACTION_HINT_BY_LEVEL = {
    "E1": "建议尽快安排兽医当面评估。",
    "E2": "可以先记录变化趋势并预约兽医检查，同时密切观察是否有升级信号。",
    "E3": "请先补充下面的关键信息，再重新判断。",
}

# ---------------------------------------------------------------- 保守兜底（§4.6 终止条件）
CONSERVATIVE_FALLBACK_ANSWER = (
    "这一轮我没有拿到足以支撑判断的可靠证据，无法基于可靠证据分级。"
    "为了安全起见，请先不要自行给药、催吐或强行灌食灌水；"
    "如果猫咪出现呼吸困难、倒地、持续抽搐、疑似中毒、无尿或严重外伤，请立即联系急诊动物医院。"
    "同时可以先回答下面的问题，帮我重新判断。"
)
CONSERVATIVE_FALLBACK_QUESTIONS = [
    "最明显的异常表现是什么？从什么时候开始？",
    "目前进食、饮水、排尿、排便和精神状态是否正常？",
    "是否出现呼吸困难、倒地、持续抽搐、疑似中毒或严重外伤？",
]

# ---------------------------------------------------------------- 模型解析失败兜底
# routers.ts L489-503
PARSE_FALLBACK = {
    "answer": "我先确认一下情况：这个表现是突然出现的吗？目前猫咪还能正常进食、饮水、排尿和排便吗？",
    "next_action": "ask_clarification",
    "urgency_level": "E3",
    "clarification_questions": [
        "症状从什么时候开始？",
        "是否伴随呼吸异常、疼痛、呕吐、腹泻或精神明显变差？",
    ],
    "intent_summary": "当前表达信息不足，需要补充上下文。",
    "normalized_symptoms": [],
    "evidence_source_ids": ["S03"],
    "user_goal": "ask_what_to_do",
    "timeline": "unknown",
    "onset": "unknown",
    "severity": "unknown",
    "companion_symptoms": [],
    "behavior_change": [],
    "missing_info": ["症状名称", "开始时间", "是否突然", "伴随表现"],
    "safety_notes": ["无法判断时不要自行用药；如出现急症红旗请立即联系急诊兽医。"],
}

# ---------------------------------------------------------------- 设备上下文
DEVICE_MISSING_LABELS = {"water_intake": "喝水", "food_intake": "吃饭", "urination": "排尿"}

# ---------------------------------------------------------------- 提醒
# routers.ts L293
REMINDER_CANDIDATE_INSTRUCTION = "请用户补充并确认具体时间、时区和提醒内容后再创建任务"
# routers.ts L551
REMINDER_PENDING_INSTRUCTION = "请补充并确认具体时间、时区和提醒内容后再创建任务"
# routers.ts L555
REMINDER_CREATED_INSTRUCTION = "记录进食、饮水及异常表现"
# routers.ts L561
REMINDER_CREATED_ANSWER = "好的，我已创建提醒：{title}，时间为 {schedule}，时区为 {timezone}。后续如需调整，可以继续告诉我。"
# routers.ts L552 / L605
REMINDER_MISSING_QUESTION = "为了创建提醒，还需要确认：{fields}。"
# routers.ts L567
REMINDER_SCHEDULE_QUESTION = "你希望什么时候提醒？提醒内容和时区是否按当前描述？"
REMINDER_TRIGGERS = r"提醒我|记得提醒|别忘了|预约|复诊|观察到明天|今晚记录"

# ---------------------------------------------------------------- 昵称 / 档案 / 记忆门禁
# routers.ts L512（已润色：更像对话，不像系统通知）
NICKNAME_CONFIRM_QUESTION = "以后我就叫它“{name}”，可以吗？"
# routers.ts L624
PROFILE_CONFIRMED_ANSWER = "好，我记下了{items}。我们继续说～"
# routers.ts L629（避免“后台档案”等生硬词；纠正比确认更容易）
PROFILE_PENDING_QUESTION = "我注意到{items}，有需要纠正的吗？"
# routers.ts L633
MEMORY_PENDING_QUESTION = "我记下“{content}”了，方便以后连续聊。不对的话你直接告诉我。"
# 用户明确说出名字时的温暖确认
NICKNAME_WARM_CONFIRMED = "好呀，以后我就叫它{name}啦～{name}今天怎么啦？"
# 设备缺失追问软化（作为「下一步你可以问我」的示例问题，由用户口吻问出）
DEVICE_MISSING_QUESTION = "它最近一次{joined_dun}大概是什么时候？"
# 设备异常趋势追问软化（作为用户可问的问题）
DEVICE_ALERT_QUESTION = "它最近的{trend}是从什么时候开始的？"
# routers.ts L277
MEMORY_NICKNAME_TEMPLATE = "用户称呼猫咪为“{name}”"

FIELD_LABELS = {
    "name": "昵称", "age": "年龄", "breed": "品种", "sex": "性别",
    "neutered": "绝育状态", "diet": "饮食习惯", "medicalHistory": "既往病史",
    "allergies": "过敏或不耐受", "dailyHabits": "日常习惯",
}

NEXT_ACTION_BY_URGENCY = {"E0": "recommend_vet_now"}
NEXT_ACTION_CLARIFY = "ask_clarification"
NEXT_ACTION_ANSWER = "answer_with_evidence"
