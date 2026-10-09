# -*- coding: utf-8 -*-
"""情景解析规则：E0 红旗、昵称、省略句、确认词、提醒三要素、档案候选、设备趋势、风险分级。

对应计划 §4.1 / §4.3 / §4.7。
来源：
  - routers.ts L105-108  detectE0
  - routers.ts L110-123  extractPetNickname
  - routers.ts L125-127  isEllipticalFollowUp
  - routers.ts L186-190  summarizeDeviceTrend
  - routers.ts L214-232  isReminderConfirmation / isNaturalConfirmation / parseReminderDetails
  - routers.ts L234-248  extractProfileCandidates
  - 阶段5 server.py L71-85  context_of
  - SKILL.md 总体原则 1 / 每轮处理流程 2
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- E0 红旗
# routers.ts L106 ∪ SKILL.md 原则 1 与 §2 红旗清单
RED_FLAGS: tuple[str, ...] = (
    "张口呼吸", "呼吸困难", "呼吸费力",
    "无尿", "尿不出来", "只尿出几滴",
    "倒地", "失去意识", "意识丧失", "昏迷",
    "持续抽搐",
    "中毒", "吃了百合", "误食百合", "百合",
    "大量出血", "车祸", "严重外伤",
)

# 口语折叠为规范红旗词（扩展，routers.ts 无此层）
FLAG_NORM: dict[str, str] = {
    "张嘴呼吸": "张口呼吸",
    "张着嘴呼吸": "张口呼吸",
    "张嘴喘": "张口呼吸",
    "尿不出来": "无尿",
    "尿不出": "无尿",
    "没有尿": "无尿",
    "尿几滴": "只尿出几滴",
    "只出几滴": "只尿出几滴",
    "晕倒": "倒地",
    "昏倒": "倒地",
    "叫不醒": "失去意识",
    "晕过去了": "失去意识",
    "误食百合": "吃了百合",
    "被车撞": "车祸",
    "撞到车": "车祸",
    "出车祸": "车祸",
    "流血不止": "大量出血",
    "一直流血": "大量出血",
    "大量流血": "大量出血",
    "抽搐不停": "持续抽搐",
    "一直抽搐": "持续抽搐",
    "连续抽搐": "持续抽搐",
    "抽个不停": "持续抽搐",
    "反复抽搐": "持续抽搐",
    # --- 扩展：毒物名 → 中毒（SKILL 原则 1「疑似中毒」为红旗）---
    "老鼠药": "中毒",
    "鼠药": "中毒",
    "毒鼠": "中毒",
    "蟑螂药": "中毒",
    "杀虫剂": "中毒",
    "农药": "中毒",
    "除草剂": "中毒",
    "防冻液": "中毒",
    # --- 扩展：人类药物 → 中毒 ---
    "人用药": "中毒",
    "布洛芬": "中毒",
    "对乙酰氨基酚": "中毒",
    "扑热息痛": "中毒",
    "阿司匹林": "中毒",
    "感冒药": "中毒",
    "安眠药": "中毒",
}

_NEGATIONS = ("没有", "不是", "并非", "未出现", "排除", "不会", "无")


def _apply_norm(text: str, mapping: dict[str, str]) -> str:
    for key in sorted(mapping, key=len, reverse=True):
        if key in text:
            text = text.replace(key, mapping[key])
    return text


def _is_negated(text: str, pos: int, window: int = 6) -> bool:
    """否定窗口防御（扩展）：红旗词前 6 字内出现否定词则不判为红旗。"""
    prefix = text[max(0, pos - window):pos]
    return any(neg in prefix for neg in _NEGATIONS)


def detect_e0(text: str) -> list[str]:
    """返回命中的规范红旗词列表（routers.ts L105-108 + 口语归一化 + 否定窗口）。

    红旗口径（含 D04 扩展）：
      - 「持续抽搐」为 SKILL 原则 1 明示项；
      - 裸「抽搐」一并按 E0 处理：猫发生抽搐临床上即急症，
        SKILL 的「持续」应视为下限而非上限。命中后归一为规范词「持续抽搐」，
        以复用 KB-004 的既有检索通道（不新增词表项，避免与「持续抽搐」重复渲染）。
    """
    normalized = _apply_norm((text or ""), FLAG_NORM)
    found: list[str] = []
    for flag in RED_FLAGS:
        start = 0
        while True:
            idx = normalized.find(flag, start)
            if idx < 0:
                break
            if not _is_negated(normalized, idx):
                found.append(flag)
                break
            start = idx + len(flag)
    # D04：裸「抽搐」→ E0（词表无法覆盖「抽搐五分钟了还没停」这类「动作 + 数值 + 单位」表达）
    if "持续抽搐" not in found:
        start = 0
        while True:
            idx = normalized.find("抽搐", start)
            if idx < 0:
                break
            if not _is_negated(normalized, idx):
                found.append("持续抽搐")
                break
            start = idx + len("抽搐")
    return found


# ---------------------------------------------------------------- 昵称
# routers.ts L114 停用词表（已扩充包含结构词本身）
NICKNAME_STOPWORDS = {
    "我", "你", "它", "这", "那", "我的", "我家", "家中", "家里",
    "今天", "现在", "刚才",
    "猫咪", "猫猫", "好", "好的", "可以", "确认", "嗯", "行", "记得",
}

# 泛称/主题词不再视为昵称（修复「猫」「猫掉毛」误识别）：
# 直接呼语种类的 1 字泛称，以及高频症状/主题短语。
NICKNAME_GENERIC = {
    "猫", "它", "她", "他", "咪", "喵",
    "大家", "大家猫",
}

# 「猫掉毛」「猫打喷嚏」类「泛称+症状」拼接必须排除；以下为常见症状主题。
# 也加入高频时间/状态词，防止「罗小」被截断（后面紧跟「今天」「吐了」时应回退到完整名字）。
NICKNAME_TOPIC_EXCLUDE = {
    "不吃", "不爱吃", "吃", "吐", "呕吐", "拉稀", "拉", "拉肚", "拉肚子",
    "打喷嚏", "喷嚏", "流鼻涕", "鼻涕", "咳嗽", "咳", "喘", "抽搐", "发抖",
    "嗜睡", "没精神", "精神", "精神差", "精神下降", "掉毛", "脱毛",
    "抓挠", "挠", "舔", "舔毛", "咬", "咬人", "抓人", "躲藏", "躲",
    "反复进出猫砂盆", "进出猫砂盆", "蹲猫砂盆", "猫砂盆",
    "食欲下降", "食欲不振", "意识改变",
    "尿闭", "尿血", "少尿", "无尿",
    "发烧", "发热", "体温", "消瘦", "瘦", "胖",
    "怀孕", "孕", "生产", "发情", "绝育",
    "疫苗", "驱虫", "洗澡",
    "耳朵", "眼睛", "鼻子", "嘴", "嘴巴", "牙", "牙齿", "皮肤", "毛",
    # 时间/状态词：防止上下文提取把名字和时间词粘在一起
    "今天", "今晚", "今早", "今晨", "昨天", "明天", "前天",
    "最近", "刚才", "刚刚", "上午", "下午", "晚上", "夜里",
}

_NICKNAME_MIN_LEN = 2  # 单字「猫」「咪」一律不再视为昵称

# 常见宠物猫昵称白名单（用户要求：对常用命名有提前考量，而非先入为主假设某类名词）。
# 作为「明示/上下文」都未命中时的兜底扫描；命中后仍需语境校验（见 _scan_common_pet_name）。
COMMON_PET_NAMES: frozenset[str] = frozenset({
    # 2 字叠词 / 高频名
    "咪咪", "喵喵", "花花", "球球", "豆豆", "嘟嘟", "团团", "圆圆", "胖胖", "点点",
    "毛毛", "妞妞", "宝宝", "贝贝", "乐乐", "欢欢", "可可", "糖糖", "果果", "橙橙",
    "布丁", "奶茶", "可乐", "咖啡", "摩卡", "雪球", "雪糕", "冰冰", "小白", "小黑",
    "小花", "小灰", "小黄", "小橘", "小橙", "小胖", "大白", "大黑", "大胖", "大橘",
    "橘座", "老爷", "公子", "公主", "招财", "进宝", "元宝", "富贵", "平安", "吉祥",
    "如意", "来福", "旺财", "皮皮", "淘淘", "闹闹", "跳跳", "飞飞", "壮壮", "帅帅",
    "美美", "莉莉", "安娜", "露娜", "悠悠", "兜兜", "阿福", "阿黄", "阿黑", "阿白",
    "阿橘", "大壮", "小七", "小八", "小九", "咕咕", "噜噜", "胖虎", "胖达", "大圣",
    "叮当", "胖丁", "波波", "奇奇", "乖乖", "顺顺", "安安", "康康", "甜甜", "蜜蜜",
    "饭团", "肉包", "年糕", "瓜子", "花卷", "草莓", "芒果", "樱桃", "柚子", "柠檬",
    "芝麻", "糯米", "麻薯", "芋圆", "汤圆", "馒头", "包子", "饺子", "月饼", "薯条",
    "饼干", "糖果", "菠萝", "苹果", "桃子", "荔枝", "葡萄", "蓝莓", "三三", "九九",
    # 3-4 字
    "巧克力", "小布丁", "大胖橘", "小橘猫", "大橘猫", "胖墩墩", "小奶猫", "小糯米",
    "小汤圆", "小包子", "小年糕", "小饼干", "巧克力豆", "咖啡猫",
})

# 宠物代词 / 称谓：用于判断词典命中是否有语境支撑（避免「我喝了奶茶」误判）
_PET_DETERMINERS = ("猫", "猫咪", "猫猫", "毛孩子", "主子", "宠物")

# 昵称首字黑名单：结构性虚词不可能出现在猫名开头。
# 修复：「它一直往猫砂盆跑…还叫得很惨」→ 旧实现把裸「叫」当命名结构，
#       捕获「得很惨」为昵称，导致一条泌尿阻塞急症被回成改昵称的客套话。
_NICKNAME_BAD_FIRST_CHARS = frozenset("得了着过个起出醒唤叫的之亦而且但")

# 「叫」后紧跟这些字说明它是动词「喊叫」而非命名结构，禁止跨过
_RE_NAMED_NEGATIVE_TAIL = r"(?!得|了|着|过|个不停|不停|起来|出声|唤|醒|什么|啥|做|起来)"

# 「叫/名字是/名叫」明示优先（兼容「我家小黑叫小白」/「猫咪叫小白」结构）
_RE_NAMED = re.compile(
    r"(?:我的|我家|家里的|家中)?(?:猫咪|猫猫|猫|它)?\s*(?:叫|名字是|名叫)"
    + _RE_NAMED_NEGATIVE_TAIL +
    r"\s*([一-龥A-Za-z0-9]{2,8})"
)

# 判断用户是否明确声明名字（而非仅上下文推断）
_RE_EXPLICIT_NAME_CLAIM = re.compile(
    r"(?:叫|名字是|名叫)" + _RE_NAMED_NEGATIVE_TAIL + r"\s*[一-龥A-Za-z0-9]{2,8}"
)

def is_explicit_name_claim(text: str) -> bool:
    return bool(_RE_EXPLICIT_NAME_CLAIM.search(text or ""))

# 「我的 X」「我家 X」「家中 X」结构。改：去掉前瞻，匹配结构词后 2-6 字
# 再交给 _is_valid_nickname 过滤（结构词本身进 STOPWORDS）
# 「我的 X」「我的 X 的 Y」「我家 X」「家中 X」结构。
# 结构词本身进 STOPWORDS，避免「我的」「我家」被误当昵称。
# 使用贪婪匹配 + 主题词过滤层兜底，让「我家罗小黑今天吐了」回退到完整名字「罗小黑」。
_RE_CONTEXTUAL = re.compile(
    r"(?:^|[，。！？；、\s])"
    r"(我的|我家|家里的|家中)"
    r"(?:的)?"
    r"([一-龥A-Za-z0-9]{2,6})"
    r"(?=[一-龥A-Za-z0-9，。！？；、\s]|$)"
)


def _trim_suffix_to_topic(candidate: str, tail: str) -> str | None:
    """去掉候选末尾被截断的主题/时间词前缀。

    如果候选末尾确实是某个主题/时间词的前半部分，且 tail 正好是该词后半部分，
    则尝试把候选截短。截短后长度不足说明整段都是时间/主题词（如「我的猫今天吐了」
    会截成「猫」），此时返回 None，让上层丢弃这个伪候选。
    """
    for topic in sorted(NICKNAME_TOPIC_EXCLUDE, key=len, reverse=True):
        for split in range(1, len(topic)):
            if candidate.endswith(topic[:split]) and tail.startswith(topic[split:]):
                trimmed = candidate[:-split]
                if len(trimmed) >= _NICKNAME_MIN_LEN:
                    return trimmed
                # 截短后连最小长度都不够：说明候选本身大概率是「猫+时间词」这类误识别
                return None
    return candidate


def _is_valid_nickname(candidate: str) -> bool:
    if not candidate:
        return False
    if len(candidate) < _NICKNAME_MIN_LEN:
        return False
    # 首字为结构性虚词 → 必为动词短语残片（「得很惨」「了个啥」），非昵称
    if candidate[0] in _NICKNAME_BAD_FIRST_CHARS:
        return False
    if candidate in NICKNAME_STOPWORDS or candidate in NICKNAME_GENERIC or candidate in NICKNAME_TOPIC_EXCLUDE:
        return False
    # 任一主题词出现即视为主题而非昵称（如「小白掉毛」不该当昵称）
    for topic in NICKNAME_TOPIC_EXCLUDE:
        if topic in candidate:
            return False
    return True


def _scan_common_pet_name(text: str) -> str | None:
    """词典兜底：当「明示叫 X」「我家 X」都未命中时，扫描是否出现常见宠物昵称。

    中文无词边界，故不用字符边界，而用语境校验避免先入为主或误判：
      - 昵称前紧接领属词/称谓（我的|我家|家里的|家中|猫|猫咪|猫猫）→ 直接采信；
      - 昵称后紧接症状/主题词（如「奶茶吐了」「咪咪今天…」）→ 视为健康陈述主语，采信；
    否则（如「我喝了奶茶」）视为普通词汇，不提取。
    """
    if not text:
        return None
    for name in sorted(COMMON_PET_NAMES, key=len, reverse=True):
        if name in NICKNAME_GENERIC or name in NICKNAME_TOPIC_EXCLUDE:
            continue
        idx = text.find(name)
        if idx == -1:
            continue
        prev = text[max(0, idx - 3):idx]
        nxt = text[idx + len(name):idx + len(name) + 4]
        if re.search(r"(我的|我家|家里的|家中|猫|猫咪|猫猫)$", prev):
            return name
        if any(topic in nxt for topic in NICKNAME_TOPIC_EXCLUDE):
            return name
    return None


def extract_pet_nickname(text: str) -> str | None:
    """routers.ts L110-123 + SKILL.md 原则 5。

    增强：
      - 候选至少 2 个字（屏蔽单字「猫/咪/它」类泛称）。
      - NICKNAME_GENERIC + NICKNAME_TOPIC_EXCLUDE + 结构词 STOPWORDS 三层过滤。
      - 偏好顺序：明示「叫/名字是/名叫」→ 「我的/我家 X」上下文。
    """
    trimmed = (text or "").strip()
    if re.match(r"^(好的?|可以|确认|嗯|行|没问题|记得)[。！!？?，,\s]*$", trimmed):
        return None
    if re.match(r"^(提醒我|记得提醒|别忘了|请提醒我)", trimmed):
        return None

    # 1) 明示结构「叫 X」「名字是 X」「名叫 X」
    named = _RE_NAMED.search(text or "")
    if named and _is_valid_nickname(named.group(1)):
        return named.group(1)

    # 2)「我的/我家/家里的/家中 X」上下文
    # 注意：必须足够保守，避免把「我的猫今天吐了」错解成名字叫「猫今」。
    contextual = _RE_CONTEXTUAL.search(text or "")
    if contextual:
        base = contextual.group(2)
        start = contextual.start(2)
        # 贪婪匹配可能把后续内容也吃进去；从长到短回退，找最短有效名字
        for end in range(len(base), _NICKNAME_MIN_LEN - 1, -1):
            cand = base[:end]
            tail = text[start + end:]
            trimmed = _trim_suffix_to_topic(cand, tail)
            if trimmed is None:
                continue
            if _is_valid_nickname(trimmed):
                return trimmed

    # 3) 词典兜底：常见宠物昵称白名单（用户要求：对常用命名有提前考量，而非先入为主假设某类名词）
    common = _scan_common_pet_name(text)
    if common:
        return common

    return None


def replace_pet_name(text: str, name: str | None, previous_name: str | None = None) -> str:
    """routers.ts L129-132：本轮称呼覆盖档案旧昵称。"""
    if not name or not previous_name or name == previous_name:
        return text
    return text.replace(previous_name, name)


# ---------------------------------------------------------------- 省略式补充与确认词
# 原文正则匹配不了「突然发生的」，此处扩展（已知偏差 7）
_RE_ELLIPTICAL = re.compile(
    r"^(突然(?:的|发生|发生的)?|刚才(?:的|发生|发生的)?|现在(?:呢)?|是突然的|不是一直|对|对的|没错|是的|就这[样个])[。！!？?，,\s]*$"
)


def is_elliptical_follow_up(text: str) -> bool:
    return bool(_RE_ELLIPTICAL.match((text or "").strip()))


def is_natural_confirmation(text: str) -> bool:
    """routers.ts L218-220"""
    return bool(re.match(r"^(好的?|可以|确认|嗯|行|没问题|对|是的|没错|就是)(?:[。！!？?，,\s].*)?$", (text or "").strip()))


def is_reminder_confirmation(text: str) -> bool:
    """routers.ts L214-216"""
    return bool(re.match(r"^(好的?|可以|确认|嗯|行|没问题|记得)(?:[。！!？?，,\s].*)?$", (text or "").strip()))


# ---------------------------------------------------------------- 提醒三要素
def parse_reminder_details(text: str) -> dict:
    """routers.ts L222-232：内容 / 时间 / 时区，缺任一即追问。"""
    raw = text or ""
    time_match = re.search(r"(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})点(?:\s*(\d{1,2})分?)?", raw)
    raw_hour = int(time_match.group(1)) if time_match else None
    hour = raw_hour + 12 if (raw_hour is not None and re.search(r"今晚|明晚|晚上", raw) and raw_hour < 12) else raw_hour
    time = f"{hour:02d}:{int(time_match.group(2) or 0):02d}" if hour is not None else None

    tz_match = re.search(r"(Asia/[A-Za-z_]+|北京时间|中国标准时间|东八区)", raw, re.I)
    timezone = (tz_match.group(1) if tz_match.group(1).startswith("Asia/") else "Asia/Shanghai") if tz_match else None

    content = re.sub(r"提醒我|记得提醒|别忘了|请提醒我|预约|复诊|好的?|可以|确认", "", raw)
    content = re.sub(r"今晚|明晚|明天|后天|每天|每周", "", content)
    content = re.sub(r"(?:早上|上午|中午|下午|晚上|凌晨)?\s*\d{1,2}点(?:\s*\d{1,2}分?)?", "", content)
    content = re.sub(r"Asia/[A-Za-z_]+|北京时间|中国标准时间|东八区", "", content, flags=re.I)
    content = re.sub(r"[，。；;、\s]+", " ", content).strip()

    missing_fields = [f for f in ("提醒内容" if not content else None,
                                  "具体时间" if not time else None,
                                  "时区" if not timezone else None) if f]
    return {"content": content, "time": time, "timezone": timezone, "missing_fields": missing_fields}


# ---------------------------------------------------------------- 档案候选
_IDENTITY_FIELDS = ("name", "age", "breed", "sex", "neutered")
_STABLE_FIELDS = ("diet", "medicalHistory", "allergies", "dailyHabits")
_FIELD_LABELS = {
    "name": "昵称", "age": "年龄", "breed": "品种", "sex": "性别",
    "neutered": "绝育状态", "diet": "饮食习惯", "medicalHistory": "既往病史",
    "allergies": "过敏或不耐受", "dailyHabits": "日常习惯",
}


def extract_profile_candidates(text: str, nickname: str | None, profile: dict) -> list[dict]:
    """routers.ts L234-248：全部进入候选层，等待用户确认。"""
    raw = text or ""
    candidates: list[dict] = []
    certainty = "low" if re.search(r"好像|可能|大概|应该", raw) else "high"

    def add(field: str, value, reason: str, confidence: str | None = None):
        if value and profile.get(field) != value:
            candidates.append({
                "field": field,
                "value": value,
                "reason": reason,
                "confidence": confidence or certainty,
                "requires_confirmation": True,
            })

    add("name", nickname, "从用户本轮自然称呼识别，等待用户确认后写入档案")
    add("age", (re.search(r"(\d+(?:\.\d+)?)\s*(岁|个月)", raw) or [None, None])[0], "从用户自然语言识别年龄，等待用户确认")
    add("breed", (re.search(r"英短|美短|布偶|暹罗|缅因|家猫|田园猫|橘猫", raw) or [None])[0], "从用户自然语言识别品种，等待用户确认")
    add("sex", (re.search(r"公猫|母猫|雄性|雌性|男孩子|女孩子", raw) or [None])[0], "从用户自然语言识别性别，等待用户确认")
    add("neutered", (re.search(r"已绝育|已经绝育|做过绝育|没有绝育|未绝育", raw) or [None])[0], "从用户自然语言识别绝育状态，等待用户确认")
    add("diet", ((re.search(r"(?:平时主要吃|主要吃|平时吃)([^，。；;]+)", raw) or [None, None])[1] or "").strip() or None,
        "从用户自然语言识别饮食习惯，等待用户确认", "low" if certainty == "low" else "medium")
    add("medicalHistory", ((re.search(r"(?:有过|曾经有|患过)([^，。；;]+)", raw) or [None, None])[1] or "").strip() or None,
        "从用户自然语言识别既往病史，等待用户确认", "low" if certainty == "low" else "medium")
    add("allergies", ((re.search(r"(?:对|吃)([^，。；;]+)(?:过敏|不耐受)", raw) or [None, None])[1] or "").strip() or None,
        "从用户自然语言识别过敏或不耐受信息，等待用户确认", "medium")
    add("dailyHabits", ((re.search(r"(每天[^，。；;]+)", raw) or [None, None])[1] or "").strip() or None,
        "从用户自然语言识别日常习惯，等待用户确认", "medium")
    return candidates


def has_explicit_profile_claim(text: str) -> bool:
    """routers.ts L275"""
    return bool(re.search(r"叫|名字是|名叫|\d+(?:\.\d+)?\s*(?:岁|个月)|英短|美短|布偶|暹罗|缅因|家猫|田园猫|橘猫|公猫|母猫|绝育", text or ""))


# ---------------------------------------------------------------- 设备趋势
def summarize_device_trend(events: list[dict]) -> str:
    """routers.ts L186-190"""
    groups: dict[str, list[str]] = {}
    for event in events:
        value = f"{event.get('value', '')}（{event.get('observedAt', '')}）" if event.get("observedAt") else str(event.get("value", ""))
        groups.setdefault(event.get("type", ""), []).append(value)
    return "；".join(f"{etype}：{'、'.join(values[-3:])}" for etype, values in groups.items())


# ---------------------------------------------------------------- 情景上下文
def context_of(text: str) -> dict:
    """阶段5 server.py L71-85；『皮肤损伤』中删除『出血』，避免 E0 大出血被当成伴随拉低等级。"""
    raw = text or ""
    sudden = any(term in raw for term in ("突然", "一下子", "今天开始", "刚刚开始", "短时间内", "立即出现", "骤然"))
    time_known = any(term in raw for term in ("今天", "昨天", "前天", "小时", "天了", "几天", "一周", "持续", "从昨", "从今", "以来", "刚刚", "最近"))
    companions = {
        "攻击性/异常行为": ["攻击性", "突然咬人", "突然抓人", "异常凶", "暴躁", "躲藏", "行为改变"],
        "精神状态异常": ["没精神", "无精打采", "虚弱", "站不稳", "倒地", "嗜睡"],
        "疼痛或明显不适": ["痛叫", "疼", "痛苦", "不让碰", "蜷缩", "弓背"],
        "消化道异常": ["呕吐", "吐了", "腹泻", "拉稀", "便血"],
        "呼吸异常": ["呼吸困难", "张口呼吸", "喘", "呼吸费力"],
        "排尿异常": ["少尿", "无尿", "尿不出来", "蹲猫砂盆", "尿血"],
        "皮肤损伤": ["秃斑", "红疹", "皮肤破损", "抓破", "结痂", "明显瘙痒"],
    }
    found = [label for label, terms in companions.items() if any(term in raw for term in terms)]
    return {"sudden": sudden, "time_known": time_known, "companions": found, "has_companion": bool(found)}


def decide_urgency(flags: list[str], retrieved: list[dict], context: dict) -> str:
    """计划 §4.3 决策表，与阶段5 server.py L106-114 逐条对齐。"""
    if flags:
        return "E0"
    if context["sudden"] and context["has_companion"]:
        return "E1"
    if context["sudden"]:
        return "E3"
    if not context["time_known"]:
        return "E3"
    if not context["has_companion"]:
        return "E2"
    order = {"E0": 0, "E1": 1, "E2": 2, "E3": 3}
    levels = [e.get("urgency", "E3") for e in retrieved] or ["E3"]
    return min(levels, key=lambda lv: order.get(lv, 3))


# ---------------------------------------------------------------- 意图字段
def detect_onset(text: str, context: dict) -> str:
    raw = text or ""
    if context.get("sudden"):
        return "sudden"
    if re.search(r"反复|经常|总是|隔三差五|又出现", raw):
        return "recurring"
    if re.search(r"逐渐|慢慢|一点点|越来越", raw):
        return "gradual"
    if context.get("time_known"):
        return "ongoing"
    return "unknown"


def detect_timeline(text: str) -> str:
    raw = text or ""
    match = re.search(r"(今天|昨天|前天|刚才|刚刚|今晚|昨天晚上|(\d+)\s*天(?:了|前)?|(\d+)\s*(?:个)?小时(?:了|前)?|(\d+)\s*周|一周|半个月|一个月|最近|这几天)", raw)
    return match.group(0) if match else "unknown"


def detect_severity(text: str, flags: list[str]) -> str:
    if flags:
        return "严重"
    raw = text or ""
    if re.search(r"很|非常|特别|严重|厉害|一直|不停", raw):
        return "明显"
    if re.search(r"有点|稍微|轻微|一点点", raw):
        return "轻微"
    return "unknown"


def detect_user_goal(text: str) -> str:
    raw = text or ""
    if re.search(r"提醒我|记得提醒|别忘了", raw):
        return "create_reminder"
    if re.search(r"要不要|需要去|用不用|该不该|是否(需要|要)|要不要紧", raw):
        return "whether_need_vet"
    if re.search(r"怎么办|怎么处理|如何|该做什么|能做什么", raw):
        return "ask_what_to_do"
    if re.search(r"为什么|怎么回事|什么原因|是什么病", raw):
        return "ask_reason"
    return "describe_situation"


_BEHAVIOR_PATTERNS = ("躲藏", "躲起来", "不理人", "不愿意理我", "攻击性", "咬人", "抓人",
                      "异常凶", "暴躁", "活动下降", "不动", "异常叫", "一直叫", "叫声异常")


def detect_behavior_change(text: str) -> list[str]:
    raw = text or ""
    return [p for p in _BEHAVIOR_PATTERNS if p in raw]


# 口语表达 → 规范症状词映射，避免「吐了」匹配不到 KB 里的「呕吐」而回退到 topic  bundle
_SYMPTOM_ALIASES: dict[str, str] = {
    "吐了": "呕吐", "吐": "呕吐", "呕吐": "呕吐",
    "拉肚子": "腹泻", "拉稀": "腹泻", "腹泻": "腹泻",
    "便血": "便血", "血便": "便血", "拉血": "便血",
    "没精神": "没精神", "无精打采": "没精神", "精神差": "没精神",
    "不吃饭": "拒食", "不吃": "拒食", "拒食": "拒食",
    "不喝水": "饮水减少", "喝水少": "饮水减少",
    "尿不出": "无尿", "尿不出来": "无尿", "少尿": "少尿",
    "呼吸困难": "呼吸困难", "喘气": "呼吸困难", "喘": "呼吸困难",
    "抽搐": "抽搐", "抽筋": "抽搐",
    "出血": "出血", "流血": "出血", "流血不止": "出血",
    "躲起来": "躲藏", "躲藏": "躲藏",
}


def detect_normalized_symptoms(text: str, retrieved: list[dict]) -> list[str]:
    """从命中条目的症状词里挑出真正出现在用户表达里的，避免凭空罗列。"""
    raw = text or ""
    # 先做一次口语归一化，例如「吐了」→「呕吐」
    normalized = raw
    for alias, canonical in sorted(_SYMPTOM_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if alias in normalized:
            normalized = normalized.replace(alias, canonical)
            break  # 只替换最先命中的最长别名，避免过度展开
    found: list[str] = []
    for entry in retrieved:
        for symptom in entry.get("symptoms") or []:
            for piece in re.split(r"[、，,或/]+", str(symptom)):
                piece = piece.strip()
                if len(piece) <= 1:
                    continue
                if piece in raw or piece in normalized:
                    if piece not in found:
                        found.append(piece)
    # 兜底：如果用户提到常见症状但 KB 里没精确匹配，用别名规范词补充
    if not found:
        for alias, canonical in sorted(_SYMPTOM_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
            if alias in raw and canonical not in found:
                found.append(canonical)
                break
    return found[:6]


def detect_missing_info(context: dict, urgency: str, has_profile_name: bool) -> list[str]:
    missing: list[str] = []
    if not context["time_known"]:
        missing.append("症状开始时间与变化趋势")
    if context["sudden"] and not context["has_companion"]:
        missing.append("是否伴随攻击性、疼痛、精神差、呕吐腹泻或皮肤损伤")
    if not has_profile_name:
        missing.append("猫咪称呼与基础档案")
    if urgency in ("E1", "E2"):
        missing.append("进食、饮水、排尿、排便与精神状态")
    return missing
