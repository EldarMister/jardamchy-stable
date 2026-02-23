"""
Главный модуль - обработчик WhatsApp webhook
Main Module for Business Assistant GO
Обновленная версия с ИИ (GPT-4.1-mini)
"""

from flask import request, jsonify
import json
import re
import logging
from datetime import datetime, timedelta, timezone

import config
from db import get_db, User, get_runtime_setting
from services import (
    send_whatsapp, send_whatsapp_buttons, send_whatsapp_image,
    send_telegram_group, send_telegram_private, send_telegram_photo, edit_telegram_message,
    speech_to_text, format_phone, format_currency
)
from nlu import parse_user_message, parse_confirmation

logger = logging.getLogger(__name__)

def _runtime_setting(key: str, default):
    return get_runtime_setting(key, default)

# Unknown/fallback anti-flood settings (IDLE only)
UNKNOWN_FALLBACK_MAX_ATTEMPTS = 5
UNKNOWN_FALLBACK_RESET_MINUTES = 15
UNKNOWN_FALLBACK_COOLDOWN_MINUTES = 10

UNKNOWN_FALLBACK_SERVICES_HINT = (
    "такси / доставка еды / курьер / магазин / аптека / портер / муравей"
)
UNKNOWN_FALLBACK_FINAL_MESSAGE = (
    "Я бот Жардамчы GO. Чтобы оформить заказ, напишите: "
    f"{UNKNOWN_FALLBACK_SERVICES_HINT}. "
    "Иначе я не смогу помочь."
)
UNKNOWN_FALLBACK_COOLDOWN_MESSAGE = (
    "Чтобы оформить заказ, напишите услугу: "
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
_WHATSAPP_PRIVATE_SUFFIXES = ("@c.us", "@s.whatsapp.net", "@lid")
_WHATSAPP_GROUP_SUFFIX = "@g.us"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    elif type_message in ("audioMessage", "pttMessage"):
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


def _handle_unknown_fallback(user: User, message: str, ai_reply: str = "") -> tuple:
    now = _utc_now()
    count_raw = user.get_temp_data("fallback_unknown_count", 0)
    count = int(count_raw) if isinstance(count_raw, int) else 0
    last_at = _parse_iso_utc(user.get_temp_data("fallback_unknown_last_at"))
    cooldown_until = _parse_iso_utc(user.get_temp_data("fallback_unknown_cooldown_until"))

    if not last_at or (now - last_at) >= timedelta(minutes=UNKNOWN_FALLBACK_RESET_MINUTES):
        count = 0
        cooldown_until = None

    count += 1
    user.set_temp_data("fallback_unknown_count", count)
    user.set_temp_data("fallback_unknown_last_at", now.isoformat())

    if count == UNKNOWN_FALLBACK_MAX_ATTEMPTS:
        cooldown_until = now + timedelta(minutes=UNKNOWN_FALLBACK_COOLDOWN_MINUTES)
        user.set_temp_data("fallback_unknown_cooldown_until", cooldown_until.isoformat())
        send_whatsapp(user.phone, UNKNOWN_FALLBACK_FINAL_MESSAGE)
        return jsonify({"status": "ok"}), 200

    if count > UNKNOWN_FALLBACK_MAX_ATTEMPTS:
        # После лимита отвечаем коротко на каждое сообщение, без повторного AI.
        next_cooldown = (
            cooldown_until
            if (cooldown_until and now < cooldown_until)
            else now + timedelta(minutes=UNKNOWN_FALLBACK_COOLDOWN_MINUTES)
        )
        user.set_temp_data("fallback_unknown_cooldown_until", next_cooldown.isoformat())
        send_whatsapp(user.phone, UNKNOWN_FALLBACK_COOLDOWN_MESSAGE)
        return jsonify({"status": "ok"}), 200

    reply = (ai_reply or "").strip()
    if not reply:
        reply = (
            "Я бот Жардамчы GO. Помогаю с заказами: такси, доставка еды, магазин, аптека, портер и муравей.\n"
            "Чтобы оформить заказ, напишите услугу и детали: что нужно, откуда забрать и куда доставить.\n"
            "Пример: Такси, от Базара до Мкр 3."
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

        send_whatsapp(user.phone, "❌ Заказ отменён.")
        db.log_transaction("CLIENT_CANCEL_TAXI", user.phone, order_id)
        return True

    # Для остальных сервисов — просто отмена и редактирование сообщения в группе
    db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED)
    _cancel_order_in_group(order_id, service_type, db, cancel_text)
    send_whatsapp(user.phone, "❌ Заказ отменён.")
    db.log_transaction("CLIENT_CANCEL_ORDER", user.phone, order_id)
    return True


# =============================================================================
# WHATSAPP WEBHOOK HANDLER
# =============================================================================

def handle_whatsapp():
    """Главная функция обработки сообщений от Клиента"""
    try:
        incoming_msg = ''
        sender_phone = ''
        media_url = ''
        media_type = ''
        button_response = ''
        type_message = ''
        
        # 1. Попытка парсинга как JSON (Green API)
        if request.is_json:
            data = request.get_json()
            
            # Проверяем тип вебхука Green API
            type_webhook = data.get('typeWebhook', '')
            
            # Обработка входящего сообщения
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

                # Игнорируем группы/невалидные sender id, чтобы не ломать маршрут клиента.
                if not sender_phone:
                    logger.warning(f"Green webhook skipped: empty sender ({raw_sender})")
                    return jsonify({"status": "ignored"}), 200

                # Технические сообщения без текста/медиа не пропускаем в NLU.
                if not incoming_msg and not media_url and not button_response:
                    logger.info(f"Green webhook ignored: empty payload type={type_message}")
                    return jsonify({"status": "ignored"}), 200
                
            elif type_webhook == 'outgoingMessageStatus':
                return jsonify({"status": "ignored"}), 200
                
        # 2. Попытка парсинга как Form Data (Twilio)
        if not sender_phone:
            incoming_msg = request.values.get('Body', '').strip()
            sender_phone = request.values.get('From', '').replace('whatsapp:', '')
            media_url = request.values.get('MediaUrl0', '')
            media_type = request.values.get('MediaContentType0', '')
            button_response = request.values.get('ButtonResponse', '')

        if not sender_phone:
            return jsonify({"status": "ignored"}), 200

        logger.info(f"Received from {sender_phone}: {incoming_msg[:50]}...")
        
        db = get_db()
        
        # Получаем или создаем пользователя
        user = db.get_user(sender_phone)
        
        if not user:
            logger.error(f"Failed to get/create user: {sender_phone}")
            return jsonify({"status": "error"}), 500

        is_voice_message = (
            type_message in ("audioMessage", "pttMessage")
            or (media_type and media_type.lower().startswith("audio/"))
        )

        # Обработка голосового сообщения
        if is_voice_message and media_url:
            logger.info(f"Processing voice from {sender_phone}")
            incoming_msg = speech_to_text(media_url)
            if _is_bad_voice_transcription(incoming_msg):
                send_whatsapp(
                    sender_phone,
                    "Не расслышал голосовое. Напишите текстом или отправьте голосовое ещё раз, пожалуйста."
                )
                return jsonify({"status": "ok"}), 200
        
        # Обработка фото (сохраняем URL)
        if media_type and media_type.startswith('image/'):
            user.set_temp_data('media_url', media_url)
            user.set_temp_data('media_type', media_type)
        
        # Обработка кнопок (если есть)
        if button_response:
            _reset_unknown_fallback(user)
            return handle_button_response(user, button_response, db)
        
        # === ROUTING ===
        
        # Проверка на отмену (в любом состоянии),
        # кроме шага выбора цены такси: "Жок/Нет" там означает стандартный тариф.
        msg_lower = incoming_msg.lower().strip()
        is_taxi_price_decline = (
            user.current_state == config.STATE_TAXI_PRICE_CHOICE and
            msg_lower in ('жок', 'нет', 'no', '2', 'btn_taxi_standard')
        )
        if _is_cancellation(incoming_msg) and not is_taxi_price_decline:
            logger.info(f"User {sender_phone} cancelled order in state {user.current_state}")
            cancelled = handle_client_cancel(user, db)
            user.set_state(config.STATE_IDLE)
            user.clear_temp_data()
            _reset_unknown_fallback(user)
            if not cancelled:
                send_whatsapp(user.phone, config.ORDER_CANCELLED)
            return jsonify({"status": "ok"}), 200

        if user.current_state != config.STATE_IDLE:
            _reset_unknown_fallback(user)

        if user.current_state == config.STATE_TAXI_REORDER_CHOICE:
            return handle_taxi_reorder_choice(user, incoming_msg, db)
        
        if user.current_state == config.STATE_IDLE:
            return handle_idle_state(user, incoming_msg, db)
        
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
        
        # Аптека
        elif user.current_state == config.STATE_PHARMACY_WAIT_RX:
            return handle_pharmacy_request(user, incoming_msg, media_url, db)
        elif user.current_state == config.STATE_PHARMACY_WAIT_PRESCRIPTION:
            return handle_pharmacy_prescription_rx(user, incoming_msg, media_url, db)
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
        elif user.current_state == config.STATE_TAXI_PRICE_CHOICE:
            return handle_taxi_price_choice(user, incoming_msg, db)
        elif user.current_state == config.STATE_TAXI_CUSTOM_PRICE:
            return handle_taxi_custom_price(user, incoming_msg, db)
        
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
    }

    # Жёсткая проверка на «меню» / запрос еды, чтобы не путать с доставкой
    menu_keywords = ["меню", "мену", "мэню", "менью", "менйу", "миню", "менюу", "menu", "меню керек", "мага меню"]
    selected_intent = service_intent_by_number.get(msg_trim) or service_intent_by_number.get(first_token_digits)
    if selected_intent:
        nlu_result = {"intent": selected_intent, "from_address": None, "to_address": None, "order_details": None, "cargo_type": None}
    elif any(k in msg_lower for k in menu_keywords):
        nlu_result = {"intent": "cafe", "from_address": None, "to_address": None, "order_details": None, "cargo_type": None}
    elif _looks_like_greeting(message):
        nlu_result = {"intent": "greeting", "from_address": None, "to_address": None, "order_details": None, "cargo_type": None}
    else:
        # Используем ИИ для определения намерения
        nlu_result = parse_user_message(message)
    intent = nlu_result.get("intent", "unknown")
    
    logger.info(f"NLU intent for {user.phone}: {intent}")
    if intent in {"taxi", "cafe", "shop", "pharmacy", "porter", "ant", "greeting"}:
        _reset_unknown_fallback(user)

    # === WEB ORDER CODE (W-xxxxx) ===
    # Проверка на код заказа с сайта
    if re.match(r'^W\d{5}$', message.strip(), re.IGNORECASE):
        code = message.strip().upper()
        order = db.get_web_order(code)
        
        if not order:
            send_whatsapp(user.phone, "❌ Заказ с таким кодом не найден. Проверьте код.")
            return jsonify({"status": "ok"}), 200
            
        if order['status'] in ['CONFIRMED', 'COMPLETED', 'CANCELLED']:
             send_whatsapp(user.phone, f"⚠️ Этот заказ уже обработан (Статус: {order['status']}).")
             return jsonify({"status": "ok"}), 200

        # Сохраняем контекст заказа
        _reset_unknown_fallback(user)
        user.set_temp_data('service_type', config.SERVICE_CAFE)
        user.set_temp_data('web_order_code', code)
        user.set_temp_data('cafe_id', order['cafe_id'])
        
        # Формируем детали заказа
        items = order['items_json']
        details_lines = [f"Кафе: {order['cafe_name']}"]
        for item in items:
            details_lines.append(f"- {item['name']} x{item['count']}")
        details_lines.append(f"\nИтого: {int(order['total_price'])} сом")
        
        order_details = "\n".join(details_lines)
        user.set_temp_data('cafe_order_details', order_details)
        
        # Переходим к вводу адреса
        user.set_state(config.STATE_WEB_ORDER_ADDRESS)
        send_whatsapp(user.phone, "📍 Введите адрес доставки (или отправьте геолокацию):")
        return jsonify({"status": "ok"}), 200
    
    # === ТАКСИ ===
    if intent == "taxi":
        from_addr = nlu_result.get("from_address")
        to_addr = nlu_result.get("to_address")
        
        if from_addr and to_addr:
            # ИИ извлёк оба адреса — спрашиваем цену
            user.set_temp_data('service_type', config.SERVICE_TAXI)
            user.set_temp_data('taxi_from', from_addr)
            user.set_temp_data('taxi_to', to_addr)
            user.set_temp_data('taxi_route', f"{from_addr} — {to_addr}")
            user.set_state(config.STATE_TAXI_CUSTOM_PRICE)
            send_whatsapp(user.phone, config.TAXI_ASK_PRICE_PROMPT.format(
                from_address=from_addr, to_address=to_addr,
            ))
        else:
            # Адреса не указаны — спрашиваем
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
            menu_msg = f"🍔 *Меню заказа / Тамак заказ кылуу*\n\nПерейдите по ссылке, чтобы выбрать блюда:\n{config.MENU_LINK}\n\nИли напишите список блюд вручную ниже.\nЖе тамактардын тизмесин төмөндө жазыңыз."
            send_whatsapp(user.phone, menu_msg)
            user.set_state(config.STATE_CAFE_ORDER)
        
        return jsonify({"status": "ok"}), 200
    
    # === МАГАЗИН ===
    elif intent == "shop":
        order_details_raw = nlu_result.get("order_details")
        order_details = order_details_raw if _is_concrete_order_details(order_details_raw, config.SERVICE_SHOP) else None
        
        if order_details:
            # ИИ извлёк список — к подтверждению
            user.set_temp_data('service_type', config.SERVICE_SHOP)
            user.set_temp_data('shop_list', order_details)
            user.set_state(config.STATE_CONFIRM_ORDER)
            
            confirm_msg = config.CONFIRM_SHOP.format(order_details=order_details, delivery_fee=_runtime_setting("shop_delivery_fee", config.SHOP_DELIVERY_FEE))
            send_whatsapp(user.phone, confirm_msg)
        else:
            user.set_state(config.STATE_SHOP_LIST)
            send_whatsapp(user.phone, config.SHOP_PROMPT)
        
        return jsonify({"status": "ok"}), 200
    
    # === АПТЕКА ===
    elif intent == "pharmacy":
        order_details = nlu_result.get("order_details")
        user.set_temp_data('service_type', config.SERVICE_PHARMACY)

        if order_details:
            # ИИ извлёк название лекарства — спросить про рецепт
            user.set_temp_data('pharmacy_request', order_details)
            user.set_state(config.STATE_PHARMACY_WAIT_RX)
            send_whatsapp(user.phone, config.PHARMACY_ASK_RX.format(medication=order_details))
        else:
            # Название неизвестно — попросить написать
            user.set_state(config.STATE_PHARMACY_WAIT_RX)
            send_whatsapp(user.phone, config.PHARMACY_PROMPT)

        return jsonify({"status": "ok"}), 200
    
    # === ПОРТЕР ===
    elif intent == "porter":
        cargo_type = nlu_result.get("cargo_type")
        from_addr = nlu_result.get("from_address")
        to_addr = nlu_result.get("to_address")
        
        if cargo_type and from_addr and to_addr:
            # Всё есть — к подтверждению
            user.set_temp_data('service_type', config.SERVICE_PORTER)
            user.set_temp_data('porter_cargo_type', cargo_type)
            user.set_temp_data('porter_from', from_addr)
            user.set_temp_data('porter_to', to_addr)
            user.set_temp_data('porter_route', f"{from_addr} — {to_addr}")
            user.set_state(config.STATE_CONFIRM_ORDER)
            
            confirm_msg = config.CONFIRM_PORTER.format(
                cargo_type=cargo_type,
                from_address=from_addr,
                to_address=to_addr
            )
            send_whatsapp(user.phone, confirm_msg)
        elif cargo_type:
            # Есть тип груза, нет маршрута
            user.set_temp_data('porter_cargo_type', cargo_type)
            user.set_state(config.STATE_PORTER_ROUTE)
            send_whatsapp(user.phone, config.PORTER_ROUTE_PROMPT)
        else:
            user.set_state(config.STATE_PORTER_CARGO_TYPE)
            send_whatsapp(user.phone, config.PORTER_CARGO_PROMPT)
        
        return jsonify({"status": "ok"}), 200
    
    # === МУРАВЕЙ ===
    elif intent == "ant":
        from_addr = nlu_result.get("from_address")
        to_addr = nlu_result.get("to_address")
        order_details = _sanitize_ant_details(
            nlu_result.get("order_details"),
            from_addr=from_addr,
            to_addr=to_addr,
            source_text=message
        )
        
        if order_details and from_addr and to_addr:
            # Всё есть — к подтверждению
            user.set_temp_data('service_type', config.SERVICE_ANT)
            user.set_temp_data('ant_details', order_details)
            user.set_temp_data('ant_from', from_addr)
            user.set_temp_data('ant_to', to_addr)
            user.set_temp_data('ant_route', f"{from_addr} — {to_addr}")
            user.set_state(config.STATE_CONFIRM_ORDER)
            
            confirm_msg = config.CONFIRM_ANT.format(
                order_details=order_details,
                from_address=from_addr,
                to_address=to_addr
            )
            send_whatsapp(user.phone, confirm_msg)
        else:
            user.set_state(config.STATE_ANT_ROUTE)
            if order_details:
                user.set_temp_data('ant_details', order_details)
            send_whatsapp(user.phone, config.ANT_PROMPT)
        
        return jsonify({"status": "ok"}), 200
    
    # === ПРИВЕТСТВИЕ или НЕИЗВЕСТНОЕ ===
    elif intent == "greeting" or _looks_like_greeting(message):
        _reset_unknown_fallback(user)
        if db.can_send_welcome(user.phone, cooldown_seconds=600):
            send_whatsapp(user.phone, config.WELCOME_MESSAGE)
            db.update_last_welcome(user.phone)
        else:
            logger.info(f"Welcome suppressed for {user.phone} (anti-flood)")
        return jsonify({"status": "ok"}), 200

    else:
        return _handle_unknown_fallback(user, message, nlu_result.get("fallback_reply"))


# =============================================================================
# UNIVERSAL CONFIRM ORDER HANDLER
# =============================================================================

def handle_confirm_order(user: User, message: str, db) -> tuple:
    """Универсальная обработка подтверждения заказа (с ИИ)"""
    
    # ИИ определяет: подтвердил, отменил, или исправляет
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
        send_whatsapp(user.phone, config.ORDER_CANCELLED)
        return jsonify({"status": "ok"}), 200


def _handle_correction(user: User, confirmation: dict, service_type: str) -> tuple:
    """Обработка исправления данных пользователем"""
    
    if service_type == config.SERVICE_TAXI:
        if confirmation.get("corrected_from"):
            user.set_temp_data('taxi_from', confirmation["corrected_from"])
        if confirmation.get("corrected_to"):
            user.set_temp_data('taxi_to', confirmation["corrected_to"])
        
        from_addr = user.get_temp_data('taxi_from', '')
        to_addr = user.get_temp_data('taxi_to', '')
        user.set_temp_data('taxi_route', f"{from_addr} — {to_addr}")
        
        confirm_msg = config.CONFIRM_TAXI.format(
            from_address=from_addr,
            to_address=to_addr
        )
        send_whatsapp(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_CAFE:
        if confirmation.get("corrected_details"):
            user.set_temp_data('cafe_order_details', confirmation["corrected_details"])
        if confirmation.get("corrected_to"):
            user.set_temp_data('cafe_address', confirmation["corrected_to"])
        
        order_details = user.get_temp_data('cafe_order_details', '')
        address = user.get_temp_data('cafe_address', '')
        
        confirm_msg = config.CONFIRM_CAFE.format(
            order_details=order_details,
            address=address
        )
        send_whatsapp(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_SHOP:
        if confirmation.get("corrected_details"):
            user.set_temp_data('shop_list', confirmation["corrected_details"])
        
        order_details = user.get_temp_data('shop_list', '')
        confirm_msg = config.CONFIRM_SHOP.format(order_details=order_details, delivery_fee=_runtime_setting("shop_delivery_fee", config.SHOP_DELIVERY_FEE))
        send_whatsapp(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_PHARMACY:
        if confirmation.get("corrected_details"):
            user.set_temp_data('pharmacy_request', confirmation["corrected_details"])
        
        order_details = user.get_temp_data('pharmacy_request', '')
        confirm_msg = config.CONFIRM_PHARMACY.format(order_details=order_details)
        send_whatsapp(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_PORTER:
        if confirmation.get("corrected_from"):
            user.set_temp_data('porter_from', confirmation["corrected_from"])
        if confirmation.get("corrected_to"):
            user.set_temp_data('porter_to', confirmation["corrected_to"])
        
        from_addr = user.get_temp_data('porter_from', '')
        to_addr = user.get_temp_data('porter_to', '')
        cargo_type = user.get_temp_data('porter_cargo_type', 'other')
        user.set_temp_data('porter_route', f"{from_addr} — {to_addr}")
        
        confirm_msg = config.CONFIRM_PORTER.format(
            cargo_type=cargo_type,
            from_address=from_addr,
            to_address=to_addr
        )
        send_whatsapp(user.phone, confirm_msg)
    
    elif service_type == config.SERVICE_ANT:
        if confirmation.get("corrected_from"):
            user.set_temp_data('ant_from', confirmation["corrected_from"])
        if confirmation.get("corrected_to"):
            user.set_temp_data('ant_to', confirmation["corrected_to"])

        from_addr = user.get_temp_data('ant_from', '')
        to_addr = user.get_temp_data('ant_to', '')
        if confirmation.get("corrected_details"):
            user.set_temp_data(
                'ant_details',
                _sanitize_ant_details(
                    confirmation["corrected_details"],
                    from_addr=from_addr,
                    to_addr=to_addr,
                    source_text=confirmation["corrected_details"]
                )
            )

        order_details = _sanitize_ant_details(
            user.get_temp_data('ant_details', ''),
            from_addr=from_addr,
            to_addr=to_addr,
            source_text=user.get_temp_data('ant_details', '')
        )
        user.set_temp_data('ant_details', order_details)
        user.set_temp_data('ant_route', f"{from_addr} — {to_addr}")
        
        confirm_msg = config.CONFIRM_ANT.format(
            order_details=order_details,
            from_address=from_addr,
            to_address=to_addr
        )
        send_whatsapp(user.phone, confirm_msg)
    
    return jsonify({"status": "ok"}), 200


# =============================================================================
# ORDER SUBMISSION FUNCTIONS
# =============================================================================

def _submit_taxi_order(user: User, db) -> tuple:
    """Отправка заказа такси"""
    route = user.get_temp_data('taxi_route', '')
    custom_price = user.get_temp_data('taxi_custom_price', None)
    
    # Если клиент предложил свою цену, сохраняем её в price_total
    price_value = float(custom_price) if custom_price else 0
    
    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_TAXI,
        details=route,
        price=price_value
    )
    
    # Комиссия всегда фиксированная
    commission_info = f"💰 Комиссия: {_runtime_setting('taxi_commission', config.TAXI_COMMISSION)} сом"
    
    # Цена в Telegram-сообщении
    price_display = f"{int(float(custom_price))} сом (цена клиента)" if custom_price else "договорная"
    
    telegram_msg = config.TAXI_ORDER_TELEGRAM.format(
        route=route,
        price=price_display,
        commission_info=commission_info,
    )
    
    buttons = [{
        "text": "🚖 Взять заказ",
        "callback": f"taxi_take_{order_id}"
    }]
    
    result = send_telegram_group(config.GROUP_TAXI_ID, telegram_msg, buttons)
    
    if result:
        db.create_auction_timer(
            order_id=order_id,
            service_type=config.SERVICE_TAXI,
            telegram_message_id=str(result.get('message_id')),
            chat_id=config.GROUP_TAXI_ID,
            timeout_seconds=int(_runtime_setting("taxi_response_timeout", config.TAXI_RESPONSE_TIMEOUT))
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
    
    # Кафе-заказы отправляем в группу кафе.
    result = send_telegram_group(config.GROUP_CAFE_ID, telegram_msg, buttons)
    target_chat_id = config.GROUP_CAFE_ID

    if result:
        db.create_auction_timer(
            order_id=order_id,
            service_type=config.SERVICE_CAFE,
            telegram_message_id=str(result.get('message_id')),
            chat_id=target_chat_id,
            timeout_seconds=int(_runtime_setting("cafe_auction_timeout", config.CAFE_AUCTION_TIMEOUT))
        )
    
    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    
    send_whatsapp(user.phone, config.CAFE_ORDER_SENT)
    
    db.log_transaction("CAFE_ORDER_CREATED", user.phone, order_id, details=order_details[:100])
    
    return jsonify({"status": "ok", "order_id": order_id}), 200


def _submit_shop_order(user: User, db) -> tuple:
    """Отправка заказа из магазина — направляем в группу такси"""
    shop_list = user.get_temp_data('shop_list', '')

    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_SHOP,
        details=shop_list
    )

    telegram_msg = f"""🛒 *ДОСТАВКА ИЗ МАГАЗИНА*

📋 *Список покупок:*
{shop_list}

📞 *Клиент:* {user.phone}
💰 *За доставку:* {_runtime_setting('shop_delivery_fee', config.SHOP_DELIVERY_FEE)} сом
💰 *Комиссия:* {_runtime_setting('taxi_commission', config.TAXI_COMMISSION)} сом

Нужно купить и доставить клиенту."""

    buttons = [{
        "text": "🚖 Взять заказ",
        "callback": f"taxi_take_{order_id}"
    }]

    result = send_telegram_group(config.GROUP_TAXI_ID, telegram_msg, buttons)

    if result:
        db.create_auction_timer(
            order_id=order_id,
            service_type=config.SERVICE_SHOP,
            telegram_message_id=str(result.get('message_id')),
            chat_id=config.GROUP_TAXI_ID,
            timeout_seconds=int(_runtime_setting("taxi_response_timeout", config.TAXI_RESPONSE_TIMEOUT))
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
    if media_url:
        result = send_telegram_photo(config.GROUP_PHARMACY_ID, media_url, telegram_msg, buttons=buttons)
    else:
        result = send_telegram_group(config.GROUP_PHARMACY_ID, telegram_msg, buttons)
    if result:
        db.create_auction_timer(
            order_id=order_id,
            service_type=config.SERVICE_PHARMACY,
            telegram_message_id=str(result.get('message_id')),
            chat_id=config.GROUP_PHARMACY_ID,
            timeout_seconds=int(_runtime_setting("pharmacy_response_timeout", config.PHARMACY_RESPONSE_TIMEOUT))
        )

    user.set_state(config.STATE_PHARMACY_WAIT_PRICE)
    user.set_temp_data('pharmacy_order_id', order_id)
    
    send_whatsapp(user.phone, config.PHARMACY_SEARCHING)
    
    db.log_transaction("PHARMACY_ORDER_CREATED", user.phone, order_id)
    
    return jsonify({"status": "ok"}), 200


def _submit_porter_order(user: User, db) -> tuple:
    """Отправка заказа портера"""
    route = user.get_temp_data('porter_route', '')
    cargo_type = user.get_temp_data('porter_cargo_type', 'other')
    
    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_PORTER,
        details=route,
        cargo_type=cargo_type
    )
    
    telegram_msg = config.PORTER_ORDER_TELEGRAM.format(
        cargo_type=cargo_type,
        route=route,
    )
    
    buttons = [{
        "text": "🚛 Взять груз",
        "callback": f"porter_take_{order_id}"
    }]

    result = send_telegram_group(config.GROUP_PORTER_ID, telegram_msg, buttons)
    if result:
        db.create_auction_timer(
            order_id=order_id,
            service_type=config.SERVICE_PORTER,
            telegram_message_id=str(result.get('message_id')),
            chat_id=config.GROUP_PORTER_ID,
            timeout_seconds=int(_runtime_setting("pending_order_auto_cancel_timeout", config.PENDING_ORDER_AUTO_CANCEL_TIMEOUT))
        )

    user.set_state(config.STATE_IDLE)
    user.clear_temp_data()
    
    send_whatsapp(user.phone, config.ORDER_SENT_GENERIC)
    
    db.log_transaction("PORTER_ORDER_CREATED", user.phone, order_id)
    
    return jsonify({"status": "ok"}), 200


def _submit_ant_order(user: User, db) -> tuple:
    """Отправка заказа муравья"""
    route = user.get_temp_data('ant_route', '')
    details = user.get_temp_data('ant_details', '')
    
    order_id = db.create_order(
        client_phone=user.phone,
        service_type=config.SERVICE_ANT,
        details=f"{details} | {route}"
    )
    
    telegram_msg = config.ANT_ORDER_TELEGRAM.format(
        details=details,
        route=route,
        phone=user.phone
    )
    
    buttons = [{
        "text": "🐜 Взять заказ",
        "callback": f"ant_take_{order_id}"
    }]

    result = send_telegram_group(config.GROUP_ANT_ID, telegram_msg, buttons)
    if result:
        db.create_auction_timer(
            order_id=order_id,
            service_type=config.SERVICE_ANT,
            telegram_message_id=str(result.get('message_id')),
            chat_id=config.GROUP_ANT_ID,
            timeout_seconds=int(_runtime_setting("pending_order_auto_cancel_timeout", config.PENDING_ORDER_AUTO_CANCEL_TIMEOUT))
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
    # Проверка на слишком общий адрес
    if _is_vague_address(message):
        send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
        return jsonify({"status": "ok"}), 200
    
    user.set_temp_data('cafe_address', message)
    user.set_temp_data('service_type', config.SERVICE_CAFE)
    
    order_details = user.get_temp_data('cafe_order_details', '')
    
    # Переход к подтверждению (без вопроса об оплате)
    user.set_state(config.STATE_CONFIRM_ORDER)
    confirm_msg = config.CONFIRM_CAFE.format(
        order_details=order_details,
        address=message
    )
    send_whatsapp(user.phone, confirm_msg)
    
    return jsonify({"status": "ok"}), 200


def handle_web_order_address(user: User, message: str, db) -> tuple:
    """Обработка адреса для веб-заказа"""
    # Validation if needed
    if len(message) < 3:
         send_whatsapp(user.phone, "Пожалуйста, введите корректный адрес:")
         return jsonify({"status": "ok"}), 200
         
    user.set_temp_data('cafe_address', message)
    
    # Update web order status/info
    code = user.get_temp_data('web_order_code')
    if code:
        db.update_web_order_status(code, 'ADDRESS_SET', client_phone=user.phone, address=message)
    
    # Proceed to confirmation
    details = user.get_temp_data('cafe_order_details', '')
    
    user.set_state(config.STATE_CONFIRM_ORDER)
    confirm_msg = config.CONFIRM_CAFE.format(
        order_details=details,
        address=message
    )
    send_whatsapp(user.phone, confirm_msg)
    return jsonify({"status": "ok"}), 200


# =============================================================================
# SHOP FLOW (упрощённый)
# =============================================================================

def handle_shop_list(user: User, message: str, db) -> tuple:
    """Обработка списка покупок — переход к подтверждению"""
    user.set_temp_data('shop_list', message)
    user.set_temp_data('service_type', config.SERVICE_SHOP)
    
    user.set_state(config.STATE_CONFIRM_ORDER)
    confirm_msg = config.CONFIRM_SHOP.format(order_details=message)
    send_whatsapp(user.phone, confirm_msg)
    
    return jsonify({"status": "ok"}), 200


# =============================================================================
# PHARMACY FLOW
# =============================================================================

def handle_pharmacy_request(user: User, message: str, media_url: str, db) -> tuple:
    """Обработка STATE_PHARMACY_WAIT_RX: либо ждём название лекарства, либо ответ да/нет на вопрос о рецепте"""
    msg_lower = (message or "").lower().strip()
    yes_words = {"да", "ооба", "yes", "ия", "oo"}
    no_words  = {"нет", "жок", "no", "нет."}

    existing_request = (user.get_temp_data('pharmacy_request', '') or '').strip()

    if existing_request:
        # Уже знаем лекарство — клиент отвечает да/нет на вопрос о рецепте
        if msg_lower in yes_words:
            user.set_state(config.STATE_PHARMACY_WAIT_PRESCRIPTION)
            send_whatsapp(user.phone, config.PHARMACY_ASK_PRESCRIPTION_UPLOAD)
        else:
            # "нет" или любой другой ответ → без рецепта, к подтверждению
            user.set_temp_data('service_type', config.SERVICE_PHARMACY)
            user.set_state(config.STATE_CONFIRM_ORDER)
            confirm_msg = config.CONFIRM_PHARMACY.format(order_details=existing_request)
            send_whatsapp(user.phone, confirm_msg)
    else:
        # Ещё не знаем лекарство — клиент пишет название или отправляет фото рецепта
        if media_url:
            # Прислали фото рецепта без названия
            user.set_temp_data('pharmacy_media_url', media_url)
            medication = message.strip() if message and message.strip() else "(фото рецепта)"
            user.set_temp_data('pharmacy_request', medication)
            user.set_temp_data('service_type', config.SERVICE_PHARMACY)
            user.set_state(config.STATE_CONFIRM_ORDER)
            confirm_msg = config.CONFIRM_PHARMACY.format(order_details=medication)
            send_whatsapp(user.phone, confirm_msg)
        elif message and message.strip():
            # Написал название лекарства → теперь спросим про рецепт
            medication = message.strip()
            user.set_temp_data('pharmacy_request', medication)
            user.set_temp_data('service_type', config.SERVICE_PHARMACY)
            send_whatsapp(user.phone, config.PHARMACY_ASK_RX.format(medication=medication))
            # Остаёмся в STATE_PHARMACY_WAIT_RX — теперь отвечает да/нет
        else:
            send_whatsapp(user.phone, config.PHARMACY_PROMPT)

    return jsonify({"status": "ok"}), 200


def handle_pharmacy_prescription_rx(user: User, message: str, media_url: str, db) -> tuple:
    """Получили фото или текст рецепта — добавляем к запросу и переходим к подтверждению"""
    if media_url:
        user.set_temp_data('pharmacy_media_url', media_url)

    medication = (user.get_temp_data('pharmacy_request', '') or '').strip()
    if message and message.strip() and not media_url:
        # Текстовое описание рецепта
        medication = f"{medication} (рецепт: {message.strip()})"
        user.set_temp_data('pharmacy_request', medication)
    elif media_url:
        # Фото рецепта — дополним пометкой
        medication = f"{medication} + фото рецепта"
        user.set_temp_data('pharmacy_request', medication)

    user.set_temp_data('service_type', config.SERVICE_PHARMACY)
    user.set_state(config.STATE_CONFIRM_ORDER)
    confirm_msg = config.CONFIRM_PHARMACY.format(order_details=medication)
    send_whatsapp(user.phone, confirm_msg)
    return jsonify({"status": "ok"}), 200


def handle_pharmacy_delivery_address(user: User, message: str, db) -> tuple:
    """Получили адрес клиента после цены аптеки: сразу оформляем доставку."""
    address = (message or "").strip()
    if not address:
        send_whatsapp(user.phone, "📍 Введите адрес доставки.")
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
        send_whatsapp(user.phone, "❌ Ошибка данных заказа. Начните заново.")
        return jsonify({"status": "ok"}), 200

    order = db.get_order(order_id)
    if not order:
        user.set_state(config.STATE_IDLE)
        user.clear_temp_data()
        send_whatsapp(user.phone, "❌ Заказ не найден. Начните заново.")
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
    send_telegram_group(config.GROUP_TAXI_ID, taxi_msg, buttons)

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

def handle_taxi_reorder_choice(user: User, message: str, db) -> tuple:
    """Обработка ответа клиента после отмены водителем: повторить заказ или начать новый."""
    msg_lower = (message or "").lower().strip()

    yes_words = {"да", "оа", "ооба", "yes", "1", "btn_taxi_reorder_yes"}
    no_words = {"нет", "жок", "no", "2", "btn_taxi_reorder_no"}

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

        raw_price = user.get_temp_data('taxi_reorder_price', 0)
        try:
            price = float(raw_price or 0)
        except (TypeError, ValueError):
            price = 0

        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_temp_data('taxi_route', route)
        user.set_temp_data('taxi_custom_price', price if price > 0 else None)

        # Для совместимости с остальным flow заполняем откуда/куда если маршрут разделён.
        parts = [p.strip() for p in re.split(r"\s*[—-]\s*", route, maxsplit=1) if p.strip()]
        if len(parts) == 2:
            user.set_temp_data('taxi_from', parts[0])
            user.set_temp_data('taxi_to', parts[1])

        return _submit_taxi_order(user, db)

    if msg_lower in no_words:
        user.clear_temp_data()
        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_temp_data('taxi_from', '')
        user.set_temp_data('taxi_to', '')
        user.set_state(config.STATE_TAXI_ROUTE)
        send_whatsapp(user.phone, config.TAXI_PROMPT)
        return jsonify({"status": "ok"}), 200

    # Нераспознанный ответ — не повторяем вопрос (только 1 раз), сбрасываем состояние
    user.clear_temp_data()
    user.set_state(config.STATE_INITIAL)
    return jsonify({"status": "ok"}), 200


def _send_taxi_price_choice(phone: str, from_address: str, to_address: str) -> bool:
    """Отправить выбор цены для такси с кнопками и fallback на текст."""
    price_choice_msg = config.TAXI_PRICE_CHOICE_PROMPT.format(
        from_address=from_address,
        to_address=to_address
    )
    buttons = [
        {"text": "✅ Да / Ооба", "id": "btn_taxi_custom"},
        {"text": "❌ Нет / Жок", "id": "btn_taxi_standard"},
    ]
    if send_whatsapp_buttons(phone, price_choice_msg, buttons):
        return True
    return send_whatsapp(phone, price_choice_msg)


def handle_taxi_route(user: User, message: str, db, is_voice_input: bool = False) -> tuple:
    """Обработка маршрута такси: собираем откуда/куда до полной информации."""
    msg = message.strip()
    if not msg:
        send_whatsapp(user.phone, config.TAXI_PROMPT)
        return jsonify({"status": "ok"}), 200

    nlu_result = parse_user_message(msg)
    parsed_from = (nlu_result.get("from_address") or "").strip()
    parsed_to = (nlu_result.get("to_address") or "").strip()

    # Fallback: если пользователь написал маршрут через дефис
    if not parsed_from and not parsed_to:
        dash_split = re.split(r"\s*[—-]\s*", msg, maxsplit=1)
        if len(dash_split) == 2 and dash_split[0].strip() and dash_split[1].strip():
            parsed_from = dash_split[0].strip()
            parsed_to = dash_split[1].strip()

    current_from = (user.get_temp_data('taxi_from', '') or "").strip()
    current_to = (user.get_temp_data('taxi_to', '') or "").strip()

    def _ask_for_to():
        send_whatsapp(
            user.phone,
            "📍 *Куда ехать? / Кайда барабыз?*\n\n"
            "Напишите конечный адрес (куда поедем).\n"
            "Акыркы даректи жазыңыз (кайда барабыз)."
        )

    def _ask_for_from():
        send_whatsapp(
            user.phone,
            "📍 *Откуда ехать? / Кайдан барабыз?*\n\n"
            "Напишите адрес подачи (где вас забрать).\n"
            "Баштапкы даректи жазыңыз (кайдан алабыз)."
        )

    def _go_to_price_choice(from_address: str, to_address: str):
        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_temp_data('taxi_from', from_address)
        user.set_temp_data('taxi_to', to_address)
        user.set_temp_data('taxi_route', f"{from_address} — {to_address}")
        user.set_state(config.STATE_TAXI_CUSTOM_PRICE)
        send_whatsapp(user.phone, config.TAXI_ASK_PRICE_PROMPT.format(
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
                "⚠️ Адрес *откуда* и *куда* получился одинаковым.\n"
                "Напишите маршрут точнее: *Откуда* и *Куда* отдельно."
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
        single = msg
        if _is_vague_address(single):
            send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
            return jsonify({"status": "ok"}), 200
        user.set_temp_data('service_type', config.SERVICE_TAXI)
        user.set_temp_data('taxi_from', single)
        _ask_for_to()
        return jsonify({"status": "ok"}), 200

    # Для голосового ввода — пошаговый сбор недостающего адреса
    if not current_from and not current_to:
        single_addr = parsed_from or parsed_to or msg
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
        to_addr = parsed_to or parsed_from or msg
        if _is_vague_address(to_addr):
            send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
            return jsonify({"status": "ok"}), 200
        if _addresses_equal(current_from, to_addr):
            send_whatsapp(
                user.phone,
                "⚠️ Адрес назначения совпадает с адресом подачи.\n"
                "Напишите другой адрес *КУДА*."
            )
            return jsonify({"status": "ok"}), 200
        return _go_to_price_choice(current_from, to_addr)

    if current_to and not current_from:
        from_addr = parsed_from or parsed_to or msg
        if _is_vague_address(from_addr):
            send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
            return jsonify({"status": "ok"}), 200
        if _addresses_equal(from_addr, current_to):
            send_whatsapp(
                user.phone,
                "⚠️ Адрес подачи совпадает с адресом назначения.\n"
                "Напишите другой адрес *ОТКУДА*."
            )
            return jsonify({"status": "ok"}), 200
        return _go_to_price_choice(from_addr, current_to)

    if _addresses_equal(current_from, current_to):
        send_whatsapp(
            user.phone,
            "⚠️ Адреса сейчас одинаковые. Напишите маршрут заново: *Откуда* и *Куда*."
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


def handle_taxi_price_choice(user: User, message: str, db) -> tuple:
    """Обработка выбора: предложить свою цену или нет"""
    msg_lower = message.lower().strip()
    
    from_addr = user.get_temp_data('taxi_from', '')
    to_addr = user.get_temp_data('taxi_to', '')
    
    # Если клиент сразу прислал число или назвал цену голосом
    price = _extract_price(message) if msg_lower not in ('1', '2') else None
    if price is not None:
        if price < _runtime_setting("taxi_custom_price_min", config.TAXI_CUSTOM_PRICE_MIN):
            send_whatsapp(user.phone, config.TAXI_CUSTOM_PRICE_TOO_LOW.format(
                min_price=int(_runtime_setting("taxi_custom_price_min", config.TAXI_CUSTOM_PRICE_MIN))
            ))
            return jsonify({"status": "ok"}), 200

        user.set_temp_data('taxi_custom_price', price)
        # Сразу создаем заказ без лишнего шага подтверждения
        return _submit_taxi_order(user, db)

    # Клиент хочет предложить свою цену (кнопкой/словом)
    if msg_lower in ('btn_taxi_custom', 'да', 'yes', 'ооба', 'ообо', '1'):
        user.set_state(config.STATE_TAXI_CUSTOM_PRICE)
        send_whatsapp(user.phone, config.TAXI_CUSTOM_PRICE_PROMPT)
        return jsonify({"status": "ok"}), 200

    # Клиент отказался — сразу стандартный тариф
    if msg_lower in ('btn_taxi_standard', 'нет', 'no', 'жок', '2'):
        user.set_temp_data('taxi_custom_price', None)
        return _submit_taxi_order(user, db)
    
    # Непонятный ответ — переспрашиваем
    _send_taxi_price_choice(user.phone, from_addr, to_addr)
    return jsonify({"status": "ok"}), 200


def handle_taxi_custom_price(user: User, message: str, db) -> tuple:
    """Обработка ввода своей цены клиентом"""
    price = _extract_price(message)

    if price is None:
        send_whatsapp(user.phone, config.TAXI_CUSTOM_PRICE_PROMPT)
        return jsonify({"status": "ok"}), 200
    
    if price < _runtime_setting("taxi_custom_price_min", config.TAXI_CUSTOM_PRICE_MIN):
        send_whatsapp(user.phone, config.TAXI_CUSTOM_PRICE_TOO_LOW.format(
            min_price=int(_runtime_setting("taxi_custom_price_min", config.TAXI_CUSTOM_PRICE_MIN))
        ))
        return jsonify({"status": "ok"}), 200

    # Сохраняем цену и сразу отправляем заказ в поиск водителя
    user.set_temp_data('taxi_custom_price', price)
    return _submit_taxi_order(user, db)


# =============================================================================
# PORTER FLOW
# =============================================================================

def handle_porter_cargo_type(user: User, message: str, db) -> tuple:
    """Обработка типа груза — сохраняем дословно"""
    cargo_type = message.strip()

    user.set_temp_data('porter_cargo_type', cargo_type)
    user.set_state(config.STATE_PORTER_ROUTE)
    
    send_whatsapp(user.phone, config.PORTER_ROUTE_PROMPT)
    
    return jsonify({"status": "ok"}), 200


def handle_porter_route(user: User, message: str, db) -> tuple:
    """Обработка маршрута портер — переход к подтверждению"""
    cargo_type = user.get_temp_data('porter_cargo_type', 'other')
    saved_from = user.get_temp_data('porter_from_partial')

    if saved_from:
        # Уже ждём куда — берём сообщение как to_addr напрямую, без NLU
        from_addr = saved_from
        to_addr = message.strip()
        user.set_temp_data('porter_from_partial', None)
    else:
        # Первый ввод — парсим NLU
        nlu_result = parse_user_message(message)
        from_addr = nlu_result.get("from_address")
        to_addr = nlu_result.get("to_address")

        if not from_addr:
            from_addr = message

        if not to_addr:
            # to не найден — сохраняем from и спрашиваем куда
            user.set_temp_data('porter_from_partial', from_addr)
            send_whatsapp(user.phone, "📍 Куда везем? / Кайда ташыйбыз?")
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
    
    confirm_msg = config.CONFIRM_PORTER.format(
        cargo_type=cargo_type,
        from_address=from_addr,
        to_address=to_addr
    )
    send_whatsapp(user.phone, confirm_msg)
    
    return jsonify({"status": "ok"}), 200


# =============================================================================
# ANT (МУРАВЕЙ) FLOW
# =============================================================================

def handle_ant_route(user: User, message: str, db) -> tuple:
    """Обработка сообщения муравей — ИИ извлекает детали и маршрут"""
    nlu_result = parse_user_message(message)
    
    from_addr = nlu_result.get("from_address") or message
    to_addr = nlu_result.get("to_address") or message
    order_details = _sanitize_ant_details(
        nlu_result.get("order_details") or user.get_temp_data('ant_details', ''),
        from_addr=from_addr,
        to_addr=to_addr,
        source_text=message
    )
    
    # Проверка на слишком общий адрес
    if _is_vague_address(from_addr) or _is_vague_address(to_addr):
        user.set_temp_data('ant_details', order_details)
        send_whatsapp(user.phone, config.VAGUE_ADDRESS_PROMPT)
        return jsonify({"status": "ok"}), 200
    
    user.set_temp_data('service_type', config.SERVICE_ANT)
    user.set_temp_data('ant_details', order_details)
    user.set_temp_data('ant_from', from_addr)
    user.set_temp_data('ant_to', to_addr)
    user.set_temp_data('ant_route', f"{from_addr} — {to_addr}")
    user.set_state(config.STATE_CONFIRM_ORDER)
    
    confirm_msg = config.CONFIRM_ANT.format(
        order_details=order_details,
        from_address=from_addr,
        to_address=to_addr
    )
    send_whatsapp(user.phone, confirm_msg)
    
    return jsonify({"status": "ok"}), 200


# =============================================================================
# BUTTON RESPONSE HANDLER
# =============================================================================

def handle_button_response(user: User, button_response: str, db) -> tuple:
    """Обработка нажатия кнопок в WhatsApp"""
    from client_confirm_handler import handle_pharmacy_client_confirm
    
    try:
        _reset_unknown_fallback(user)

        # Такси: выбор цены
        if user.current_state == config.STATE_TAXI_PRICE_CHOICE:
            return handle_taxi_price_choice(user, button_response, db)
        
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
