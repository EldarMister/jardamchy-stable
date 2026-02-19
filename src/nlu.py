"""
NLU Module - Распознавание намерений через OpenAI GPT-4.1-mini
Natural Language Understanding для Business Assistant GO
"""

import json
import logging
import re
import requests

import config

logger = logging.getLogger(__name__)
MAX_FALLBACK_REPLY_CHARS = 2000

# Системный промпт для распознавания намерений
INTENT_SYSTEM_PROMPT = """Ты — NLU-модуль бота "Жардамчы ГО" в г. Шамалды-Сай, Кыргызстан.
Твоя задача — точно определить намерение пользователя и извлечь адреса/детали из сообщения.

КОНТЕКСТ: Шамалды-Сай — небольшой шахтёрский город. Жители пишут коротко, неформально,
часто смешивают русский и кыргызский в одном сообщении. Многие сообщения приходят после
голосового распознавания — могут быть опечатки, пропущенные буквы, искажённые слова.

═══ УСЛУГИ БОТА ═══
1. "taxi"     — такси (такси, машина, такси керек, алып кет, вези, отвези, taxi, унаа керек)
2. "cafe"     — еда из кафе (кафе, тамак, поесть, ашкана, оокат, меню, мену, миню, голоден, жейм)
3. "shop"     — доставка продуктов (магазин, продукты, дүкөн, буюм, товар, закупка)
4. "pharmacy" — лекарства (аптека, дарыкана, дары, лекарство, таблетки, дарылар)
5. "porter"   — крупный груз/переезд (портер, жүк, мебель, стройматериалы, скот, мал)
6. "ant"      — мелкий груз/посылка (муравей, желмаян, кичи жүк, мелкий груз, посылка, ун ташыш)
7. "greeting" — приветствие без запроса (салам, привет, здравствуй, ассаламу алейкум)
8. "unknown"  — не удалось определить однозначно

ПРАВИЛА ОПРЕДЕЛЕНИЯ УСЛУГИ:
- "муравей" или "желмаян" → всегда "ant", даже если речь о грузе (не путать с porter)
- Если упоминается конкретная улица/адрес без услуги — это скорее всего "taxi"
- Если перечисляются продукты/товары — это "shop"
- Если упоминается боль/болезнь/рецепт — это "pharmacy"

═══ ИЗВЛЕЧЕНИЕ АДРЕСОВ (только для taxi, porter, ant) ═══
Извлекай from_address и to_address если они есть в сообщении.
Если адрес слишком общий ("дом", "уй", "үй", "домой", "үйгө", "үйдөн") — ставь null.
Если адрес конкретный (улица, микрорайон, ориентир, магазин) — извлекай как есть.

Кыргызские падежные суффиксы — убирай суффикс, оставляй чистое название:
  ОТКУДА (-дан/-ден/-тан/-тен/-нан/-нен/-дон/-дөн/-дан): from_address
  КУДА   (-га/-ге/-ка/-ке/-го/-до/-жа/-же/-кe): to_address
Примеры суффиксов:
- "базардан" → from="базар"
- "адыр 2га" / "адырга" → to="Адыр 2"
- "ЖД 8ден базарга" → from="ЖД 8", to="базар"
- "северная 12ге" → to="северная 12"

═══ НОРМАЛИЗАЦИЯ МЕСТНЫХ НАЗВАНИЙ г. Шамалды-Сай ═══
Всегда приводи к каноническому написанию:

• ЖД-квартал: "жд 4/5/6/7/8", "жд4" и т.д. → "ЖД N" (единый адрес, НЕ разбивать)
  "8 базар", "8-й базар", "восьмой базар" — это РЫНОК, не ЖД!
• Кумар аке: "кумар", "кумар аке", "кумар магазин", "кумардан", "кумар акеден" → "Кумар аке"
• Миң түркүн: "мин туркун", "минтүркүн", "миң түркүн", "мин туркундан" → "Миң түркүн"
• Үй бүлөлүк: "уй булолук", "уй булолук магазин", "семейный магазин", "уйбулолук" → "Үй бүлөлүк магазин"
• Магазин 777: "777", "3 жети", "үч жети", "три семерки", "магазин 777" → "Магазин 777"
• Көк магазин: "кок магазин", "кок дукон", "кок маг" → "Көк магазин"

═══ ПРАВИЛА ДЛЯ order_details ═══
Заполняй ТОЛЬКО при наличии конкретных позиций (блюда, товары с названием/количеством).
НЕ заполняй для общих фраз: "тамак керек", "нужна еда", "товарлар", "буюмдар", "продукты".

═══ FALLBACK (intent="unknown") ═══
Обязательно заполни fallback_reply:
- тон: спокойный, дружелюбный, без формальностей
- язык: как у пользователя (ru/ky), если неясно — двуязычно
- кратко объясни что умеет бот и как написать правильно
- 1-2 конкретных примера ("Такси с базара на северную", "Тамак: лагман 1 порция")

Ответь ТОЛЬКО валидным JSON (без markdown):
{
  "intent": "taxi|cafe|shop|pharmacy|porter|ant|greeting|unknown",
  "from_address": "адрес отправления или null",
  "to_address": "адрес назначения или null",
  "order_details": "детали заказа или null",
  "cargo_type": "furniture|trash|construction|livestock|other или null",
  "fallback_reply": "подробный ответ до ~2000 символов; для unknown обязателен, иначе null"
}"""

# Системный промпт для подтверждения
CONFIRM_SYSTEM_PROMPT = """Ты определяешь, подтвердил ли пользователь действие.
Пользователи пишут на русском и кыргызском.

Слова подтверждения: да, ооба, оа, конечно, конешна, албетте, мм, ыы, верно, правильно, ок, ok, yes, хорошо, жакшы, макул, майли, ха, хоп (узбекское да)
Слова отказа: нет, жок, отмена, нет, no, cancel, отменить

Если пользователь исправляет адрес или данные — это НЕ подтверждение, а исправление.

Ответь ТОЛЬКО валидным JSON (без markdown):
{
  "confirmed": true или false,
  "is_correction": true или false,
  "corrected_from": "новый адрес отправления или null",
  "corrected_to": "новый адрес назначения или null",
  "corrected_details": "исправленные детали или null"
}"""


def _call_gpt(system_prompt: str, user_message: str, max_tokens: int = 600) -> dict:
    """Вызов OpenAI GPT-4.1-mini API"""
    try:
        if not config.OPENAI_API_KEY:
            logger.warning("OpenAI API key not configured")
            return {}

        url = "https://api.openai.com/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "gpt-4.1-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens
        }

        response = requests.post(url, headers=headers, json=payload, timeout=25)

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()

            # Убираем возможные markdown-обёртки
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                # Sometimes model wraps JSON with extra text on long/noisy input.
                match = re.search(r"\{.*\}", content, flags=re.DOTALL)
                if not match:
                    raise
                parsed = json.loads(match.group(0))
            logger.info(f"GPT response: {parsed}")
            return parsed
        else:
            logger.error(f"GPT API error {response.status_code}: {response.text}")
            return {}

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GPT response as JSON: {e}")
        return {}
    except Exception as e:
        logger.exception(f"GPT API exception: {e}")
        return {}


def parse_user_message(message: str) -> dict:
    """
    Распознать намерение пользователя.
    
    Returns:
        {
            "intent": "taxi|cafe|shop|pharmacy|porter|greeting|unknown",
            "from_address": str or None,
            "to_address": str or None,
            "order_details": str or None,
            "cargo_type": str or None,
            "fallback_reply": str or None
        }
    """
    result = _call_gpt(INTENT_SYSTEM_PROMPT, message)

    if not result:
        # Фолбэк на простое определение по ключевым словам
        fallback = _fallback_intent(message)
        if fallback.get("intent") == "unknown":
            fallback["fallback_reply"] = _fallback_unknown_reply(message)
        else:
            fallback["fallback_reply"] = None
        return fallback

    intent = result.get("intent", "unknown")
    fallback_reply = result.get("fallback_reply")
    if isinstance(fallback_reply, str):
        fallback_reply = fallback_reply.strip()
    else:
        fallback_reply = ""

    if intent == "unknown" and not fallback_reply:
        fallback_reply = _fallback_unknown_reply(message)
    if intent != "unknown":
        fallback_reply = None
    elif len(fallback_reply) > MAX_FALLBACK_REPLY_CHARS:
        fallback_reply = fallback_reply[:MAX_FALLBACK_REPLY_CHARS].rstrip() + "..."

    return {
        "intent": intent,
        "from_address": result.get("from_address"),
        "to_address": result.get("to_address"),
        "order_details": result.get("order_details"),
        "cargo_type": result.get("cargo_type"),
        "fallback_reply": fallback_reply
    }


def parse_confirmation(message: str) -> dict:
    """
    Определить, подтвердил ли пользователь заказ или исправляет данные.
    
    Returns:
        {
            "confirmed": bool,
            "is_correction": bool,
            "corrected_from": str or None,
            "corrected_to": str or None,
            "corrected_details": str or None
        }
    """
    result = _call_gpt(CONFIRM_SYSTEM_PROMPT, message)

    if not result:
        # Фолбэк
        return _fallback_confirmation(message)

    return {
        "confirmed": result.get("confirmed", False),
        "is_correction": result.get("is_correction", False),
        "corrected_from": result.get("corrected_from"),
        "corrected_to": result.get("corrected_to"),
        "corrected_details": result.get("corrected_details")
    }


def _fallback_intent(message: str) -> dict:
    """Фолбэк — определение по ключевым словам (если GPT недоступен)"""
    msg_lower = message.lower()
    msg_trim = (message or "").strip()
    msg_compact = re.sub(r"\s+", " ", msg_trim).strip()

    intent = "unknown"
    from_address = None
    to_address = None

    has_cafe_keyword = bool(re.search(r"\bкафе\b", msg_lower)) or any(
        w in msg_lower for w in ["еда", "поесть", "тамак", "меню", "мену", "миню", "минйу", "менйу", "мэню", "менюу", "мага меню", "меню керек", "menu"]
    )

    if has_cafe_keyword:
        intent = "cafe"
    elif any(w in msg_lower for w in ["магазин", "продукты", "покупки", "дүкөн"]):
        intent = "shop"
    elif any(w in msg_lower for w in ["аптека", "лекарство", "таблетки", "дарыкана", "дары"]):
        intent = "pharmacy"
    elif any(w in msg_lower for w in ["такси", "машина", "поехать", "вези"]):
        intent = "taxi"
    elif any(w in msg_lower for w in ["муравей", "желмаян", "мелкий груз"]) or msg_compact == "6":
        intent = "ant"
    elif any(w in msg_lower for w in ["портер", "груз", "перевезти", "мебель", "жүк", "жук", "ташыш", "ташуу", "ташыш"]) or msg_compact == "5":
        intent = "porter"
    elif any(w in msg_lower for w in ["салам", "привет", "здравствуйте", "ассалам"]):
        intent = "greeting"

    if intent in {"taxi", "porter", "ant"} and msg_compact:
        route_match = re.search(
            r"(?P<from>[^\s,;]+(?:\s+[^\s,;]+){0,4}?(?:дан|ден|тан|тен|нан|нен|дон|дөн))\s+"
            r"(?P<to>[^\s,;]+(?:\s+[^\s,;]+){0,4}?(?:га|ге|ка|ке|го|до|жа|же))\b",
            msg_compact,
            flags=re.IGNORECASE
        )
        if route_match:
            from_address = route_match.group("from").strip()
            to_address = route_match.group("to").strip()
            from_address = re.sub(r"(дан|ден|тан|тен|нан|нен|дон|дөн)$", "", from_address, flags=re.IGNORECASE).strip(" ,.-")
            to_address = re.sub(r"(га|ге|ка|ке|го|до|жа|же)$", "", to_address, flags=re.IGNORECASE).strip(" ,.-")
            filler_tokens = {
                "мага", "мне", "керек", "нужно", "надо", "муравей", "портер", "такси",
                "жүк", "жук", "ташыш", "ташуу", "ташыш", "кереk", "керек"
            }
            from_parts = [p for p in from_address.split() if p]
            if len(from_parts) > 2:
                from_parts = from_parts[-2:]
            while len(from_parts) > 1 and from_parts[0].lower() in filler_tokens:
                from_parts.pop(0)
            from_address = " ".join(from_parts).strip(" ,.-")
        else:
            dash_split = re.split(r"\s*[—-]\s*", msg_compact, maxsplit=1)
            if len(dash_split) == 2 and dash_split[0].strip() and dash_split[1].strip():
                from_address = dash_split[0].strip(" ,.-")
                to_address = dash_split[1].strip(" ,.-")

    return {
        "intent": intent,
        "from_address": from_address,
        "to_address": to_address,
        "order_details": None,
        "cargo_type": None,
        "fallback_reply": None
    }


def _looks_kyrgyz_text(message: str) -> bool:
    msg = (message or "").lower()
    if any(ch in msg for ch in ("ң", "ү", "ө", "ә")):
        return True
    kyrgyz_markers = (
        "салам", "керек", "жеткир", "дарек", "кайда", "канча",
        "жок", "ооба", "кылып", "беринчи", "берчи"
    )
    return sum(1 for marker in kyrgyz_markers if marker in msg) >= 2


def _fallback_unknown_reply(message: str) -> str:
    """Локальный подробный ответ для неизвестного запроса."""
    if _looks_kyrgyz_text(message):
        return (
            "Мен Жардамчы GO ботмун. Такси, тамак жеткирүү, дүкөн, дарыкана, портер жана муравей кызматтарын аткарам.\n"
            "Заказ берүү үчүн кызматтын атын жана негизги маалыматты жазыңыз: эмне керек, кайдан алып, кайда жеткирүү керек.\n"
            "Мисал 1: Такси, Базардан 3-микрорайонго.\n"
            "Мисал 2: Дүкөн, нан 2 даана жана сүт 1 литр, дарек: Шамалды-Сай борбору."
        )

    return (
        "Я бот Жардамчы GO. Помогаю с заказами: такси, доставка еды, магазин, аптека, портер и муравей.\n"
        "Чтобы оформить заказ, напишите услугу и детали: что нужно, откуда забрать и куда доставить.\n"
        "Пример 1: Такси, от Базара до Мкр 3.\n"
        "Пример 2: Магазин, хлеб 2 шт и молоко 1 л, адрес доставки: центр Шамалды-Сай."
    )


def _fallback_confirmation(message: str) -> dict:
    """Фолбэк — определение подтверждения по ключевым словам"""
    msg_lower = message.lower()

    confirmed = any(w in msg_lower for w in ["да", "туура", "ооба", "оа", "yes", "ок", "ok", "хорошо", "жакшы", "макул", "майли", "конечно", "ха", "хоп"])
    denied = any(w in msg_lower for w in ["нет", "жок", "no", "отмена", "отменить", "cancel"])

    if denied:
        confirmed = False

    return {
        "confirmed": confirmed,
        "is_correction": False,
        "corrected_from": None,
        "corrected_to": None,
        "corrected_details": None
    }
