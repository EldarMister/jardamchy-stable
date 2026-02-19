"""
NLU Module - Распознавание намерений через OpenAI GPT-4.1-mini
Natural Language Understanding для Business Assistant GO
"""

import json
import logging
import requests

import config

logger = logging.getLogger(__name__)
MAX_FALLBACK_REPLY_CHARS = 2000

# Системный промпт для распознавания намерений
INTENT_SYSTEM_PROMPT = """Ты — NLU-модуль бота "Жардамчы ГО" в г. Шамалды-Сай (Кыргызстан).
Твоя задача — определить намерение пользователя из его сообщения.

Пользователи пишут на русском и кыргызском языках.

Услуги бота:
1. "taxi" — вызов такси (ключевые слова: такси, машина, поехать, вези, taxi)
2. "cafe" — заказ еды (кафе, еда, поесть, тамак, голоден, меню, мену, миню, минйу, менйу)
3. "shop" — доставка продуктов из магазина (магазин, продукты, покупки, дүкөн)
4. "pharmacy" — лекарства из аптеки (аптека, лекарство, таблетки, дарыкана, дары)
5. "porter" — грузоперевозки крупных грузов (портер, груз, перевезти мебель, жүк)
6. "ant" — муравей/желмаян, мелкие грузы (муравей, желмаян, ун ташыш, мелкий груз)
7. "greeting" — приветствие без конкретного запроса (салам, здравствуйте, привет)
8. "unknown" — не удалось определить

ВАЖНО: Если пользователь явно упоминает "муравей" или "желмаян" — это всегда "ant", даже если он также говорит о грузе. Не путай с "porter".

ВАЖНО про адреса: Если пользователь указывает слишком общий адрес (например: "дом", "уй", "үй", "үйгө", "үйдөн", "домой") без конкретного улицы/дома/микрорайона — установи этот адрес как null, чтобы бот переспросил.

ВАЖНО про order_details: Фразы намерения НЕ являются списком товаров/блюд.
Примеры НЕ order_details: "тамак керек", "оокат керек", "кафе", "товарлар", "нужна еда", "нужны товары".
Заполняй order_details только когда есть конкретные позиции (названия блюд/товаров) или количества.

Если пользователь сразу указал адреса или детали — извлеки их.

Учитывай реальные сообщения клиентов:
- смешанный русский/кыргызский в одном тексте;
- опечатки, транслит, разговорные сокращения;
- короткие фразы без структуры, где нужно понять скрытое намерение.

Если intent="unknown", обязательно заполни fallback_reply:
- спокойно, по-человечески, без резкости;
- на языке пользователя (русский или кыргызский), если язык неясен — можно двуязычно;
- объясни как пользоваться ботом и какой формат сообщения прислать;
- 1-2 наглядных примера для старта.

ВАЖНО про местные топонимы г. Шамалды-Сай:
- "ЖД" (железная дорога) + цифра = единый адрес, НЕ разбивать:
  "ЖД 4", "ЖД 5", "ЖД 6", "ЖД 7", "ЖД 8" — конкретные места/дома в ЖД-квартале.
  Пример: "с жд 8 на базар" → from_address="ЖД 8", to_address="базар"
- "8-й базар", "восьмой базар" и просто "8 базар" — это название рынка, не связано с ЖД.
- Если видишь "жд" или "жд N" (N — любая цифра), всегда сохраняй как единый адрес "ЖД N".
- "Кумар аке", "Кумар аке магазин", "кумар магазин" — конкретный магазин-ориентир в городе.
- "Мин туркун", "мин туркун магазин", "миң түркүн", "минтүркүн" — одно и то же место.
  Всегда нормализуй к правильному написанию: "Миң түркүн".
- "уй булолук", "уй булолук магазин", "семейный магазин", "үй бүлөлүк", "уйбулолук" — одно и то же место.
  Всегда нормализуй к: "Үй бүлөлүк магазин".
  Пример: "уй булолуктан" → from_address="Үй бүлөлүк магазин"
- "777", "магазин 777", "3 жети", "үч жети", "три семерки" — одно и то же место (магазин 777).
  Всегда нормализуй к: "Магазин 777".
  Пример: "777ден базарга" → from_address="Магазин 777", to_address="базар"
- "кок магазин", "көк магазин", "кок дукон", "көк дүкөн" — одно и то же место.
  Всегда нормализуй к: "Көк магазин".
  Пример: "кок магазиндан" → from_address="Көк магазин"

ВАЖНО про кыргызские падежные суффиксы в адресах:
Суффиксы -дан/-ден/-тан/-тен/-нан/-нен/-дон/-дөн = ОТКУДА (from_address).
Суффиксы -га/-ге/-ка/-ке/-го/-до/-жа/-же = КУДА (to_address).
Нужно убирать суффикс и сохранять чистое название места.
Примеры:
- "кумардан" → from_address="Кумар аке"
- "кумар акеден" → from_address="Кумар аке"
- "мин туркундан" → from_address="Миң түркүн"
- "миң түркүндөн" → from_address="Миң түркүн"
- "базардан северная 12ге" → from_address="базар", to_address="северная 12"
- "ЖД 8ден базарга" → from_address="ЖД 8", to_address="базар"
- "адыр 2га", "адырга", "адырды" → to_address="Адыр 2" (или "Адыр")

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

        response = requests.post(url, headers=headers, json=payload, timeout=15)

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()

            # Убираем возможные markdown-обёртки
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

            parsed = json.loads(content)
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

    intent = "unknown"

    if any(w in msg_lower for w in ["кафе", "еда", "поесть", "тамак", "меню", "мену", "миню", "минйу", "менйу", "мэню", "менюу", "мага меню", "меню керек", "menu"]):
        intent = "cafe"
    elif any(w in msg_lower for w in ["магазин", "продукты", "покупки", "дүкөн"]):
        intent = "shop"
    elif any(w in msg_lower for w in ["аптека", "лекарство", "таблетки", "дарыкана", "дары"]):
        intent = "pharmacy"
    elif any(w in msg_lower for w in ["такси", "машина", "поехать", "вези"]):
        intent = "taxi"
    elif any(w in msg_lower for w in ["муравей", "6", "желмаян", "мелкий груз"]):
        intent = "ant"
    elif any(w in msg_lower for w in ["портер", "5", "груз", "перевезти", "мебель", "жүк"]):
        intent = "porter"
    elif any(w in msg_lower for w in ["салам", "привет", "здравствуйте", "ассалам"]):
        intent = "greeting"

    return {
        "intent": intent,
        "from_address": None,
        "to_address": None,
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
