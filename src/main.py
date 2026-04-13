"""
Главный модуль - обработчик WhatsApp webhook
Main Module for Business Assistant GO
Обновленная версия с ИИ (GPT-4.1-mini)
"""

from flask import request, jsonify, has_request_context
import json
import re
import logging
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone

import config
from db import get_db, User, get_runtime_setting
from services import (
    send_whatsapp, send_whatsapp_buttons, send_whatsapp_image,
    send_telegram_private, edit_telegram_message,
    dispatch_telegram_group_notification,
    speech_to_text, format_phone, format_currency, send_confirmation_buttons,
    WHATSAPP_CANCEL_BUTTON_ID, WHATSAPP_MAIN_MENU_BUTTON_ID, send_order_cancelled_with_main_menu
)
from nlu import parse_user_message, parse_confirmation

logger = logging.getLogger(__name__)

def _runtime_setting(key: str, default):
    return get_runtime_setting(key, default)


def _bishkek_now_naive() -> datetime:
    return datetime.utcnow() + timedelta(hours=6)

# Unknown/fallback anti-flood settings (IDLE only)
UNKNOWN_FALLBACK_MAX_ATTEMPTS = 5
UNKNOWN_FALLBACK_RESET_MINUTES = 15
UNKNOWN_FALLBACK_COOLDOWN_MINUTES = 10

UNKNOWN_FALLBACK_SERVICES_HINT = (
    "такси / тамак / курьер / магазин / аптека / портер / желмаян"
)
UNKNOWN_FALLBACK_FINAL_MESSAGE = (
    "Мен Жардамчы GO ботумун. Заказ берүү үчүн жазыңыз: "
    f"{UNKNOWN_FALLBACK_SERVICES_HINT}. "
    "Болбосо жардам бере албайм."
)
UNKNOWN_FALLBACK_COOLDOWN_MESSAGE = (
    "Заказ берүү үчүн кызматты жазыңыз: "
    f"{UNKNOWN_FALLBACK_SERVICES_HINT}."
)

_GREETING_TEXT_VARIANTS = {
    "привет",
    "салам",
    "саламс",
    "салам алейкум",
    "саламалейкум",
    "ассалом алейкум",
    "ассаломалейкум",
    "алейкум салам",
    "алейкумсалам",
    "ало",
    "эй",
    "здравствуйте",
    "добрый день",
    "добрый вечер",
}
_SERVICE_TEXT_HINTS = (
    "такси", "кафе", "еда", "доставка", "курьер", "груз", "портер", "муравей",
    "аптека", "магазин", "меню", "order", "заказ"
)
_SHORT_VOICE_OK = {
    "да", "нет", "жок", "ок", "ok", "yes", "no", "1", "2"
}
_VOICE_ERROR_MARKERS = (
    "ошибка распозна",
    "ошибка загрузки аудио",
    "распознавание голоса недоступно",
    "не удалось распознать",
    "error recognition",
    "speech recognition error",
    "transcription error",
    "no api key",
)
_WHATSAPP_PRIVATE_SUFFIXES = ("@c.us", "@s.whatsapp.net", "@lid")
_WHATSAPP_GROUP_SUFFIX = "@g.us"

FLOW_SWITCH_PENDING_KEY = "flow_switch_pending"
FLOW_SWITCH_MODE_INTENT_CONFLICT = "intent_conflict"
FLOW_SWITCH_MODE_STALE_RESUME = "stale_resume"

FLOW_SWITCH_BUTTON_YES = "btn_switch_yes"
FLOW_SWITCH_BUTTON_NO = "btn_switch_no"
FLOW_STALE_BUTTON_NEW = "btn_stale_new"
FLOW_STALE_BUTTON_CONTINUE = "btn_stale_continue"
MED_EJE_NEED_BUTTON_ID = "med_eje_need"
MED_EJE_BACK_BUTTON_ID = "med_eje_back"

FLOW_SWITCH_SCOPE = {
    config.SERVICE_TAXI,
    config.SERVICE_CAFE,
    config.SERVICE_SHOP,
    config.SERVICE_PORTER,
    config.SERVICE_ANT,
}
FLOW_STALE_TTL_MINUTES = {
    config.SERVICE_TAXI: 45,
    config.SERVICE_CAFE: 240,
    config.SERVICE_SHOP: 240,
    config.SERVICE_PORTER: 240,
    config.SERVICE_ANT: 240,
}
FLOW_LABELS = {
    config.SERVICE_TAXI: "Такси",
    config.SERVICE_CAFE: "Кафе/Меню",
    config.SERVICE_SHOP: "Жеткирүү",
    config.SERVICE_PORTER: "Груз",
    config.SERVICE_ANT: "Муравей",
}
FLOW_LABELS_LOWER = {
    config.SERVICE_TAXI: "такси",
    config.SERVICE_CAFE: "кафе",
    config.SERVICE_SHOP: "жеткирүү",
    config.SERVICE_PORTER: "груз",
    config.SERVICE_ANT: "муравей",
}
FLOW_BY_STATE = {
    config.STATE_TAXI_ROUTE: config.SERVICE_TAXI,
    # STATE_TAXI_REORDER_CHOICE намеренно исключён: это лёгкий yes/no вопрос,
    # stale/conflict механизм не нужен — новый запрос такси должен проходить напрямую.
    config.STATE_CAFE_ORDER: config.SERVICE_CAFE,
    config.STATE_CAFE_ADDRESS: config.SERVICE_CAFE,
    config.STATE_SHOP_LIST: config.SERVICE_SHOP,
    config.STATE_SHOP_ADDRESS: config.SERVICE_SHOP,
    config.STATE_PORTER_CARGO_TYPE: config.SERVICE_PORTER,
    config.STATE_PORTER_ROUTE: config.SERVICE_PORTER,
    config.STATE_ANT_ROUTE: config.SERVICE_ANT,
}
EXPECTED_STEP_BY_STATE = {
    config.STATE_TAXI_ROUTE: "ожидаю маршрут",
    config.STATE_TAXI_REORDER_CHOICE: "ожидаю ответ по повтору заказа",
    config.STATE_PHARMACY_REORDER_CHOICE: "ожидаю ответ по повтору заказа аптеки",
    config.STATE_MED_EJE_MENU: "ожидаю выбор по медпомощи",
    config.STATE_CAFE_ORDER: "ожидаю список блюд",
    config.STATE_CAFE_ADDRESS: "ожидаю адрес доставки",
    config.STATE_SHOP_LIST: "ожидаю список покупок",
    config.STATE_SHOP_ADDRESS: "ожидаю адрес доставки",
    config.STATE_PORTER_CARGO_TYPE: "ожидаю тип груза",
    config.STATE_PORTER_ROUTE: "ожидаю маршрут груза",
    config.STATE_ANT_ROUTE: "ожидаю маршрут",
    config.STATE_CONFIRM_ORDER: "ожидаю подтверждение заказа",
}

FLOW_SWITCH_IGNORE_MESSAGES = {
    "да", "ооба", "ok", "ок", "yes", "ага",
    "нет", "жок", "no",
    "продолжить", "continue",
    "1", "2",
}
INTENT_KEYWORDS_CAFE = (
    "кафе", "еда", "меню", "menu", "роллы", "рол", "пицца",
    "ашкана", "тамак", "поесть", "жейм",
)
INTENT_KEYWORDS_TAXI = (
    "такси", "taxi", "машина", "уехать", "поехать",
    "откуда", "куда", "кайдан", "кайда", "унаа",
)
INTENT_KEYWORDS_ANT = (
    "муравей", "желмаян",
)
INTENT_KEYWORDS_PORTER = (
    "груз", "перевезти", "портер", "таш", "кум", "мал", "жүк", "жук",
)


INTENT_KEYWORDS_COMPUTER = (
    "компьютер", "компьютерные услуги", "компьютер кызматы", "компьютер кызматтар",
    "компьютер кызматтары", "компьютердик кызматтар", "ноутбук", "пк", "принтер",
    "полиграфия", "баннер", "визитка", "dtf", "сайт", "сайттар", "crm", "saas",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _send_confirm_with_buttons(phone: str, msg: str) -> None:
    """Отправить подтверждение + кнопки Ооба/Жок в одном сообщении."""
    buttons = [{"id": "confirm_yes", "text": "✅ Ооба"}, {"id": "confirm_no", "text": "❌ Жок"}]
    if config.WHATSAPP_PROVIDER == "cloud":
        send_whatsapp_buttons(phone, msg, buttons, include_cancel=False)
    else:
        send_whatsapp(phone, msg)
        send_confirmation_buttons(phone)


def _extract_green_sender(sender_data: dict) -> tuple:
    """Извлечь номер отправителя и тип WA-аккаунта из Green API webhook."""
    raw_sender = (
        (sender_data.get("sender") or "").strip()
        or (sender_data.get("chatId") or "").strip()
    )
    if not raw_sender:
        return "", "unknown", ""

    sender_kind = "personal"
    if raw_sender.endswith("@s.whatsapp.net") or raw_sender.endswith("@lid"):
        sender_kind = "business"
    if raw_sender.endswith(_WHATSAPP_GROUP_SUFFIX):
        return "", "group", raw_sender

    normalized = raw_sender.replace("whatsapp:", "").strip()
    for suffix in _WHATSAPP_PRIVATE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break

    digits = "".join(ch for ch in normalized if ch.isdigit())
    if not digits:
        return "", sender_kind, raw_sender

    sender_phone = digits
    return sender_phone, sender_kind, raw_sender


def _extract_green_message_payload(message_data: dict) -> tuple:
    """Нормализовать входящие поля Green API для разных типов сообщений."""
    incoming_msg = ""
    media_url = ""
    media_type = ""
    button_response = ""
    type_message = (message_data.get("typeMessage") or "").strip()

    if type_message == "textMessage":
        incoming_msg = message_data.get("textMessageData", {}).get("textMessage", "")
    elif type_message == "extendedTextMessage":
        incoming_msg = message_data.get("extendedTextMessageData", {}).get("text", "")
    elif type_message == "imageMessage":
        media_data = message_data.get("fileMessageData", {})
        media_url = media_data.get("downloadUrl", "")
        media_type = media_data.get("mimeType", "image/jpeg")
        incoming_msg = media_data.get("caption", "")
    elif type_message in ("audioMessage", "pttMessage", "voiceMessage", "voiceNoteMessage", "audio", "voice"):
        media_data = message_data.get("fileMessageData", {})
        media_url = media_data.get("downloadUrl", "")
        media_type = media_data.get("mimeType", "audio/ogg")
    elif type_message == "buttonsResponseMessage":
        btn_data = message_data.get("buttonsResponseMessageData", {})
        button_response = (
            btn_data.get("selectedButtonId")
            or btn_data.get("selectedDisplayText")
            or btn_data.get("selectedButtonText")
            or ""
        )
        incoming_msg = button_response
    elif type_message == "listResponseMessage":
        list_data = message_data.get("listResponseMessageData", {})
        button_response = list_data.get("selectedRowId") or list_data.get("title") or ""
        incoming_msg = button_response
    elif type_message == "locationMessage":
        loc = message_data.get("locationMessageData", {})
        latitude = loc.get("latitude")
        longitude = loc.get("longitude")
        if latitude is not None and longitude is not None:
            incoming_msg = f"location {latitude},{longitude}"

    if not incoming_msg:
        incoming_msg = (
            message_data.get("textMessageData", {}).get("textMessage")
            or message_data.get("extendedTextMessageData", {}).get("text")
            or ""
        )

    return (incoming_msg or "").strip(), media_url, media_type, (button_response or "").strip(), type_message


def _extract_cloud_sender(value: dict) -> tuple:
    """Извлечь номер отправителя из Cloud API webhook (entry.changes.value)."""
    messages = value.get("messages", [])
    if not messages:
        return "", "private", ""
    sender = (messages[0].get("from") or "").strip()
    digits = "".join(ch for ch in sender if ch.isdigit())
    return digits, "private", sender


def _extract_cloud_message_payload(value: dict) -> tuple:
    """Нормализовать входящие поля Cloud API для разных типов сообщений."""
    incoming_msg = ""
    media_url = ""
    media_type = ""
    button_response = ""
    type_message = ""

    messages = value.get("messages", [])
    if not messages:
        return incoming_msg, media_url, media_type, button_response, type_message

    msg = messages[0]
    msg_type = (msg.get("type") or "").strip()
    type_message = msg_type

    if msg_type == "text":
        incoming_msg = msg.get("text", {}).get("body", "")
    elif msg_type in ("audio", "voice"):
        audio_data = msg.get("audio") or msg.get("voice") or {}
        media_id = audio_data.get("id", "")
        if media_id:
            media_url = f"cloud_media:{media_id}"
            media_type = "audio/ogg"
    elif msg_type == "image":
        image_data = msg.get("image", {})
        media_id = image_data.get("id", "")
        if media_id:
            media_url = f"cloud_media:{media_id}"
            media_type = "image/jpeg"
        incoming_msg = image_data.get("caption", "")
    elif msg_type == "interactive":
        interactive = msg.get("interactive", {})
        btn_reply = interactive.get("button_reply", {})
        list_reply = interactive.get("list_reply", {})
        button_response = btn_reply.get("id", "") or list_reply.get("id", "")
        incoming_msg = btn_reply.get("title", "") or list_reply.get("title", "")
    elif msg_type == "button":
        btn = msg.get("button", {})
        button_response = btn.get("payload", "")
        incoming_msg = btn.get("text", "")

    return (incoming_msg or "").strip(), media_url, media_type, (button_response or "").strip(), type_message


def _parse_iso_utc(value):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_loose_text(text: str) -> str:
    lowered = (text or "").lower().strip()
    cleaned = re.sub(r"[^\w\s]+", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


ADDRESS_CANONICAL_NAMES = (
    "Северная",
    "Южная",
    "Пушкина",
    "Ленина",
    "Спортивная",
    "Атакулов",
    "Аксы",
    "Адыр",
    "Пионерская",
    "Токтосун-Абайдуллаев",
    "Фрунзе",
    "Панфилова",
    "Исанова",
    "Кураева",
    "Сергеева",
    "Чынгыз Айтматов",
    "Ыскак Раззаков",
    "Тыныбекова",
    "Орозбекова",
    "Зулпукарова",
    "Тарыкчиева",
    "Солнечная",
    "Нагорная",
    "Горная",
    "Лермонтова",
    "Достук",
    "Сыдыкова",
    "Набережная",
    "Дружба",
    "Ынтымак",
)

ADDRESS_MANUAL_ALIASES = {
    "Токтосун-Абайдуллаев": (
        "токтосун абайдуллаев",
        "токтосуна абайдуллаева",
        "токтосуна абайдулаева",
    ),
    "Чынгыз Айтматов": (
        "чынгыз айтматов",
        "чынгыза айтматова",
        "чингиза айтматова",
        "айтматова",
    ),
    "Ыскак Раззаков": (
        "ыскак раззаков",
        "ыскака раззакова",
        "ысака раззакова",
        "раззакова",
    ),
    "Ынтымак": ("интымак",),
}

ADDRESS_CASE_SUFFIXES = (
    "дан", "ден", "тан", "тен", "нан", "нен", "дон", "дөн",
    "га", "ге", "ка", "ке", "го", "гө", "ко", "кө",
    "до", "дө", "то", "тө", "жа", "же", "нө",
)
VOICE_FIX_REPLACEMENTS = (
    (" та кси ", " такси "),
    (" муровей ", " муравей "),
    (" жел маян ", " желмаян "),
    (" дары кана ", " дарыкана "),
    (" аптек ", " аптека "),
    (" пор тир ", " портер "),
    (" куткорат ", " кочкор ата "),
    (" кочкората ", " кочкор ата "),
    (" майлысу ", " майлуу суу "),
    (" майлису ", " майлуу суу "),
    (" майлуусуу ", " майлуу суу "),
    (" майлусу ", " майлуу суу "),
    (" маилусу ", " майлуу суу "),
)
VOICE_ADDRESS_HINTS = (
    "кайдан", "кайда", "откуда", "куда", "от", "до",
    "улица", "көчө", "кочо", "базар", "жд", "микрорайон",
    "дом", "квартира", "кв", "үй",
)
KY_NUM_TOKEN_ALIASES = {
    "уник": "он эки",
    "оники": "он эки",
    "онеки": "он эки",
    "онэки": "он эки",
    "онеке": "он эки",
    "онбир": "он бир",
    "онуч": "он үч",
    "онторт": "он төрт",
    "онбеш": "он беш",
    "оналты": "он алты",
    "онжети": "он жети",
    "онсегиз": "он сегиз",
    "онтогуз": "он тогуз",
    "жерма": "жыйырма",
    "жиырма": "жыйырма",
    "жыйрма": "жыйырма",
    "отус": "отуз",
    "кырк": "кырк",
    "елуу": "элүү",
    "элу": "элүү",
    "алтынмыш": "алтымыш",
    "алтымш": "алтымыш",
    "жетмиш": "жетимиш",
    "сексин": "сексен",
    "токсон": "токсон",
    "мин": "миң",
    "жуз": "жүз",
}
KY_NUM_UNITS = {
    "нөл": 0, "ноль": 0, "0": 0,
    "бир": 1, "1": 1,
    "эки": 2, "2": 2,
    "үч": 3, "уч": 3, "3": 3,
    "төрт": 4, "торт": 4, "4": 4,
    "беш": 5, "5": 5,
    "алты": 6, "6": 6,
    "жети": 7, "7": 7,
    "сегиз": 8, "8": 8,
    "тогуз": 9, "9": 9,
}
KY_NUM_TENS = {
    "он": 10,
    "жыйырма": 20,
    "отуз": 30,
    "кырк": 40,
    "элүү": 50, "елуу": 50,
    "алтымыш": 60,
    "жетимиш": 70,
    "сексен": 80,
    "токсон": 90,
}
KY_NUM_SCALE = {
    "жүз": 100, "жуз": 100,
    "миң": 1000, "мин": 1000,
}


def _normalize_address_match_text(text: str) -> str:
    normalized = (text or "").lower().strip().replace("ё", "е")
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"[^\w\s/]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _strip_address_case_suffix(token: str) -> str:
    for suffix in ADDRESS_CASE_SUFFIXES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def _build_address_variants() -> dict[str, tuple[str, ...]]:
    variants: dict[str, tuple[str, ...]] = {}
    for canonical in ADDRESS_CANONICAL_NAMES:
        local_variants = {_normalize_address_match_text(canonical)}
        for alias in ADDRESS_MANUAL_ALIASES.get(canonical, ()):
            normalized_alias = _normalize_address_match_text(alias)
            if normalized_alias:
                local_variants.add(normalized_alias)
        variants[canonical] = tuple(sorted(
            local_variants,
            key=lambda item: (-len(item.split()), -len(item)),
        ))
    return variants


ADDRESS_VARIANTS = _build_address_variants()
ADDRESS_MAX_WORDS = max(
    (len(v.split()) for variants in ADDRESS_VARIANTS.values() for v in variants),
    default=1,
)
ADDRESS_HINT_KEYWORDS = frozenset(
    list(VOICE_ADDRESS_HINTS)
    + [
        word
        for variants in ADDRESS_VARIANTS.values()
        for variant in variants
        for word in variant.split()
        if len(word) >= 3
    ]
)


def _normalize_kyrgyz_number_aliases(text: str) -> str:
    normalized = f" {text} "
    for alias, replacement in KY_NUM_TOKEN_ALIASES.items():
        normalized = re.sub(
            rf"(?<!\w){re.escape(alias)}(?!\w)",
            replacement,
            normalized,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_kyrgyz_number_sequence(tokens: list[str], start: int) -> tuple[int | None, int]:
    i = start
    consumed = 0
    total = 0

    # Optional thousands part: "эки миң", "миң"
    if i < len(tokens):
        t = tokens[i]
        t_base = _strip_address_case_suffix(t)
        if i + 1 < len(tokens):
            next_token = _strip_address_case_suffix(tokens[i + 1])
            if t_base in KY_NUM_UNITS and next_token in ("миң", "мин"):
                total += KY_NUM_UNITS[t_base] * 1000
                i += 2
                consumed += 2
        if consumed == 0 and t_base in ("миң", "мин"):
            total += 1000
            i += 1
            consumed += 1

    # Optional hundreds part: "эки жүз", "жүз"
    if i < len(tokens):
        t = _strip_address_case_suffix(tokens[i])
        if i + 1 < len(tokens):
            next_token = _strip_address_case_suffix(tokens[i + 1])
            if t in KY_NUM_UNITS and next_token in ("жүз", "жуз"):
                total += KY_NUM_UNITS[t] * 100
                i += 2
                consumed += 2
        if i < len(tokens):
            t = _strip_address_case_suffix(tokens[i])
            if t in ("жүз", "жуз"):
                total += 100
                i += 1
                consumed += 1

    # Tens + units: "он эки", "жыйырма үч", or standalone "эки"
    if i < len(tokens):
        t = _strip_address_case_suffix(tokens[i])
        if t in KY_NUM_TENS:
            total += KY_NUM_TENS[t]
            i += 1
            consumed += 1
            if i < len(tokens):
                unit_token = _strip_address_case_suffix(tokens[i])
                if unit_token in KY_NUM_UNITS and KY_NUM_UNITS[unit_token] > 0:
                    total += KY_NUM_UNITS[unit_token]
                    i += 1
                    consumed += 1
        elif t in KY_NUM_UNITS:
            total += KY_NUM_UNITS[t]
            i += 1
            consumed += 1

    if consumed == 0:
        return None, 0
    return total, consumed


def _convert_kyrgyz_numbers_to_digits(text: str) -> str:
    if not text:
        return text

    prepared = _normalize_kyrgyz_number_aliases(text.lower())
    tokens = [t for t in prepared.split() if t]
    if not tokens:
        return text

    out_tokens = []
    i = 0
    while i < len(tokens):
        value, consumed = _parse_kyrgyz_number_sequence(tokens, i)
        if consumed > 0 and value is not None:
            out_tokens.append(str(value))
            i += consumed
            continue
        out_tokens.append(tokens[i])
        i += 1

    return " ".join(out_tokens).strip()


def _looks_like_address_text(text: str) -> bool:
    lowered = f" {(text or '').lower()} "
    if any(h in lowered for h in VOICE_ADDRESS_HINTS):
        return True
    words = set(re.findall(r"[a-zа-яёүөңқһі0-9]+", lowered, flags=re.IGNORECASE))
    return any(w in ADDRESS_HINT_KEYWORDS for w in words)


def _address_match_threshold(word_count: int, char_count: int) -> float:
    if word_count >= 2:
        return 0.78
    if char_count <= 4:
        return 0.93
    return 0.85


def _pick_canonical_address_name(segment: str) -> str | None:
    normalized = _normalize_address_match_text(segment)
    if not normalized:
        return None

    stripped_words = [_strip_address_case_suffix(w) for w in normalized.split()]
    normalized = " ".join(w for w in stripped_words if w)
    if not normalized:
        return None

    segment_word_count = len(normalized.split())
    best_name = None
    best_score = 0.0

    for canonical, variants in ADDRESS_VARIANTS.items():
        for variant in variants:
            if len(variant.split()) != segment_word_count:
                continue
            score = SequenceMatcher(None, normalized, variant).ratio()
            if score > best_score:
                best_name = canonical
                best_score = score

    if not best_name:
        return None

    threshold = _address_match_threshold(segment_word_count, len(normalized))
    if best_score < threshold:
        return None
    return best_name


def _canonicalize_address_value(address: str) -> str:
    raw = (address or "").strip()
    if not raw:
        return raw

    tokens = re.split(r"\s+", raw)
    out_tokens = []
    i = 0

    while i < len(tokens):
        best_name = None
        best_span = 0
        max_span = min(ADDRESS_MAX_WORDS, len(tokens) - i)

        for span in range(max_span, 0, -1):
            segment = " ".join(tokens[i:i + span])
            if not re.search(r"[A-Za-zА-Яа-яЁёҮүӨөҢңҚқҺһІі]", segment):
                continue
            candidate = _pick_canonical_address_name(segment)
            if candidate:
                best_name = candidate
                best_span = span
                break

        if best_name:
            out_tokens.append(best_name)
            i += best_span
            continue

        out_tokens.append(tokens[i])
        i += 1

    return re.sub(r"\s+", " ", " ".join(out_tokens)).strip()


def _canonicalize_optional_address(address: str | None) -> str | None:
    corrected = _canonicalize_address_value((address or "").strip())
    return corrected or None


def _enhance_voice_transcript(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return raw

    normalized = f" {_normalize_loose_text(raw)} "
    for old, new in VOICE_FIX_REPLACEMENTS:
        normalized = normalized.replace(old, new)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return raw

    normalized = _convert_kyrgyz_numbers_to_digits(normalized)

    should_canonicalize = (
        _looks_like_address_text(normalized)
        or bool(re.search(r"\b\d{1,4}\b", normalized))
        or " - " in raw
        or " — " in raw
    )
    if should_canonicalize:
        normalized = _canonicalize_address_value(normalized)
    return normalized


def _is_bad_voice_transcription(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return True
    if raw.startswith("[") and raw.endswith("]"):
        return True

    normalized = _normalize_loose_text(raw)
    if not normalized:
        return True
    if not any(ch.isalnum() for ch in normalized):
        return True
    if any(marker in normalized for marker in _VOICE_ERROR_MARKERS):
        return True

    if len(normalized) < 4:
        if normalized in _SHORT_VOICE_OK:
            return False
        if any(hint in normalized for hint in _SERVICE_TEXT_HINTS):
            return False
        return True

    return False


def _looks_like_greeting(text: str) -> bool:
    normalized = _normalize_loose_text(text)
    if not normalized:
        return False
    if any(hint in normalized for hint in _SERVICE_TEXT_HINTS):
        return False
    compact = normalized.replace(" ", "")
    return normalized in _GREETING_TEXT_VARIANTS or compact in _GREETING_TEXT_VARIANTS


def _reset_unknown_fallback(user: User) -> None:
    if (
        user.get_temp_data("fallback_unknown_count", 0) in (0, None)
        and not user.get_temp_data("fallback_unknown_last_at")
        and not user.get_temp_data("fallback_unknown_cooldown_until")
    ):
        return

    user.set_temp_data("fallback_unknown_count", 0)
    user.set_temp_data("fallback_unknown_last_at", None)
    user.set_temp_data("fallback_unknown_cooldown_until", None)


def _resolve_active_flow(user: User) -> str | None:
    flow = FLOW_BY_STATE.get(user.current_state)
    if flow:
        return flow
    if user.current_state == config.STATE_CONFIRM_ORDER:
        service_type = (user.get_temp_data("service_type") or "").strip().lower()
        if service_type:
            return service_type
    return None


def _resolve_flow_label(flow: str) -> str:
    return FLOW_LABELS.get(flow, flow or "сценарию")


def _resolve_flow_label_lower(flow: str) -> str:
    return FLOW_LABELS_LOWER.get(flow, (flow or "сценарию").lower())


def _resolve_expected_step(state: str) -> str:
    return EXPECTED_STEP_BY_STATE.get(state, "ожидаю данные")


def _extract_flow_keyword_intent(message: str) -> str | None:
    normalized = _normalize_loose_text(message)
    if not normalized or normalized in FLOW_SWITCH_IGNORE_MESSAGES:
        return None

    def _contains_any(keywords: tuple[str, ...]) -> bool:
        tokens = normalized.split()
        for keyword in keywords:
            key = keyword.strip().lower()
            if not key:
                continue
            if " " in key and key in normalized:
                return True
            if len(key) <= 3:
                if key in tokens:
                    return True
                continue
            if key in tokens or key in normalized:
                return True
        return False

    if _contains_any(INTENT_KEYWORDS_ANT):
        return config.SERVICE_ANT
    if _contains_any(INTENT_KEYWORDS_CAFE):
        return config.SERVICE_CAFE
    if _contains_any(INTENT_KEYWORDS_TAXI):
        return config.SERVICE_TAXI
    if _contains_any(INTENT_KEYWORDS_PORTER):
        return config.SERVICE_PORTER
    return None


def _get_user_updated_at_utc(user: User) -> datetime | None:
    raw_value = getattr(user, "updated_at", None)
    if raw_value is None:
        return None

    if isinstance(raw_value, datetime):
        if raw_value.tzinfo is None:
            return raw_value.replace(tzinfo=timezone.utc)
        return raw_value.astimezone(timezone.utc)

    if isinstance(raw_value, str):
        return _parse_iso_utc(raw_value)

    return None


def _is_active_flow_stale(user: User, active_flow: str) -> bool:
    ttl_minutes = FLOW_STALE_TTL_MINUTES.get(active_flow)
    if not ttl_minutes:
        return False

    updated_at = _get_user_updated_at_utc(user)
    if not updated_at:
        return False

    return (_utc_now() - updated_at) >= timedelta(minutes=ttl_minutes)


def _build_flow_switch_pending(
    mode: str,
    user: User,
    from_flow: str,
    source_message: str,
    to_flow: str | None = None,
) -> dict:
    return {
        "mode": mode,
        "from_flow": from_flow,
        "to_flow": to_flow,
        "source_message": source_message,
        "state_snapshot": {
            "state": user.current_state,
            "service_type": user.get_temp_data("service_type"),
        },
        "created_at": _utc_now().isoformat(),
    }


def _set_flow_switch_pending(user: User, pending: dict) -> None:
    user.set_temp_data(FLOW_SWITCH_PENDING_KEY, pending)


def _clear_flow_switch_pending(user: User) -> None:
    user.set_temp_data(FLOW_SWITCH_PENDING_KEY, None)


def _get_flow_switch_pending(user: User) -> dict | None:
    pending = user.get_temp_data(FLOW_SWITCH_PENDING_KEY)
    if isinstance(pending, dict) and pending.get("mode"):
        return pending
    return None


def _send_intent_conflict_prompt(user: User, pending: dict) -> None:
    from_flow = pending.get("from_flow")
    to_flow = pending.get("to_flow")
    state_snapshot = pending.get("state_snapshot") or {}
    current_state = state_snapshot.get("state", user.current_state)
    expected_step = _resolve_expected_step(current_state)

    from_label = _resolve_flow_label(from_flow)
    to_label = _resolve_flow_label(to_flow)
    from_lower = _resolve_flow_label_lower(from_flow)

    prompt = (
        f"{to_label} кызматына өткүңүз келет, бирок {from_label} аяктаган жок "
        f"({expected_step}).\n"
        f"Учурдагыны жокко чыгарып, {to_label} кызматына өтөсүзбү?"
    )
    buttons = [
        {"text": "✅ Өтүү", "id": FLOW_SWITCH_BUTTON_YES},
        {"text": f"❌ {from_lower} улантуу", "id": FLOW_SWITCH_BUTTON_NO},
    ]
    if not send_whatsapp_buttons(user.phone, prompt, buttons):
        send_whatsapp(user.phone, prompt + "\n1. Өтүү\n2. Улантуу")


def _send_stale_flow_prompt(user: User, pending: dict) -> None:
    from_flow = pending.get("from_flow")
    state_snapshot = pending.get("state_snapshot") or {}
    current_state = state_snapshot.get("state", user.current_state)
    flow_label = _resolve_flow_label(from_flow)
    expected_step = _resolve_expected_step(current_state)
    prompt = (
        f"{flow_label} сценарийи эскирди ({expected_step}).\n"
        "Мурунку заказды улантасызбы же жаңы баштайсызбы?"
    )
    buttons = [
        {"text": "✅ Жаңы баштоо", "id": FLOW_STALE_BUTTON_NEW},
        {"text": "↩️ Мурункуну улантуу", "id": FLOW_STALE_BUTTON_CONTINUE},
    ]
    if not send_whatsapp_buttons(user.phone, prompt, buttons):
        send_whatsapp(user.phone, prompt + "\n1. Жаңы баштоо\n2. Мурункуну улантуу")


def _resend_confirm_step(user: User) -> bool:
    service_type = (user.get_temp_data("service_type") or "").strip().lower()
    if service_type == config.SERVICE_TAXI:
        from_addr = user.get_temp_data("taxi_from", "")
        to_addr = user.get_temp_data("taxi_to", "")
        _send_confirm_with_buttons(user.phone, config.CONFIRM_TAXI.format(
            from_address=from_addr,
            to_address=to_addr,
        ))
        return True

    if service_type == config.SERVICE_CAFE:
        _send_confirm_with_buttons(
            user.phone,
            config.CONFIRM_CAFE.format(
                order_details=user.get_temp_data("cafe_order_details", ""),
                address=user.get_temp_data("cafe_address", ""),
            ),
        )
        return True

    if service_type == config.SERVICE_PORTER:
        _cargo = (user.get_temp_data("porter_cargo") or "").strip()
        _cargo_line = f"\n📦 *Жүк:* {_cargo}" if _cargo else ""
        _send_confirm_with_buttons(
            user.phone,
            config.CONFIRM_PORTER.format(
                from_address=user.get_temp_data("porter_from", ""),
                to_address=user.get_temp_data("porter_to", ""),
                cargo_line=_cargo_line,
            ),
        )
        return True

    if service_type == config.SERVICE_ANT:
        _cargo = (user.get_temp_data("ant_cargo") or "").strip()
        _cargo_line = f"\n📦 *Жүк:* {_cargo}" if _cargo else ""
        _send_confirm_with_buttons(
            user.phone,
            config.CONFIRM_ANT.format(
                from_address=user.get_temp_data("ant_from", ""),
                to_address=user.get_temp_data("ant_to", ""),
                cargo_line=_cargo_line,
            ),
        )
        return True

    return False


def _resend_current_step_prompt(user: User) -> None:
    state = user.current_state
    if state == config.STATE_TAXI_ROUTE:
        send_whatsapp(user.phone, config.TAXI_PROMPT)
        return
    if state == config.STATE_TAXI_REORDER_CHOICE:
        send_whatsapp(
            user.phone,
            "Мурунку такси заказыбызды улантабыз.\n"
            "1. Заказды кайтала\n"
            "2. Жаңы маршрут",
        )
        return
    if state == config.STATE_CAFE_ORDER:
        send_whatsapp(user.phone, config.CAFE_PROMPT)
        return
    if state == config.STATE_CAFE_ADDRESS:
        send_whatsapp(user.phone, config.CAFE_ADDRESS_PROMPT)
        return
    if state == config.STATE_PORTER_CARGO_TYPE:
        send_whatsapp(user.phone, config.PORTER_CARGO_PROMPT)
        return
    if state == config.STATE_PORTER_ROUTE:
        send_whatsapp(user.phone, config.PORTER_ROUTE_PROMPT)
        return
    if state == config.STATE_ANT_ROUTE:
        send_whatsapp(user.phone, config.ANT_PROMPT)
        return
    if state == config.STATE_CONFIRM_ORDER and _resend_confirm_step(user):
        return

    send_whatsapp(user.phone, config.WELCOME_MESSAGE)


def _abort_active_flow(user: User, db, pending: dict, reason: str) -> None:
    details = {
        "reason": reason,
        "mode": pending.get("mode"),
        "from_flow": pending.get("from_flow"),
        "to_flow": pending.get("to_flow"),
        "state_snapshot": pending.get("state_snapshot"),
        "source_message": pending.get("source_message"),
    }
    db.log_transaction(
        "WHATSAPP_FLOW_ABORTED",
        user.phone,
        details=json.dumps(details, ensure_ascii=False),
    )
    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    _reset_unknown_fallback(user)


def _parse_pending_decision(mode: str, message: str) -> str | None:
    normalized = _normalize_loose_text(message)
    if not normalized:
        return None

    if mode == FLOW_SWITCH_MODE_INTENT_CONFLICT:
        accept_values = {
            FLOW_SWITCH_BUTTON_YES,
            "1",
            "да",
            "ооба",
            "yes",
            "перейти",
            "switch",
        }
        continue_values = {
            FLOW_SWITCH_BUTTON_NO,
            "2",
            "нет",
            "жок",
            "no",
            "продолжить",
            "continue",
        }
        if normalized in accept_values or "перейти" in normalized:
            return "switch"
        if normalized in continue_values or "продолжить" in normalized:
            return "continue"
        return None

    if mode == FLOW_SWITCH_MODE_STALE_RESUME:
        new_values = {
            FLOW_STALE_BUTTON_NEW,
            "1",
            "да",
            "ооба",
            "yes",
            "начать новый",
            "новый",
            "заново",
        }
        continue_values = {
            FLOW_STALE_BUTTON_CONTINUE,
            "2",
            "нет",
            "жок",
            "no",
            "продолжить",
            "continue",
        }
        if normalized in new_values or "начать новый" in normalized or "заново" in normalized:
            return "new"
        if normalized in continue_values or "продолжить" in normalized:
            return "continue"
        return None

    return None


def _handle_flow_switch_pending(user: User, message: str, db) -> tuple | None:
    pending = _get_flow_switch_pending(user)
    if not pending:
        return None

    mode = pending.get("mode")
    decision = _parse_pending_decision(mode, message)
    if not decision:
        if mode == FLOW_SWITCH_MODE_INTENT_CONFLICT:
            _send_intent_conflict_prompt(user, pending)
        elif mode == FLOW_SWITCH_MODE_STALE_RESUME:
            _send_stale_flow_prompt(user, pending)
        return jsonify({"status": "ok"}), 200

    source_message = (pending.get("source_message") or "").strip() or message

    if mode == FLOW_SWITCH_MODE_INTENT_CONFLICT:
        if decision == "switch":
            _abort_active_flow(user, db, pending, "intent_conflict_switch")
            return handle_idle_state(user, source_message, db)
        _clear_flow_switch_pending(user)
        _resend_current_step_prompt(user)
        return jsonify({"status": "ok"}), 200

    if mode == FLOW_SWITCH_MODE_STALE_RESUME:
        if decision == "new":
            _abort_active_flow(user, db, pending, "stale_start_new")
            return handle_idle_state(user, source_message, db)
        _clear_flow_switch_pending(user)
        _resend_current_step_prompt(user)
        return jsonify({"status": "ok"}), 200

    _clear_flow_switch_pending(user)
    return None


def _maybe_prompt_flow_switch(user: User, message: str) -> tuple | None:
    if user.current_state == config.STATE_IDLE:
        return None

    active_flow = _resolve_active_flow(user)
    if active_flow not in FLOW_SWITCH_SCOPE:
        return None

    if _is_active_flow_stale(user, active_flow):
        pending = _build_flow_switch_pending(
            mode=FLOW_SWITCH_MODE_STALE_RESUME,
            user=user,
            from_flow=active_flow,
            source_message=message,
        )
        _set_flow_switch_pending(user, pending)
        logger.info(
            "Flow stale prompt for %s: flow=%s state=%s",
            user.phone,
            active_flow,
            user.current_state,
        )
        _send_stale_flow_prompt(user, pending)
        return jsonify({"status": "ok"}), 200

    detected_flow = _extract_flow_keyword_intent(message)
    if not detected_flow or detected_flow == active_flow:
        return None
    if detected_flow not in FLOW_SWITCH_SCOPE:
        return None

    pending = _build_flow_switch_pending(
        mode=FLOW_SWITCH_MODE_INTENT_CONFLICT,
        user=user,
        from_flow=active_flow,
        to_flow=detected_flow,
        source_message=message,
    )
    _set_flow_switch_pending(user, pending)
    logger.info(
        "Flow switch prompt for %s: from=%s to=%s state=%s",
        user.phone,
        active_flow,
        detected_flow,
        user.current_state,
    )
    _send_intent_conflict_prompt(user, pending)
    return jsonify({"status": "ok"}), 200


def _handle_unknown_fallback(user: User, message: str, ai_reply: str = "") -> tuple:
    # Always answer unknown messages using AI fallback text when available.
    _reset_unknown_fallback(user)
    reply = (ai_reply or "").strip()
    if not reply:
        reply = (
            "Мен Жардамчы GO ботумун. Такси, тамак, магазин, аптека, портер жана желмаян боюнча жардам берем.\n"
            "Заказ берүү үчүн кызматты жана деталдарды жазыңыз: эмне керек, кайдан алуу жана кайда жеткирүү.\n"
            "Мисал: Такси, Базардан Мкр 3 чейин."
        )
    send_whatsapp(user.phone, reply)
    return jsonify({"status": "ok"}), 200


# =============================================================================
# VAGUE ADDRESS DETECTION & CANCELLATION
# =============================================================================

# Слова, которые ВСЕГДА означают неточный адрес (даже внутри фразы)
# "базардан уйго" -> "уйго" = strictly vague -> бот переспросит
STRICTLY_VAGUE = {
    "домой", "дома", "уйго", "үйгө", "уйдон", "үйдөн",
    "үйүмө", "уйума", "үйгө", "уйума", "үйүнө",
    "уйумо", "уйге", "үйгө"
}

# Слова, которые неточны ТОЛЬКО если весь адрес состоит только из них
# "дом" = vague, но "дом 5" = ok
MAYBE_VAGUE = {"дом", "уй", "үй", "квартира", "кв"}

# Слова отмены заказа (включая опечатки и варианты на кыргызском)
CANCEL_WORDS = {
    "отмена", "отменить", "отказ", "cancel", "стоп", "stop",
    "токтот", "баш тарт",
    "атмина", "атмин", "одмена", "кайтуу"
}
CANCEL_PREFIXES = ("отмен", "атмин", "атмина", "одмен", "артка", "кайт")

def _is_vague_address(address: str) -> bool:
    """Проверяет, является ли адрес слишком общим (дом, уйго, үйгө и т.д.)"""
    if not address:
        return True
    words = address.lower().strip().split()
    # Если ЛЮБОЕ слово — строго неточное (домой, уйго, үйгө) → всегда плохо
    for w in words:
        if w in STRICTLY_VAGUE:
            return True
    # Если ВЕСЬ адрес — это только "может быть неточный" (дом, уй) без конкретики
    if all(w in MAYBE_VAGUE for w in words):
        return True
    return False

_SUPPORT_KEYWORDS = {
    "жардам", "жардам бер", "помощь", "помоги", "помогите",
    "поддержка", "техподдержка", "тех поддержка", "тех.поддержка",
    "help", "support", "колдоо", "кömek", "комек",
}

_MED_EJE_KEYWORDS = {
    "7", "доктор", "мед эже", "медеже", "медсестра", "мед сестра",
    "мед помощь", "медициналык жардам",
    "врач", "укол", "уколы", "капельница", "капельницу", "капельницы",
}

_COMPUTER_SERVICE_FUZZY_TARGETS = tuple(
    phrase.replace(" ", "")
    for phrase in (
        "компьютерные услуги",
        "компютерные услуги",
        "компьютер кызматы",
        "компьютер кызматтар",
        "компьютер кызматтары",
        "компьютердик кызматтар",
    )
)


def _is_support_request(msg_lower: str) -> bool:
    """Проверяет, просит ли пользователь тех поддержку"""
    s = msg_lower.strip()
    if s in _SUPPORT_KEYWORDS:
        return True
    first = s.split()[0] if s else ""
    return first in _SUPPORT_KEYWORDS


def _extract_web_order_code(message: str) -> str | None:
    match = re.search(r"\bW\d{5}\b", (message or "").strip(), flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(0).upper()


def _looks_like_web_order_message(message: str) -> bool:
    normalized = _normalize_loose_text(message)
    if not normalized:
        return False
    if "заказ с сайта" in normalized:
        return True
    if not _extract_web_order_code(message):
        return False
    return any(marker in normalized for marker in ("код", "кафе", "итого"))


def _handle_web_order_code(user: User, message: str, db) -> tuple | None:
    code = _extract_web_order_code(message)
    if not code:
        return None

    looks_like_web_order = _looks_like_web_order_message(message) or message.strip().upper() == code
    if not looks_like_web_order:
        return None

    order = db.get_web_order(code)
    if not order:
        send_whatsapp(user.phone, "❌ Мындай код менен заказ табылган жок. Кодду текшериңиз.")
        return jsonify({"status": "ok"}), 200

    if order['status'] in ['CONFIRMED', 'COMPLETED', 'CANCELLED']:
        send_whatsapp(user.phone, f"⚠️ Бул заказ буга чейин иштелип бүткөн (Статус: {order['status']}).")
        return jsonify({"status": "ok"}), 200

    _reset_unknown_fallback(user)
    user.set_temp_data('service_type', config.SERVICE_CAFE)
    user.set_temp_data('web_order_code', code)
    user.set_temp_data('cafe_id', order['cafe_id'])

    items = order['items_json']
    details_lines = [f"Кафе: {order['cafe_name']}"]
    for item in items:
        details_lines.append(f"- {item['name']} x{item['count']}")
    details_lines.append(f"\nЖыйынтык: {int(order['total_price'])} сом")

    order_details = "\n".join(details_lines)
    user.set_temp_data('cafe_order_details', order_details)
    user.set_state(config.STATE_WEB_ORDER_ADDRESS)
    send_whatsapp(user.phone, "📍 Жеткирүү дарегин жазыңыз (же геолокация жөнөтүңүз):")
    return jsonify({"status": "ok"}), 200


def _is_computer_service_request(message: str) -> bool:
    normalized = _normalize_loose_text(message)
    if not normalized:
        return False

    if _looks_like_web_order_message(message):
        return False

    if normalized in {"8", "8."}:
        return True

    compact = normalized.replace(" ", "")
    if any(keyword in normalized for keyword in INTENT_KEYWORDS_COMPUTER if len(keyword) > 2):
        return True

    tokens = normalized.split()
    if any(token.startswith("комп") for token in tokens):
        if len(tokens) == 1:
            return True
        if any(
            token.startswith(prefix)
            for token in tokens
            for prefix in ("услуг", "кызмат", "сайт", "полиграф", "автомат", "визит", "баннер")
        ):
            return True

    if len(compact) >= 8 and any(
        SequenceMatcher(None, compact, target).ratio() >= 0.78
        for target in _COMPUTER_SERVICE_FUZZY_TARGETS
    ):
        return True

    return False


def _is_med_eje_request(message: str) -> bool:
    """Проверяет, просит ли пользователь медицинскую помощь / Мед Эже."""
    normalized = _normalize_loose_text(message)
    if not normalized:
        return False

    compact = normalized.replace(" ", "")
    if normalized in _MED_EJE_KEYWORDS or compact in _MED_EJE_KEYWORDS:
        return True

    return any(
        phrase in normalized
        for phrase in (
            "мед помощь",
            "мед эже",
            "медициналык жардам",
            "мед сестра",
            "медсестра",
            "капельница",
            "укол",
        )
    )


def _show_med_eje_menu(user: User):
    _reset_unknown_fallback(user)
    user.clear_temp_data()
    user.set_state(config.STATE_MED_EJE_MENU)
    send_whatsapp_buttons(
        user.phone,
        config.MED_EJE_MESSAGE,
        [
            {"id": MED_EJE_NEED_BUTTON_ID, "text": "✅ Керек"},
            {"id": MED_EJE_BACK_BUTTON_ID, "text": "🏠 Артка"},
        ],
        include_cancel=False,
    )
    return jsonify({"status": "ok"}), 200


def _send_specialist_request(user: User, client_message: str, service_name: str):
    _reset_unknown_fallback(user)
    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    send_whatsapp(user.phone, client_message)
    if config.SUPPORT_TELEGRAM_ID:
        send_telegram_private(
            config.SUPPORT_TELEGRAM_ID,
            config.SPECIALIST_REQUEST_TO_OPERATOR.format(
                service_name=service_name,
                client_phone=user.phone,
            ),
        )
    return jsonify({"status": "ok"}), 200


def _send_poputka_list(user: User, db):
    _reset_unknown_fallback(user)
    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()

    offers = db.list_active_poputka_offers(limit=15, now_local=_bishkek_now_naive())
    if not offers:
        send_whatsapp(user.phone, config.POPUTKA_LIST_EMPTY)
        return jsonify({"status": "ok"}), 200

    lines = [config.POPUTKA_LIST_HEADER]
    for idx, offer in enumerate(offers, start=1):
        departure_time = offer.get("departure_time")
        departure_text = departure_time.strftime("%H:%M") if hasattr(departure_time, "strftime") else str(departure_time)
        phone = format_phone((offer.get("driver_phone") or "").strip()) if offer.get("driver_phone") else "—"
        seats = offer.get("seats_available")
        seats_text = str(seats) if seats is not None else "—"
        route = f"{offer.get('from_address', '')} → {offer.get('to_address', '')}"
        lines.append(
            f"*{idx}.*\n"
            f"📍 Маршрут: {route}\n"
            f"👥 Орун: {seats_text}\n"
            f"🕒 Чыгуу: {departure_text}\n"
            f"📞 Телефон: {phone}"
        )

    send_whatsapp(user.phone, "\n\n".join(lines))
    return jsonify({"status": "ok"}), 200


def handle_med_eje_menu(user: User, message: str, db) -> tuple:
    normalized = _normalize_loose_text(message)

    if normalized in {"керек", "нужно", "надо", "need"}:
        user.set_state(config.STATE_IDLE)
        user.clear_temp_data()
        send_whatsapp(
            user.phone,
            config.MED_EJE_PHONE_MESSAGE.format(
                phone_1=format_phone(config.MED_EJE_PHONE),
                phone_2=format_phone(config.MED_EJE_PHONE_2),
            ),
        )
        return jsonify({"status": "ok"}), 200

    if normalized in {"артка", "назад", "back", "меню"}:
        user.set_state(config.STATE_IDLE)
        user.clear_temp_data()
        _reset_unknown_fallback(user)
        send_whatsapp(user.phone, config.WELCOME_MESSAGE)
        db.update_last_welcome(user.phone)
        return jsonify({"status": "ok"}), 200

    return _show_med_eje_menu(user)


def _is_cancellation(message: str) -> bool:
    """Проверяет, хочет ли пользователь отменить заказ"""
    msg_lower = message.lower().strip()
    if not msg_lower:
        return False

    # Эти слова используются в шагах подтверждения/выбора и не должны быть
    # глобальной отменой.
    if msg_lower in ('жок', 'нет', 'жо', 'жог'):
        return False

    # Точное совпадение
    if msg_lower in CANCEL_WORDS:
        return True

    # Если первое слово — отмена
    first_word = msg_lower.split()[0] if msg_lower else ""
    if first_word in CANCEL_WORDS:
        return True

    # По префиксу ловим формы вроде "отмен...", "кайт...", "артка..."
    if any(msg_lower.startswith(prefix) for prefix in CANCEL_PREFIXES):
        return True
    if any(first_word.startswith(prefix) for prefix in CANCEL_PREFIXES):
        return True

    return False


def _normalize_address(address: str) -> str:
    """Нормализовать адрес для сравнения."""
    if not address:
        return ""
    normalized = re.sub(r"\s+", " ", address.lower().strip())
    normalized = re.sub(r"[^\w\s\-а-яё]", "", normalized, flags=re.IGNORECASE)
    return normalized


def _addresses_equal(addr1: str, addr2: str) -> bool:
    """Проверка адресов на равенство после нормализации."""
    n1 = _normalize_address(addr1)
    n2 = _normalize_address(addr2)
    return bool(n1) and n1 == n2


def _is_concrete_order_details(text: str, service: str) -> bool:
    """
    True only for concrete dish/product lists.
    Intent-only phrases like "тамак керек", "кафе", "товарлар" return False.
    """
    if not text:
        return False

    raw = text.strip()
    if not raw:
        return False

    lowered = re.sub(r"\s+", " ", raw.lower()).strip()
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", lowered)
    if not tokens:
        return False

    generic_intent_words = {
        "кафе", "еда", "тамак", "оокат", "меню", "мену", "миню", "мэню",
        "товар", "товары", "товарлар", "продукт", "продукты", "магазин", "дүкөн",
        "заказ", "керек", "нужно", "хочу"
    }
    if service == config.SERVICE_SHOP:
        generic_intent_words.update({"сатып", "алуу", "покупка", "покупки"})

    # Clear intent-only short phrases
    if len(tokens) <= 4 and all(t in generic_intent_words for t in tokens):
        return False
    if lowered in {"тамак керек", "оокат керек", "кафе", "товарлар", "товары", "магазин"}:
        return False

    # Strong concrete signals: numbers/quantities/list formatting
    if re.search(r"\b\d+\b", lowered):
        return True
    if re.search(r"\b(шт|кг|гр|г|л|мл|kg|gr|ml|x\d+)\b", lowered):
        return True
    if any(sep in raw for sep in [",", ";", "\n"]):
        return True
    if re.search(r"(^|\n)\s*[-*•]\s*", raw):
        return True

    meaningful = [t for t in tokens if t not in generic_intent_words]
    if len(meaningful) >= 2:
        return True
    if len(meaningful) == 1:
        return True

    return False


def _sanitize_ant_details(order_details: str, from_addr: str = "", to_addr: str = "", source_text: str = "") -> str:
    """Keep only cargo item for ant flow, removing route fragments and helper words."""
    raw = (order_details or source_text or "").strip()
    if not raw:
        return ""

    cleaned = raw
    from_root = (_normalize_loose_text(from_addr).split()[:1] or [""])[0]
    to_root = (_normalize_loose_text(to_addr).split()[:1] or [""])[0]

    if from_root and to_root:
        cleaned = re.sub(
            rf"\b{re.escape(from_root)}\w*\b.*?\b{re.escape(to_root)}\w*\b",
            " ",
            cleaned,
            flags=re.IGNORECASE
        )

    # Remove endpoint names if they still remain in detail text.
    for endpoint in (from_addr, to_addr):
        ep = (endpoint or "").strip()
        if ep:
            cleaned = re.sub(re.escape(ep), " ", cleaned, flags=re.IGNORECASE)

    # Remove transport helper phrases to keep only what is being carried.
    cleaned = re.sub(r"\bташыш\s+керек\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bташуу\s+керек\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bперевезти\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bдоставить\b", " ", cleaned, flags=re.IGNORECASE)

    stopwords = {
        "мага", "мне", "керек", "нужно", "надо",
        "муравей", "желмаян", "ташыш", "ташуу", "жук", "жүк"
    }
    tokens = []
    for token in re.split(r"\s+", cleaned):
        tok = token.strip(" ,.;:!?-—")
        if not tok:
            continue
        lower = tok.lower()
        if lower in stopwords:
            continue
        if re.search(r"(дан|ден|тан|тен|нан|нен|дон|дөн|га|ге|ка|ке|го|до|жа|же|астынан|үстүнөн)$", lower):
            continue
        if from_root and lower.startswith(from_root):
            continue
        if to_root and lower.startswith(to_root):
            continue
        tokens.append(tok)

    normalized = " ".join(tokens).strip()
    return normalized or raw


def _cancel_order_in_group(order_id: str, service_type: str, db, text: str) -> None:
    """Обновить сообщение в группе на 'заказ отменен' и убрать кнопки"""
    timer = db.get_latest_auction_timer(order_id, service_type)
    if not timer:
        return
    try:
        chat_id = timer.get('chat_id')
        message_id = int(timer.get('telegram_message_id'))
        if chat_id and message_id:
            edit_telegram_message(chat_id, message_id, text, buttons=[])
        db.mark_auction_processed(timer['id'])
    except Exception:
        logger.exception("Failed to edit group message for cancellation")


def handle_client_cancel(user: User, db) -> bool:
    """Отмена последнего активного заказа клиентом"""
    order = db.get_latest_active_order(user.phone)
    if not order:
        return False

    order_id = order.get('order_id')
    service_type = order.get('service_type')
    status = order.get('status')

    cancel_text = "❌ *ЗАКАЗ ОТМЕНЁН*\n\nКлиент отменил заказ."

    # Такси: отдельная логика комиссии и уведомления водителя
    if service_type == config.SERVICE_TAXI:
        if status in (config.ORDER_STATUS_PENDING, config.ORDER_STATUS_AUCTION, config.ORDER_STATUS_URGENT):
            db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED)
            _cancel_order_in_group(order_id, config.SERVICE_TAXI, db, cancel_text)
        else:
            db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED)
            # Уведомляем водителя и возвращаем комиссию
            driver_id = order.get('driver_id')
            commission = float(order.get('driver_commission') or _runtime_setting("taxi_commission", config.TAXI_COMMISSION))
            if driver_id:
                if commission > 0:
                    db.update_driver_balance(driver_id, commission, reason=f"Client cancel taxi {order_id}")
                send_telegram_private(driver_id, "❌ Заказ отменён клиентом. Комиссия не списана.")

            # Обновляем сообщение в группе (если уже было «ЗАКАЗ ЗАБРАН»)
            _cancel_order_in_group(order_id, 'taxi_accepted', db, cancel_text)

        send_whatsapp(user.phone, "❌ Заказ жокко чыгарылды.")
        db.log_transaction("CLIENT_CANCEL_TAXI", user.phone, order_id)
        return True

    # Для остальных сервисов — просто отмена и редактирование сообщения в группе
    db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED)
    _cancel_order_in_group(order_id, service_type, db, cancel_text)

    # Для кафе — уведомить принявшее кафе в Telegram + вернуть баланс если уже списали
    if service_type == config.SERVICE_CAFE:
        provider_id = order.get('provider_id')
        if provider_id:
            refund_msg = ""
            if status == config.ORDER_STATUS_READY:
                order_amount = order.get('price_total', 0) or 1000
                _, new_balance = db.add_cafe_balance(
                    provider_id,
                    round(order_amount * _runtime_setting('cafe_commission_percent', config.CAFE_COMMISSION_PERCENT) / 100, 2),
                    f"Возврат комиссии: клиент отменил заказ #{order_id}"
                )
                refund_msg = f"\n💳 Комиссия возвращена. Баланс: *{new_balance:.0f} сом*"
            send_telegram_private(provider_id, f"❌ *Заказ #{order_id} отменён клиентом.*{refund_msg}")

    send_whatsapp(user.phone, "❌ Заказ отменён.")
    db.log_transaction("CLIENT_CANCEL_ORDER", user.phone, order_id)
    return True


# =============================================================================
# WHATSAPP WEBHOOK HANDLER
# =============================================================================

def extract_whatsapp_queue_metadata(payload: dict) -> dict:
    """Извлечь стабильные поля для очереди входящих WhatsApp webhook."""
    metadata = {
        "provider": "unknown",
        "sender_phone": None,
        "external_message_id": None,
    }

    if not isinstance(payload, dict):
        return metadata

    if payload.get("object") == "whatsapp_business_account":
        entry = (payload.get("entry") or [{}])[0]
        changes = (entry.get("changes") or [{}])[0]
        value = changes.get("value") or {}
        messages = value.get("messages") or [{}]
        metadata["provider"] = "cloud"
        metadata["sender_phone"] = "".join(ch for ch in str(messages[0].get("from") or "") if ch.isdigit()) or None
        metadata["external_message_id"] = (messages[0].get("id") or "").strip() or None
        return metadata

    type_webhook = (payload.get("typeWebhook") or "").strip()
    if type_webhook:
        metadata["provider"] = type_webhook
    sender_data = payload.get("senderData") or {}
    message_data = payload.get("messageData") or {}
    sender_phone, _, _ = _extract_green_sender(sender_data)
    metadata["sender_phone"] = sender_phone or None
    external_message_id = (
        payload.get("idMessage")
        or message_data.get("idMessage")
        or (message_data.get("messageData") or {}).get("idMessage")
        or (message_data.get("textMessageData") or {}).get("idMessage")
        or ""
    )
    metadata["external_message_id"] = str(external_message_id).strip() or None
    return metadata


def handle_whatsapp(request_json: dict = None, form_values=None):
    """Главная функция обработки сообщений от Клиента"""
    try:
        incoming_msg = ''
        sender_phone = ''
        media_url = ''
        media_type = ''
        button_response = ''
        type_message = ''
        wa_message_id = ''

        data = request_json if request_json is not None else None
        if data is None and request.is_json:
            data = request.get_json()

        # 1. Попытка парсинга как JSON (Cloud API или Green API)
        if isinstance(data, dict):

            # --- WhatsApp Cloud API (Meta Graph API) ---
            if data.get('object') == 'whatsapp_business_account':
                entry = (data.get('entry') or [{}])[0]
                changes = (entry.get('changes') or [{}])[0]
                value = changes.get('value') or {}

                # Только статусы доставки — игнорируем
                if 'statuses' in value and 'messages' not in value:
                    return jsonify({"status": "ignored"}), 200

                if 'messages' not in value:
                    return jsonify({"status": "ignored"}), 200

                sender_phone, sender_kind, raw_sender = _extract_cloud_sender(value)
                incoming_msg, media_url, media_type, button_response, type_message = _extract_cloud_message_payload(value)
                wa_message_id = (value.get("messages") or [{}])[0].get("id") or ""

                logger.info(f"Cloud webhook type={type_message} sender={sender_phone}")

                if not sender_phone:
                    logger.warning(f"Cloud webhook skipped: empty sender")
                    return jsonify({"status": "ignored"}), 200

                if not incoming_msg and not media_url and not button_response:
                    logger.info(f"Cloud webhook ignored: empty payload type={type_message}")
                    return jsonify({"status": "ignored"}), 200

            # --- Green API ---
            else:
                type_webhook = data.get('typeWebhook', '')

                if type_webhook == 'incomingCall':
                    return jsonify({"status": "ignored"}), 200

                if type_webhook == 'incomingMessageReceived':
                    sender_data = data.get('senderData', {})
                    message_data = data.get('messageData', {})

                    sender_phone, sender_kind, raw_sender = _extract_green_sender(sender_data)
                    incoming_msg, media_url, media_type, button_response, type_message = _extract_green_message_payload(message_data)

                    logger.info(
                        f"Green webhook type={type_message} sender_kind={sender_kind} sender={raw_sender}"
                    )
                    wa_message_id = (
                        data.get("idMessage")
                        or message_data.get("idMessage")
                        or (message_data.get("messageData") or {}).get("idMessage")
                        or (message_data.get("textMessageData") or {}).get("idMessage")
                        or ""
                    )
                    wa_message_id = str(wa_message_id).strip() if wa_message_id else ""

                    if not sender_phone:
                        logger.warning(f"Green webhook skipped: empty sender ({raw_sender})")
                        return jsonify({"status": "ignored"}), 200

                    if not incoming_msg and not media_url and not button_response:
                        logger.info(f"Green webhook ignored: empty payload type={type_message}")
                        return jsonify({"status": "ignored"}), 200

                elif type_webhook == 'outgoingMessageStatus':
                    return jsonify({"status": "ignored"}), 200

        # 2. Попытка парсинга как Form Data (Twilio)
        if not sender_phone:
            values = form_values if form_values is not None else (request.values if has_request_context() else {})
            incoming_msg = (values.get('Body', '') or '').strip()
            sender_phone = (values.get('From', '') or '').replace('whatsapp:', '')
            media_url = (values.get('MediaUrl0', '') or '')
            media_type = (values.get('MediaContentType0', '') or '')
            button_response = (values.get('ButtonResponse', '') or '')
            wa_message_id = (values.get('MessageSid', '') or '').strip()

        if not sender_phone:
            return jsonify({"status": "ignored"}), 200

        logger.info(f"Received from {sender_phone}: {incoming_msg[:50]}...")

        db = get_db()

        # Логируем входящее сообщение в БД (для админки чатов)
        if sender_phone and (incoming_msg or button_response or media_url):
            try:
                log_body = incoming_msg or (
                    f"Нажал кнопку: {button_response}" if button_response else f"[медиа: {media_type or 'файл'}]"
                )
                saved_incoming = db.save_message(
                    phone=sender_phone,
                    direction='in',
                    body=log_body,
                    msg_type=type_message or 'text',
                    wa_message_id=wa_message_id or None,
                    button_id=button_response or None,
                    media_url=media_url or None,
                )
                # Идемпотентность: если это дубль того же сообщения (same phone + wa_message_id),
                # прекращаем обработку до создания заказа.
                if wa_message_id and saved_incoming is False:
                    logger.info(
                        "Duplicate incoming message ignored phone=%s wa_message_id=%s",
                        sender_phone, wa_message_id
                    )
                    return jsonify({"status": "duplicate_ignored"}), 200
            except Exception as _log_err:
                logger.warning(f"Failed to log incoming message: {_log_err}")

        # Получаем или создаем пользователя
        user = db.get_user(sender_phone)
        
        if not user:
            logger.error(f"Failed to get/create user: {sender_phone}")
            return jsonify({"status": "error"}), 500

        # Проверка блокировки
        block_status = db.get_block_status(sender_phone)
        if block_status['is_blocked']:
            send_whatsapp(sender_phone, config.BLOCKED_MESSAGE.format(
                support_phone=config.SUPPORT_PHONE
            ))
            return jsonify({"status": "ok"}), 200

        type_message_lower = (type_message or "").lower()
        is_voice_message = (
            (media_type and media_type.lower().startswith("audio/"))
            or type_message_lower in ("audiomessage", "pttmessage", "voicemessage", "voicenotemessage", "audio", "voice")
            or "audio" in type_message_lower
            or "voice" in type_message_lower
            or "ptt" in type_message_lower
        )

        # Обработка голосового сообщения
        if is_voice_message and media_url:
            safe_media_ref = "cloud_media" if media_url.startswith("cloud_media:") else (media_url[:80] + "...")
            logger.info(
                "Processing voice from %s type=%s mime=%s url=%s",
                sender_phone,
                type_message,
                media_type or "-",
                safe_media_ref,
            )
            incoming_msg = speech_to_text(media_url)
            if _is_bad_voice_transcription(incoming_msg):
                send_whatsapp(
                    sender_phone,
                    "Не расслышал голосовое. Напишите текстом или отправьте голосовое ещё раз, пожалуйста."
                )
                return jsonify({"status": "ok"}), 200
            incoming_msg = _enhance_voice_transcript(incoming_msg)
        
        # Обработка фото (сохраняем URL)
        if media_type and media_type.startswith('image/'):
            user.set_temp_data('media_url', media_url)
            user.set_temp_data('media_type', media_type)
        
        # Обработка кнопок (если есть)
        if button_response:
            pending_result = _handle_flow_switch_pending(user, button_response, db)
            if pending_result:
                return pending_result
            _reset_unknown_fallback(user)
            return handle_button_response(user, button_response, db)

        pending_result = _handle_flow_switch_pending(user, incoming_msg, db)
        if pending_result:
            return pending_result
        
        # === ROUTING ===

        if user.current_state == config.STATE_MED_EJE_MENU:
            return handle_med_eje_menu(user, incoming_msg, db)
        
        # Проверка на отмену (в любом состоянии)
        msg_lower = incoming_msg.lower().strip()
        if _is_cancellation(incoming_msg):
            logger.info(f"User {sender_phone} cancelled order in state {user.current_state}")
            cancelled = handle_client_cancel(user, db)
            user.set_state(config.STATE_IDLE)
            user.clear_temp_data()
            _reset_unknown_fallback(user)
            if not cancelled:
                send_order_cancelled_with_main_menu(user.phone)
            return jsonify({"status": "ok"}), 200

        # Запрос тех поддержки (в любом состоянии)
        if _is_support_request(msg_lower):
            send_whatsapp(user.phone, config.SUPPORT_TO_CLIENT.format(
                support_phone=config.SUPPORT_PHONE
            ))
            if config.SUPPORT_TELEGRAM_ID:
                send_telegram_private(config.SUPPORT_TELEGRAM_ID, config.SUPPORT_TO_OPERATOR.format(
                    client_phone=user.phone
                ))
            return jsonify({"status": "ok"}), 200

        web_order_result = _handle_web_order_code(user, incoming_msg, db)
        if web_order_result:
            return web_order_result

        if _is_computer_service_request(incoming_msg):
            return _send_specialist_request(
                user,
                config.COMPUTER_SERVICES_MESSAGE,
                "Компьютерные услуги",
            )

        if _is_med_eje_request(incoming_msg):
            return _show_med_eje_menu(user)

        if user.current_state != config.STATE_IDLE:
            _reset_unknown_fallback(user)
            switch_prompt_result = _maybe_prompt_flow_switch(user, incoming_msg)
            if switch_prompt_result:
                return switch_prompt_result

        if user.current_state == config.STATE_TAXI_REORDER_CHOICE:
            return handle_taxi_reorder_choice(user, incoming_msg, db)

        if user.current_state == config.STATE_PHARMACY_REORDER_CHOICE:
            return handle_pharmacy_reorder_choice(user, incoming_msg, db)

        if user.current_state == config.STATE_IDLE:
            return handle_idle_state(user, incoming_msg, db)
        elif user.current_state == config.STATE_MED_EJE_MENU:
            return handle_med_eje_menu(user, incoming_msg, db)
        
        # Подтверждение заказа (универсальное)
        elif user.current_state == config.STATE_CONFIRM_ORDER:
            return handle_confirm_order(user, incoming_msg, db)
        
        # Кафе
        elif user.current_state == config.STATE_CAFE_ORDER:
            return handle_cafe_order_details(user, incoming_msg, db)
        elif user.current_state == config.STATE_CAFE_ADDRESS:
            return handle_cafe_address(user, incoming_msg, db)
        
        # Магазин
        elif user.current_state == config.STATE_SHOP_LIST:
            return handle_shop_list(user, incoming_msg, db)
        elif user.current_state == config.STATE_SHOP_ADDRESS:
            return handle_shop_address(user, incoming_msg, db)
        
        # Аптека
        elif user.current_state == config.STATE_PHARMACY_WAIT_RX:
            return handle_pharmacy_request(user, incoming_msg, media_url, db)
        elif user.current_state == config.STATE_PHARMACY_ADDRESS:
            return handle_pharmacy_delivery_address(user, incoming_msg, db)
        
        # Такси
        elif user.current_state == config.STATE_TAXI_ROUTE:
            return handle_taxi_route(
                user,
                incoming_msg,
                db,
                is_voice_input=is_voice_message
            )
        # Веб-заказ меню
        elif user.current_state == config.STATE_WEB_ORDER_ADDRESS:
            return handle_web_order_address(user, incoming_msg, db)
        
        # Портер
        elif user.current_state == config.STATE_PORTER_CARGO_TYPE:
            return handle_porter_cargo_type(user, incoming_msg, db)
        elif user.current_state == config.STATE_PORTER_ROUTE:
            return handle_porter_route(user, incoming_msg, db)
        
        # Муравей
        elif user.current_state == config.STATE_ANT_ROUTE:
            return handle_ant_route(user, incoming_msg, db)
        
        # Неизвестное состояние
        else:
            user.set_state(config.STATE_IDLE)
            send_whatsapp(sender_phone, config.WELCOME_MESSAGE)
            return jsonify({"status": "ok"}), 200
            
    except Exception as e:
        logger.exception("Error handling WhatsApp webhook")
        return jsonify({"status": "error", "message": str(e)}), 500


def process_whatsapp_webhook_queue(limit: int = 20, stale_after_seconds: int = 300) -> int:
    """Обработать webhook-и WhatsApp, сохранённые в БД-очереди."""
    db = get_db()
    entries = db.claim_whatsapp_webhooks(limit=limit, stale_after_seconds=stale_after_seconds)
    processed = 0

    for entry in entries:
        queue_id = entry["id"]
        payload = entry.get("payload_json") or {}
        try:
            result = handle_whatsapp(request_json=payload)
            status_code = 200
            if isinstance(result, tuple) and len(result) >= 2:
                status_code = int(result[1])
            elif hasattr(result, "status_code"):
                status_code = int(result.status_code)

            if status_code >= 500:
                attempts = int(entry.get("attempts") or 1)
                delay_seconds = min(300, 15 * (2 ** min(max(attempts - 1, 0), 4)))
                db.mark_whatsapp_webhook_retry(
                    queue_id,
                    last_error=f"Handler returned status={status_code}",
                    delay_seconds=delay_seconds,
                )
                continue

            db.mark_whatsapp_webhook_processed(queue_id)
            processed += 1
        except Exception as exc:
            attempts = int(entry.get("attempts") or 1)
            delay_seconds = min(300, 15 * (2 ** min(max(attempts - 1, 0), 4)))
            db.mark_whatsapp_webhook_retry(
                queue_id,
                last_error=str(exc),
                delay_seconds=delay_seconds,
            )
            logger.exception("Error processing WhatsApp webhook queue id=%s", queue_id)

    return processed


# =============================================================================
# IDLE STATE HANDLER (с ИИ)
# =============================================================================

def handle_idle_state(user: User, message: str, db) -> tuple:
    """Обработка состояния ожидания — ИИ определяет намерение"""
    msg_lower = message.lower()
    msg_trim = message.strip()
    first_token = msg_trim.split()[0] if msg_trim else ""
    first_token_digits = "".join(ch for ch in first_token if ch.isdigit())

    service_intent_by_number = {
        "1": "cafe",
        "2": "shop",
        "3": "pharmacy",
        "4": "taxi",
        "5": "porter",
        "6": "ant",
        "7": "med_eje",
        "8": "computer",
        "9": "poputka",
        "10": "plumbing",
    }

    # Жёсткая проверка на «меню» / запрос еды, чтобы не путать с доставкой
    menu_keywords = ["меню", "мену", "мэню", "менью", "менйу", "миню", "менюу", "menu", "меню керек", "мага меню"]

    # Быстрые ключевые слова — пропускают OpenAI (экономят ~500мс)
    _QUICK_INTENTS = {
        "такси": "taxi", "taxi": "taxi", "машина": "taxi", "унаа": "taxi", "унаа керек": "taxi",
        "кафе": "cafe", "ашкана": "cafe", "тамак": "cafe", "еда": "cafe",
        "магазин": "shop", "дүкөн": "shop", "дукон": "shop", "продукты": "shop",
        "аптека": "pharmacy", "дарыкана": "pharmacy",
        "портер": "porter", "жүк": "porter",
        "муравей": "ant", "желмаян": "ant",
        "мед эже": "med_eje", "медеже": "med_eje", "мед помощь": "med_eje", "доктор": "med_eje",
        "компьютер": "computer", "компьютерные услуги": "computer", "ноутбук": "computer",
        "пк": "computer", "принтер": "computer", "интернет": "computer",
        "попутка": "poputka", "попутчик": "poputka", "попутка керек": "poputka",
        "сантехника": "plumbing", "сантехник": "plumbing", "сантех": "plumbing",
    }

    _EMPTY_NLU = {"from_address": None, "to_address": None, "order_details": None, "cargo_type": None}

    selected_intent = service_intent_by_number.get(msg_trim) or service_intent_by_number.get(first_token_digits)
    if selected_intent:
        nlu_result = {"intent": selected_intent, **_EMPTY_NLU}
    elif _is_computer_service_request(message):
        nlu_result = {"intent": "computer", **_EMPTY_NLU}
    elif msg_lower in _QUICK_INTENTS:
        nlu_result = {"intent": _QUICK_INTENTS[msg_lower], **_EMPTY_NLU}
        logger.info(f"Quick intent match (no NLU): {msg_lower!r} → {nlu_result['intent']}")
    elif _is_med_eje_request(message):
        nlu_result = {"intent": "med_eje", **_EMPTY_NLU}
    elif any(k in msg_lower for k in menu_keywords):
        nlu_result = {"intent": "cafe", **_EMPTY_NLU}
    elif _looks_like_greeting(message):
        nlu_result = {"intent": "greeting", **_EMPTY_NLU}
    else:
        # Используем ИИ для определения намерения
        nlu_result = parse_user_message(message)
    intent = nlu_result.get("intent", "unknown")
    
    logger.info(f"NLU intent for {user.phone}: {intent}")
    if intent in {"taxi", "cafe", "shop", "pharmacy", "porter", "ant", "med_eje", "computer", "poputka", "plumbing", "greeting"}:
        _reset_unknown_fallback(user)

    # === ТАКСИ ===
    if intent == "taxi":
        from_addr = _canonicalize_optional_address(nlu_result.get("from_address"))
        to_addr = _canonicalize_optional_address(nlu_result.get("to_address"))

        if from_addr and to_addr:
            # ИИ извлёк оба адреса — сразу к подтверждению
            user.set_temp_data('service_type', config.SERVICE_TAXI)
            user.set_temp_data('taxi_from', from_addr)
            user.set_temp_data('taxi_to', to_addr)
            user.set_temp_data('taxi_route', f"{from_addr} — {to_addr}")
            user.set_state(config.STATE_CONFIRM_ORDER)
            _send_confirm_with_buttons(user.phone, config.CONFIRM_TAXI.format(
                from_address=from_addr, to_address=to_addr,
            ))
        else:
            # Адреса не указаны — спрашиваем маршрут
            user.set_temp_data('taxi_from', '')
            user.set_temp_data('taxi_to', '')
            user.set_state(config.STATE_TAXI_ROUTE)
            send_whatsapp(user.phone, config.TAXI_PROMPT)

        return jsonify({"status": "ok"}), 200
    
    # === КАФЕ ===
    elif intent == "cafe":
        order_details_raw = nlu_result.get("order_details")
        order_details = order_details_raw if _is_concrete_order_details(order_details_raw, config.SERVICE_CAFE) else None
        
        if order_details:
            # ИИ извлёк детали заказа — спрашиваем адрес
            user.set_temp_data('cafe_order_details', order_details)
            user.set_state(config.STATE_CAFE_ADDRESS)
            send_whatsapp(user.phone, config.CAFE_ADDRESS_PROMPT)
        else:
            # Предлагаем меню или ручной ввод
            menu_msg = (
                f"🍔 *Тамак заказ кылуу*\n\n"
                f"📲 Менюну ачуу:\n{config.MENU_LINK}\n\n"
                f"Же тамактарды жазыңыз."
            )
            send_whatsapp_buttons(
                user.phone, menu_msg,
                [{"id": WHATSAPP_MAIN_MENU_BUTTON_ID, "text": "🏠 Артка"}],
                include_cancel=False
            )
            user.set_state(config.STATE_CAFE_ORDER)
        
        return jsonify({"status": "ok"}), 200
    
    # === МАГАЗИН ===
    elif intent == "shop":
        order_details_raw = nlu_result.get("order_details")
        order_details = order_details_raw if _is_concrete_order_details(order_details_raw, config.SERVICE_SHOP) else None
        
        if order_details:
            # ИИ извлёк список — спрашиваем адрес доставки
            user.set_temp_data('service_type', config.SERVICE_SHOP)
            user.set_temp_data('shop_list', order_details)
            user.set_state(config.STATE_SHOP_ADDRESS)
            send_whatsapp(user.phone, config.SHOP_ADDRESS_PROMPT)
        else:
            user.set_state(config.STATE_SHOP_LIST)
            send_whatsapp(user.phone, config.SHOP_PROMPT)
        
        return jsonify({"status": "ok"}), 200
    
    # === АПТЕКА ===
    elif intent == "pharmacy":
        order_details = nlu_result.get("order_details")
        user.set_temp_data('service_type', config.SERVICE_PHARMACY)

        if order_details:
            # ИИ извлёк название лекарства — сразу к подтверждению
            user.set_temp_data('pharmacy_request', order_details)
            user.set_state(config.STATE_CONFIRM_ORDER)
            _send_confirm_with_buttons(user.phone, config.CONFIRM_PHARMACY.format(order_details=order_details))
        else:
            # Название неизвестно — попросить написать
            user.set_state(config.STATE_PHARMACY_WAIT_RX)
            send_whatsapp(user.phone, config.PHARMACY_PROMPT)

        return jsonify({"status": "ok"}), 200
    
    # === ПОРТЕР ===
    elif intent == "porter":
        from_addr = _canonicalize_optional_address(nlu_result.get("from_address"))
        to_addr = _canonicalize_optional_address(nlu_result.get("to_address"))
        cargo = (nlu_result.get("cargo_type") or "").strip()

        if from_addr and to_addr:
            # Оба адреса есть — к подтверждению
            user.set_temp_data('service_type', config.SERVICE_PORTER)
            user.set_temp_data('porter_from', from_addr)
            user.set_temp_data('porter_to', to_addr)
            user.set_temp_data('porter_route', f"{from_addr} — {to_addr}")
            user.set_temp_data('porter_cargo', cargo)
            user.set_state(config.STATE_CONFIRM_ORDER)

            cargo_line = f"\n📦 *Жүк:* {cargo}" if cargo else ""
            confirm_msg = config.CONFIRM_PORTER.format(
                from_address=from_addr,
                to_address=to_addr,
                cargo_line=cargo_line,
            )
            _send_confirm_with_buttons(user.phone, confirm_msg)
        else:
            user.set_temp_data('porter_cargo', cargo)
            user.set_state(config.STATE_PORTER_ROUTE)
            send_whatsapp(user.phone, config.PORTER_ROUTE_PROMPT)

        return jsonify({"status": "ok"}), 200

    # === МУРАВЕЙ ===
    elif intent == "ant":
        from_addr = _canonicalize_optional_address(nlu_result.get("from_address"))
        to_addr = _canonicalize_optional_address(nlu_result.get("to_address"))
        cargo = (nlu_result.get("cargo_type") or "").strip()

        if from_addr and to_addr:
            # Оба адреса есть — к подтверждению
            user.set_temp_data('service_type', config.SERVICE_ANT)
            user.set_temp_data('ant_from', from_addr)
            user.set_temp_data('ant_to', to_addr)
            user.set_temp_data('ant_route', f"{from_addr} — {to_addr}")
            user.set_temp_data('ant_cargo', cargo)
            user.set_state(config.STATE_CONFIRM_ORDER)

            cargo_line = f"\n📦 *Жүк:* {cargo}" if cargo else ""
            confirm_msg = config.CONFIRM_ANT.format(
                from_address=from_addr,
                to_address=to_addr,
                cargo_line=cargo_line,
            )
            _send_confirm_with_buttons(user.phone, confirm_msg)
        else:
            user.set_temp_data('ant_cargo', cargo)
            user.set_state(config.STATE_ANT_ROUTE)
            send_whatsapp(user.phone, config.ANT_PROMPT)

        return jsonify({"status": "ok"}), 200

    # === МЕД ЭЖЕ ===
    elif intent == "med_eje":
        return _show_med_eje_menu(user)

    # === КОМПЬЮТЕРНЫЕ УСЛУГИ ===
    elif intent == "computer":
        return _send_specialist_request(
            user,
            config.COMPUTER_SERVICES_MESSAGE.format(
                support_phone=config.SUPPORT_PHONE,
            ),
            "Компьютерные услуги",
        )

    # === ПОПУТКА ===
    elif intent == "poputka":
        return _send_poputka_list(user, db)

    # === САНТЕХНИКА ===
    elif intent == "plumbing":
        return _send_specialist_request(
            user,
            config.PLUMBING_MESSAGE.format(
                support_phone=config.SUPPORT_PHONE,
            ),
            "Сантехника",
        )
    
    # === ТЕХ ПОДДЕРЖКА ===
    elif intent == "support":
        send_whatsapp(user.phone, config.SUPPORT_TO_CLIENT.format(
            support_phone=config.SUPPORT_PHONE
        ))
        if config.SUPPORT_TELEGRAM_ID:
            send_telegram_private(config.SUPPORT_TELEGRAM_ID, config.SUPPORT_TO_OPERATOR.format(
                client_phone=user.phone
            ))
        return jsonify({"status": "ok"}), 200

    # === ПРИВЕТСТВИЕ или НЕИЗВЕСТНОЕ ===
    elif intent == "greeting" or _looks_like_greeting(message):
        _reset_unknown_fallback(user)
        send_whatsapp(user.phone, config.WELCOME_MESSAGE)
        db.update_last_welcome(user.phone)
        return jsonify({"status": "ok"}), 200

    else:
        return _handle_unknown_fallback(user, message, nlu_result.get("fallback_reply"))


# =============================================================================
# UNIVERSAL CONFIRM ORDER HANDLER
# =============================================================================

_FAST_CONFIRM_YES = frozenset({
    "да", "ооба", "оа", "ok", "ок", "yes", "ага", "жакшы", "макул", "майли", "хоп", "ха",
    "конечно", "верно", "правильно", "хорошо", "албетте", "мм", "ыы",
})
_FAST_CONFIRM_NO = frozenset({
    "нет", "жок", "no", "cancel", "отмена", "жо", "жог",
})


def handle_confirm_order(user: User, message: str, db) -> tuple:
    """Универсальная обработка подтверждения заказа (с ИИ)"""
    msg_lower = message.lower().strip()

    # Быстрый путь для простых да/нет — пропускаем NLU (~500мс экономии)
    if msg_lower in _FAST_CONFIRM_YES:
        confirmation = {"confirmed": True, "is_correction": False,
                        "corrected_from": None, "corrected_to": None, "corrected_details": None}
    elif msg_lower in _FAST_CONFIRM_NO:
        confirmation = {"confirmed": False, "is_correction": False,
                        "corrected_from": None, "corrected_to": None, "corrected_details": None}
    else:
        # Только для сложных случаев (исправление адреса и т.п.) — вызываем NLU
        confirmation = parse_confirmation(message)
    
    service_type = user.get_temp_data('service_type', '')
    
    # Если пользователь исправляет данные
    if confirmation.get("is_correction"):
        return _handle_correction(user, confirmation, service_type)
    
    # Если подтвердил
    if confirmation.get("confirmed"):
        if service_type == config.SERVICE_TAXI:
            return _submit_taxi_order(user, db)
        elif service_type == config.SERVICE_CAFE:
            return _submit_cafe_order(user, db)
        elif service_type == config.SERVICE_SHOP:
            return _submit_shop_order(user, db)
        elif service_type == config.SERVICE_PHARMACY:
            return _submit_pharmacy_order(user, db)
        elif service_type == config.SERVICE_PORTER:
            return _submit_porter_order(user, db)
        elif service_type == config.SERVICE_ANT:
            return _submit_ant_order(user, db)
        else:
            # Неизвестный тип — сбрасываем
            user.set_state(config.STATE_IDLE)
            user.clear_temp_data()
            send_whatsapp(user.phone, config.WELCOME_MESSAGE)
            return jsonify({"status": "ok"}), 200
    
    # Если отменил
    else:
        user.set_state(config.STATE_IDLE)
        user.clear_temp_data()
        send_order_cancelled_with_main_menu(user.phone)
        return jsonify({"status": "ok"}), 200


def _handle_correction(user: User, confirmation: dict, service_type: str) -> tuple:
    """Обработка исправления данных пользователем"""
    
    if service_type == config.SERVICE_TAXI:
        if confirmation.get("corrected_from"):
            user.set_temp_data('taxi_from', _canonicalize_address_value(confirmation["corrected_from"]))
        if confirmation.get("corrected_to"):
            user.set_temp_data('taxi_to', _canonicalize_address_value(confirmation["corrected_to"]))
        
        from_addr = user.get_temp_data('taxi_from', '')
        to_addr = user.get_temp_data('taxi_to', '')
        user.set_temp_data('taxi_route', f"{from_addr} — {to_addr}")
        
        confirm_msg = config.CONFIRM_TAXI.format(
            from_address=from_addr,
            to_address=to_addr
        )
        _send_confirm_with_buttons(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_CAFE:
        if confirmation.get("corrected_details"):
            user.set_temp_data('cafe_order_details', confirmation["corrected_details"])
        if confirmation.get("corrected_to"):
            user.set_temp_data('cafe_address', _canonicalize_address_value(confirmation["corrected_to"]))
        
        order_details = user.get_temp_data('cafe_order_details', '')
        address = user.get_temp_data('cafe_address', '')
        
        confirm_msg = config.CONFIRM_CAFE.format(
            order_details=order_details,
            address=address
        )
        _send_confirm_with_buttons(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_SHOP:
        if confirmation.get("corrected_details"):
            user.set_temp_data('shop_list', confirmation["corrected_details"])
        if confirmation.get("corrected_to"):
            user.set_temp_data('shop_address', _canonicalize_address_value(confirmation["corrected_to"]))
        
        order_details = user.get_temp_data('shop_list', '')
        address = user.get_temp_data('shop_address', '')
        confirm_msg = config.CONFIRM_SHOP.format(order_details=order_details, address=address)
        _send_confirm_with_buttons(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_PHARMACY:
        if confirmation.get("corrected_details"):
            user.set_temp_data('pharmacy_request', confirmation["corrected_details"])
        
        order_details = user.get_temp_data('pharmacy_request', '')
        confirm_msg = config.CONFIRM_PHARMACY.format(order_details=order_details)
        _send_confirm_with_buttons(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_PORTER:
        if confirmation.get("corrected_from"):
            user.set_temp_data('porter_from', _canonicalize_address_value(confirmation["corrected_from"]))
        if confirmation.get("corrected_to"):
            user.set_temp_data('porter_to', _canonicalize_address_value(confirmation["corrected_to"]))

        from_addr = user.get_temp_data('porter_from', '')
        to_addr = user.get_temp_data('porter_to', '')
        user.set_temp_data('porter_route', f"{from_addr} — {to_addr}")

        cargo = (user.get_temp_data('porter_cargo') or "").strip()
        cargo_line = f"\n📦 *Жүк:* {cargo}" if cargo else ""
        confirm_msg = config.CONFIRM_PORTER.format(
            from_address=from_addr,
            to_address=to_addr,
            cargo_line=cargo_line,
        )
        _send_confirm_with_buttons(user.phone, confirm_msg)

    elif service_type == config.SERVICE_ANT:
        if confirmation.get("corrected_from"):
            user.set_temp_data('ant_from', _canonicalize_address_value(confirmation["corrected_from"]))
        if confirmation.get("corrected_to"):
            user.set_temp_data('ant_to', _canonicalize_address_value(confirmation["corrected_to"]))

        from_addr = user.get_temp_data('ant_from', '')
        to_addr = user.get_temp_data('ant_to', '')
        user.set_temp_data('ant_route', f"{from_addr} — {to_addr}")

        cargo = (user.get_temp_data('ant_cargo') or "").strip()
        cargo_line = f"\n📦 *Жүк:* {cargo}" if cargo else ""
        confirm_msg = config.CONFIRM_ANT.format(
            from_address=from_addr,
            to_address=to_addr,
            cargo_line=cargo_line,
        )
        _send_confirm_with_buttons(user.phone, confirm_msg)
    
    return jsonify({"status": "ok"}), 200


# =============================================================================
# ORDER SUBMISSION FUNCTIONS
# =============================================================================

def _submit_taxi_order(user: User, db) -> tuple:
    """Отправка заказа такси"""
    route = user.get_temp_data('taxi_route', '')

    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_TAXI,
        details=route,
        price=0
    )

    # Комиссия всегда фиксированная
    commission_info = f"💰 Комиссия: {_runtime_setting('taxi_commission', config.TAXI_COMMISSION)} сом"

    price_display = "договорная"
    
    telegram_msg = config.TAXI_ORDER_TELEGRAM.format(
        route=route,
        price=price_display,
        commission_info=commission_info,
    )
    
    buttons = [{
        "text": "🚖 Взять заказ",
        "callback": f"taxi_take_{order_id}"
    }]
    
    dispatch_telegram_group_notification(
        config.GROUP_TAXI_ID,
        telegram_msg,
        buttons,
        order_id=order_id,
        service_type=config.SERVICE_TAXI,
        timeout_seconds=int(_runtime_setting("taxi_response_timeout", config.TAXI_RESPONSE_TIMEOUT)),
    )
    
    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    
    send_whatsapp(user.phone, config.TAXI_PRICE_INFO)
    
    db.log_transaction("TAXI_ORDER_CREATED", user.phone, order_id)
    
    return jsonify({"status": "ok", "order_id": order_id}), 200


def _submit_cafe_order(user: User, db) -> tuple:
    """Отправка заказа в кафе (включая подтверждение веб-заказа)"""
    order_details = user.get_temp_data('cafe_order_details', '')
    address = user.get_temp_data('cafe_address', '')
    web_order_code = user.get_temp_data('web_order_code')
    
    # Если это подтверждение веб-заказа - получаем данные из web_order
    if web_order_code:
        web_order = db.get_web_order(web_order_code)
        if web_order:
            # Используем детали из web_order
            items = web_order['items_json']
            order_details = "\n".join([f"• {item['name']} x{item['count']}" for item in items])
            cafe_name = web_order['cafe_name']
            total_price = web_order['total_price']
            
            # Обновляем web_order
            db.update_web_order_status(web_order_code, 'CONFIRMED', client_phone=user.phone, address=address)
    
    # Создаем основной заказ
    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_CAFE,
        details=order_details,
        address=address,
        payment_method=config.PAYMENT_CASH
    )
    
    # Если был web_order - сохраняем связь
    if web_order_code:
        db.update_web_order_status(web_order_code, 'CONFIRMED', address=str(order_id))
    
    commission_info = f"💰 Комиссия: {_runtime_setting('cafe_commission_percent', config.CAFE_COMMISSION_PERCENT)}%"
    
    telegram_msg = config.CAFE_ORDER_TELEGRAM.format(
        order_id=order_id,
        order_details=order_details[:200],
        address=address,
        payment=config.PAYMENT_METHODS.get(config.PAYMENT_CASH, config.PAYMENT_CASH),
        phone=user.phone
    ) + f"\n\n{commission_info}"
    
    buttons = [
        {"text": "✅ Принять (2 мин)", "callback": f"cafe_accept_{order_id}"},
        {"text": "❌ Отказать", "callback": f"cafe_decline_{order_id}"}
    ]
    
    dispatch_telegram_group_notification(
        config.GROUP_CAFE_ID,
        telegram_msg,
        buttons,
        order_id=order_id,
        service_type=config.SERVICE_CAFE,
        timeout_seconds=int(_runtime_setting("cafe_auction_timeout", config.CAFE_AUCTION_TIMEOUT)),
    )
    
    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    
    send_whatsapp(user.phone, config.CAFE_ORDER_SENT)
    
    db.log_transaction("CAFE_ORDER_CREATED", user.phone, order_id, details=order_details[:100])
    
    return jsonify({"status": "ok", "order_id": order_id}), 200


def _submit_shop_order(user: User, db) -> tuple:
    """Отправка заказа из магазина — направляем в группу такси"""
    shop_list = user.get_temp_data('shop_list', '')
    shop_address = user.get_temp_data('shop_address', '')

    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_SHOP,
        details=shop_list,
        address=shop_address
    )

    address_line = f"\n\ud83d\udccd *Куда доставить:* {shop_address}" if shop_address else ""

    telegram_msg = f"""🛒 *ДОСТАВКА ИЗ МАГАЗИНА*

📋 *Список покупок:*
{shop_list}{address_line}

📞 *Клиент:* {user.phone}
💰 *За доставку:* {_runtime_setting('shop_delivery_fee', config.SHOP_DELIVERY_FEE)} сом
💰 *Комиссия:* {_runtime_setting('taxi_commission', config.TAXI_COMMISSION)} сом

Нужно купить и доставить клиенту."""


    buttons = [{
        "text": "🚖 Взять заказ",
        "callback": f"taxi_take_{order_id}"
    }]

    dispatch_telegram_group_notification(
        config.GROUP_TAXI_ID,
        telegram_msg,
        buttons,
        order_id=order_id,
        service_type=config.SERVICE_SHOP,
        timeout_seconds=int(_runtime_setting("taxi_response_timeout", config.TAXI_RESPONSE_TIMEOUT)),
    )

    send_whatsapp(user.phone, config.ORDER_SENT_GENERIC)
    db.log_transaction("SHOP_ORDER_CREATED", user.phone, order_id)

    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()

    return jsonify({"status": "ok"}), 200


def _submit_pharmacy_order(user: User, db) -> tuple:
    """Отправка заказа в аптеку"""
    request_text = user.get_temp_data('pharmacy_request', '')
    media_url = user.get_temp_data('pharmacy_media_url', '')
    
    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_PHARMACY,
        details=request_text
    )
    
    telegram_msg = config.PHARMACY_ORDER_TELEGRAM.format(
        request=request_text[:200],
        phone=user.phone
    )
    
    buttons = [{
        "text": "💊 У нас есть (указать цену)",
        "callback": f"pharm_bid_{order_id}"
    }]
    dispatch_telegram_group_notification(
        config.GROUP_PHARMACY_ID,
        telegram_msg,
        buttons,
        order_id=order_id,
        service_type=config.SERVICE_PHARMACY,
        timeout_seconds=int(_runtime_setting("pharmacy_response_timeout", config.PHARMACY_RESPONSE_TIMEOUT)),
        photo_url=media_url or None,
    )

    user.set_state(config.STATE_PHARMACY_WAIT_PRICE)
    user.set_temp_data('pharmacy_order_id', order_id)
    
    send_whatsapp(user.phone, config.PHARMACY_SEARCHING)
    
    db.log_transaction("PHARMACY_ORDER_CREATED", user.phone, order_id)
    
    return jsonify({"status": "ok"}), 200


def _submit_porter_order(user: User, db) -> tuple:
    """Отправка заказа портера"""
    route = user.get_temp_data('porter_route', '')
    cargo = (user.get_temp_data('porter_cargo') or "").strip()

    details = f"{route} | Жүк: {cargo}" if cargo else route

    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_PORTER,
        details=details,
    )

    cargo_line = f"\n📦 *Жүк:* {cargo}" if cargo else ""
    telegram_msg = config.PORTER_ORDER_TELEGRAM.format(
        route=route,
        cargo_line=cargo_line,
    )
    
    buttons = [{
        "text": "🚛 Взять груз",
        "callback": f"porter_take_{order_id}"
    }]

    dispatch_telegram_group_notification(
        config.GROUP_PORTER_ID,
        telegram_msg,
        buttons,
        order_id=order_id,
        service_type=config.SERVICE_PORTER,
        timeout_seconds=int(_runtime_setting("pending_order_auto_cancel_timeout", config.PENDING_ORDER_AUTO_CANCEL_TIMEOUT)),
    )

    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    
    send_whatsapp(user.phone, config.ORDER_SENT_GENERIC)
    
    db.log_transaction("PORTER_ORDER_CREATED", user.phone, order_id)
    
    return jsonify({"status": "ok"}), 200


def _submit_ant_order(user: User, db) -> tuple:
    """Отправка заказа муравья"""
    route = user.get_temp_data('ant_route', '')
    cargo = (user.get_temp_data('ant_cargo') or "").strip()

    details = f"{route} | Жүк: {cargo}" if cargo else route

    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_ANT,
        details=details
    )

    cargo_line = f"\n📦 *Жүк:* {cargo}" if cargo else ""
    telegram_msg = config.ANT_ORDER_TELEGRAM.format(
        route=route,
        cargo_line=cargo_line,
        phone=user.phone
    )
    
    buttons = [{
        "text": "🐜 Взять заказ",
        "callback": f"ant_take_{order_id}"
    }]

    dispatch_telegram_group_notification(
        config.GROUP_ANT_ID,
        telegram_msg,
        buttons,
        order_id=order_id,
        service_type=config.SERVICE_ANT,
        timeout_seconds=int(_runtime_setting("pending_order_auto_cancel_timeout", config.PENDING_ORDER_AUTO_CANCEL_TIMEOUT)),
    )

    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    
    send_whatsapp(user.phone, config.ORDER_SENT_GENERIC)
    
    db.log_transaction("ANT_ORDER_CREATED", user.phone, order_id)
    
    return jsonify({"status": "ok"}), 200


# =============================================================================
# CAFE FLOW (упрощённый — без оплаты)
# =============================================================================

def handle_cafe_order_details(user: User, message: str, db) -> tuple:
    """Обработка деталей заказа кафе"""
    user.set_temp_data('cafe_order_details', message)
    user.set_state(config.STATE_CAFE_ADDRESS)
    send_whatsapp(user.phone, config.CAFE_ADDRESS_PROMPT)
    return jsonify({"status": "ok"}), 200


def handle_cafe_address(user: User, message: str, db) -> tuple:
    """Обработка адреса доставки — переход к подтверждению"""
    address = _canonicalize_address_value(message)

    # Проверка на слишком общий адрес
    if _is_vague_address(address):
        send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
        return jsonify({"status": "ok"}), 200
    
    user.set_temp_data('cafe_address', address)
    user.set_temp_data('service_type', config.SERVICE_CAFE)
    
    order_details = user.get_temp_data('cafe_order_details', '')
    
    # Переход к подтверждению (без вопроса об оплате)
    user.set_state(config.STATE_CONFIRM_ORDER)
    confirm_msg = config.CONFIRM_CAFE.format(
        order_details=order_details,
        address=address
    )
    _send_confirm_with_buttons(user.phone, confirm_msg)
    
    return jsonify({"status": "ok"}), 200


def handle_web_order_address(user: User, message: str, db) -> tuple:
    """Обработка адреса для веб-заказа"""
    address = _canonicalize_address_value(message)

    # Validation if needed
    if len(address) < 3:
         send_whatsapp(user.phone, "Туура даректи жазыңыз:")
         return jsonify({"status": "ok"}), 200
         
    user.set_temp_data('cafe_address', address)
    
    # Update web order status/info
    code = user.get_temp_data('web_order_code')
    if code:
        db.update_web_order_status(code, 'ADDRESS_SET', client_phone=user.phone, address=address)
    
    # Proceed to confirmation
    details = user.get_temp_data('cafe_order_details', '')
    
    user.set_state(config.STATE_CONFIRM_ORDER)
    confirm_msg = config.CONFIRM_CAFE.format(
        order_details=details,
        address=address
    )
    _send_confirm_with_buttons(user.phone, confirm_msg)
    return jsonify({"status": "ok"}), 200


# =============================================================================
# SHOP FLOW (упрощённый)
# =============================================================================

def handle_shop_list(user: User, message: str, db) -> tuple:
    """Обработка списка покупок — переход к запросу адреса"""
    user.set_temp_data('shop_list', message)
    user.set_temp_data('service_type', config.SERVICE_SHOP)
    
    user.set_state(config.STATE_SHOP_ADDRESS)
    send_whatsapp(user.phone, config.SHOP_ADDRESS_PROMPT)
    
    return jsonify({"status": "ok"}), 200


def handle_shop_address(user: User, message: str, db) -> tuple:
    """Обработка адреса доставки для магазина — переход к подтверждению"""
    address = _canonicalize_address_value(message)

    # Проверка на слишком общий адрес
    if _is_vague_address(address):
        send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
        return jsonify({"status": "ok"}), 200

    user.set_temp_data('shop_address', address)

    shop_list = user.get_temp_data('shop_list', '')

    user.set_state(config.STATE_CONFIRM_ORDER)
    confirm_msg = config.CONFIRM_SHOP.format(
        order_details=shop_list,
        address=address
    )
    _send_confirm_with_buttons(user.phone, confirm_msg)

    return jsonify({"status": "ok"}), 200


# =============================================================================
# PHARMACY FLOW
# =============================================================================

def handle_pharmacy_request(user: User, message: str, media_url: str, db) -> tuple:
    """Обработка STATE_PHARMACY_WAIT_RX: ждём название лекарства"""
    if media_url:
        user.set_temp_data('pharmacy_media_url', media_url)
        medication = message.strip() if message and message.strip() else "(фото рецепта)"
    elif message and message.strip():
        medication = message.strip()
    else:
        send_whatsapp(user.phone, config.PHARMACY_PROMPT)
        return jsonify({"status": "ok"}), 200

    user.set_temp_data('pharmacy_request', medication)
    user.set_temp_data('service_type', config.SERVICE_PHARMACY)
    user.set_state(config.STATE_CONFIRM_ORDER)
    _send_confirm_with_buttons(user.phone, config.CONFIRM_PHARMACY.format(order_details=medication))
    return jsonify({"status": "ok"}), 200


def handle_pharmacy_delivery_address(user: User, message: str, db) -> tuple:
    """Получили адрес клиента после цены аптеки: сразу оформляем доставку."""
    address = _canonicalize_address_value((message or "").strip())
    if not address:
        send_whatsapp(user.phone, "📍 Жеткирүү дарегин жазыңыз.")
        return jsonify({"status": "ok"}), 200

    if _is_vague_address(address):
        send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
        return jsonify({"status": "ok"}), 200

    order_id = user.get_temp_data('pharmacy_order_id')
    pharmacy_id = user.get_temp_data('pharmacy_selected_pharmacy_id')
    pharmacy_name = user.get_temp_data('pharmacy_selected_pharmacy_name', 'Аптека')
    drug_price = float(user.get_temp_data('pharmacy_selected_price', 0) or 0)

    if not order_id or not pharmacy_id or drug_price <= 0:
        user.set_state(config.STATE_IDLE)
        user.clear_temp_data()
        send_whatsapp(user.phone, "❌ Заказ маалыматында ката. Кайра баштаңыз.")
        return jsonify({"status": "ok"}), 200

    order = db.get_order(order_id)
    if not order:
        user.set_state(config.STATE_IDLE)
        user.clear_temp_data()
        send_whatsapp(user.phone, "❌ Заказ табылган жок. Кайра баштаңыз.")
        return jsonify({"status": "ok"}), 200

    total_price = drug_price + _runtime_setting("pharmacy_delivery_fee", config.PHARMACY_DELIVERY_FEE) + _runtime_setting("taxi_pharmacy_commission", config.TAXI_PHARMACY_COMMISSION)

    # Записываем адрес, итоговую цену и переводим в готовность к доставке
    db.update_order_status(
        order_id,
        config.ORDER_STATUS_READY,
        provider_id=pharmacy_id,
        price=total_price,
        address=address
    )

    taxi_msg = f"""💊 *ЗАКАЗ АПТЕКА (ДОСТАВКА)*

🏥 *Забрать из:* {pharmacy_name}
📋 *Лекарство:* {order.get('details', '')}
💵 *Цена лекарства:* {int(drug_price)} сом
💰 *С клиента взять:* {int(total_price)} сом
📍 *Куда доставить:* {address}
📞 *Клиент:* {user.phone}"""

    buttons = [{
        "text": "🚖 Взять доставку",
        "callback": f"delivery_take_{order_id}"
    }]
    dispatch_telegram_group_notification(config.GROUP_TAXI_ID, taxi_msg, buttons)

    send_telegram_private(
        str(pharmacy_id),
        f"✅ Клиент оформил заказ #{order_id}.\nПодготовьте медикаменты — скоро приедет таксист."
    )

    send_whatsapp(
        user.phone,
        f"✅ Заказ оформлен.\n🚖 Ищем курьера для доставки из аптеки.\n💰 К оплате: {int(total_price)} сом."
    )

    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    db.log_transaction("PHARMACY_ADDRESS_CONFIRMED", user.phone, order_id, amount=total_price)

    return jsonify({"status": "ok"}), 200


# =============================================================================
# TAXI FLOW
# =============================================================================

def handle_pharmacy_reorder_choice(user: User, message: str, db) -> tuple:
    """Обработка ответа клиента после автоотмены аптеки: повторить заказ или нет."""
    msg_lower = (message or "").lower().strip()

    yes_words = {"да", "оа", "ооба", "yes", "1", "pharm_reorder_yes"}
    no_words = {"нет", "жок", "no", "2", "pharm_reorder_no"}

    if msg_lower in yes_words:
        request = (user.get_temp_data('pharmacy_reorder_request') or '').strip()
        user.clear_temp_data()
        if request:
            user.set_temp_data('service_type', config.SERVICE_PHARMACY)
            user.set_temp_data('pharmacy_request', request)
            user.set_state(config.STATE_CONFIRM_ORDER)
            _send_confirm_with_buttons(user.phone, config.CONFIRM_PHARMACY.format(order_details=request))
        else:
            user.set_state(config.STATE_PHARMACY_WAIT_RX)
            send_whatsapp(user.phone, config.PHARMACY_PROMPT)
        return jsonify({"status": "ok"}), 200

    if msg_lower in no_words:
        user.clear_temp_data()
        user.set_state(config.STATE_IDLE)
        send_whatsapp(user.phone, config.WELCOME_MESSAGE)
        return jsonify({"status": "ok"}), 200

    # Нераспознанный ответ — сбрасываем, показываем главное меню
    user.clear_temp_data()
    user.set_state(config.STATE_IDLE)
    send_whatsapp(user.phone, config.WELCOME_MESSAGE)
    return jsonify({"status": "ok"}), 200


def handle_taxi_reorder_choice(user: User, message: str, db) -> tuple:
    """Обработка ответа клиента после отмены водителем: повторить заказ или начать новый."""
    msg_lower = (message or "").lower().strip()

    yes_words = {"да", "оа", "ооба", "yes", "1", "btn_taxi_reorder_yes", "reorder_yes"}
    no_words = {"нет", "жок", "no", "2", "btn_taxi_reorder_no", "reorder_no"}

    if msg_lower in yes_words:
        route = (user.get_temp_data('taxi_reorder_route', '') or '').strip()
        if not route:
            user.clear_temp_data()
            user.set_temp_data('service_type', config.SERVICE_TAXI)
            user.set_temp_data('taxi_from', '')
            user.set_temp_data('taxi_to', '')
            user.set_state(config.STATE_TAXI_ROUTE)
            send_whatsapp(user.phone, config.TAXI_PROMPT)
            return jsonify({"status": "ok"}), 200

        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_temp_data('taxi_route', route)

        # Для совместимости с остальным flow заполняем откуда/куда если маршрут разделён.
        parts = [p.strip() for p in re.split(r"\s*[—-]\s*", route, maxsplit=1) if p.strip()]
        if len(parts) == 2:
            user.set_temp_data('taxi_from', parts[0])
            user.set_temp_data('taxi_to', parts[1])

        return _submit_taxi_order(user, db)

    if msg_lower in no_words:
        user.clear_temp_data()
        user.set_state(config.STATE_IDLE)
        send_whatsapp(user.phone, config.WELCOME_MESSAGE)
        return jsonify({"status": "ok"}), 200

    # Пользователь явно запросил новое такси (например, "мага такси керек") —
    # сбрасываем reorder-состояние и сразу запускаем новый флоу.
    if _extract_flow_keyword_intent(message) == config.SERVICE_TAXI:
        user.clear_temp_data()
        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_state(config.STATE_TAXI_ROUTE)
        send_whatsapp(user.phone, config.TAXI_PROMPT)
        return jsonify({"status": "ok"}), 200

    # Нераспознанный ответ — сбрасываем состояние и показываем главное меню
    user.clear_temp_data()
    user.set_state(config.STATE_IDLE)
    send_whatsapp(user.phone, config.WELCOME_MESSAGE)
    return jsonify({"status": "ok"}), 200


def handle_taxi_route(user: User, message: str, db, is_voice_input: bool = False) -> tuple:
    """Обработка маршрута такси: собираем откуда/куда до полной информации."""
    msg = message.strip()
    if not msg:
        send_whatsapp(user.phone, config.TAXI_PROMPT)
        return jsonify({"status": "ok"}), 200

    nlu_result = parse_user_message(msg)
    parsed_from = (_canonicalize_optional_address(nlu_result.get("from_address")) or "").strip()
    parsed_to = (_canonicalize_optional_address(nlu_result.get("to_address")) or "").strip()

    # Fallback: если пользователь написал маршрут через дефис
    if not parsed_from and not parsed_to:
        dash_split = re.split(r"\s*[—-]\s*", msg, maxsplit=1)
        if len(dash_split) == 2 and dash_split[0].strip() and dash_split[1].strip():
            parsed_from = _canonicalize_address_value(dash_split[0].strip())
            parsed_to = _canonicalize_address_value(dash_split[1].strip())

    current_from = _canonicalize_address_value((user.get_temp_data('taxi_from', '') or "").strip())
    current_to = _canonicalize_address_value((user.get_temp_data('taxi_to', '') or "").strip())

    def _ask_for_to():
        send_whatsapp(
            user.phone,
            "📍 *Кайда барабыз?*\n\nАкыркы даректи жазыңыз."
        )

    def _ask_for_from():
        send_whatsapp(
            user.phone,
            "📍 *Кайдан барабыз?*\n\nБаштапкы даректи жазыңыз."
        )

    def _go_to_price_choice(from_address: str, to_address: str):
        from_address = _canonicalize_address_value(from_address)
        to_address = _canonicalize_address_value(to_address)
        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_temp_data('taxi_from', from_address)
        user.set_temp_data('taxi_to', to_address)
        user.set_temp_data('taxi_route', f"{from_address} — {to_address}")
        user.set_state(config.STATE_CONFIRM_ORDER)
        _send_confirm_with_buttons(user.phone, config.CONFIRM_TAXI.format(
            from_address=from_address, to_address=to_address,
        ))
        return jsonify({"status": "ok"}), 200

    # Если сразу извлекли оба адреса
    if parsed_from and parsed_to:
        if _is_vague_address(parsed_from) or _is_vague_address(parsed_to):
            send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
            return jsonify({"status": "ok"}), 200
        if _addresses_equal(parsed_from, parsed_to):
            send_whatsapp(
                user.phone,
                "⚠️ *Кайдан* жана *кайда* дареги бирдей болуп калды.\n"
                "Маршрутту так жазыңыз: *Кайдан* жана *Кайда* өзүнчө."
            )
            return jsonify({"status": "ok"}), 200
        return _go_to_price_choice(parsed_from, parsed_to)

    # Для текстового ввода — пошаговый сбор адресов (как для голосового)
    if not is_voice_input and not current_from and not current_to:
        if parsed_from and not parsed_to:
            user.set_temp_data('service_type', config.SERVICE_TAXI)
            user.set_temp_data('taxi_from', parsed_from)
            _ask_for_to()
            return jsonify({"status": "ok"}), 200
        if parsed_to and not parsed_from:
            user.set_temp_data('service_type', config.SERVICE_TAXI)
            user.set_temp_data('taxi_to', parsed_to)
            _ask_for_from()
            return jsonify({"status": "ok"}), 200
        # ИИ ничего не распознал — считаем весь текст как адрес отправления
        single = _canonicalize_address_value(msg)
        if _is_vague_address(single):
            send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
            return jsonify({"status": "ok"}), 200
        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_temp_data('taxi_from', single)
        _ask_for_to()
        return jsonify({"status": "ok"}), 200

    # Для голосового ввода — пошаговый сбор недостающего адреса
    if not current_from and not current_to:
        single_addr = _canonicalize_address_value(parsed_from or parsed_to or msg)
        if _is_vague_address(single_addr):
            send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
            return jsonify({"status": "ok"}), 200

        # Если ИИ нашёл только "куда", то сначала просим "откуда"
        if parsed_to and not parsed_from:
            user.set_temp_data('service_type', config.SERVICE_TAXI)
            user.set_temp_data('taxi_to', single_addr)
            _ask_for_from()
            return jsonify({"status": "ok"}), 200

        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_temp_data('taxi_from', single_addr)
        _ask_for_to()
        return jsonify({"status": "ok"}), 200

    if current_from and not current_to:
        to_addr = _canonicalize_address_value(parsed_to or parsed_from or msg)
        if _is_vague_address(to_addr):
            send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
            return jsonify({"status": "ok"}), 200
        if _addresses_equal(current_from, to_addr):
            send_whatsapp(
                user.phone,
                "⚠️ Барчу дарек чыгуу дарегине дал келет.\n"
                "Башка *КАЙДА* дарегин жазыңыз."
            )
            return jsonify({"status": "ok"}), 200
        return _go_to_price_choice(current_from, to_addr)

    if current_to and not current_from:
        from_addr = _canonicalize_address_value(parsed_from or parsed_to or msg)
        if _is_vague_address(from_addr):
            send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
            return jsonify({"status": "ok"}), 200
        if _addresses_equal(from_addr, current_to):
            send_whatsapp(
                user.phone,
                "⚠️ Чыгуу дареги барчу дарегине дал келет.\n"
                "Башка *КАЙДАН* дарегин жазыңыз."
            )
            return jsonify({"status": "ok"}), 200
        return _go_to_price_choice(from_addr, current_to)

    if _addresses_equal(current_from, current_to):
        send_whatsapp(
            user.phone,
            "⚠️ Даректер азыр бирдей. Маршрутту кайра жазыңыз: *Кайдан* жана *Кайда*."
        )
        return jsonify({"status": "ok"}), 200

    return _go_to_price_choice(current_from, current_to)


def _extract_price(text: str) -> int | None:
    """Извлекает цену из текста: цифры или числительные (рус/кырг)."""
    nums = re.findall(r'\d+', text)
    if nums:
        return int(nums[0])

    t = text.lower().strip()
    t = re.sub(r'\bсом(ов)?\b', '', t)
    t = re.sub(r'\s+', ' ', t).strip()

    # Порядок важен: составные формы до компонентов
    WORDS = [
        ('тысяча', 1000), ('тысячи', 1000), ('тысяч', 1000), ('миң', 1000),
        ('девятьсот', 900), ('восемьсот', 800), ('семьсот', 700), ('шестьсот', 600),
        ('пятьсот', 500), ('четыреста', 400), ('триста', 300), ('двести', 200), ('сто', 100),
        ('беш жүз', 500), ('төрт жүз', 400), ('үч жүз', 300), ('эки жүз', 200), ('жүз', 100),
        ('девяносто', 90), ('восемьдесят', 80), ('семьдесят', 70), ('шестьдесят', 60),
        ('пятьдесят', 50), ('сорок', 40), ('тридцать', 30), ('двадцать', 20),
        ('токсон', 90), ('сексен', 80), ('жетимиш', 70), ('алтымыш', 60),
        ('элүү', 50), ('кырк', 40), ('отуз', 30), ('жыйырма', 20),
        ('девятнадцать', 19), ('восемнадцать', 18), ('семнадцать', 17), ('шестнадцать', 16),
        ('пятнадцать', 15), ('четырнадцать', 14), ('тринадцать', 13), ('двенадцать', 12),
        ('одиннадцать', 11), ('десять', 10),
        ('девять', 9), ('восемь', 8), ('семь', 7), ('шесть', 6), ('пять', 5),
        ('четыре', 4), ('три', 3), ('две', 2), ('два', 2), ('одна', 1), ('один', 1),
        ('тогуз', 9), ('сегиз', 8), ('жети', 7), ('алты', 6), ('беш', 5),
        ('төрт', 4), ('үч', 3), ('эки', 2), ('бир', 1), ('он', 10),
    ]

    total = 0
    found = False
    for word, val in WORDS:
        if word in t:
            total += val
            t = t.replace(word, '', 1)
            found = True

    return total if found and total > 0 else None


# =============================================================================
# PORTER FLOW
# =============================================================================

def handle_porter_cargo_type(user: User, message: str, db) -> tuple:
    """Перенаправляем в STATE_PORTER_ROUTE (тип груза больше не запрашивается)"""
    user.set_state(config.STATE_PORTER_ROUTE)
    return handle_porter_route(user, message, db)


def handle_porter_route(user: User, message: str, db) -> tuple:
    """Обработка маршрута портер — переход к подтверждению"""
    saved_from = _canonicalize_address_value(user.get_temp_data('porter_from_partial'))

    if saved_from:
        # Уже ждём куда — берём сообщение как to_addr напрямую, без NLU
        from_addr = saved_from
        to_addr = _canonicalize_address_value(message.strip())
        user.set_temp_data('porter_from_partial', None)
    else:
        # Первый ввод — парсим NLU
        nlu_result = parse_user_message(message)
        from_addr = _canonicalize_optional_address(nlu_result.get("from_address"))
        to_addr = _canonicalize_optional_address(nlu_result.get("to_address"))

        # Обновляем cargo если извлечён и ещё не сохранён
        nlu_cargo = (nlu_result.get("cargo_type") or "").strip()
        if nlu_cargo and not user.get_temp_data('porter_cargo'):
            user.set_temp_data('porter_cargo', nlu_cargo)

        if not from_addr:
            from_addr = _canonicalize_address_value(message)

        if not to_addr:
            # to не найден — сохраняем from и спрашиваем куда
            user.set_temp_data('porter_from_partial', from_addr)
            send_whatsapp(user.phone, "📍 Кайда ташыйбыз?")
            return jsonify({"status": "ok"}), 200

    # Проверка на слишком общий адрес
    if _is_vague_address(from_addr) or _is_vague_address(to_addr):
        send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
        return jsonify({"status": "ok"}), 200

    user.set_temp_data('service_type', config.SERVICE_PORTER)
    user.set_temp_data('porter_from', from_addr)
    user.set_temp_data('porter_to', to_addr)
    user.set_temp_data('porter_route', f"{from_addr} — {to_addr}")
    user.set_state(config.STATE_CONFIRM_ORDER)

    cargo = (user.get_temp_data('porter_cargo') or "").strip()
    cargo_line = f"\n📦 *Жүк:* {cargo}" if cargo else ""
    confirm_msg = config.CONFIRM_PORTER.format(
        from_address=from_addr,
        to_address=to_addr,
        cargo_line=cargo_line,
    )
    _send_confirm_with_buttons(user.phone, confirm_msg)

    return jsonify({"status": "ok"}), 200


# =============================================================================
# ANT (МУРАВЕЙ) FLOW
# =============================================================================

def handle_ant_route(user: User, message: str, db) -> tuple:
    """Обработка маршрута муравей — переход к подтверждению"""
    saved_from = _canonicalize_address_value(user.get_temp_data('ant_from_partial'))
    if saved_from:
        from_addr = saved_from
        to_addr = _canonicalize_address_value(message.strip())
        user.set_temp_data('ant_from_partial', None)
    else:
        nlu_result = parse_user_message(message)
        from_addr = _canonicalize_optional_address(nlu_result.get("from_address"))
        to_addr = _canonicalize_optional_address(nlu_result.get("to_address"))

        # Обновляем cargo если извлечён и ещё не сохранён
        nlu_cargo = (nlu_result.get("cargo_type") or "").strip()
        if nlu_cargo and not user.get_temp_data('ant_cargo'):
            user.set_temp_data('ant_cargo', nlu_cargo)

        if not from_addr:
            from_addr = _canonicalize_address_value(message.strip())

        if not to_addr:
            user.set_temp_data('ant_from_partial', from_addr)
            send_whatsapp(user.phone, "📍 Кайда ташыйбыз?")
            return jsonify({"status": "ok"}), 200

    # Проверка на слишком общий адрес
    if _is_vague_address(from_addr) or _is_vague_address(to_addr):
        send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
        return jsonify({"status": "ok"}), 200

    user.set_temp_data('service_type', config.SERVICE_ANT)
    user.set_temp_data('ant_from', from_addr)
    user.set_temp_data('ant_to', to_addr)
    user.set_temp_data('ant_route', f"{from_addr} — {to_addr}")
    user.set_state(config.STATE_CONFIRM_ORDER)

    cargo = (user.get_temp_data('ant_cargo') or "").strip()
    cargo_line = f"\n📦 *Жүк:* {cargo}" if cargo else ""
    confirm_msg = config.CONFIRM_ANT.format(
        from_address=from_addr,
        to_address=to_addr,
        cargo_line=cargo_line,
    )
    _send_confirm_with_buttons(user.phone, confirm_msg)

    return jsonify({"status": "ok"}), 200


# =============================================================================
# BUTTON RESPONSE HANDLER
# =============================================================================

def handle_button_response(user: User, button_response: str, db) -> tuple:
    """Обработка нажатия кнопок в WhatsApp"""
    from client_confirm_handler import handle_pharmacy_client_confirm

    try:
        _reset_unknown_fallback(user)
        if button_response in {WHATSAPP_MAIN_MENU_BUTTON_ID, "btn_main_menu", "main_menu"}:
            user.set_state(config.STATE_IDLE)
            user.clear_temp_data()
            _reset_unknown_fallback(user)
            send_whatsapp(user.phone, config.WELCOME_MESSAGE)
            db.update_last_welcome(user.phone)
            return jsonify({"status": "ok"}), 200

        if button_response == MED_EJE_NEED_BUTTON_ID:
            user.set_state(config.STATE_IDLE)
            user.clear_temp_data()
            send_whatsapp(
                user.phone,
                config.MED_EJE_PHONE_MESSAGE.format(
                    phone_1=format_phone(config.MED_EJE_PHONE),
                    phone_2=format_phone(config.MED_EJE_PHONE_2),
                ),
            )
            return jsonify({"status": "ok"}), 200

        if button_response == MED_EJE_BACK_BUTTON_ID:
            user.set_state(config.STATE_IDLE)
            user.clear_temp_data()
            _reset_unknown_fallback(user)
            send_whatsapp(user.phone, config.WELCOME_MESSAGE)
            db.update_last_welcome(user.phone)
            return jsonify({"status": "ok"}), 200

        if button_response in {WHATSAPP_CANCEL_BUTTON_ID, "btn_cancel", "cancel"}:
            handle_client_cancel(user, db)
            user.set_state(config.STATE_IDLE)
            user.clear_temp_data()
            _reset_unknown_fallback(user)
            send_whatsapp(user.phone, config.WELCOME_MESSAGE)
            return jsonify({"status": "ok"}), 200

        # Универсальное подтверждение заказа (Cloud API кнопки Да/Нет)
        if user.current_state == config.STATE_CONFIRM_ORDER:
            if button_response == "confirm_yes":
                service_type = user.get_temp_data('service_type', '')
                if service_type == config.SERVICE_TAXI:
                    return _submit_taxi_order(user, db)
                elif service_type == config.SERVICE_CAFE:
                    return _submit_cafe_order(user, db)
                elif service_type == config.SERVICE_SHOP:
                    return _submit_shop_order(user, db)
                elif service_type == config.SERVICE_PHARMACY:
                    return _submit_pharmacy_order(user, db)
                elif service_type == config.SERVICE_PORTER:
                    return _submit_porter_order(user, db)
                elif service_type == config.SERVICE_ANT:
                    return _submit_ant_order(user, db)
            elif button_response == "confirm_no":
                user.set_state(config.STATE_IDLE)
                user.clear_temp_data()
                _reset_unknown_fallback(user)
                send_whatsapp(user.phone, config.WELCOME_MESSAGE)
                db.update_last_welcome(user.phone)
                return jsonify({"status": "ok"}), 200

        # Аптека: подтверждение
        if user.current_state == config.STATE_PHARMACY_CONFIRM:
            return handle_pharmacy_client_confirm(user, button_response, db)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error handling button response")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# HEALTH CHECK
# =============================================================================

def health_check():
    """Проверка работоспособности сервиса"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "3.0.0",
        "ramadan_mode": config.IS_RAMADAN,
        "ai_enabled": True
    }), 200
