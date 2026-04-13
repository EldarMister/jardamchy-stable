"""
Обработчик callback-запросов от Telegram
Telegram Handler Module for Business Assistant GO
Обновленная версия согласно ТЗ v2.0
"""

from flask import request, jsonify
import json
import logging
import re
from datetime import datetime, timedelta

import config
from db import get_db, get_runtime_setting
from services import (
    send_whatsapp, send_whatsapp_buttons, send_whatsapp_with_main_menu,
    send_telegram_private, dispatch_telegram_group_notification,
    edit_telegram_message, delete_telegram_message, format_phone,
    answer_telegram_callback, send_telegram_contact_request
)

# In-memory lock для защиты от двойного нажатия (1 действие = 1 нажатие)
import threading
_callback_locks = {}
_locks_lock = threading.Lock()

def _get_callback_lock(key: str):
    """Получить или создать lock для конкретного callback"""
    with _locks_lock:
        if key not in _callback_locks:
            _callback_locks[key] = threading.Lock()
        return _callback_locks[key]

def _cleanup_old_locks():
    """Очистка старых locks (вызывать периодически)"""
    with _locks_lock:
        # Оставляем только последние 1000 locks
        if len(_callback_locks) > 1000:
            # Удаляем случайные старые ключи
            keys_to_remove = list(_callback_locks.keys())[:500]
            for k in keys_to_remove:
                del _callback_locks[k]

logger = logging.getLogger(__name__)

def _runtime_setting(key: str, default):
    return get_runtime_setting(key, default)


def _bishkek_now_naive() -> datetime:
    return datetime.utcnow() + timedelta(hours=6)


# =============================================================================
# DRIVER PROFILE HELPERS
# =============================================================================

def _format_phone_for_whatsapp(phone: str) -> str:
    """Форматировать номер телефона для отображения в WhatsApp (формат: 0220 203 021)"""
    if not phone:
        return "—"
    
    # Удалить все нецифровые символы
    phone = ''.join(c for c in phone if c.isdigit())
    
    # Если номер начинается с 996 → заменить на 0
    if phone.startswith("996"):
        phone = "0" + phone[3:]
    
    # Если номер начинается с +996 → убрать + и 996, добавить 0
    if phone.startswith("+996"):
        phone = "0" + phone[4:]
    
    # Форматировать как XXXX XXX XXX (10 цифр: 4 + 3 + 3)
    if len(phone) == 10:
        return f"{phone[:4]} {phone[4:7]} {phone[7:]}"
    
    return phone


def _format_phone_for_telegram(phone: str) -> str:
    """Форматировать телефон для Telegram в кликабельный вид без пробелов (+996XXXXXXXXX)."""
    if not phone:
        return "—"

    raw = str(phone).strip()
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if not digits:
        return raw

    # KG international: 996XXXXXXXXX
    if digits.startswith("996") and len(digits) == 12:
        return f"+{digits}"

    # KG local: 0XXXXXXXXX -> +996XXXXXXXXX
    if digits.startswith("0") and len(digits) == 10:
        return f"+996{digits[1:]}"

    # RU local: 8XXXXXXXXXX -> +7XXXXXXXXXX
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]

    # RU mobile without country code: 9XXXXXXXXX -> +79XXXXXXXXX
    if digits.startswith("9") and len(digits) == 10:
        digits = "7" + digits

    # RU/KZ: 7XXXXXXXXXX
    if digits.startswith("7") and len(digits) == 11:
        return f"+{digits}"

    # Fallback: keep as international with +
    if len(digits) >= 9:
        return f"+{digits}"

    return raw


def _normalize_driver_profile(driver, fallback_name: str = "") -> dict:
    """Normalize driver profile data from DB with safe fallbacks."""
    def _clean(value):
        return (value or "").strip()

    name = _clean(driver.get('name') if driver else "")
    if not name:
        name = _clean(fallback_name)
    if not name:
        name = "—"

    phone_raw = _clean(driver.get('phone') if driver else "")
    phone = format_phone(phone_raw) if phone_raw else "—"

    car_model = _clean(driver.get('car_model') if driver else "") or "—"
    plate = _clean(driver.get('plate') if driver else "") or "—"

    return {
        "name": name,
        "phone": phone,
        "car_model": car_model,
        "plate": plate,
    }


def _answer_callback(callback_query_id: str, text: str = None) -> None:
    """Safely answer Telegram callback query (optional text)."""
    if not callback_query_id:
        return
    try:
        answer_telegram_callback(callback_query_id, text)
    except Exception:
        logger.exception("Failed to answer Telegram callback")


# =============================================================================
# TELEGRAM WEBHOOK HANDLER
# =============================================================================

def handle_telegram_webhook():
    """Главная функция обработки запросов от Telegram"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400
        
        # Обработка callback_query (нажатие кнопок)
        if 'callback_query' in data:
            return handle_callback_query(data['callback_query'])
        
        # Обработка обычных сообщений
        if 'message' in data:
            return handle_telegram_message(data['message'])
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling Telegram webhook")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_callback_query(callback_query: dict) -> tuple:
    """Обработка нажатия кнопок в Telegram"""
    try:
        data = callback_query.get('data', '')
        callback_query_id = callback_query.get('id', '')
        user_id = str(callback_query['from']['id'])
        user_name = callback_query['from'].get('first_name', 'Unknown')
        message_id = callback_query['message']['message_id']
        chat_id = str(callback_query['message']['chat']['id'])
        
        logger.info(f"Callback from {user_name} ({user_id}): {data}")

        # Дедупликация: проверяем не обрабатывали ли уже этот callback
        dedup_key = f"cbq_{callback_query_id}"
        if dedup_key in _callback_locks:
            return jsonify({"status": "ok"}), 200
        _callback_locks[dedup_key] = True
        
        # СРАЗУ отвечаем на callback для мгновенной реакции кнопки
        answer_telegram_callback(callback_query_id)
        
        db = get_db()
        
        # === КАФЕ ===
        if data.startswith("cafe_accept_"):
            return handle_cafe_accept(data, user_id, user_name, chat_id, message_id, db, callback_query_id)
        elif data.startswith("cafe_decline_"):
            return handle_cafe_decline(data, user_id, user_name, chat_id, message_id, db)
        elif data.startswith("cafe_ready_"):
            return handle_cafe_ready_time(data, user_id, user_name, db)
        elif data.startswith("cafe_delivery_self_"):
            return handle_cafe_self_courier_choice(data, user_id, user_name, db)
        elif data.startswith("cafe_delivery_go_"):
            return handle_cafe_go_courier_choice(data, user_id, user_name, db)
        elif data.startswith("cafe_self_done_"):
            return handle_cafe_self_delivery_done(data, user_id, user_name, db)
        
        # === ПОПУТКА ===
        elif data.startswith("poputka_accept_"):
            return handle_poputka_accept(data, user_id, user_name, chat_id, message_id, db, callback_query_id)

        # === РАЗНАРАБОЧИЙ ===
        elif data.startswith("razna_accept_"):
            return handle_raznarabochi_accept(data, user_id, user_name, chat_id, message_id, db, callback_query_id)

        # Pharmacy disabled
        elif data.startswith("pharm_bid_") or data.startswith("pharm_price_"):
            return _handle_removed_pharmacy_callback(user_id, callback_query_id)

        elif data.startswith("taxi_take_"):
            return handle_taxi_take(data, user_id, user_name, chat_id, message_id, db, callback_query_id)
        elif data.startswith("taxi_arrived_"):
            return handle_taxi_arrived(data, user_id, user_name, chat_id, message_id, db)
        elif data.startswith("taxi_cancel_"):
            return handle_taxi_cancel(data, user_id, user_name, chat_id, message_id, db)
        elif data.startswith("taxi_finish_"):
            return handle_taxi_finish(data, user_id, user_name, chat_id, message_id, db)
        
        # === ПОРТЕР / МУРАВЕЙ ===
        elif data.startswith("porter_take_") or data.startswith("ant_take_"):
            return handle_porter_take(data, user_id, user_name, chat_id, message_id, db, callback_query_id)
        
        # === МАГАЗИН ===
        elif data.startswith("shop_take_"):
            return handle_shop_take(data, user_id, user_name, db)
        elif data.startswith("shop_self_delivery_"):
            return handle_shop_self_delivery(data, user_id, db)
        elif data.startswith("shop_call_taxi_"):
            return handle_shop_call_taxi(data, user_id, chat_id, message_id, db)
        
        # === ДОСТАВКА ЕДЫ ===
        elif data.startswith("delivery_take_"):
            return handle_delivery_take(data, user_id, user_name, chat_id, message_id, db, callback_query_id)
        elif data.startswith("delivery_arrived_"):
            return handle_delivery_arrived(data, user_id, user_name, chat_id, message_id, db)
        elif data.startswith("delivery_finish_"):
            return handle_delivery_finish(data, user_id, user_name, chat_id, message_id, db)
        elif data.startswith("delivery_cancel_"):
            return handle_delivery_cancel(data, user_id, user_name, chat_id, message_id, db)
        
        # === АДМИН ===
        elif data.startswith("admin_"):
            return handle_admin_callback(data, user_id, db)
        
        # === РЕГИСТРАЦИЯ ВОДИТЕЛЕЙ ===
        elif data.startswith("dreg_"):
            return handle_driver_reg_callback(data, user_id, user_name, db)
        
        # === КОМАНДЫ ЧЕРЕЗ КНОПКИ ===
        elif data.startswith("cmd_"):
            return _handle_cmd_button(data, user_id, db)
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling callback query")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# CAFE HANDLERS
# =============================================================================

def handle_cafe_accept(data: str, user_id: str, user_name: str,
                       chat_id: str, message_id: int, db,
                       callback_query_id: str = None) -> tuple:
    """Обработка принятия заказа кафе"""
    try:
        order_id = data.split("_")[2]

        def _reply(text: str = None) -> None:
            _answer_callback(callback_query_id, text)

        # Получаем заказ
        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ не найден.")
            _reply()
            return jsonify({"status": "ok"}), 200
        if order.get('status') in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            _reply()
            return jsonify({"status": "ok"}), 200

        # Проверяем баланс кафе
        order_amount = order.get('price_total', 0) or 1000
        enough, balance, commission = db.check_cafe_balance(user_id, order_amount)
        if not enough:
            commission_pct = _runtime_setting('cafe_commission_percent', config.CAFE_COMMISSION_PERCENT)
            send_telegram_private(
                user_id,
                f"❌ *Недостаточно баланса для принятия заказа #{order_id}*\n\n"
                f"💰 Комиссия ({commission_pct}%): *{commission:.0f} сом*\n"
                f"💳 Ваш баланс: *{balance:.0f} сом*\n\n"
                f"Пополните баланс у администратора и попробуйте снова."
            )
            _reply()
            return jsonify({"status": "ok"}), 200

        # Обновляем статус
        db.update_order_status(order_id, config.ORDER_STATUS_ACCEPTED, provider_id=user_id)
        
        # Запрашиваем время готовности
        time_buttons = []
        for minutes in config.CAFE_READY_TIMES:
            time_buttons.append({
                "text": f"⏱ {minutes} мин",
                "callback": f"cafe_ready_{order_id}_{minutes}"
            })
        
        msg = f"""✅ *Заказ #{order_id} принят!*

Укажите время готовности:"""
        
        send_telegram_private(user_id, msg, time_buttons)
        
        # Обновляем сообщение в группе
        updated_text = f"""🍔 *ЗАКАЗ #{order_id} - ПРИНЯТ* ✅

🏠 *Кафе:* {user_name}
⏱ Ожидаем время готовности...

📞 Клиент: {order.get('client_phone', 'N/A')}"""
        
        edit_telegram_message(chat_id, message_id, updated_text, buttons=[])
        
        # Уведомляем клиента — кратко, полные данные придут после выбора времени готовности
        client_msg = f"✅ *Заказ #{order_id}* принят кафе *{user_name}*. Ожидайте время готовности..."
        send_whatsapp(order.get('client_phone', ''), client_msg)
        
        db.log_transaction("CAFE_ORDER_ACCEPTED", user_id, order_id)
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling cafe accept")
        send_telegram_private(user_id, "❌ Ошибка при принятии заказа.")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_cafe_decline(data: str, user_id: str, user_name: str,
                        chat_id: str, message_id: int, db) -> tuple:
    """Обработка отказа кафе с запросом причины."""
    try:
        order_id = data.split("_")[2]

        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ не найден.")
            return jsonify({"status": "ok"}), 200

        status = order.get('status')
        if status in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            return jsonify({"status": "ok"}), 200
        if status in (config.ORDER_STATUS_ACCEPTED, config.ORDER_STATUS_READY, config.ORDER_STATUS_IN_DELIVERY):
            send_telegram_private(user_id, "❌ Заказ уже в работе. Отказ недоступен.")
            return jsonify({"status": "ok"}), 200

        # Сразу отменяем заказ и отключаем кнопки в группе
        db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED, provider_id=user_id)

        updated_text = f"""❌ *ЗАКАЗ #{order_id} - ОТКАЗ*

🏠 *Кафе:* {user_name}
📝 Ожидаем комментарий причины отказа...

📞 Клиент: {order.get('client_phone', '')}"""
        edit_telegram_message(chat_id, message_id, updated_text, buttons=[])

        # Помечаем аукцион обработанным, чтобы не сработал таймаут
        timer = db.get_latest_auction_timer(order_id, config.SERVICE_CAFE)
        if timer:
            db.mark_auction_processed(timer['id'])

        # Запрашиваем причину в ЛС
        db.set_telegram_session_state(user_id, config.STATE_CAFE_DECLINE_REASON)
        db.set_telegram_session_data(user_id, "cafe_decline_order_id", order_id)
        db.set_telegram_session_data(user_id, "cafe_decline_chat_id", chat_id)
        db.set_telegram_session_data(user_id, "cafe_decline_message_id", message_id)

        send_telegram_private(user_id, config.CAFE_DECLINE_PROMPT)
        db.log_transaction("CAFE_ORDER_DECLINED", user_id, order_id)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error handling cafe decline")
        send_telegram_private(user_id, "❌ Ошибка при отказе от заказа.")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_cafe_ready_time(data: str, user_id: str, user_name: str, db) -> tuple:
    """Обработка времени готовности кафе"""
    try:
        parts = data.split("_")
        order_id = parts[2]
        ready_time = int(parts[3])
        
        # Получаем заказ
        order = db.get_order(order_id)
        if not order:
            return jsonify({"status": "error"}), 404
        if order.get('status') in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            return jsonify({"status": "ok"}), 200
        if order.get('provider_id') and str(order.get('provider_id')) != str(user_id):
            send_telegram_private(user_id, "❌ Этот заказ закреплён за другим кафе.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_ACCEPTED:
            send_telegram_private(user_id, "❌ Заказ ещё не принят кафе. Указать время нельзя.")
            return jsonify({"status": "ok"}), 200

        # Обновляем заказ
        db.update_order_status(order_id, config.ORDER_STATUS_READY, ready_time=ready_time)

        # Списываем комиссию с баланса
        order_amount = order.get('price_total', 0) or 1000
        commission_pct = _runtime_setting('cafe_commission_percent', config.CAFE_COMMISSION_PERCENT)
        commission_amount = round(order_amount * commission_pct / 100)
        _, new_balance = db.deduct_cafe_balance(user_id, order_amount)

        client_phone = order.get('client_phone', 'N/A')
        cafe_name, cafe_phone = _get_cafe_identity(db, user_id, user_name)

        # Уведомление кафе: номер заказа, комиссия, остаток баланса, данные клиента
        send_telegram_private(
            user_id,
            f"✅ *Заказ #{order_id}* — время готовности *{ready_time} мин* сохранено.\n\n"
            f"💰 Списано ({commission_pct}%): *{commission_amount} сом*\n"
            f"💳 Остаток баланса: *{new_balance:.0f} сом*\n\n"
            f"👤 *Данные клиента:*\n"
            f"📞 Телефон: {_format_phone_for_whatsapp(client_phone)}\n"
            f"📍 Адрес: {order.get('address', '—')}"
        )

        # Уведомление клиента: данные кафе + время готовности
        cafe_phone_line = f"📞 *Телефон кафе:* {_format_phone_for_whatsapp(cafe_phone)}\n" if cafe_phone else ""
        client_msg = (
            f"✅ *Заказ #{order_id} подтверждён!*\n\n"
            f"🏠 *Кафе:* {cafe_name}\n"
            f"{cafe_phone_line}"
            f"⏱ *Время готовности:* {ready_time} минут\n"
            f"📍 *Адрес доставки:* {order.get('address', '—')}"
        )
        send_whatsapp(client_phone, client_msg)

        db.log_transaction(
            "CAFE_READY_TIME_SET",
            user_id,
            order_id,
            details=f"Ready in {ready_time} min"
        )

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error handling cafe ready time")
        return jsonify({"status": "error", "message": str(e)}), 500


def _normalize_cafe_courier_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", (raw or "").strip())
    if digits.startswith("996") and len(digits) == 12:
        digits = "0" + digits[3:]
    elif len(digits) == 9:
        digits = "0" + digits

    if len(digits) != 10 or not digits.startswith("0"):
        return None

    return digits


def _get_cafe_identity(db, cafe_telegram_id: str, fallback_name: str) -> tuple[str, str]:
    cafe_profile = db.get_cafe(cafe_telegram_id) or {}
    cafe_name = (cafe_profile.get('name') or fallback_name or "Кафе").strip()
    cafe_phone = (cafe_profile.get('phone') or '').strip()
    return cafe_name, cafe_phone


def _dispatch_cafe_order_to_go_courier(order: dict, cafe_name: str, cafe_phone: str, commission_info: str) -> None:
    order_id = order['order_id']
    ready_time = order.get('ready_time') or 0
    order_details = (order.get('details') or '').strip()
    details_block = f"\n📋 *Состав заказа:*\n{order_details[:500]}" if order_details else ""

    taxi_msg = f"""📦 *ДОСТАВКА ЕДЫ*

🏠 *Забрать из:* {cafe_name}
📋 *Заказ:* #{order_id}
{details_block}
⏱ *Готово через:* {ready_time} мин
📍 *Куда:* {order.get('address', 'Уточнить')}
💳 *Оплата:* {config.PAYMENT_METHODS.get(order.get('payment_method'), 'Наличные')}

📞 *Клиент:* {order.get('client_phone', '')}

{commission_info}"""

    buttons = [{
        "text": "🚖 Взять доставку",
        "callback": f"delivery_take_{order_id}"
    }]
    dispatch_telegram_group_notification(config.GROUP_TAXI_ID, taxi_msg, buttons)

    cafe_phone_line = ""
    if cafe_phone:
        cafe_phone_line = f"📞 *Телефон кафе:* {_format_phone_for_whatsapp(cafe_phone)}\n"

    client_msg = f"""✅ *Заказ #{order_id}*

🏠 *Кафе:* {cafe_name}
{cafe_phone_line}⏱ *Готово через:* {ready_time} минут
🚖 Ищем курьера для доставки...
"""
    send_whatsapp(order.get('client_phone', ''), client_msg)


def handle_cafe_go_courier_choice(data: str, user_id: str, user_name: str, db) -> tuple:
    """Кафе выбрало доставку через Жардамчы GO."""
    try:
        order_id = data.split("_")[3]
        order = db.get_order(order_id)
        if not order:
            return jsonify({"status": "error"}), 404

        if order.get('status') in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            return jsonify({"status": "ok"}), 200
        if order.get('provider_id') and str(order.get('provider_id')) != str(user_id):
            send_telegram_private(user_id, "❌ Этот заказ закреплён за другим кафе.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_READY:
            send_telegram_private(user_id, "❌ Сначала укажите время готовности заказа.")
            return jsonify({"status": "ok"}), 200
        if order.get('delivery_mode') == config.DELIVERY_MODE_JARDAMCHY_GO:
            send_telegram_private(user_id, f"✅ Заказ #{order_id} уже передан курьерам Жардамчы GO.")
            return jsonify({"status": "ok"}), 200
        if order.get('delivery_mode') == config.DELIVERY_MODE_SELF and order.get('external_courier_phone'):
            send_telegram_private(user_id, f"❌ Заказ #{order_id} уже закреплён за вашим курьером.")
            return jsonify({"status": "ok"}), 200

        db.clear_telegram_session(user_id)
        db.set_order_delivery_mode(order_id, config.DELIVERY_MODE_JARDAMCHY_GO)

        cafe_name, cafe_phone = _get_cafe_identity(db, user_id, user_name)
        commission_info = (
            f"💰 Комиссия ({_runtime_setting('cafe_commission_percent', config.CAFE_COMMISSION_PERCENT)}%) "
            "добавлена в долг"
        )
        _dispatch_cafe_order_to_go_courier(order, cafe_name, cafe_phone, commission_info)

        send_telegram_private(user_id, f"✅ Заказ #{order_id} передан на доставку Жардамчы GO. {commission_info}")
        db.log_transaction("CAFE_DELIVERY_GO_SELECTED", user_id, order_id)
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error handling cafe GO courier choice")
        send_telegram_private(user_id, "❌ Ошибка при передаче заказа курьерам Жардамчы GO.")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_cafe_self_courier_choice(data: str, user_id: str, user_name: str, db) -> tuple:
    """Кафе выбрало доставку своим курьером."""
    try:
        order_id = data.split("_")[3]
        order = db.get_order(order_id)
        if not order:
            return jsonify({"status": "error"}), 404

        if order.get('status') in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            return jsonify({"status": "ok"}), 200
        if order.get('provider_id') and str(order.get('provider_id')) != str(user_id):
            send_telegram_private(user_id, "❌ Этот заказ закреплён за другим кафе.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_READY:
            send_telegram_private(user_id, "❌ Сначала укажите время готовности заказа.")
            return jsonify({"status": "ok"}), 200
        if order.get('delivery_mode') == config.DELIVERY_MODE_JARDAMCHY_GO:
            send_telegram_private(user_id, f"❌ Заказ #{order_id} уже передан курьерам Жардамчы GO.")
            return jsonify({"status": "ok"}), 200
        if order.get('delivery_mode') == config.DELIVERY_MODE_SELF and order.get('external_courier_phone'):
            send_telegram_private(user_id, f"✅ Для заказа #{order_id} номер вашего курьера уже сохранён.")
            return jsonify({"status": "ok"}), 200

        db.set_order_delivery_mode(order_id, config.DELIVERY_MODE_SELF)
        db.set_telegram_session_state(user_id, config.STATE_CAFE_OWN_COURIER_PHONE)
        db.set_telegram_session_data(user_id, "cafe_self_courier_order_id", order_id)

        prompt = (
            f"📱 Отправьте номер телефона вашего курьера для заказа #{order_id}.\n\n"
            "Клиент увидит этот номер.\n"
            "Можно отправить контакт кнопкой ниже или написать номер текстом."
        )
        send_telegram_contact_request(user_id, prompt, "📱 Отправить контакт курьера")
        db.log_transaction("CAFE_SELF_COURIER_SELECTED", user_id, order_id)
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error handling cafe self courier choice")
        send_telegram_private(user_id, "❌ Ошибка при выборе своего курьера.")
        return jsonify({"status": "error", "message": str(e)}), 500


def _handle_cafe_self_courier_phone(user_id: str, user_name: str, text: str, db) -> tuple:
    """Сохранить номер внешнего курьера кафе и уведомить клиента."""
    phone = _normalize_cafe_courier_phone(text)
    if not phone:
        send_telegram_private(
            user_id,
            "⚠️ Введите корректный номер курьера.\nПример: `0555123456`."
        )
        return jsonify({"status": "ok"}), 200

    order_id = db.get_telegram_session_data(user_id, "cafe_self_courier_order_id")
    if not order_id:
        db.clear_telegram_session(user_id)
        send_telegram_private(user_id, "❌ Сессия выбора курьера не найдена. Укажите время готовности заново.")
        return jsonify({"status": "ok"}), 200

    order = db.get_order(order_id)
    if not order:
        db.clear_telegram_session(user_id)
        send_telegram_private(user_id, "❌ Заказ не найден.")
        return jsonify({"status": "ok"}), 200
    if order.get('status') in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
        db.clear_telegram_session(user_id)
        send_telegram_private(user_id, "❌ Заказ уже закрыт.")
        return jsonify({"status": "ok"}), 200
    if order.get('provider_id') and str(order.get('provider_id')) != str(user_id):
        db.clear_telegram_session(user_id)
        send_telegram_private(user_id, "❌ Этот заказ закреплён за другим кафе.")
        return jsonify({"status": "ok"}), 200
    if order.get('status') != config.ORDER_STATUS_READY:
        db.clear_telegram_session(user_id)
        send_telegram_private(user_id, "❌ Для этого заказа уже нельзя сохранить номер курьера.")
        return jsonify({"status": "ok"}), 200

    db.set_order_delivery_mode(order_id, config.DELIVERY_MODE_SELF, phone)
    db.clear_telegram_session(user_id)

    cafe_name, cafe_phone = _get_cafe_identity(db, user_id, user_name)
    ready_time = order.get('ready_time') or 0
    cafe_phone_line = ""
    if cafe_phone:
        cafe_phone_line = f"📞 *Телефон кафе:* {_format_phone_for_whatsapp(cafe_phone)}\n"

    client_msg = f"""✅ *Заказ #{order_id}*

🏠 *Кафе:* {cafe_name}
{cafe_phone_line}⏱ *Готово через:* {ready_time} минут
🚶 *Доставка:* своим курьером кафе
📞 *Телефон курьера:* {_format_phone_for_whatsapp(phone)}
"""
    send_whatsapp(order.get('client_phone', ''), client_msg)

    buttons = [{"text": "✅ Заказ доставлен", "callback": f"cafe_self_done_{order_id}"}]
    send_telegram_private(
        user_id,
        f"✅ Номер курьера сохранён для заказа #{order_id}.\n"
        f"Клиент получил номер: {_format_phone_for_whatsapp(phone)}.\n\n"
        "Когда заказ будет доставлен, нажмите кнопку ниже.",
        buttons
    )

    db.log_transaction("CAFE_SELF_COURIER_PHONE_SAVED", user_id, order_id, details=phone)
    return jsonify({"status": "ok"}), 200


def handle_cafe_self_delivery_done(data: str, user_id: str, user_name: str, db) -> tuple:
    """Кафе подтверждает, что свой курьер доставил заказ."""
    try:
        order_id = data.split("_")[3]
        order = db.get_order(order_id)
        if not order:
            return jsonify({"status": "error"}), 404

        if order.get('status') in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            return jsonify({"status": "ok"}), 200
        if order.get('provider_id') and str(order.get('provider_id')) != str(user_id):
            send_telegram_private(user_id, "❌ Этот заказ закреплён за другим кафе.")
            return jsonify({"status": "ok"}), 200
        if order.get('delivery_mode') != config.DELIVERY_MODE_SELF:
            send_telegram_private(user_id, "❌ Этот заказ не отмечен как доставка своим курьером.")
            return jsonify({"status": "ok"}), 200

        db.update_order_status(order_id, config.ORDER_STATUS_COMPLETED, completed_at=datetime.now())
        send_whatsapp_with_main_menu(
            order.get('client_phone', ''),
            "✅ Заказ доставлен курьером кафе. Спасибо, что выбрали нас!"
        )
        send_telegram_private(user_id, f"✅ Заказ #{order_id} закрыт как доставленный вашим курьером.")
        db.log_transaction("CAFE_SELF_DELIVERY_COMPLETED", user_id, order_id)
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error finishing cafe self delivery")
        send_telegram_private(user_id, "❌ Ошибка при завершении доставки своим курьером.")
        return jsonify({"status": "error", "message": str(e)}), 500


def _handle_cafe_decline_reason(user_id: str, user_name: str, reason: str, db) -> tuple:
    """Сохранить причину отказа и уведомить клиента."""
    reason = (reason or "").strip()
    if not reason:
        send_telegram_private(user_id, "❌ Причина не может быть пустой. Напишите причину отказа.")
        return jsonify({"status": "ok"}), 200

    order_id = db.get_telegram_session_data(user_id, "cafe_decline_order_id")
    chat_id = db.get_telegram_session_data(user_id, "cafe_decline_chat_id")
    message_id = db.get_telegram_session_data(user_id, "cafe_decline_message_id")

    if not order_id:
        db.clear_telegram_session(user_id)
        send_telegram_private(user_id, "❌ Не найден заказ для отказа.")
        return jsonify({"status": "ok"}), 200

    order = db.get_order(order_id)
    if not order:
        db.clear_telegram_session(user_id)
        send_telegram_private(user_id, "❌ Заказ не найден.")
        return jsonify({"status": "ok"}), 200

    # Обновляем сообщение в группе с причиной
    if chat_id and message_id:
        updated_text = f"""❌ *ЗАКАЗ #{order_id} - ОТКАЗ*

🏠 *Кафе:* {user_name}
📝 Причина: {reason}

📞 Клиент: {order.get('client_phone', '')}"""
        edit_telegram_message(chat_id, int(message_id), updated_text, buttons=[])

    client_msg = config.CAFE_DECLINE_CLIENT.format(order_id=order_id, reason=reason)
    send_whatsapp(order.get('client_phone', ''), client_msg)

    db.clear_telegram_session(user_id)
    db.log_transaction("CAFE_DECLINE_REASON", user_id, order_id, details=reason[:200])
    return jsonify({"status": "ok"}), 200


# =============================================================================
def _handle_removed_pharmacy_callback(user_id: str, callback_query_id: str = None) -> tuple:
    send_telegram_private(user_id, config.PHARMACY_DISABLED_MESSAGE)
    _answer_callback(callback_query_id, "Аптека өчүрүлдү")
    return jsonify({"status": "ok"}), 200


# PHARMACY HANDLERS
# =============================================================================

def handle_pharmacy_bid(data: str, user_id: str, user_name: str,
                        chat_id: str, message_id: int, db) -> tuple:
    """Обработка отклика аптеки - запрос цены"""
    try:
        order_id = data.split("_")[2]

        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ не найден.")
            return jsonify({"status": "ok"}), 200

        status = order.get('status')
        if status in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            return jsonify({"status": "ok"}), 200

        current_provider = order.get('provider_id')
        if current_provider and str(current_provider) != str(user_id):
            send_telegram_private(user_id, "❌ Этот заказ уже забрала другая аптека.")
            return jsonify({"status": "ok"}), 200

        # Помечаем заказ как забранный аптекой
        db.update_order_status(order_id, config.ORDER_STATUS_ACCEPTED, provider_id=user_id)

        # Обновляем сообщение в группе: кнопка больше не активна
        group_text = f"""💊 *ЗАКАЗ ЗАБРАН АПТЕКОЙ* ✅

🏥 *Аптека:* {user_name}
📋 *Заказ:* #{order_id}

⏱ Ожидаем цену от аптеки..."""
        edit_telegram_message(chat_id, message_id, group_text, buttons=[])
        
        # Запрашиваем цену у аптеки через ЛС
        msg = f"""💊 *УКАЖИТЕ ЦЕНУ*

Заказ: #{order_id}

Ответьте на это сообщение указав цену (только цифра):

Пример: *450*"""
        
        send_telegram_private(user_id, msg)
        
        # Сохраняем контекст
        db.set_telegram_session_data(user_id, 'pending_pharmacy_order', order_id)
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling pharmacy bid")
        return jsonify({"status": "error", "message": str(e)}), 500


def _submit_pharmacy_price(order_id: str, user_id: str, user_name: str, price: float, db) -> tuple:
    """Сохранить цену аптеки и попросить клиента ввести адрес доставки."""
    try:
        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ не найден.")
            return jsonify({"status": "ok"}), 200

        if order.get('service_type') != config.SERVICE_PHARMACY:
            send_telegram_private(user_id, "❌ Это не заказ аптеки.")
            return jsonify({"status": "ok"}), 200

        if order.get('status') in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            return jsonify({"status": "ok"}), 200

        current_provider = order.get('provider_id')
        if current_provider and str(current_provider) != str(user_id):
            send_telegram_private(user_id, "❌ Этот заказ уже обрабатывает другая аптека.")
            return jsonify({"status": "ok"}), 200

        # Фиксируем аптеку и цену лекарства
        db.add_pharmacy_bid(order_id, user_id, price)
        db.update_order_status(order_id, config.ORDER_STATUS_ACCEPTED, provider_id=user_id, price=price)

        client_phone = order.get('client_phone', '')
        client_user = db.get_user(client_phone)
        if client_user:
            client_user.set_state(config.STATE_PHARMACY_ADDRESS)
            client_user.set_temp_data('service_type', config.SERVICE_PHARMACY)
            client_user.set_temp_data('pharmacy_order_id', order_id)
            client_user.set_temp_data('pharmacy_selected_pharmacy_id', user_id)
            client_user.set_temp_data('pharmacy_selected_pharmacy_name', user_name)
            client_user.set_temp_data('pharmacy_selected_price', float(price))

        client_msg = f"""💊 *Лекарство найдено*

🏥 *Аптека:* {user_name}
💵 *Цена лекарства:* {int(price)} сом

📍 Введите адрес доставки.
Заказ автоматически оформим после адреса."""
        send_whatsapp(client_phone, client_msg)

        send_telegram_private(
            user_id,
            f"✅ Цена указана: {int(price)} сом\n\nОжидаем адрес клиента для оформления."
        )
        db.set_telegram_session_data(user_id, 'pending_pharmacy_order', None)

        db.log_transaction("PHARMACY_PRICE_SUBMITTED", user_id, order_id, amount=price)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.exception("Error submitting pharmacy price")
        send_telegram_private(user_id, "❌ Ошибка при отправке цены.")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_pharmacy_price_submit(data: str, user_id: str, user_name: str, db) -> tuple:
    """Обработка отправки цены аптекой"""
    try:
        parts = data.split("_")
        order_id = parts[2]
        price = float(parts[3])
        return _submit_pharmacy_price(order_id, user_id, user_name, price, db)
        
    except Exception as e:
        logger.exception("Error handling pharmacy price submit")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# TAXI HANDLERS
# =============================================================================

def _taxi_driver_key(order_id: str, suffix: str) -> str:
    return f"taxi_order_{order_id}_{suffix}"


def _close_taxi_driver_message(chat_id: str, message_id: int, text: str) -> None:
    """Закрыть (деактивировать) сообщение с кнопками у водителя."""
    try:
        if chat_id and message_id:
            edit_telegram_message(chat_id, message_id, text, buttons=[])
    except Exception:
        logger.exception("Failed to close taxi driver message")


def _is_taxi_order_closed(order: dict) -> bool:
    status = order.get('status')
    return status in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED)


def handle_taxi_take(data: str, user_id: str, user_name: str,
                     chat_id: str, message_id: int, db,
                     callback_query_id: str = None) -> tuple:
    """Обработка взятия заказа таксистом"""
    lock = None
    try:
        order_id = data.split("_")[2]
        
        # Защита от двойного нажатия: 1 заказ = 1 действие
        lock_key = f"taxi_take_{order_id}_{user_id}"
        lock = _get_callback_lock(lock_key)
        if not lock.acquire(blocking=False):
            return jsonify({"status": "ok"}), 200
        
        # Получаем информацию о водителе (нужна для проверки баланса и комиссии)
        driver = db.get_driver(user_id)
        if not driver:
            send_telegram_private(
                user_id,
                "❌ Вы не зарегистрированы!\n\nДля регистрации напишите боту /register в личные сообщения."
            )
            return jsonify({"status": "ok"}), 200

        # Скутеры могут брать только заказы доставки (не такси)
        if driver.get('driver_type') in config.DELIVERY_ONLY_DRIVER_TYPES:
            order_check = db.get_order(order_id)
            if order_check and order_check.get('service_type') == config.SERVICE_TAXI:
                answer_telegram_callback(callback_query_id, "❌ Скутеры не берут заказы такси")
                send_telegram_private(
                    user_id,
                    "🛵 Вы зарегистрированы как скутер.\n\n"
                    "Скутеры могут брать только заказы доставки еды (кафе) и товаров из магазина.\n"
                    "Заказы такси недоступны."
                )
                return jsonify({"status": "ok"}), 200

        # Проверяем баланс до атомарного захвата
        balance = float(driver.get('balance', 0))
        if balance < config.MIN_DRIVER_BALANCE:
            send_telegram_private(
                user_id,
                f"❌ *Недостаточно средств!*\n\n"
                f"💰 Ваш баланс: *{balance} сом*\n"
                f"⚠️ Минимальный баланс для приёма заказов: *{config.MIN_DRIVER_BALANCE} сом*\n\n"
                f"📌 Пополните баланс и попробуйте снова."
            )
            return jsonify({"status": "ok"}), 200

        # Проверяем: нет ли у водителя уже активного заказа
        active = db.get_driver_active_order(user_id)
        if active and str(active.get('order_id')) != str(order_id):
            send_telegram_private(
                user_id,
                f"⚠️ У вас уже есть активный заказ #{active['order_id']}.\n\n"
                f"Завершите или отмените текущий заказ перед тем, как брать новый."
            )
            return jsonify({"status": "ok"}), 200

        # Предопределяем комиссию (без обращения к заказу)
        # Комиссия определяется позже на основе цены заказа
        commission = _runtime_setting("taxi_commission", config.TAXI_COMMISSION)  # default

        # АТОМАРНЫЙ ЗАХВАТ: одним UPDATE проверяем и назначаем
        now = datetime.now()
        assigned = db.assign_order_to_driver(
            order_id,
            config.ORDER_STATUS_IN_DELIVERY,
            driver_id=user_id,
            allowed_statuses=[
                config.ORDER_STATUS_PENDING,
                config.ORDER_STATUS_AUCTION,
                config.ORDER_STATUS_URGENT
            ],
            driver_assigned_at=now,
            driver_commission=commission
        )
        
        if not assigned:
            # Уточняем причину неудачи
            order = db.get_order(order_id)
            if not order:
                send_telegram_private(user_id, "❌ Заказ не найден")
            elif order.get('driver_id') == str(user_id):
                send_telegram_private(user_id, "✅ Заказ уже ваш")
            elif order.get('driver_id'):
                send_telegram_private(user_id, "❌ Заказ уже забрал другой")
            else:
                send_telegram_private(user_id, "❌ Заказ уже недоступен")
            return jsonify({"status": "ok"}), 200
        
        # Заказ захвачен - получаем данные для уведомлений
        order = db.get_order(order_id)
        client_phone_tg = _format_phone_for_telegram(order.get('client_phone', ''))
        
        # Сразу обновляем сообщение в группе
        updated_text = f"""🚖 *ЗАКАЗ ЗАБРАН* ✅

👤 Водитель: *{user_name}*
📞 Клиент: {client_phone_tg}

⏱ Заказ в работе."""
        edit_telegram_message(chat_id, message_id, updated_text, buttons=[])
        
        # Списываем комиссию
        success, new_balance = db.update_driver_balance(
            user_id, 
            -commission,
            reason=f"Taxi order {order_id}"
        )
        commission_msg = f"\n💰 Списано комиссии: {commission} сом\n💳 Новый баланс: {new_balance} сом"
        
        profile = _normalize_driver_profile(driver, user_name)
        driver_name = profile["name"]
        driver_phone = _format_phone_for_whatsapp(profile["phone"])
        driver_car = profile["car_model"]
        driver_plate = profile["plate"]

        # Сообщаем клиенту
        driver_msg = f"""✅ *Машина найдена и выехала!*

🚘 *Автомобиль:* {driver_car}
🔢 *Номер:* {driver_plate}
👤 *Водитель:* {driver_name}
📞 *Телефон:* {driver_phone}

⏱ Ожидайте прибытия."""
        
        send_whatsapp(order.get('client_phone', ''), driver_msg)
        
        # Сообщаем водителю с кнопкой "Приехал"
        driver_private_msg = f"""🚖 *Заказ ваш!*

📞 *Клиент:* {client_phone_tg}
🛣 *Маршрут:* {order.get('details', '')}

💰 Не забудьте взять оплату по прибытию.{commission_msg}

✅ Удачной поездки!"""
        
        arrived_button = [
            {"text": "📍 Я приехал", "callback": f"taxi_arrived_{order_id}"},
            {"text": "❌ Отмена", "callback": f"taxi_cancel_{order_id}"}
        ]
        
        private_result = send_telegram_private(user_id, driver_private_msg, arrived_button)
        if private_result and private_result.get("message_id"):
            db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "active_message_id"), int(private_result["message_id"]))
            db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "arrived_notified"), False)
            db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "closed"), False)
        
        # Обновляем сообщение в группе
        updated_text = f"""🚖 *ЗАКАЗ ЗАБРАН* ✅

👤 Водитель: *{user_name}*
📞 Клиент: {client_phone_tg}

⏱ Заказ в работе."""
        
        edit_telegram_message(chat_id, message_id, updated_text, buttons=[])
        
        # Таймер на удаление сообщения "ЗАКАЗ ЗАБРАН" через 30 мин
        db.create_auction_timer(
            order_id=order_id,
            service_type='taxi_accepted',
            telegram_message_id=str(message_id),
            chat_id=chat_id,
            timeout_seconds=int(_runtime_setting("taxi_accepted_timeout", config.TAXI_ACCEPTED_TIMEOUT))
        )
        
        db.log_transaction("TAXI_ORDER_TAKEN", user_id, order_id)
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling taxi take")
        send_telegram_private(user_id, "❌ Ошибка при взятии заказа.")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        # Освобождаем lock
        if lock:
            try:
                lock.release()
            except:
                pass


def handle_taxi_arrived(data: str, user_id: str, user_name: str,
                        chat_id: str, message_id: int, db) -> tuple:
    """Обработка кнопки 'Я приехал'."""
    try:
        order_id = data.split("_")[2]
        order = db.get_order(order_id)
        if not order:
            _close_taxi_driver_message(chat_id, message_id, "❌ Заказ уже закрыт или не найден.")
            return jsonify({"status": "ok"}), 200
        if _is_taxi_order_closed(order):
            _close_taxi_driver_message(chat_id, message_id, "❌ Заказ уже закрыт. Кнопки отключены.")
            return jsonify({"status": "ok"}), 200
        if order.get('driver_id') and str(order.get('driver_id')) != str(user_id):
            _close_taxi_driver_message(chat_id, message_id, "❌ Этот заказ закреплён за другим водителем.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_IN_DELIVERY:
            _close_taxi_driver_message(chat_id, message_id, "❌ Действие недоступно для текущего статуса заказа.")
            return jsonify({"status": "ok"}), 200

        active_msg_id = db.get_telegram_session_data(user_id, _taxi_driver_key(order_id, "active_message_id"))
        if active_msg_id and str(active_msg_id) != str(message_id):
            _close_taxi_driver_message(chat_id, message_id, "❌ Это устаревшее сообщение. Используйте актуальное.")
            return jsonify({"status": "ok"}), 200

        arrived_notified = db.get_telegram_session_data(user_id, _taxi_driver_key(order_id, "arrived_notified"), False)
        if arrived_notified:
            _close_taxi_driver_message(chat_id, message_id, "✅ Клиент уже уведомлён.")
            return jsonify({"status": "ok"}), 200

        driver = db.get_driver(user_id)
        profile = _normalize_driver_profile(driver, user_name)
        driver_name = profile["name"]
        driver_phone = _format_phone_for_whatsapp(profile["phone"])
        driver_car = profile["car_model"]
        driver_plate = profile["plate"]
        car_info = f"\n🚘 *{driver_car}* | {driver_plate}"

        client_msg = (
            "📍 *Водитель приехал и ожидает вас!*"
            f"{car_info}\n"
            f"👤 *Водитель:* {driver_name}\n"
            f"📞 *Телефон:* {driver_phone}\n\n"
            "🚶 Пожалуйста, выходите."
        )
        send_whatsapp(order.get('client_phone', ''), client_msg)

        db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "arrived_notified"), True)
        db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "active_message_id"), int(message_id))

        edit_telegram_message(
            chat_id,
            message_id,
            "✅ *Клиент уведомлён!*\n\n📍 Ожидайте клиента.",
            [
                {"text": "✅ Завершить поездку", "callback": f"taxi_finish_{order_id}"},
                {"text": "❌ Отменить", "callback": f"taxi_cancel_{order_id}"}
            ]
        )

        db.log_transaction("TAXI_DRIVER_ARRIVED", user_id, order_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.exception("Error handling taxi arrived")
        _close_taxi_driver_message(chat_id, message_id, "❌ Ошибка обработки действия.")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_taxi_finish(data: str, user_id: str, user_name: str,
                       chat_id: str, message_id: int, db) -> tuple:
    """Завершение поездки водителем."""
    try:
        order_id = data.split("_")[2]
        order = db.get_order(order_id)
        if not order:
            _close_taxi_driver_message(chat_id, message_id, "❌ Заказ уже закрыт или не найден.")
            return jsonify({"status": "ok"}), 200
        if _is_taxi_order_closed(order):
            _close_taxi_driver_message(chat_id, message_id, "❌ Заказ уже закрыт. Кнопки отключены.")
            return jsonify({"status": "ok"}), 200
        if order.get('driver_id') and str(order.get('driver_id')) != str(user_id):
            _close_taxi_driver_message(chat_id, message_id, "❌ Этот заказ закреплён за другим водителем.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_IN_DELIVERY:
            _close_taxi_driver_message(chat_id, message_id, "❌ Действие недоступно для текущего статуса заказа.")
            return jsonify({"status": "ok"}), 200

        active_msg_id = db.get_telegram_session_data(user_id, _taxi_driver_key(order_id, "active_message_id"))
        if active_msg_id and str(active_msg_id) != str(message_id):
            _close_taxi_driver_message(chat_id, message_id, "❌ Это устаревшее сообщение. Используйте актуальное.")
            return jsonify({"status": "ok"}), 200

        arrived_notified = db.get_telegram_session_data(user_id, _taxi_driver_key(order_id, "arrived_notified"), False)
        if not arrived_notified:
            _close_taxi_driver_message(chat_id, message_id, "❌ Сначала нажмите «Я приехал».")
            return jsonify({"status": "ok"}), 200

        db.update_order_status(order_id, config.ORDER_STATUS_COMPLETED, completed_at=datetime.now())
        send_whatsapp_with_main_menu(
            order.get('client_phone', ''),
            "✅ Ваша поездка завершена. Спасибо, что выбрали нас!"
        )

        db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "closed"), True)
        db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "active_message_id"), int(message_id))
        _close_taxi_driver_message(chat_id, message_id, "✅ Поездка завершена. Заказ закрыт.")

        db.log_transaction("TAXI_TRIP_FINISHED", user_id, order_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.exception("Error finishing taxi trip")
        _close_taxi_driver_message(chat_id, message_id, "❌ Ошибка завершения поездки.")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_taxi_cancel(data: str, user_id: str, user_name: str,
                       chat_id: str, message_id: int, db) -> tuple:
    """Отмена заказа водителем."""
    try:
        order_id = data.split("_")[2]
        order = db.get_order(order_id)
        if not order:
            _close_taxi_driver_message(chat_id, message_id, "❌ Заказ уже закрыт или не найден.")
            return jsonify({"status": "ok"}), 200
        if _is_taxi_order_closed(order):
            _close_taxi_driver_message(chat_id, message_id, "❌ Заказ уже закрыт. Кнопки отключены.")
            return jsonify({"status": "ok"}), 200
        if order.get('driver_id') and str(order.get('driver_id')) != str(user_id):
            _close_taxi_driver_message(chat_id, message_id, "❌ Этот заказ закреплён за другим водителем.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_IN_DELIVERY:
            _close_taxi_driver_message(chat_id, message_id, "❌ Действие недоступно для текущего статуса заказа.")
            return jsonify({"status": "ok"}), 200

        active_msg_id = db.get_telegram_session_data(user_id, _taxi_driver_key(order_id, "active_message_id"))
        if active_msg_id and str(active_msg_id) != str(message_id):
            _close_taxi_driver_message(chat_id, message_id, "❌ Это устаревшее сообщение. Используйте актуальное.")
            return jsonify({"status": "ok"}), 200

        commission = float(order.get('driver_commission') or _runtime_setting("taxi_commission", config.TAXI_COMMISSION))
        assigned_at = order.get('driver_assigned_at')
        refund = False
        if assigned_at:
            delta = datetime.now() - assigned_at
            refund = delta.total_seconds() <= 30

        if refund and commission > 0:
            db.update_driver_balance(user_id, commission, reason=f"Refund taxi {order_id}")

        db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED, driver_id=None)

        driver_msg = "❌ Заказ отменён."
        if refund:
            driver_msg += "\n💰 Комиссия не списана."
        else:
            driver_msg += "\n💰 Комиссия уже списана."

        db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "closed"), True)
        db.set_telegram_session_data(user_id, _taxi_driver_key(order_id, "active_message_id"), int(message_id))
        _close_taxi_driver_message(chat_id, message_id, driver_msg)

        # КРИТИЧНО: Сначала отправляем WhatsApp клиенту, ПОТОМ работаем с состоянием
        client_msg = ("❌ Сиздин заказыңыз жокко чыгарылды.\n"
                      "Ошол эле дарекке жана баага такси чакыргыңыз келеби?")
        client_phone = order.get('client_phone', '')
        reorder_buttons = [
            {"id": "reorder_yes", "text": "✅ Ооба"},
            {"id": "reorder_no", "text": "❌ Жок"},
        ]

        # Отправляем WhatsApp ДО любых операций с БД (чтобы гарантировать отправку)
        if not send_whatsapp_buttons(client_phone, client_msg, reorder_buttons, include_cancel=False):
            send_whatsapp(client_phone, client_msg)

        # Теперь безопасно работаем с состоянием клиента
        if client_phone:
            try:
                client_user = db.get_user(client_phone)
                if client_user:
                    client_user.set_state(config.STATE_TAXI_REORDER_CHOICE)
                    client_user.set_temp_data('service_type', config.SERVICE_TAXI)
                    client_user.set_temp_data('taxi_reorder_route', order.get('details', '') or '')
                    client_user.set_temp_data('taxi_reorder_price', float(order.get('price_total') or 0))
            except Exception as e:
                logger.error(f"Error setting client state after cancel: {e}")

        db.log_transaction("TAXI_DRIVER_CANCEL", user_id, order_id, amount=(-commission if refund else None))
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.exception("Error cancelling taxi trip")
        _close_taxi_driver_message(chat_id, message_id, "❌ Ошибка отмены заказа.")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# PORTER HANDLERS
# =============================================================================

def handle_porter_take(data: str, user_id: str, user_name: str,
                       chat_id: str, message_id: int, db,
                       callback_query_id: str = None) -> tuple:
    """Обработка взятия заказа портером/муравьём"""
    lock = None
    try:
        order_id = data.split("_")[2]
        
        # Защита от двойного нажатия: 1 заказ = 1 действие
        lock_key = f"porter_take_{order_id}_{user_id}"
        lock = _get_callback_lock(lock_key)
        
        if not lock.acquire(blocking=False):
            # Уже обрабатывается или было обработано
            return jsonify({"status": "ok"}), 200
        
        # Callback уже отвечен в handle_callback_query для скорости
        
        # Сначала получаем заказ для проверки статуса
        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ не найден.")
            return jsonify({"status": "ok"}), 200
            
        # Проверяем, не занят ли заказ другим водителем
        if order.get('driver_id') and str(order.get('driver_id')) != str(user_id):
            send_telegram_private(user_id, "❌ Заказ уже забрал другой водитель!")
            return jsonify({"status": "ok"}), 200
            
        if order.get('status') in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED):
            send_telegram_private(user_id, "❌ Заказ уже закрыт.")
            return jsonify({"status": "ok"}), 200
        
        # Получаем информацию о водителе
        driver = db.get_driver(user_id)

        if not driver:
            send_telegram_private(
                user_id,
                "❌ Вы не зарегистрированы!\n\nДля регистрации напишите боту /register в личные сообщения."
            )
            return jsonify({"status": "ok"}), 200

        order_service_type = (order.get('service_type') or '').strip().lower()
        driver_type = (driver.get('driver_type') or '').strip().lower()

        # Strict type isolation between porter and ant.
        if order_service_type == config.SERVICE_PORTER and driver_type != 'porter':
            send_telegram_private(user_id, "❌ Заказ портера могут брать только портеристы.")
            return jsonify({"status": "ok"}), 200
        if order_service_type == config.SERVICE_ANT and driver_type != 'ant':
            send_telegram_private(user_id, "❌ Заказ муравея могут брать только муравьи.")
            return jsonify({"status": "ok"}), 200

        # Проверяем: нет ли у портера уже активного заказа
        active = db.get_driver_active_order(user_id)
        if active and str(active.get('order_id')) != str(order_id):
            send_telegram_private(
                user_id,
                f"⚠️ У вас уже есть активный заказ #{active['order_id']}.\n\n"
                f"Завершите или отмените текущий заказ перед тем, как брать новый."
            )
            return jsonify({"status": "ok"}), 200

        # Атомарно назначаем водителя
        now = datetime.now()
        assigned = db.assign_order_to_driver(
            order_id,
            config.ORDER_STATUS_IN_DELIVERY,
            driver_id=user_id,
            allowed_statuses=[
                config.ORDER_STATUS_PENDING,
                config.ORDER_STATUS_AUCTION,
                config.ORDER_STATUS_ACCEPTED,
                config.ORDER_STATUS_READY,
                config.ORDER_STATUS_URGENT
            ],
            driver_assigned_at=now
        )
        if not assigned:
            send_telegram_private(user_id, "❌ Заказ уже забрали другие!")
            return jsonify({"status": "ok"}), 200
        
        # Получаем заказ
        order = db.get_order(order_id)
        
        # Сразу обновляем сообщение в группе
        updated_text = f"""🚛 *ГРУЗ ЗАБРАН* ✅

👤 Водитель: *{user_name}*
📞 Клиент: {order.get('client_phone', '')}

⏱ Заказ в работе."""
        edit_telegram_message(chat_id, message_id, updated_text, buttons=[])
        
        # Списываем комиссию
        commission = _runtime_setting("porter_commission", config.PORTER_COMMISSION)
        success, new_balance = db.update_driver_balance(
            user_id,
            -commission,
            reason=f"Porter order {order_id}"
        )
        
        profile = _normalize_driver_profile(driver, user_name)
        # Сообщаем клиенту
        client_msg = f"""✅ *Водитель найден!*

🚛 *Транспорт:* {profile["car_model"]}
👤 *Водитель:* {profile["name"]}
📞 *Телефон:* {_format_phone_for_whatsapp(profile["phone"])}
🔢 *Номер:* {profile["plate"]}

💰 Цена: *Договорная*

Скоро позвонит для уточнения."""
        
        send_whatsapp(order.get('client_phone', ''), client_msg)
        
        # Сообщаем водителю
        driver_msg = f"""🚛 *ЗАКАЗ ВАШ!*

📞 *Клиент:* {order.get('client_phone', '')}
📦 *Тип груза:* {config.CARGO_TYPES.get(order.get('cargo_type'), 'Другое')}
🛣 *Маршрут:* {order.get('details', '')}

💰 Цена: *Договорная*
💰 Комиссия: {commission} сом

Свяжитесь с клиентом для уточнения деталей."""
        
        send_telegram_private(user_id, driver_msg)
        
        # Обновляем сообщение в группе
        updated_text = f"""🚛 *ГРУЗ ЗАБРАН* ✅

👤 Водитель: *{user_name}*
📞 Клиент: {order.get('client_phone', '')}

⏱ Заказ в работе."""
        
        edit_telegram_message(chat_id, message_id, updated_text, buttons=[])
        
        log_action = "ANT_ORDER_TAKEN" if order_service_type == config.SERVICE_ANT else "PORTER_ORDER_TAKEN"
        db.log_transaction(log_action, user_id, order_id)
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling porter take")
        send_telegram_private(user_id, "❌ Ошибка при взятии заказа.")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        # Освобождаем lock
        if lock:
            try:
                lock.release()
            except:
                pass


# =============================================================================
# SHOP HANDLERS
# =============================================================================

def handle_shop_take(data: str, user_id: str, user_name: str, db) -> tuple:
    """Обработка взятия заказа закупщиком"""
    try:
        order_id = data.split("_")[2]
        
        # Получаем заказ
        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ не найден.")
            return jsonify({"status": "ok"}), 200
        
        # Списываем комиссию 10 сом с закупщика
        commission = _runtime_setting("shopper_commission", config.SHOPPER_COMMISSION)
        success, new_balance = db.update_driver_balance(
            user_id,
            -commission,
            reason=f"Shop order {order_id}"
        )
        
        if not success:
            send_telegram_private(user_id, f"❌ Недостаточно средств на балансе. Нужно: {commission} сом")
            return jsonify({"status": "ok"}), 200
        
        # Обновляем статус
        db.update_order_status(order_id, config.ORDER_STATUS_ACCEPTED, provider_id=user_id)
        
        # Предлагаем варианты доставки
        msg = f"""🛒 *ЗАКАЗ ВЗЯТ*

📋 *Список:*
{order.get('details', '')}

📞 *Клиент:* {order.get('client_phone', '')}

Выберите способ доставки:"""
        
        buttons = [
            {"text": "🚶 Доставлю сам", "callback": f"shop_self_delivery_{order_id}"},
            {"text": "🚖 Вызвать такси", "callback": f"shop_call_taxi_{order_id}"}
        ]
        
        send_telegram_private(user_id, msg, buttons)
        
        db.log_transaction("SHOP_ORDER_TAKEN", user_id, order_id)
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling shop take")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_shop_self_delivery(data: str, user_id: str, db) -> tuple:
    """Закупщик доставляет сам"""
    try:
        order_id = data.split("_")[3]
        
        # Получаем заказ
        order = db.get_order(order_id)
        if not order:
            return jsonify({"status": "error"}), 404
        
        # Уведомляем клиента
        client_msg = f"""✅ *Закупщик назначен!*

👤 *Курьер:* Закупщик
📞 Скоро свяжется для уточнения.

💰 Услуга: *{_runtime_setting('shopper_service_fee', config.SHOPPER_SERVICE_FEE)} сом*
📦 Товары: по чеку

Курьер доставит самостоятельно."""
        
        send_whatsapp(order.get('client_phone', ''), client_msg)
        
        # Уведомляем закупщика
        send_telegram_private(
            user_id,
            f"✅ Клиент уведомлен.\n💰 Ваш заработок: {_runtime_setting('shopper_service_fee', config.SHOPPER_SERVICE_FEE)} сом"
        )
        
        db.log_transaction("SHOP_SELF_DELIVERY", user_id, order_id)
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling shop self delivery")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_shop_call_taxi(data: str, user_id: str, chat_id: str, message_id: int, db) -> tuple:
    """Закупщик вызывает такси для доставки"""
    try:
        order_id = data.split("_")[3]
        
        # Получаем заказ
        order = db.get_order(order_id)
        if not order:
            return jsonify({"status": "error"}), 404
        
        # Отправляем заявку в группу такси
        taxi_msg = f"""🛒 *ДОСТАВКА ИЗ МАГАЗИНА*

📋 *Заказ:* #{order_id}
📦 *Забрать у:* Закупщика
📍 *Куда:* {order.get('client_phone', '')}
💰 *С клиента:* Чек + {_runtime_setting('shopper_service_fee', config.SHOPPER_SERVICE_FEE)} сом
💰 *Таксисту:* Чек + {_runtime_setting('shop_delivery_fee', config.SHOP_DELIVERY_FEE)} сом

📞 *Закупщик:* {user_id}"""
        
        buttons = [{
            "text": "🚖 Взять доставку",
            "callback": f"delivery_take_{order_id}"
        }]
        
        dispatch_telegram_group_notification(config.GROUP_TAXI_ID, taxi_msg, buttons)
        
        # Уведомляем закупщика
        send_telegram_private(
            user_id,
            f"✅ Заявка на такси отправлена.\n💰 Ваш заработок: {_runtime_setting('shopper_service_fee', config.SHOPPER_SERVICE_FEE)} сом"
        )
        
        # Уведомляем клиента
        client_msg = f"""✅ *Закупщик назначен!*

👤 *Курьер:* Закупщик
🚖 *Доставка:* Через такси

💰 Услуга: *{_runtime_setting('shopper_service_fee', config.SHOPPER_SERVICE_FEE)} сом*
📦 Товары: по чеку

Ищем такси для доставки..."""
        
        send_whatsapp(order.get('client_phone', ''), client_msg)
        
        db.log_transaction("SHOP_TAXI_CALLED", user_id, order_id)
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling shop call taxi")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# DELIVERY HANDLERS
# =============================================================================

def _delivery_driver_key(order_id: str, suffix: str) -> str:
    return f"delivery_order_{order_id}_{suffix}"


def _close_delivery_driver_message(chat_id: str, message_id: int, text: str) -> None:
    """Закрыть (деактивировать) сообщение с кнопками у водителя доставки."""
    try:
        if chat_id and message_id:
            edit_telegram_message(chat_id, message_id, text, buttons=[])
    except Exception:
        logger.exception("Failed to close delivery driver message")


def _is_delivery_order_closed(order: dict) -> bool:
    status = order.get('status')
    return status in (config.ORDER_STATUS_CANCELLED, config.ORDER_STATUS_COMPLETED)


def handle_delivery_take(data: str, user_id: str, user_name: str,
                         chat_id: str, message_id: int, db,
                         callback_query_id: str = None) -> tuple:
    """Обработка взятия доставки еды/лекарств/магазина"""
    lock = None
    try:
        order_id = data.split("_")[2]

        # Защита от двойного нажатия: 1 заказ = 1 действие
        lock_key = f"delivery_take_{order_id}_{user_id}"
        lock = _get_callback_lock(lock_key)
        
        if not lock.acquire(blocking=False):
            return jsonify({"status": "ok"}), 200

        # Получаем информацию о водителе
        driver = db.get_driver(user_id)
        if not driver:
            send_telegram_private(user_id, "❌ Вы не зарегистрированы!\n\nДля регистрации напишите боту /register.")
            return jsonify({"status": "ok"}), 200
        
        # Определяем тип доставки и комиссию
        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ не найден")
            return jsonify({"status": "ok"}), 200
            
        if _is_delivery_order_closed(order):
            send_telegram_private(user_id, "❌ Заказ уже закрыт")
            return jsonify({"status": "ok"}), 200
        if order.get('delivery_mode') == config.DELIVERY_MODE_SELF:
            send_telegram_private(user_id, "❌ Этот заказ кафе доставляет своим курьером.")
            return jsonify({"status": "ok"}), 200
        service_type = order.get('service_type')
        if service_type == config.SERVICE_PHARMACY:
            send_telegram_private(user_id, config.PHARMACY_DISABLED_MESSAGE)
            return jsonify({"status": "ok"}), 200

        commission = 0
        commission_msg = ""
        
        if service_type == config.SERVICE_SHOP:
            # Доставка из магазина - 10 сом с таксиста
            commission = _runtime_setting("taxi_shop_commission", config.TAXI_SHOP_COMMISSION)
        
        # Получаем информацию о водителе
        driver = db.get_driver(user_id)
        if not driver:
            send_telegram_private(
                user_id,
                "❌ Вы не зарегистрированы!\n\nДля регистрации напишите боту /register в личные сообщения."
            )
            return jsonify({"status": "ok"}), 200
        
        # АТОМАРНЫЙ ЗАХВАТ (доставка может быть назначена на заказ принятый кафе)
        now = datetime.now()
        assigned = db.assign_order_to_driver(
            order_id,
            config.ORDER_STATUS_IN_DELIVERY,
            driver_id=user_id,
            allowed_statuses=[
                config.ORDER_STATUS_PENDING,
                config.ORDER_STATUS_AUCTION,
                config.ORDER_STATUS_URGENT,
                config.ORDER_STATUS_ACCEPTED,
                config.ORDER_STATUS_READY
            ],
            driver_assigned_at=now
        )
        if not assigned:
            if order.get('driver_id') == str(user_id):
                send_telegram_private(user_id, "✅ Заказ уже ваш")
            elif order.get('driver_id'):
                send_telegram_private(user_id, "❌ Заказ уже забрал другой")
            else:
                send_telegram_private(user_id, "❌ Заказ уже недоступен")
            return jsonify({"status": "ok"}), 200
        
        # Сразу обновляем сообщение в группе (до всех остальных операций)
        updated_text = f"""📦 *ДОСТАВКА ЗАБРАТА* ✅

👤 Водитель: *{user_name}*
📞 Клиент: {order.get('client_phone', '')}

⏱ Доставка в процессе."""
        edit_telegram_message(chat_id, message_id, updated_text, buttons=[])
        
        # Списываем комиссию если есть
        if commission > 0:
            success, new_balance = db.update_driver_balance(
                user_id,
                -commission,
                reason=f"Delivery {service_type} order {order_id}"
            )
            if success:
                commission_msg = f"\n💰 Списано комиссии: {commission} сом"
            else:
                # Если не удалось списать - все равно продолжаем
                commission_msg = ""
        
        profile = _normalize_driver_profile(driver, user_name)
        
        # Сообщаем клиенту
        client_msg = f"""✅ *Курьер найден!*

👤 *Водитель:* {profile["name"]}
📞 *Телефон:* {_format_phone_for_whatsapp(profile["phone"])}
🚘 *Авто:* {profile["car_model"]}
🔢 *Номер:* {profile["plate"]}

⏱ Ожидайте доставку."""
        
        send_whatsapp(order.get('client_phone', ''), client_msg)
        
        # Получаем информацию о провайдере (откуда забирать)
        provider_name = "Неизвестно"
        provider_address = "Проверьте детали"
        provider_phone = ""
        ready_time_str = ""
        
        provider_id = order.get('provider_id')
        if provider_id:
            if service_type == config.SERVICE_CAFE:
                cafe = db.get_cafe(provider_id)
                if cafe:
                    provider_name = cafe.get('name', 'Кафе')
                    provider_address = cafe.get('address', 'Адрес не указан')
                    provider_phone = cafe.get('phone', '')
                
                # Время готовности
                ready_time = order.get('ready_time')
                if ready_time:
                    ready_time_str = f"⏱ *Готово через:* {ready_time} мин\n"
                    
            elif service_type == config.SERVICE_SHOP:
                # Если доставка из магазина, провайдер может быть или сам магазин (если есть ID) или просто "Магазин"
                # В текущей реализации магазина provider_id может быть shopper_id если это закупщик
                # Но логика handle_shop_take ставит provider_id = shopper_id
                shopper = db.get_shopper(provider_id)
                if shopper:
                    provider_name = f"Закупщик {shopper.get('name', '')}"
                    provider_address = "Связаться с закупщиком"
                    provider_phone = shopper.get('phone', '')
                else:
                    shop = db.get_cafe(provider_id) # Возможно это магазин как кафе
                    if shop:
                        provider_name = shop.get('name', 'Магазин')
                        provider_address = shop.get('address', 'Адрес не указан')


        # Оплата и цена
        payment_method = config.PAYMENT_METHODS.get(order.get('payment_method'), 'Наличные')
        price_total = order.get('price_total', 0)
        price_str = f"{int(price_total)} сом" if price_total else "По чеку/Договорная"

        # Детали заказа
        details = order.get('details', 'Нет деталей')
        # Сообщаем водителю
        driver_msg = f"""📦 *ДОСТАВКА ВАША!*
{commission_msg}

🏪 *Откуда:* {provider_name}
📍 *Адрес:* {provider_address}
{f'📞 *Тел:* {provider_phone}' if provider_phone else ''}

📋 *Заказ:* #{order_id}
{config.ORDER_STATUS_READY if order.get('status') == config.ORDER_STATUS_READY else ''}
{ready_time_str}
📝 *Состав:*
{details}

👤 *Клиент:* {order.get('client_phone', '')}
📍 *Куда:* {order.get('address', 'Уточнить у клиента')}

💰 *Оплата:* {payment_method}
💵 *Сумма:* {price_str}

✅ Свяжитесь с отправителем и клиентом!"""

        delivery_buttons = [
            {"text": "📍 Я приехал", "callback": f"delivery_arrived_{order_id}"},
            {"text": "❌ Отменить", "callback": f"delivery_cancel_{order_id}"}
        ]
        private_result = send_telegram_private(user_id, driver_msg, delivery_buttons)
        if private_result and private_result.get("message_id"):
            db.set_telegram_session_data(user_id, _delivery_driver_key(order_id, "active_message_id"), int(private_result["message_id"]))
            db.set_telegram_session_data(user_id, _delivery_driver_key(order_id, "arrived_notified"), False)
            db.set_telegram_session_data(user_id, _delivery_driver_key(order_id, "closed"), False)
        
        db.log_transaction("DELIVERY_TAKEN", user_id, order_id)
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling delivery take")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        # Освобождаем lock
        if lock:
            try:
                lock.release()
            except:
                pass


def handle_delivery_arrived(data: str, user_id: str, user_name: str,
                            chat_id: str, message_id: int, db) -> tuple:
    """Водитель доставки нажал 'Я приехал'."""
    try:
        order_id = data.split("_")[2]
        order = db.get_order(order_id)
        if not order:
            _close_delivery_driver_message(chat_id, message_id, "❌ Заказ уже закрыт или не найден.")
            return jsonify({"status": "ok"}), 200
        if _is_delivery_order_closed(order):
            _close_delivery_driver_message(chat_id, message_id, "❌ Заказ уже закрыт. Кнопки отключены.")
            return jsonify({"status": "ok"}), 200
        if order.get('driver_id') and str(order.get('driver_id')) != str(user_id):
            _close_delivery_driver_message(chat_id, message_id, "❌ Этот заказ закреплён за другим водителем.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_IN_DELIVERY:
            _close_delivery_driver_message(chat_id, message_id, "❌ Действие недоступно для текущего статуса заказа.")
            return jsonify({"status": "ok"}), 200

        active_msg_id = db.get_telegram_session_data(user_id, _delivery_driver_key(order_id, "active_message_id"))
        if active_msg_id and str(active_msg_id) != str(message_id):
            _close_delivery_driver_message(chat_id, message_id, "❌ Это устаревшее сообщение. Используйте актуальное.")
            return jsonify({"status": "ok"}), 200

        arrived_notified = db.get_telegram_session_data(user_id, _delivery_driver_key(order_id, "arrived_notified"), False)
        if arrived_notified:
            _close_delivery_driver_message(chat_id, message_id, "✅ Клиент уже уведомлён.")
            return jsonify({"status": "ok"}), 200

        driver = db.get_driver(user_id)
        profile = _normalize_driver_profile(driver, user_name)

        client_msg = (
            "📍 *Курьер приехал и ожидает вас!*\n"
            f"👤 *Курьер:* {profile['name']}\n"
            f"📞 *Телефон:* {_format_phone_for_whatsapp(profile['phone'])}\n"
            f"🔢 *Номер:* {profile['plate']}\n\n"
            "🚶 Пожалуйста, выходите."
        )
        send_whatsapp(order.get('client_phone', ''), client_msg)

        db.set_telegram_session_data(user_id, _delivery_driver_key(order_id, "arrived_notified"), True)
        db.set_telegram_session_data(user_id, _delivery_driver_key(order_id, "active_message_id"), int(message_id))

        edit_telegram_message(
            chat_id,
            message_id,
            "✅ *Клиент уведомлён!*\n\n📍 Ожидайте клиента.",
            [
                {"text": "✅ Завершить доставку", "callback": f"delivery_finish_{order_id}"},
                {"text": "❌ Отменить", "callback": f"delivery_cancel_{order_id}"}
            ]
        )

        db.log_transaction("DELIVERY_DRIVER_ARRIVED", user_id, order_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.exception("Error handling delivery arrived")
        _close_delivery_driver_message(chat_id, message_id, "❌ Ошибка обработки действия.")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_delivery_finish(data: str, user_id: str, user_name: str,
                           chat_id: str, message_id: int, db) -> tuple:
    """Завершение доставки водителем."""
    try:
        order_id = data.split("_")[2]
        order = db.get_order(order_id)
        if not order:
            _close_delivery_driver_message(chat_id, message_id, "❌ Заказ уже закрыт или не найден.")
            return jsonify({"status": "ok"}), 200
        if _is_delivery_order_closed(order):
            _close_delivery_driver_message(chat_id, message_id, "❌ Заказ уже закрыт. Кнопки отключены.")
            return jsonify({"status": "ok"}), 200
        if order.get('driver_id') and str(order.get('driver_id')) != str(user_id):
            _close_delivery_driver_message(chat_id, message_id, "❌ Этот заказ закреплён за другим водителем.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_IN_DELIVERY:
            _close_delivery_driver_message(chat_id, message_id, "❌ Действие недоступно для текущего статуса заказа.")
            return jsonify({"status": "ok"}), 200

        active_msg_id = db.get_telegram_session_data(user_id, _delivery_driver_key(order_id, "active_message_id"))
        if active_msg_id and str(active_msg_id) != str(message_id):
            _close_delivery_driver_message(chat_id, message_id, "❌ Это устаревшее сообщение. Используйте актуальное.")
            return jsonify({"status": "ok"}), 200

        arrived_notified = db.get_telegram_session_data(user_id, _delivery_driver_key(order_id, "arrived_notified"), False)
        if not arrived_notified:
            _close_delivery_driver_message(chat_id, message_id, "❌ Сначала нажмите «Я приехал».")
            return jsonify({"status": "ok"}), 200

        db.update_order_status(order_id, config.ORDER_STATUS_COMPLETED, completed_at=datetime.now())
        send_whatsapp_with_main_menu(
            order.get('client_phone', ''),
            "✅ Доставка завершена. Спасибо, что выбрали нас!"
        )

        db.set_telegram_session_data(user_id, _delivery_driver_key(order_id, "closed"), True)
        db.set_telegram_session_data(user_id, _delivery_driver_key(order_id, "active_message_id"), int(message_id))
        _close_delivery_driver_message(chat_id, message_id, "✅ Доставка завершена. Заказ закрыт.")

        db.log_transaction("DELIVERY_FINISHED", user_id, order_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.exception("Error finishing delivery")
        _close_delivery_driver_message(chat_id, message_id, "❌ Ошибка завершения доставки.")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_delivery_cancel(data: str, user_id: str, user_name: str,
                           chat_id: str, message_id: int, db) -> tuple:
    """Отмена доставки водителем."""
    try:
        order_id = data.split("_")[2]
        order = db.get_order(order_id)
        if not order:
            _close_delivery_driver_message(chat_id, message_id, "❌ Заказ уже закрыт или не найден.")
            return jsonify({"status": "ok"}), 200
        if _is_delivery_order_closed(order):
            _close_delivery_driver_message(chat_id, message_id, "❌ Заказ уже закрыт. Кнопки отключены.")
            return jsonify({"status": "ok"}), 200
        if order.get('driver_id') and str(order.get('driver_id')) != str(user_id):
            _close_delivery_driver_message(chat_id, message_id, "❌ Этот заказ закреплён за другим водителем.")
            return jsonify({"status": "ok"}), 200
        if order.get('status') != config.ORDER_STATUS_IN_DELIVERY:
            _close_delivery_driver_message(chat_id, message_id, "❌ Действие недоступно для текущего статуса заказа.")
            return jsonify({"status": "ok"}), 200

        active_msg_id = db.get_telegram_session_data(user_id, _delivery_driver_key(order_id, "active_message_id"))
        if active_msg_id and str(active_msg_id) != str(message_id):
            _close_delivery_driver_message(chat_id, message_id, "❌ Это устаревшее сообщение. Используйте актуальное.")
            return jsonify({"status": "ok"}), 200

        db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED, driver_id=None)
        _close_delivery_driver_message(chat_id, message_id, "❌ Доставка отменена. Заказ закрыт.")

        send_whatsapp(order.get('client_phone', ''), "❌ Доставка отменена курьером. Мы можем оформить новый заказ.")

        db.log_transaction("DELIVERY_CANCELLED", user_id, order_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.exception("Error cancelling delivery")
        _close_delivery_driver_message(chat_id, message_id, "❌ Ошибка отмены доставки.")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# ADMIN HANDLERS
# =============================================================================

def handle_admin_callback(data: str, user_id: str, db) -> tuple:
    """Обработка админских команд"""
    try:
        # Проверяем, является ли пользователь админом
        if user_id not in config.ADMIN_TELEGRAM_IDS:
            send_telegram_private(user_id, "❌ У вас нет прав администратора.")
            return jsonify({"status": "ok"}), 200
        
        action = data.split("_")[1]
        
        if action == "stats":
            # Показываем статистику
            stats = db.get_daily_stats()
            msg = f"""📊 *Статистика за сегодня*

📦 Всего заказов: {stats.get('total_orders', 0)}
✅ Выполнено: {stats.get('completed', 0)}
❌ Отменено: {stats.get('cancelled', 0)}
💰 Выручка: {stats.get('total_revenue', 0)} сом
💼 Комиссия: {stats.get('total_commission', 0)} сом"""
            
            send_telegram_private(user_id, msg)
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling admin callback")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# ОБРАБОТКА КНОПОК МЕНЮ (cmd_*)
# =============================================================================

def _handle_cmd_button(data: str, user_id: str, db) -> tuple:
    """Обработка нажатий кнопок главного меню"""
    try:
        cmd = data.replace("cmd_", "")
        
        if cmd == "register":
            return _handle_register_command(user_id, '/register', db)
        elif cmd == "balance":
            return _handle_balance_command(user_id, db)
        elif cmd == "profile":
            return _handle_profile_command(user_id, db)
        elif cmd == "stats":
            return _handle_stats_command(user_id, db)
        elif cmd == "poputka":
            return _start_poputka_flow(user_id, db)
        elif cmd == "help":
            send_telegram_private(user_id, config.DRIVER_HELP_MSG)
            return jsonify({"status": "ok"}), 200
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling cmd button")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# TELEGRAM MESSAGE HANDLER (команды + регистрация водителей)
# =============================================================================

def handle_telegram_message(message: dict) -> tuple:
    """Обработка сообщений от Telegram (личные сообщения боту)"""
    try:
        chat_type = message['chat'].get('type', 'private')
        
        # Обрабатываем только личные сообщения (не групповые)
        if chat_type != 'private':
            return jsonify({"status": "ok"}), 200
        
        text = message.get('text', '').strip()
        user_id = str(message['from']['id'])
        user_name = message['from'].get('first_name', 'Unknown')
        contact = message.get('contact') or {}

        db = get_db()

        if contact and contact.get('phone_number'):
            session = db.get_telegram_session(user_id)
            if session and session.get('state') == config.STATE_DRIVER_REG_PHONE:
                return _handle_reg_phone(user_id, contact.get('phone_number', ''), db)
            if session and session.get('state') == config.STATE_POPUTKA_PHONE:
                return _handle_poputka_phone(user_id, contact.get('phone_number', ''), db)
            if session and session.get('state') == config.STATE_CAFE_OWN_COURIER_PHONE:
                return _handle_cafe_self_courier_phone(
                    user_id,
                    user_name,
                    contact.get('phone_number', ''),
                    db
                )

        if not text:
            return jsonify({"status": "ok"}), 200
        
        logger.info(f"Telegram DM from {user_name} ({user_id}): {text}")
        
        # =====================================================================
        # ОБРАБОТКА КОМАНД
        # =====================================================================
        
        text_lower = text.lower().strip()
        
        # /start — Приветствие
        if text_lower in ('/start', 'start', 'привет', 'здравствуйте'):
            send_telegram_private(user_id, config.DRIVER_WELCOME, config.DRIVER_WELCOME_BUTTONS)
            db.clear_telegram_session(user_id)
            return jsonify({"status": "ok"}), 200
        
        # /help — Помощь
        if text_lower in ('/help', 'help', 'помощь'):
            send_telegram_private(user_id, config.DRIVER_HELP_MSG)
            return jsonify({"status": "ok"}), 200
        
        # /register — Начать регистрацию
        if text_lower in ('/register', 'register', 'регистрация', '/update', 'update'):
            return _handle_register_command(user_id, text_lower, db)
        
        # /balance — Проверить баланс
        if text_lower in ('/balance', 'balance', 'баланс'):
            return _handle_balance_command(user_id, db)
        
        # /profile — Мой профиль
        if text_lower in ('/profile', 'profile', 'профиль'):
            return _handle_profile_command(user_id, db)
        
        # /stats — Моя статистика
        if text_lower in ('/stats', 'stats', 'статистика'):
            return _handle_stats_command(user_id, db)

        # /poputka — добавить ближайший выезд
        if text_lower in ('/poputka', 'poputka', 'попутка'):
            return _start_poputka_flow(user_id, db)
        
        # /cancel — Отмена текущего действия
        if text_lower in ('/cancel', 'cancel', 'отмена'):
            db.clear_telegram_session(user_id)
            send_telegram_private(user_id, "❌ Действие отменено.")
            send_telegram_private(user_id, config.DRIVER_WELCOME, config.DRIVER_WELCOME_BUTTONS)
            return jsonify({"status": "ok"}), 200
        
        # =====================================================================
        # ОБРАБОТКА СОСТОЯНИЙ РЕГИСТРАЦИИ
        # =====================================================================
        
        session = db.get_telegram_session(user_id)
        
        if session:
            state = session.get('state', 'IDLE')
            
            if state == config.STATE_DRIVER_REG_TYPE:
                return _handle_reg_type(user_id, text, db)
            
            elif state == config.STATE_DRIVER_REG_NAME:
                return _handle_reg_name(user_id, text, db)
            
            elif state == config.STATE_DRIVER_REG_PHONE:
                return _handle_reg_phone(user_id, text, db)
            
            elif state == config.STATE_DRIVER_REG_CAR:
                return _handle_reg_car(user_id, text, db)
            
            elif state == config.STATE_DRIVER_REG_PLATE:
                return _handle_reg_plate(user_id, text, db)
            
            elif state == config.STATE_DRIVER_REG_CONFIRM:
                return _handle_reg_confirm(user_id, text, db)

            elif state == config.STATE_POPUTKA_PHONE:
                return _handle_poputka_phone(user_id, text, db)

            elif state == config.STATE_POPUTKA_FROM:
                return _handle_poputka_from(user_id, text, db)

            elif state == config.STATE_POPUTKA_TO:
                return _handle_poputka_to(user_id, text, db)

            elif state == config.STATE_POPUTKA_SEATS:
                return _handle_poputka_seats(user_id, text, db)

            elif state == config.STATE_POPUTKA_TIME:
                return _handle_poputka_time(user_id, text, db)
            
            elif state == config.STATE_CAFE_DECLINE_REASON:
                return _handle_cafe_decline_reason(user_id, user_name, text, db)

            elif state == config.STATE_CAFE_OWN_COURIER_PHONE:
                return _handle_cafe_self_courier_phone(user_id, user_name, text, db)
        
        # =====================================================================
        # ВВОД ЦЕНЫ АПТЕКОЙ (через ЛС)
        # =====================================================================
        
        if text.isdigit():
            price = int(text)

            # 1) Пытаемся взять pending order из telegram_session
            pending_order_id = db.get_telegram_session_data(user_id, 'pending_pharmacy_order')

            # 2) Если нет, пробуем вытащить order_id из reply_to_message
            if not pending_order_id:
                reply_text = (message.get('reply_to_message') or {}).get('text', '')
                m = re.search(r'#(GO\d+)', reply_text, flags=re.IGNORECASE)
                if m:
                    pending_order_id = m.group(1).upper()

            if pending_order_id:
                db.set_telegram_session_data(user_id, "pending_pharmacy_order", None)
                send_telegram_private(user_id, config.PHARMACY_DISABLED_MESSAGE)
                return jsonify({"status": "ok"}), 200

            send_telegram_private(
                user_id,
                config.PHARMACY_DISABLED_MESSAGE
            )
            return jsonify({"status": "ok"}), 200
        
        # Неизвестное сообщение — показать меню
        send_telegram_private(user_id, config.DRIVER_WELCOME, config.DRIVER_WELCOME_BUTTONS)
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling telegram message")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# КОМАНДЫ ВОДИТЕЛЯ
# =============================================================================

def _handle_register_command(user_id: str, command: str, db) -> tuple:
    """Обработка команды /register или /update"""
    
    is_update = command in ('/update', 'update')
    logger.info(f"[DRIVER_REG_START] tid={user_id} command='{command}' is_update={is_update}")
    
    # Проверяем, зарегистрирован ли уже
    driver = db.get_driver(user_id)
    profile_incomplete = False
    if driver:
        _dtype = driver.get('driver_type', 'taxi')
        if _dtype in ('ant', 'scooter', 'raznarabochi'):
            profile_incomplete = not (driver.get('name') and driver.get('phone'))
        else:
            profile_incomplete = not (driver.get('name') and driver.get('phone') and driver.get('car_model') and driver.get('plate'))
    
    if driver and not is_update and not profile_incomplete:
        # Уже зарегистрирован — показываем данные
        driver_type_key = driver.get('driver_type', 'taxi')
        type_emoji = config.DRIVER_TYPES.get(driver_type_key, '🚖 Такси').split(' ')[0]
        
        msg = config.DRIVER_REG_ALREADY.format(
            type_emoji=type_emoji,
            driver_type=config.DRIVER_TYPES.get(driver_type_key, driver_type_key),
            name=driver.get('name', 'Не указано'),
            phone=driver.get('phone', 'Не указан'),
            car_model=driver.get('car_model', 'Не указано'),
            plate=driver.get('plate', 'Не указан'),
            balance=driver.get('balance', 0)
        )
        send_telegram_private(user_id, msg)
        return jsonify({"status": "ok"}), 200
    
    # Начинаем регистрацию/обновление
    db.create_telegram_session(user_id)
    db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_TYPE)

    # DEBUG LOG - проверяем что сессия создана
    session = db.get_telegram_session(user_id)
    temp_data_log = (session.get('temp_data') or {}) if session else 'NO_SESSION'
    logger.info(f"[DRIVER_REG_START] tid={user_id} session created. state={session.get('state') if session else 'NO_SESSION'} temp_data={temp_data_log}")

    # Отправляем с кнопками
    buttons = [
        {"text": "🚖 Такси", "callback": "dreg_type_taxi"},
        {"text": "🚛 Портер", "callback": "dreg_type_porter"},
        {"text": "🐜 Муравей", "callback": "dreg_type_ant"},
        {"text": "🛵 Скутер", "callback": "dreg_type_scooter"},
        {"text": "👷 Разнарабочий", "callback": "dreg_type_raznarabochi"},
        {"text": "🚘 Попутка", "callback": "dreg_type_poputka"},
    ]

    send_telegram_private(user_id, config.DRIVER_REG_TYPE_PROMPT, buttons)
    return jsonify({"status": "ok"}), 200


def _handle_balance_command(user_id: str, db) -> tuple:
    """Обработка команды /balance"""
    
    driver = db.get_driver(user_id)
    
    if not driver:
        send_telegram_private(user_id, config.DRIVER_NOT_REGISTERED)
        return jsonify({"status": "ok"}), 200
    
    balance = float(driver.get('balance', 0))
    
    if balance >= 100:
        status = "✅ Баланс достаточный для приёма заказов."
    elif balance >= 0:
        status = "⚠️ Баланс низкий. Рекомендуем пополнить."
    else:
        status = "🔴 Баланс отрицательный! Пополните для продолжения работы."
    
    msg = config.DRIVER_BALANCE_MSG.format(
        balance=balance,
        status=status
    )
    send_telegram_private(user_id, msg)
    return jsonify({"status": "ok"}), 200


def _handle_profile_command(user_id: str, db) -> tuple:
    """Обработка команды /profile"""
    
    driver = db.get_driver(user_id)
    
    if not driver:
        send_telegram_private(user_id, config.DRIVER_NOT_REGISTERED)
        return jsonify({"status": "ok"}), 200
    
    driver_type_key = driver.get('driver_type', 'taxi')
    type_emoji = config.DRIVER_TYPES.get(driver_type_key, '🚖 Такси').split(' ')[0]
    
    created_at = driver.get('created_at', '')
    if hasattr(created_at, 'strftime'):
        created_at = created_at.strftime('%d.%m.%Y')
    
    msg = config.DRIVER_PROFILE_MSG.format(
        type_emoji=type_emoji,
        driver_type=config.DRIVER_TYPES.get(driver_type_key, driver_type_key),
        name=driver.get('name', 'Не указано'),
        phone=driver.get('phone', 'Не указан'),
        car_model=driver.get('car_model', 'Не указано'),
        plate=driver.get('plate', 'Не указан'),
        balance=driver.get('balance', 0),
        created_at=created_at
    )
    send_telegram_private(user_id, msg)
    return jsonify({"status": "ok"}), 200


def _handle_stats_command(user_id: str, db) -> tuple:
    """Обработка команды /stats"""
    
    driver = db.get_driver(user_id)
    
    if not driver:
        send_telegram_private(user_id, config.DRIVER_NOT_REGISTERED)
        return jsonify({"status": "ok"}), 200
    
    stats = db.get_driver_order_stats(user_id)
    balance = float(driver.get('balance', 0))
    
    msg = f"""📊 *Моя статистика*

📦 Всего заказов: {stats.get('total_orders', 0)}
✅ Выполнено: {stats.get('completed', 0)}
❌ Отменено: {stats.get('cancelled', 0)}
📅 Сегодня: {stats.get('today', 0)}

💰 Текущий баланс: {balance} сом"""
    
    send_telegram_private(user_id, msg)
    return jsonify({"status": "ok"}), 200


def _start_poputka_flow(user_id: str, db) -> tuple:
    db.create_telegram_session(user_id)
    driver = db.get_driver(user_id)
    driver_phone = (driver.get("phone") or "").strip() if driver else ""
    if driver_phone:
        db.set_telegram_session_data(user_id, "poputka_phone", driver_phone)
        db.set_telegram_session_state(user_id, config.STATE_POPUTKA_FROM)
        send_telegram_private(user_id, config.POPUTKA_DRIVER_START)
        return jsonify({"status": "ok"}), 200

    db.set_telegram_session_state(user_id, config.STATE_POPUTKA_PHONE)
    send_telegram_contact_request(user_id, config.POPUTKA_DRIVER_PHONE_PROMPT, "📱 Контактты бөлүшүү")
    return jsonify({"status": "ok"}), 200


def _handle_poputka_phone(user_id: str, text: str, db) -> tuple:
    raw = (text or "").strip()
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("996"):
        digits = "0" + digits[3:]

    if len(digits) < 9 or len(digits) > 12:
        send_telegram_private(user_id, config.POPUTKA_DRIVER_INVALID_PHONE)
        return jsonify({"status": "ok"}), 200

    db.set_telegram_session_data(user_id, "poputka_phone", digits)
    db.set_telegram_session_state(user_id, config.STATE_POPUTKA_FROM)
    send_telegram_private(user_id, config.POPUTKA_DRIVER_START)
    return jsonify({"status": "ok"}), 200


def _handle_poputka_from(user_id: str, text: str, db) -> tuple:
    value = text.strip()
    if len(value) < 2:
        send_telegram_private(user_id, "⚠️ Кайдан чыгарыңызды жазыңыз.")
        return jsonify({"status": "ok"}), 200

    db.set_telegram_session_data(user_id, "poputka_from", value)
    db.set_telegram_session_state(user_id, config.STATE_POPUTKA_TO)
    send_telegram_private(user_id, config.POPUTKA_DRIVER_TO_PROMPT)
    return jsonify({"status": "ok"}), 200


def _handle_poputka_to(user_id: str, text: str, db) -> tuple:
    value = text.strip()
    if len(value) < 2:
        send_telegram_private(user_id, "⚠️ Кайда барарыңызды жазыңыз.")
        return jsonify({"status": "ok"}), 200

    db.set_telegram_session_data(user_id, "poputka_to", value)
    db.set_telegram_session_state(user_id, config.STATE_POPUTKA_SEATS)
    send_telegram_private(user_id, config.POPUTKA_DRIVER_SEATS_PROMPT)
    return jsonify({"status": "ok"}), 200


def _handle_poputka_seats(user_id: str, text: str, db) -> tuple:
    raw = text.strip()
    if not raw.isdigit():
        send_telegram_private(user_id, config.POPUTKA_DRIVER_INVALID_SEATS)
        return jsonify({"status": "ok"}), 200

    seats = int(raw)
    if seats < 1 or seats > 20:
        send_telegram_private(user_id, config.POPUTKA_DRIVER_INVALID_SEATS)
        return jsonify({"status": "ok"}), 200

    db.set_telegram_session_data(user_id, "poputka_seats", seats)
    db.set_telegram_session_state(user_id, config.STATE_POPUTKA_TIME)
    send_telegram_private(user_id, config.POPUTKA_DRIVER_TIME_PROMPT)
    return jsonify({"status": "ok"}), 200


def _parse_poputka_departure_time(raw: str) -> datetime | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None

    now_local = _bishkek_now_naive()
    departure = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if departure <= now_local:
        return None
    return departure


def _handle_poputka_time(user_id: str, text: str, db) -> tuple:
    departure_time = _parse_poputka_departure_time(text)
    if departure_time is None:
        send_telegram_private(user_id, config.POPUTKA_DRIVER_INVALID_TIME)
        return jsonify({"status": "ok"}), 200

    session = db.get_telegram_session(user_id)
    temp_data = (session.get("temp_data") or {}) if session else {}
    from_address = (temp_data.get("poputka_from") or "").strip()
    to_address = (temp_data.get("poputka_to") or "").strip()
    seats = int(temp_data.get("poputka_seats") or 0)
    phone = (temp_data.get("poputka_phone") or "").strip()
    if not phone:
        driver = db.get_driver(user_id)
        phone = (driver.get("phone") or "").strip() if driver else ""

    if not from_address or not to_address or seats <= 0 or not phone:
        db.clear_telegram_session(user_id)
        send_telegram_private(
            user_id,
            "❌ Попутканы сактай албай калдык. /poputka менен кайра баштаңыз."
        )
        return jsonify({"status": "ok"}), 200

    db.create_poputka_offer(
        driver_id=user_id,
        driver_phone=phone,
        from_address=from_address,
        to_address=to_address,
        seats_available=seats,
        departure_time=departure_time,
    )
    db.clear_telegram_session(user_id)

    send_telegram_private(
        user_id,
        config.POPUTKA_DRIVER_SUCCESS.format(
            from_address=from_address,
            to_address=to_address,
            seats=seats,
            departure_time=departure_time.strftime("%H:%M"),
        )
    )
    return jsonify({"status": "ok"}), 200


# =============================================================================
# ПОПУТКА — ПРИНЯТИЕ КЛИЕНТСКОГО ЗАПРОСА
# =============================================================================

def handle_poputka_accept(data: str, user_id: str, user_name: str,
                          chat_id: str, message_id: int, db,
                          callback_query_id: str = None) -> tuple:
    """Водитель принимает клиентский запрос попутки."""
    try:
        order_id = data.split("_")[2]

        def _reply(text: str = None) -> None:
            _answer_callback(callback_query_id, text)

        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ табылган жок.")
            _reply()
            return jsonify({"status": "ok"}), 200

        if order.get('status') in (config.ORDER_STATUS_ACCEPTED, config.ORDER_STATUS_COMPLETED, config.ORDER_STATUS_CANCELLED):
            send_telegram_private(user_id, "❌ Бул заказды башка айдоочу алып кетти.")
            _reply()
            return jsonify({"status": "ok"}), 200

        # Проверка баланса водителя
        expires_at = order.get('expires_at')
        if expires_at and expires_at <= _bishkek_now_naive():
            try:
                if hasattr(db, "cancel_order_if_status"):
                    db.cancel_order_if_status(
                        order_id,
                        [
                            config.ORDER_STATUS_PENDING,
                            config.ORDER_STATUS_AUCTION,
                            config.ORDER_STATUS_URGENT,
                        ],
                    )
                else:
                    db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED)
            except Exception:
                logger.exception("Failed to cancel expired poputka order %s", order_id)

            try:
                timer = db.get_latest_auction_timer(order_id, config.SERVICE_POPUTKA)
                if timer:
                    db.mark_auction_processed(timer['id'])
            except Exception:
                logger.exception("Failed to close expired poputka timer %s", order_id)

            try:
                delete_telegram_message(chat_id, message_id)
            except Exception:
                edit_telegram_message(chat_id, message_id, "вЏ± РЈР±Р°РєС‹С‚С‹ С‚РёС€РёРї РєР°Р»РіР°РЅРґС‹РєС‚Р°РЅ, Р·Р°РєР°Р· Р¶Р°Р±С‹Р»РґС‹.", buttons=[])

            send_telegram_private(user_id, "вќЊ Р—Р°РєР°Р·РґС‹РЅ СѓР±Р°РєС‹С‚С‹ С‚РёС€РёРї РєР°Р»РґС‹. РђР» СѓР¶Рµ Р¶Р°Р±С‹Р»РґС‹.")
            _reply()
            return jsonify({"status": "ok"}), 200

        commission = config.POPUTKA_COMMISSION
        balance = db.get_driver_balance(user_id)
        if balance < commission:
            send_telegram_private(
                user_id,
                f"❌ *Балансыңыз жетишсиз.*\n\n"
                f"💰 Комиссия: *{commission} сом*\n"
                f"💳 Сиздин баланс: *{balance:.0f} сом*\n\n"
                f"Балансты толтуруп, кайра аракет кылыңыз."
            )
            _reply()
            return jsonify({"status": "ok"}), 200

        # Списываем комиссию
        db.update_driver_balance(user_id, -commission, reason=f"Поputka order {order_id} commission")

        # Обновляем заказ
        db.update_order_status(order_id, config.ORDER_STATUS_ACCEPTED, provider_id=user_id)

        # Получаем профиль водителя
        driver = db.get_driver(user_id)
        driver_name = (driver.get('name') or user_name or "Айдоочу").strip() if driver else user_name
        driver_phone = (driver.get('phone') or "—").strip() if driver else "—"
        car_model = (driver.get('car_model') or "").strip() if driver else ""
        plate = (driver.get('plate') or "").strip() if driver else ""
        car_info = f"{car_model} {plate}".strip() or "—"
        new_balance = balance - commission

        # Уведомляем водителя
        send_telegram_private(
            user_id,
            f"✅ *Заказ #{order_id} алынды!*\n\n"
            f"📞 Кардар номери: {_format_phone_for_whatsapp(order.get('client_phone', '—'))}\n"
            f"📍 Багыт: {order.get('address', '—')}\n"
            f"📋 {order.get('details', '')}\n\n"
            f"💰 Балансыңыздан {commission} сом алынды. Учурдагы баланс: *{new_balance:.0f} сом*"
        )

        # Обновляем сообщение в группе
        edit_telegram_message(
            chat_id, message_id,
            f"🚘 *ПОПУТКА #{order_id} — АЛЫНДЫ* ✅\n\n"
            f"👤 Айдоочу: {driver_name}",
            buttons=[]
        )

        # Уведомляем клиента через WhatsApp
        client_msg = config.POPUTKA_CLIENT_DRIVER_FOUND.format(
            driver_name=driver_name,
            driver_phone=_format_phone_for_whatsapp(driver_phone),
            car_info=car_info,
        )
        send_whatsapp(order.get('client_phone', ''), client_msg)

        # Закрываем таймер аукциона
        timer = db.get_latest_auction_timer(order_id, config.SERVICE_POPUTKA)
        if timer:
            db.mark_auction_processed(timer['id'])

        db.log_transaction("POPUTKA_ACCEPTED", user_id, order_id)
        _reply()
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error handling poputka accept")
        send_telegram_private(user_id, "❌ Ката кетти. Кайра аракет кылыңыз.")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# РАЗНАРАБОЧИЙ — ПРИНЯТИЕ КЛИЕНТСКОГО ЗАПРОСА
# =============================================================================

def handle_raznarabochi_accept(data: str, user_id: str, user_name: str,
                               chat_id: str, message_id: int, db,
                               callback_query_id: str = None) -> tuple:
    """Рабочий принимает заказ разнарабочего."""
    try:
        order_id = data.split("_")[2]

        def _reply(text: str = None) -> None:
            _answer_callback(callback_query_id, text)

        order = db.get_order(order_id)
        if not order:
            send_telegram_private(user_id, "❌ Заказ табылган жок.")
            _reply()
            return jsonify({"status": "ok"}), 200

        if order.get('status') in (config.ORDER_STATUS_ACCEPTED, config.ORDER_STATUS_COMPLETED, config.ORDER_STATUS_CANCELLED):
            send_telegram_private(user_id, "❌ Бул заказды башка рабочий алып кетти.")
            _reply()
            return jsonify({"status": "ok"}), 200

        try:
            workers_count = int(order.get('cargo_type') or 0)
        except (TypeError, ValueError):
            workers_count = 0
        if workers_count <= 0:
            workers_count = 1

        commission = int(order.get('price_total') or 0) or (workers_count * int(config.RAZNARABOCHI_COMMISSION))
        balance = db.get_driver_balance(user_id)
        if balance < commission:
            send_telegram_private(
                user_id,
                f"❌ *Балансыңыз жетишсиз.*\n\n"
                f"👥 Керек адам: *{workers_count}*\n"
                f"💰 Комиссия: *{commission} сом*\n"
                f"💳 Сиздин баланс: *{balance:.0f} сом*\n\n"
                f"Балансты толтуруп, кайра аракет кылыңыз."
            )
            _reply()
            return jsonify({"status": "ok"}), 200

        db.update_driver_balance(user_id, -commission, reason=f"Raznarabochi order {order_id} commission")
        db.update_order_status(order_id, config.ORDER_STATUS_ACCEPTED, provider_id=user_id)

        driver = db.get_driver(user_id)
        worker_name = (driver.get('name') or user_name or "Жумушчу").strip() if driver else user_name
        worker_phone = (driver.get('phone') or "—").strip() if driver else "—"
        new_balance = balance - commission

        send_telegram_private(
            user_id,
            f"✅ *Заказ #{order_id} алынды!*\n\n"
            f"📋 Иш: {order.get('details', '—')}\n"
            f"👥 Керек адам: {workers_count}\n"
            f"📞 Кардар номери: {_format_phone_for_whatsapp(order.get('client_phone', '—'))}\n\n"
            f"💰 Балансыңыздан {commission} сом алынды. Учурдагы баланс: *{new_balance:.0f} сом*"
        )

        edit_telegram_message(
            chat_id, message_id,
            f"👷 *РАЗНАРАБОЧИЙ #{order_id} — АЛЫНДЫ* ✅\n\n"
            f"👤 Жумушчу: {worker_name}\n"
            f"👥 Керек адам: {workers_count}\n"
            f"💰 Комиссия: {commission} сом",
            buttons=[]
        )

        client_msg = config.RAZNARABOCHI_WORKER_FOUND.format(
            worker_name=worker_name,
            worker_phone=_format_phone_for_whatsapp(worker_phone),
        )
        send_whatsapp(order.get('client_phone', ''), client_msg)

        timer = db.get_latest_auction_timer(order_id, config.SERVICE_RAZNARABOCHI)
        if timer:
            db.mark_auction_processed(timer['id'])

        db.log_transaction("RAZNARABOCHI_ACCEPTED", user_id, order_id)
        _reply()
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error handling raznarabochi accept")
        send_telegram_private(user_id, "❌ Ката кетти. Кайра аракет кылыңыз.")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# РЕГИСТРАЦИЯ ВОДИТЕЛЯ — ПОШАГОВЫЙ FLOW
# =============================================================================

def _handle_reg_type(user_id: str, text: str, db) -> tuple:
    """Шаг 1: Выбор типа водителя"""
    text_lower = text.lower().strip()
    
    driver_type = None
    
    if text_lower in ('1', 'такси', 'taxi', '🚖'):
        driver_type = 'taxi'
    elif text_lower in ('2', 'портер', 'porter', 'грузовик', '🚛'):
        driver_type = 'porter'
    elif text_lower in ('3', 'муравей', 'ant', 'дамас', '🐜'):
        driver_type = 'ant'
    elif text_lower in ('4', 'скутер', 'scooter', '🛵'):
        driver_type = 'scooter'
    elif text_lower in ('5', 'разнарабочий', 'raznarabochi', '👷'):
        driver_type = 'raznarabochi'
    elif text_lower in ('6', 'попутка', 'poputka', '🚘'):
        driver_type = 'poputka'

    if not driver_type:
        send_telegram_private(
            user_id,
            "⚠️ Выберите тип: *1* (Такси), *2* (Портер), *3* (Муравей), *4* (Скутер), *5* (Разнарабочий) или *6* (Попутка)"
        )
        return jsonify({"status": "ok"}), 200
    
    db.set_telegram_session_data(user_id, 'driver_type', driver_type)
    db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_NAME)
    
    # DEBUG LOG
    session = db.get_telegram_session(user_id)
    temp_data_log = (session.get('temp_data') or {}) if session else 'NO_SESSION'
    logger.info(f"[DRIVER_REG_STEP1] tid={user_id} driver_type='{driver_type}' temp_data={temp_data_log}")
    
    send_telegram_private(user_id, config.DRIVER_REG_NAME_PROMPT)
    return jsonify({"status": "ok"}), 200


def _handle_reg_name(user_id: str, text: str, db) -> tuple:
    """Шаг 2: Ввод ФИО"""
    
    if len(text) < 2:
        send_telegram_private(user_id, "⚠️ Имя слишком короткое. Введите ваше ФИО.")
        return jsonify({"status": "ok"}), 200
    
    if len(text) > 100:
        send_telegram_private(user_id, "⚠️ Имя слишком длинное. Максимум 100 символов.")
        return jsonify({"status": "ok"}), 200
    
    db.set_telegram_session_data(user_id, 'name', text)
    db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_PHONE)

    # DEBUG LOG
    session = db.get_telegram_session(user_id)
    temp_data_log = (session.get('temp_data') or {}) if session else 'NO_SESSION'
    logger.info(f"[DRIVER_REG_STEP2] tid={user_id} name='{text}' temp_data={temp_data_log}")

    # Отправляем запрос телефона с кнопкой "Поделиться контактом"
    send_telegram_contact_request(
        user_id,
        config.DRIVER_REG_PHONE_PROMPT + "\n\nИли нажмите кнопку ниже, чтобы поделиться контактом:",
        "📱 Поделиться номером телефона"
    )
    return jsonify({"status": "ok"}), 200


def _handle_reg_phone(user_id: str, text: str, db) -> tuple:
    """Шаг 3: Ввод телефона"""

    # Очищаем номер (работает и для номеров из контакта, и для ручного ввода)
    phone = text.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('+', '')
    
    if len(phone) < 9 or not phone.isdigit():
        send_telegram_private(
            user_id, 
            "⚠️ Неверный формат номера.\n\nВведите номер цифрами, например: *0555123456*"
        )
        return jsonify({"status": "ok"}), 200
    
    db.set_telegram_session_data(user_id, 'phone', phone)
    
    # DEBUG LOG
    session = db.get_telegram_session(user_id)
    temp_data_log = (session.get('temp_data') or {}) if session else 'NO_SESSION'
    logger.info(f"[DRIVER_REG_STEP3] tid={user_id} phone='{phone}' temp_data={temp_data_log}")
    
    # Выбираем подсказку в зависимости от типа
    driver_type = db.get_telegram_session_data(user_id, 'driver_type', 'taxi')
    
    if driver_type == 'ant':
        # Муравьи регистрируются БЕЗ марки авто и госномера
        db.set_telegram_session_data(user_id, 'car_model', 'Муравей')
        db.set_telegram_session_data(user_id, 'plate', '—')
        db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_CONFIRM)

        session = db.get_telegram_session(user_id)
        temp_data = (session.get('temp_data') or {}) if session else {}
        logger.info(f"[DRIVER_REG_STEP3_ANT] tid={user_id} temp_data={temp_data}")

        msg = config.DRIVER_REG_CONFIRM_TEMPLATE_ANT.format(
            type_emoji='🐜',
            driver_type=config.DRIVER_TYPES.get('ant', 'Муравей'),
            name=temp_data.get('name', ''),
            phone=phone
        )
        buttons = [
            {"text": "✅ Да, всё верно", "callback": "dreg_confirm_yes"},
            {"text": "❌ Нет, начать заново", "callback": "dreg_confirm_no"}
        ]
        send_telegram_private(user_id, msg, buttons)
        return jsonify({"status": "ok"}), 200

    if driver_type == 'scooter':
        # Скутеры регистрируются БЕЗ марки авто и госномера
        db.set_telegram_session_data(user_id, 'car_model', 'Скутер')
        db.set_telegram_session_data(user_id, 'plate', '—')
        db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_CONFIRM)

        session = db.get_telegram_session(user_id)
        temp_data = (session.get('temp_data') or {}) if session else {}
        logger.info(f"[DRIVER_REG_STEP3_SCOOTER] tid={user_id} temp_data={temp_data}")

        msg = config.DRIVER_REG_CONFIRM_TEMPLATE_SCOOTER.format(
            name=temp_data.get('name', ''),
            phone=phone
        )
        buttons = [
            {"text": "✅ Да, всё верно", "callback": "dreg_confirm_yes"},
            {"text": "❌ Нет, начать заново", "callback": "dreg_confirm_no"}
        ]
        send_telegram_private(user_id, msg, buttons)
        return jsonify({"status": "ok"}), 200

    if driver_type == 'raznarabochi':
        # Разнарабочие регистрируются БЕЗ марки авто и госномера
        db.set_telegram_session_data(user_id, 'car_model', '—')
        db.set_telegram_session_data(user_id, 'plate', '—')
        db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_CONFIRM)

        session = db.get_telegram_session(user_id)
        temp_data = (session.get('temp_data') or {}) if session else {}
        logger.info(f"[DRIVER_REG_STEP3_RAZNARABOCHI] tid={user_id} temp_data={temp_data}")

        msg = config.DRIVER_REG_CONFIRM_TEMPLATE_RAZNARABOCHI.format(
            name=temp_data.get('name', ''),
            phone=phone
        )
        buttons = [
            {"text": "✅ Да, всё верно", "callback": "dreg_confirm_yes"},
            {"text": "❌ Нет, начать заново", "callback": "dreg_confirm_no"}
        ]
        send_telegram_private(user_id, msg, buttons)
        return jsonify({"status": "ok"}), 200

    db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_CAR)
    
    if driver_type == 'porter':
        prompt = config.DRIVER_REG_CAR_PROMPT_PORTER
    else:
        prompt = config.DRIVER_REG_CAR_PROMPT_TAXI
    
    send_telegram_private(user_id, prompt)
    return jsonify({"status": "ok"}), 200


def _handle_reg_car(user_id: str, text: str, db) -> tuple:
    """Шаг 4: Ввод марки авто"""
    
    if len(text) < 2:
        send_telegram_private(user_id, "⚠️ Введите марку и модель вашего транспорта.")
        return jsonify({"status": "ok"}), 200
    
    db.set_telegram_session_data(user_id, 'car_model', text)
    db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_PLATE)
    
    # DEBUG LOG
    session = db.get_telegram_session(user_id)
    temp_data_log = (session.get('temp_data') or {}) if session else 'NO_SESSION'
    logger.info(f"[DRIVER_REG_STEP4] tid={user_id} car_model='{text}' temp_data={temp_data_log}")
    
    send_telegram_private(user_id, config.DRIVER_REG_PLATE_PROMPT)
    return jsonify({"status": "ok"}), 200


def _handle_reg_plate(user_id: str, text: str, db) -> tuple:
    """Шаг 5: Ввод госномера"""
    
    if len(text) < 3:
        send_telegram_private(user_id, "⚠️ Введите государственный номер вашего транспорта.")
        return jsonify({"status": "ok"}), 200
    
    db.set_telegram_session_data(user_id, 'plate', text.upper())
    db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_CONFIRM)
    
    # Собираем все данные для подтверждения
    session = db.get_telegram_session(user_id)
    temp_data = (session.get('temp_data') or {}) if session else {}
    
    # DEBUG LOG
    logger.info(f"[DRIVER_REG_STEP5] tid={user_id} plate='{text.upper()}' temp_data={temp_data}")
    
    driver_type_key = temp_data.get('driver_type', 'taxi')
    type_emoji = config.DRIVER_TYPES.get(driver_type_key, '🚖 Такси').split(' ')[0]
    
    msg = config.DRIVER_REG_CONFIRM_TEMPLATE.format(
        type_emoji=type_emoji,
        driver_type=config.DRIVER_TYPES.get(driver_type_key, driver_type_key),
        name=temp_data.get('name', ''),
        phone=temp_data.get('phone', ''),
        car_model=temp_data.get('car_model', ''),
        plate=text.upper()
    )
    
    buttons = [
        {"text": "✅ Да, всё верно", "callback": "dreg_confirm_yes"},
        {"text": "❌ Нет, начать заново", "callback": "dreg_confirm_no"}
    ]
    
    send_telegram_private(user_id, msg, buttons)
    return jsonify({"status": "ok"}), 200


def _handle_reg_confirm(user_id: str, text: str, db) -> tuple:
    """Шаг 6: Подтверждение регистрации"""
    text_lower = text.lower().strip()
    
    # DEBUG LOG
    session = db.get_telegram_session(user_id)
    temp_data_log = (session.get('temp_data') or {}) if session else 'NO_SESSION'
    logger.info(f"[DRIVER_REG_STEP6] tid={user_id} text='{text}' temp_data={temp_data_log}")
    
    # Проверяем состояние
    if not session or session.get('state') != config.STATE_DRIVER_REG_CONFIRM:
        logger.warning(f"[DRIVER_REG_STEP6] tid={user_id} ignored: wrong state or no session")
        return jsonify({"status": "ok"}), 200
    
    if text_lower in ('да', 'yes', 'ооба', 'верно', 'ок', 'ok', '✅'):
        return _save_driver_registration(user_id, db)
    
    elif text_lower in ('нет', 'no', 'жок', 'неверно', '❌'):
        # Начинаем заново
        db.create_telegram_session(user_id)
        db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_TYPE)

        buttons = [
            {"text": "🚖 Такси", "callback": "dreg_type_taxi"},
            {"text": "🚛 Портер", "callback": "dreg_type_porter"},
            {"text": "🐜 Муравей", "callback": "dreg_type_ant"},
            {"text": "🛵 Скутер", "callback": "dreg_type_scooter"},
            {"text": "👷 Разнарабочий", "callback": "dreg_type_raznarabochi"},
            {"text": "🚘 Попутка", "callback": "dreg_type_poputka"},
        ]

        send_telegram_private(
            user_id,
            "🔄 Начинаем заново.\n\n" + config.DRIVER_REG_TYPE_PROMPT,
            buttons
        )
        return jsonify({"status": "ok"}), 200
    
    else:
        send_telegram_private(user_id, "⚠️ Напишите *Да* или *Нет*.")
        return jsonify({"status": "ok"}), 200


def _save_driver_registration(user_id: str, db) -> tuple:
    """Сохранение регистрации водителя"""

    # Проверяем, не зарегистрирован ли уже водитель (защита от двойного вызова)
    existing_driver = db.get_driver(user_id)
    if existing_driver and existing_driver.get('name') and existing_driver.get('phone'):
        logger.info(f"[DRIVER_REG_SAVE] Driver {user_id} already registered, skipping")
        return jsonify({"status": "ok"}), 200

    session = db.get_telegram_session(user_id)
    if not session:
        logger.error(f"[DRIVER_REG_SAVE] No session found for driver {user_id}")
        # Если сессии нет, но водитель существует - просто возвращаем успех
        if existing_driver:
            return jsonify({"status": "ok"}), 200
        send_telegram_private(user_id, "❌ Ошибка: сессия не найдена. Начните регистрацию заново с /register")
        return jsonify({"status": "error"}), 400

    # Защита от None в temp_data (если в БД NULL)
    temp_data = (session.get('temp_data') or {})

    # DEBUG: Логируем все данные
    logger.info(f"[DRIVER_REG_SAVE] tid={user_id} temp_data={temp_data}")

    driver_type = temp_data.get('driver_type', 'taxi')
    name = (temp_data.get('name', '') or '').strip()
    phone = (temp_data.get('phone', '') or '').strip()
    car_model = (temp_data.get('car_model', '') or '').strip()
    plate = (temp_data.get('plate', '') or '').strip()

    logger.info(f"[DRIVER_REG_SAVE] tid={user_id} name='{name}' phone='{phone}' car_model='{car_model}' plate='{plate}' driver_type='{driver_type}'")

    # Валидация критичных полей
    if not name or not phone:
        logger.error(f"[DRIVER_REG_SAVE] FAILED: missing data for {user_id}: name={bool(name)}, phone={bool(phone)}")
        send_telegram_private(
            user_id,
            "❌ Ошибка регистрации: данные сессии отсутствуют или неполные.\n"
            "Пожалуйста, введите /register и пройдите регистрацию заново."
        )
        return jsonify({"status": "error", "message": "Missing registration data"}), 200

    # Сохраняем водителя
    db.add_driver(
        telegram_id=user_id,
        name=name,
        phone=phone,
        car_model=car_model,
        plate=plate,
        driver_type=driver_type
    )
    
    # Очищаем сессию
    db.clear_telegram_session(user_id)
    
    # Получаем баланс
    balance = db.get_driver_balance(user_id)
    
    # Определяем ссылку на группу
    group_link = "https://t.me/jardamchy_go"  # Fallback
    if driver_type == 'taxi':
        group_link = "https://t.me/+ZhceAJUcbmJjODAy"  # ЗАМЕНИТЬ НА РЕАЛЬНУЮ ССЫЛКУ ТАКСИ
    elif driver_type == 'scooter':
        group_link = "https://t.me/+ZhceAJUcbmJjODAy"  # Скутеры — та же группа, что такси
    elif driver_type == 'porter':
        group_link = "https://t.me/+l88NvbDcTWg1MThi"  # ЗАМЕНИТЬ НА РЕАЛЬНУЮ ССЫЛКУ ПОРТЕР
    elif driver_type == 'ant':
        group_link = "https://t.me/+l88NvbDcTWg1MThi"  # ЗАМЕНИТЬ НА РЕАЛЬНУЮ ССЫЛКУ МУРАВЕЙ
    elif driver_type == 'raznarabochi':
        group_link = "https://t.me/jardamchy_go"  # ЗАМЕНИТЬ НА РЕАЛЬНУЮ ССЫЛКУ РАЗНАРАБОЧИЙ
    elif driver_type == 'poputka':
        group_link = "https://t.me/jardamchy_go"  # ЗАМЕНИТЬ НА РЕАЛЬНУЮ ССЫЛКУ ПОПУТКА
        
    msg = config.DRIVER_REG_SUCCESS.format(
        driver_type=config.DRIVER_TYPES.get(driver_type, driver_type),
        balance=balance,
        group_link=group_link
    )
    
    send_telegram_private(user_id, msg)
    
    # Логируем
    db.log_transaction(
        "DRIVER_SELF_REGISTERED",
        user_id,
        details=f"Type: {driver_type}, Name: {name}, Car: {car_model} {plate}"
    )
    
    logger.info(f"New driver registered: {name} ({user_id}) - {driver_type}")
    
    return jsonify({"status": "ok"}), 200


# =============================================================================
# ОБРАБОТКА CALLBACK КНОПОК РЕГИСТРАЦИИ
# =============================================================================

def handle_driver_reg_callback(data: str, user_id: str, user_name: str, db) -> tuple:
    """Обработка нажатия кнопок регистрации водителя"""
    try:
        # DEBUG LOG
        logger.info(f"[DRIVER_REG_CALLBACK] tid={user_id} data='{data}'")
        
        # dreg_type_taxi, dreg_type_porter, dreg_type_ant
        if data.startswith("dreg_type_"):
            driver_type = data.replace("dreg_type_", "")

            if driver_type not in ('taxi', 'porter', 'ant', 'scooter', 'raznarabochi', 'poputka'):
                return jsonify({"status": "ok"}), 200
            
            db.set_telegram_session_data(user_id, 'driver_type', driver_type)
            db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_NAME)
            
            # DEBUG LOG - проверяем что записалось
            session = db.get_telegram_session(user_id)
            temp_data_log = (session.get('temp_data') or {}) if session else 'NO_SESSION'
            logger.info(f"[DRIVER_REG_CALLBACK] tid={user_id} driver_type='{driver_type}' saved. temp_data={temp_data_log}")
            
            type_name = config.DRIVER_TYPES.get(driver_type, driver_type)
            send_telegram_private(
                user_id, 
                f"✅ Выбран тип: *{type_name}*\n\n" + config.DRIVER_REG_NAME_PROMPT
            )
            return jsonify({"status": "ok"}), 200
        
        # dreg_confirm_yes, dreg_confirm_no
        elif data == "dreg_confirm_yes":
            # Проверяем, что мы в правильном состоянии
            session = db.get_telegram_session(user_id)
            if not session or session.get('state') != config.STATE_DRIVER_REG_CONFIRM:
                logger.warning(f"[DRIVER_REG_CALLBACK] tid={user_id} confirm ignored: wrong state or no session")
                return jsonify({"status": "ok"}), 200
            logger.info(f"[DRIVER_REG_CALLBACK] tid={user_id} confirming registration")
            return _save_driver_registration(user_id, db)
        
        elif data == "dreg_confirm_no":
            db.create_telegram_session(user_id)
            db.set_telegram_session_state(user_id, config.STATE_DRIVER_REG_TYPE)

            buttons = [
                {"text": "🚖 Такси", "callback": "dreg_type_taxi"},
                {"text": "🚛 Портер", "callback": "dreg_type_porter"},
                {"text": "🐜 Муравей", "callback": "dreg_type_ant"},
                {"text": "🛵 Скутер", "callback": "dreg_type_scooter"},
                {"text": "👷 Разнарабочий", "callback": "dreg_type_raznarabochi"},
                {"text": "🚘 Попутка", "callback": "dreg_type_poputka"},
            ]

            send_telegram_private(
                user_id,
                "🔄 Начинаем заново.\n\n" + config.DRIVER_REG_TYPE_PROMPT,
                buttons
            )
            return jsonify({"status": "ok"}), 200
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.exception("Error handling driver registration callback")
        return jsonify({"status": "error", "message": str(e)}), 500
