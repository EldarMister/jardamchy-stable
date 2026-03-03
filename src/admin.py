"""
Модуль администрирования для Business Assistant GO
Admin Module — расширенная версия с визуальной админкой
"""

from flask import Blueprint, request, jsonify, send_from_directory, Response
import logging
import os
import requests as req_lib
from datetime import datetime, timedelta
from decimal import Decimal

import config
from db import get_db, RUNTIME_SETTING_DEFAULTS
from services import send_telegram_private, send_telegram_broadcast, send_telegram_group, edit_telegram_message, send_whatsapp_plain

logger = logging.getLogger(__name__)

# Создаем Blueprint для админских роутов
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Путь к файлам админ-панели
ADMIN_PANEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'admin_panel')


def is_admin(telegram_id: str) -> bool:
    """Проверить, является ли пользователь администратором"""
    return telegram_id in config.ADMIN_TELEGRAM_IDS


def _serialize(obj):
    """Сериализация объектов для JSON"""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _clean_row(row):
    """Очистить строку от несериализуемых типов"""
    if not row:
        return row
    return {k: _serialize(v) for k, v in row.items()}


def _clean_rows(rows):
    """Очистить список строк"""
    return [_clean_row(r) for r in rows]


# =============================================================================
# ADMIN PANEL STATIC FILES
# =============================================================================

@admin_bp.route('/panel')
@admin_bp.route('/panel/')
def serve_panel():
    """Отдать главную страницу админки"""
    return send_from_directory(ADMIN_PANEL_DIR, 'index.html')


@admin_bp.route('/panel/<path:filename>')
def serve_panel_file(filename):
    """Отдать статические файлы админки"""
    return send_from_directory(ADMIN_PANEL_DIR, filename)


# =============================================================================
# DASHBOARD
# =============================================================================

@admin_bp.route('/dashboard', methods=['GET'])
def get_dashboard():
    """Агрегированная статистика для дашборда"""
    try:
        db = get_db()
        runtime = db.get_runtime_settings()
        cafe_commission_percent = float(runtime["cafe_commission_percent"])
        porter_commission = float(runtime["porter_commission"])
        ant_commission = float(runtime["ant_commission"])
        taxi_commission = float(runtime["taxi_commission"])
        taxi_shop_commission = float(runtime["taxi_shop_commission"])
        pharmacy_commission_percent = float(runtime["pharmacy_commission_percent"])
        taxi_turnover = 120  # Фиксированный оборот такси за заказ

        # --- Orders and earnings for selected periods ---
        with db.get_cursor() as cur:
            # Сегодня
            cur.execute(f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) as completed,
                    COUNT(CASE WHEN status = 'CANCELLED' THEN 1 END) as cancelled,
                    COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_turnover} ELSE price_total END
                    ELSE 0 END), 0) as revenue,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_commission} ELSE COALESCE(commission, 0) END
                    ELSE 0 END), 0) as commission
                FROM orders WHERE DATE(created_at) = CURRENT_DATE
            """)
            today = _clean_row(cur.fetchone())

            # Неделя
            cur.execute(f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) as completed,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_turnover} ELSE price_total END
                    ELSE 0 END), 0) as revenue,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_commission} ELSE COALESCE(commission, 0) END
                    ELSE 0 END), 0) as commission
                FROM orders WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'
            """)
            week = _clean_row(cur.fetchone())

            # Месяц
            cur.execute(f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) as completed,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_turnover} ELSE price_total END
                    ELSE 0 END), 0) as revenue,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_commission} ELSE COALESCE(commission, 0) END
                    ELSE 0 END), 0) as commission
                FROM orders WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
            """)
            month = _clean_row(cur.fetchone())

            # Все время
            cur.execute(f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) as completed,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_turnover} ELSE price_total END
                    ELSE 0 END), 0) as revenue,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_commission} ELSE COALESCE(commission, 0) END
                    ELSE 0 END), 0) as commission
                FROM orders
            """)
            all_time = _clean_row(cur.fetchone())

            # По типам услуг — за каждый период
            # Комиссия считается по формулам:
            #   cafe: 5% от price_total
            #   porter: 20 сом фикс за заказ
            #   ant: 10 сом фикс за заказ
            #   taxi/shop: берём из колонки commission (или 10 сом фикс если 0)
            #   pharmacy: 5% от price_total
            def _by_service_query(where_clause=""):
                sql = f"""
                    SELECT
                        service_type,
                        COUNT(*) as count,
                        COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                            CASE WHEN service_type = 'taxi' THEN {taxi_turnover} ELSE price_total END
                        ELSE 0 END), 0) as revenue,
                        COALESCE(SUM(
                            CASE WHEN status = 'COMPLETED' THEN
                                CASE
                                    WHEN service_type = 'cafe' THEN price_total * ({cafe_commission_percent} / 100.0)
                                    WHEN service_type = 'porter' THEN {porter_commission}
                                    WHEN service_type = 'ant' THEN {ant_commission}
                                    WHEN service_type = 'pharmacy' THEN price_total * ({pharmacy_commission_percent} / 100.0)
                                    WHEN service_type = 'taxi' THEN {taxi_commission}
                                    WHEN service_type = 'shop' THEN {taxi_shop_commission}
                                    ELSE COALESCE(commission, 0)
                                END
                            ELSE 0 END
                        ), 0) as commission
                    FROM orders
                    {where_clause}
                    GROUP BY service_type
                """
                cur.execute(sql)
                return _clean_rows(cur.fetchall())

            by_service_day = _by_service_query("WHERE DATE(created_at) = CURRENT_DATE")
            by_service_week = _by_service_query("WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'")
            by_service_month = _by_service_query("WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'")
            by_service_all = _by_service_query()
            by_service = by_service_all  # backward compat

            # Заказы по дням за последние 7 дней
            cur.execute(f"""
                SELECT
                    DATE(created_at) as date,
                    COUNT(*) as count,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN
                        CASE WHEN service_type = 'taxi' THEN {taxi_turnover} ELSE price_total END
                    ELSE 0 END), 0) as revenue
                FROM orders
                WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'
                GROUP BY DATE(created_at)
                ORDER BY date
            """)
            daily_chart = _clean_rows(cur.fetchall())

            # Кол-во сущностей
            cur.execute("SELECT COUNT(*) as count FROM drivers WHERE is_active = TRUE")
            drivers_count = cur.fetchone()['count']

            cur.execute("SELECT COUNT(*) as count FROM cafes WHERE is_active = TRUE")
            cafes_count = cur.fetchone()['count']

            cur.execute("SELECT COUNT(*) as count FROM users")
            users_count = cur.fetchone()['count']

            cur.execute("SELECT COUNT(*) as count FROM pharmacies WHERE is_active = TRUE")
            pharmacies_count = cur.fetchone()['count']

        return jsonify({
            "today": today,
            "week": week,
            "month": month,
            "all_time": all_time,
            "by_service": by_service,
            "by_service_day": by_service_day,
            "by_service_week": by_service_week,
            "by_service_month": by_service_month,
            "by_service_all": by_service_all,
            "daily_chart": daily_chart,
            "counts": {
                "drivers": drivers_count,
                "cafes": cafes_count,
                "users": users_count,
                "pharmacies": pharmacies_count
            },
            "ramadan_mode": config.IS_RAMADAN
        }), 200

    except Exception as e:
        logger.exception("Error getting dashboard")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# DRIVER MANAGEMENT
# =============================================================================

@admin_bp.route('/drivers', methods=['GET'])
def list_drivers():
    """Получить список водителей"""
    try:
        db = get_db()
        driver_type = request.args.get('type')
        active_only = request.args.get('active', 'true').lower() == 'true'
        drivers = db.list_drivers(driver_type=driver_type, active_only=active_only)

        # Добавляем статистику заказов к каждому водителю
        for d in drivers:
            stats = db.get_driver_order_stats(d['telegram_id'])
            d['order_stats'] = _clean_row(stats)

        return jsonify({
            "count": len(drivers),
            "drivers": _clean_rows(drivers)
        }), 200

    except Exception as e:
        logger.exception("Error listing drivers")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/drivers', methods=['POST'])
def add_driver():
    """Добавить нового водителя"""
    try:
        data = request.get_json()

        telegram_id = data.get('telegram_id')
        name = data.get('name')
        phone = data.get('phone')
        car_model = data.get('car_model')
        plate = data.get('plate')
        driver_type = data.get('type', 'taxi')

        if not telegram_id or not name:
            return jsonify({"error": "telegram_id and name are required"}), 400

        db = get_db()
        success = db.add_driver(telegram_id, name, phone, car_model, plate, driver_type)

        if success:
            welcome_msg = f"""✅ *Вы добавлены в систему Жардамчы ГО!*

👤 *Имя:* {name}
🚗 *Тип:* {driver_type}

Теперь вы можете принимать заказы в группе.

💰 Не забудьте пополнить баланс для приема заказов."""

            send_telegram_private(telegram_id, welcome_msg)

            return jsonify({"success": True, "message": "Driver added successfully"}), 201
        else:
            return jsonify({"error": "Failed to add driver"}), 500

    except Exception as e:
        logger.exception("Error adding driver")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/drivers/<telegram_id>', methods=['PUT'])
def update_driver(telegram_id):
    """Обновить данные водителя"""
    try:
        data = request.get_json()

        name = data.get('name')
        phone = data.get('phone')
        car_model = data.get('car_model')
        plate = data.get('plate')

        db = get_db()
        success = db.update_driver_info(
            telegram_id,
            name=name,
            phone=phone,
            car_model=car_model,
            plate=plate
        )

        if success:
            return jsonify({"success": True, "message": "Driver updated"}), 200
        else:
            return jsonify({"error": "Driver not found or no changes"}), 404

    except Exception as e:
        logger.exception("Error updating driver")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/drivers/<telegram_id>', methods=['DELETE'])
def remove_driver(telegram_id):
    """Удалить водителя"""
    try:
        db = get_db()
        success = db.remove_driver(telegram_id)

        if success:
            msg = "❌ Вы удалены из системы Жардамчы ГО.\n\nОбратитесь к администратору для уточнения."
            send_telegram_private(telegram_id, msg)

            return jsonify({"success": True, "message": "Driver removed"}), 200
        else:
            return jsonify({"error": "Driver not found"}), 404

    except Exception as e:
        logger.exception("Error removing driver")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/drivers/<telegram_id>/balance', methods=['POST'])
def update_driver_balance(telegram_id):
    """Обновить баланс водителя"""
    try:
        data = request.get_json()
        amount = data.get('amount')
        reason = data.get('reason', 'Пополнение через админку')

        if amount is None:
            return jsonify({"error": "amount is required"}), 400

        db = get_db()
        success, new_balance = db.update_driver_balance(telegram_id, amount, reason)

        if success:
            action = "пополнен" if amount > 0 else "списан"
            msg = f"""💰 *Баланс {action}*

Сумма: {abs(amount)} сом
Причина: {reason}

💳 Новый баланс: {new_balance} сом"""

            send_telegram_private(telegram_id, msg)

            return jsonify({
                "success": True,
                "new_balance": float(new_balance)
            }), 200
        else:
            return jsonify({"error": "Insufficient balance or driver not found"}), 400

    except Exception as e:
        logger.exception("Error updating driver balance")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# CAFE MANAGEMENT
# =============================================================================

@admin_bp.route('/cafes', methods=['GET'])
def list_cafes():
    """Получить список кафе"""
    try:
        db = get_db()
        cafes = db.list_cafes(active_only=False)

        return jsonify({
            "count": len(cafes),
            "cafes": _clean_rows(cafes)
        }), 200

    except Exception as e:
        logger.exception("Error listing cafes")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/cafes', methods=['POST'])
def add_cafe():
    """Добавить новое кафе"""
    try:
        data = request.get_json()

        telegram_id = data.get('telegram_id')
        name = data.get('name')
        phone = data.get('phone')
        address = data.get('address')

        if not telegram_id or not name:
            return jsonify({"error": "telegram_id and name are required"}), 400

        db = get_db()
        success = db.add_cafe(telegram_id, name, phone, address)

        if success:
            welcome_msg = f"""✅ *{name} добавлено в систему Жардамчы ГО!*

Теперь вы будете получать заказы в группе.

💰 Комиссия: {config.CAFE_COMMISSION_PERCENT}% от суммы заказа."""

            send_telegram_private(telegram_id, welcome_msg)

            return jsonify({"success": True, "message": "Cafe added successfully"}), 201
        else:
            return jsonify({"error": "Failed to add cafe"}), 500

    except Exception as e:
        logger.exception("Error adding cafe")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/cafes/<telegram_id>', methods=['PUT'])
def update_cafe(telegram_id):
    """Обновить данные кафе"""
    try:
        data = request.get_json()

        name = data.get('name')
        phone = data.get('phone')
        address = data.get('address')
        commission_percent = data.get('commission_percent')
        is_active = data.get('is_active')

        db = get_db()
        success = db.update_cafe_info(
            telegram_id,
            name=name,
            phone=phone,
            address=address,
            commission_percent=commission_percent,
            is_active=is_active
        )

        if success:
            return jsonify({"success": True, "message": "Cafe updated"}), 200
        else:
            return jsonify({"error": "Cafe not found or no changes"}), 404

    except Exception as e:
        logger.exception("Error updating cafe")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/cafes/<telegram_id>', methods=['DELETE'])
def remove_cafe(telegram_id):
    """Деактивировать/удалить кафе"""
    try:
        db = get_db()
        success = db.remove_cafe(telegram_id)

        if success:
            return jsonify({"success": True, "message": "Cafe removed"}), 200
        else:
            return jsonify({"error": "Cafe not found"}), 404

    except Exception as e:
        logger.exception("Error removing cafe")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/cafes/<telegram_id>/debt', methods=['GET'])
def get_cafe_debt(telegram_id):
    """Получить долг кафе"""
    try:
        db = get_db()
        debt = db.get_cafe_debt(telegram_id)

        return jsonify({"debt": float(debt)}), 200

    except Exception as e:
        logger.exception("Error getting cafe debt")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/cafes/<telegram_id>/debt', methods=['POST'])
def update_cafe_debt(telegram_id):
    """Ручная корректировка долга кафе через админку."""
    try:
        data = request.get_json() or {}
        amount = data.get('amount')
        reason = data.get('reason', 'Пополнение через админку')

        if amount is None:
            return jsonify({"error": "amount is required"}), 400

        db = get_db()
        success, new_debt = db.adjust_cafe_debt(telegram_id, float(amount), reason)
        if not success:
            return jsonify({"error": "Cafe not found"}), 404

        return jsonify({
            "success": True,
            "new_debt": float(new_debt),
            "new_balance": float(-new_debt)
        }), 200
    except Exception as e:
        logger.exception("Error updating cafe debt")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# ORDERS MANAGEMENT
# =============================================================================

@admin_bp.route('/orders', methods=['GET'])
def list_orders():
    """Получить список заказов с фильтрацией"""
    try:
        db = get_db()
        status = request.args.get('status')
        service = request.args.get('service')
        period = request.args.get('period', 'all')
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)

        with db.get_cursor() as cur:
            query = "SELECT * FROM orders WHERE 1=1"
            count_query = "SELECT COUNT(*) as total FROM orders WHERE 1=1"
            params = []
            count_params = []

            if status:
                query += " AND status = %s"
                count_query += " AND status = %s"
                params.append(status)
                count_params.append(status)

            if service:
                query += " AND service_type = %s"
                count_query += " AND service_type = %s"
                params.append(service)
                count_params.append(service)

            if period == 'day':
                query += " AND DATE(created_at) = CURRENT_DATE"
                count_query += " AND DATE(created_at) = CURRENT_DATE"
            elif period == 'week':
                query += " AND created_at >= CURRENT_DATE - INTERVAL '7 days'"
                count_query += " AND created_at >= CURRENT_DATE - INTERVAL '7 days'"
            elif period == 'month':
                query += " AND created_at >= CURRENT_DATE - INTERVAL '30 days'"
                count_query += " AND created_at >= CURRENT_DATE - INTERVAL '30 days'"

            # Count
            cur.execute(count_query, count_params)
            total = cur.fetchone()['total']

            # Data
            query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])

            cur.execute(query, params)
            orders = _clean_rows([dict(row) for row in cur.fetchall()])

        return jsonify({
            "total": total,
            "count": len(orders),
            "orders": orders
        }), 200

    except Exception as e:
        logger.exception("Error listing orders")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/orders/<order_id>', methods=['GET'])
def get_order_detail(order_id):
    """Получить детали заказа"""
    try:
        db = get_db()
        order = db.get_order(order_id)
        if not order:
            return jsonify({"error": "Order not found"}), 404

        order_data = _clean_row(order)

        # Подгружаем информацию о водителе по telegram_id
        driver_telegram_id = order.get('driver_id')
        if driver_telegram_id:
            with db.get_cursor() as cur:
                cur.execute(
                    "SELECT name, phone, car_model, plate, driver_type FROM drivers WHERE telegram_id = %s",
                    (str(driver_telegram_id),)
                )
                driver_row = cur.fetchone()
                if driver_row:
                    order_data['driver_info'] = _clean_row(dict(driver_row))

        return jsonify({"order": order_data}), 200

    except Exception as e:
        logger.exception("Error getting order")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/orders', methods=['POST'])
def create_order_admin():
    """Создать заказ из админки"""
    try:
        db = get_db()
        body = request.json or {}
        client_phone = body.get('client_phone', '').strip()
        service_type = body.get('service_type', '').strip()
        details = body.get('details', '').strip()
        if not client_phone or not service_type or not details:
            return jsonify({"error": "client_phone, service_type, details обязательны"}), 400
        order_id = db.create_order(
            client_phone=client_phone,
            service_type=service_type,
            details=details,
            address=body.get('address') or None,
            payment_method=body.get('payment_method') or None,
            price=float(body.get('price') or 0),
        )
        return jsonify({"order_id": order_id}), 201
    except Exception as e:
        logger.exception("Error creating order from admin")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/orders/<order_id>', methods=['PATCH'])
def patch_order_admin(order_id):
    """Сменить статус заказа из админки"""
    try:
        db = get_db()
        body = request.json or {}
        status = body.get('status', '').strip().upper()
        valid = {'PENDING', 'AUCTION', 'ACCEPTED', 'READY', 'IN_DELIVERY', 'COMPLETED', 'CANCELLED', 'URGENT'}
        if status not in valid:
            return jsonify({"error": f"Недопустимый статус: {status}"}), 400
        ok = db.update_order_status(order_id, status)
        if not ok:
            return jsonify({"error": "Заказ не найден"}), 404

        # При отмене — редактируем сообщение в Telegram-группе
        if status == 'CANCELLED':
            try:
                timer = db.get_latest_auction_timer(order_id)
                if timer:
                    chat_id = timer.get('chat_id')
                    message_id = timer.get('telegram_message_id')
                    if chat_id and message_id:
                        edit_telegram_message(
                            chat_id, int(message_id),
                            "❌ *ЗАКАЗ ОТМЕНЁН*\n\nАдмин отменил заказ.",
                            buttons=[]
                        )
                    db.mark_auction_processed(timer['id'])
            except Exception:
                logger.exception("Failed to edit group message on admin cancel order_id=%s", order_id)

        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.exception("Error patching order from admin")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# CAFE SETTINGS (global discount)
# =============================================================================

@admin_bp.route('/cafe-settings', methods=['GET'])
def admin_cafe_settings_get():
    try:
        db = get_db()
        cafe_id = request.args.get('cafe_id')
        if not cafe_id:
            return jsonify({"error": "cafe_id required"}), 400
        settings = db.get_cafe_settings(int(cafe_id))
        return jsonify(settings), 200
    except Exception as e:
        logger.exception("Error getting cafe settings")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/cafe-settings', methods=['PUT'])
def admin_cafe_settings_put():
    try:
        db = get_db()
        data = request.get_json() or {}
        cafe_id = data.get('cafe_id')
        if not cafe_id:
            return jsonify({"error": "cafe_id required"}), 400
        success = db.update_cafe_settings(
            int(cafe_id),
            global_discount_percent=float(data.get('global_discount_percent', 0)),
            global_discount_active=bool(data.get('global_discount_active', False))
        )
        return jsonify({"success": success}), 200
    except Exception as e:
        logger.exception("Error updating cafe settings")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# PHARMACIES
# =============================================================================

@admin_bp.route('/pharmacies', methods=['GET'])
def list_pharmacies():
    """Получить список аптек"""
    try:
        db = get_db()
        with db.get_cursor() as cur:
            cur.execute("SELECT * FROM pharmacies ORDER BY name")
            pharmacies = _clean_rows([dict(row) for row in cur.fetchall()])

        return jsonify({
            "count": len(pharmacies),
            "pharmacies": pharmacies
        }), 200

    except Exception as e:
        logger.exception("Error listing pharmacies")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/pharmacies', methods=['POST'])
def add_pharmacy():
    """Добавить новую аптеку"""
    try:
        data = request.get_json()
        telegram_id = data.get('telegram_id')
        name = data.get('name')
        phone = data.get('phone', '')
        address = data.get('address', '')

        if not telegram_id or not name:
            return jsonify({"error": "telegram_id and name are required"}), 400

        db = get_db()
        with db.get_cursor() as cur:
            cur.execute("""
                INSERT INTO pharmacies (telegram_id, name, phone, address, is_active, created_at)
                VALUES (%s, %s, %s, %s, TRUE, CURRENT_TIMESTAMP)
                ON CONFLICT (telegram_id) DO UPDATE SET name = %s, phone = %s, address = %s, is_active = TRUE
            """, (telegram_id, name, phone, address, name, phone, address))

        send_telegram_private(telegram_id, f"✅ *{name}* добавлена в систему Жардамчы ГО!")

        return jsonify({"success": True, "message": "Pharmacy added"}), 201

    except Exception as e:
        logger.exception("Error adding pharmacy")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/pharmacies/<telegram_id>', methods=['DELETE'])
def remove_pharmacy(telegram_id):
    """Удалить аптеку"""
    try:
        db = get_db()
        with db.get_cursor() as cur:
            cur.execute("UPDATE pharmacies SET is_active = FALSE WHERE telegram_id = %s", (telegram_id,))
            if cur.rowcount == 0:
                return jsonify({"error": "Pharmacy not found"}), 404

        return jsonify({"success": True, "message": "Pharmacy removed"}), 200

    except Exception as e:
        logger.exception("Error removing pharmacy")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# SHOPPERS
# =============================================================================

@admin_bp.route('/shoppers', methods=['GET'])
def list_shoppers():
    """Получить список закупщиков"""
    try:
        db = get_db()
        with db.get_cursor() as cur:
            cur.execute("SELECT * FROM shoppers ORDER BY name")
            shoppers = _clean_rows([dict(row) for row in cur.fetchall()])

        return jsonify({
            "count": len(shoppers),
            "shoppers": shoppers
        }), 200

    except Exception as e:
        logger.exception("Error listing shoppers")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/shoppers', methods=['POST'])
def add_shopper():
    """Добавить нового закупщика"""
    try:
        data = request.get_json()
        telegram_id = data.get('telegram_id')
        name = data.get('name')
        phone = data.get('phone', '')

        if not telegram_id or not name:
            return jsonify({"error": "telegram_id and name are required"}), 400

        db = get_db()
        with db.get_cursor() as cur:
            cur.execute("""
                INSERT INTO shoppers (telegram_id, name, phone, is_active, balance, created_at)
                VALUES (%s, %s, %s, TRUE, 0, CURRENT_TIMESTAMP)
                ON CONFLICT (telegram_id) DO UPDATE SET name = %s, phone = %s, is_active = TRUE
            """, (telegram_id, name, phone, name, phone))

        send_telegram_private(telegram_id, f"✅ *{name}*, вы добавлены в систему Жардамчы ГО как закупщик!")

        return jsonify({"success": True, "message": "Shopper added"}), 201

    except Exception as e:
        logger.exception("Error adding shopper")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/shoppers/<telegram_id>', methods=['DELETE'])
def remove_shopper(telegram_id):
    """Удалить закупщика"""
    try:
        db = get_db()
        with db.get_cursor() as cur:
            cur.execute("UPDATE shoppers SET is_active = FALSE WHERE telegram_id = %s", (telegram_id,))
            if cur.rowcount == 0:
                return jsonify({"error": "Shopper not found"}), 404

        return jsonify({"success": True, "message": "Shopper removed"}), 200

    except Exception as e:
        logger.exception("Error removing shopper")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# USERS (WhatsApp)
# =============================================================================

@admin_bp.route('/users', methods=['GET'])
def list_users():
    """Получить список WhatsApp пользователей"""
    try:
        db = get_db()
        with db.get_cursor() as cur:
            cur.execute("""
                SELECT u.*, 
                    (SELECT COUNT(*) FROM orders WHERE client_phone = u.phone) as order_count
                FROM users u
                ORDER BY u.created_at DESC
                LIMIT 200
            """)
            users = _clean_rows([dict(row) for row in cur.fetchall()])

        return jsonify({
            "count": len(users),
            "users": users
        }), 200

    except Exception as e:
        logger.exception("Error listing users")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# BROADCAST
# =============================================================================

@admin_bp.route('/broadcast', methods=['POST'])
def broadcast_message():
    """Рассылка сообщения всем выбранным группам и чатам"""
    try:
        data = request.get_json()
        message = data.get('message')
        targets = data.get('targets', [])  # Expecting a list: ['drivers', 'group_taxi', ...]

        if not message:
            return jsonify({"error": "message is required"}), 400
        
        if not targets:
             return jsonify({"error": "No targets selected"}), 400

        db = get_db()
        recipient_ids = set()
        group_ids = []

        # Helper to get IDs
        def get_ids(table):
            with db.get_cursor() as cur:
                cur.execute(f"SELECT telegram_id FROM {table} WHERE is_active = TRUE")
                return [row['telegram_id'] for row in cur.fetchall()]

        # --- Private Chats ---
        if 'drivers' in targets:
            recipient_ids.update(get_ids('drivers'))
        
        if 'cafes' in targets:
            recipient_ids.update(get_ids('cafes'))
            
        if 'pharmacies' in targets:
            recipient_ids.update(get_ids('pharmacies'))
            
        if 'shoppers' in targets:
            recipient_ids.update(get_ids('shoppers'))
            
        # --- Telegram Groups ---
        if 'group_taxi' in targets:
            group_ids.append(config.GROUP_TAXI_ID)
            
        if 'group_cafe' in targets:
            group_ids.append(config.GROUP_CAFE_ID)
            
        if 'group_porter' in targets:
            group_ids.append(config.GROUP_PORTER_ID)
            
        if 'group_ant' in targets:
            # Avoid duplicate if Ant group is same as Porter group
            if config.GROUP_ANT_ID != config.GROUP_PORTER_ID or 'group_porter' not in targets:
                group_ids.append(config.GROUP_ANT_ID)
                
        if 'group_pharmacy' in targets:
            group_ids.append(config.GROUP_PHARMACY_ID)
            
        if 'group_shop' in targets:
            group_ids.append(config.GROUP_SHOP_ID)

        # Send broadcast to private users
        results = send_telegram_broadcast(list(recipient_ids), message)
        
        # Send broadcast to groups
        group_success = 0
        group_failed = 0
        
        for chat_id in group_ids:
            try:
                # Use send_telegram_group from services
                if send_telegram_group(chat_id, message):
                    group_success += 1
                else:
                    group_failed += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to group {chat_id}: {e}")
                group_failed += 1

        successful = sum(1 for v in results.values() if v)
        failed = len(results) - successful

        db.log_transaction(
            "BROADCAST_SENT",
            details=f"Targets: {', '.join(targets)}. Users: {successful}/{len(recipient_ids)}. Groups: {group_success}/{len(group_ids)}"
        )

        return jsonify({
            "success": True,
            "sent": successful + group_success,
            "failed": failed + group_failed,
            "total": len(results) + len(group_ids),
            "groups_sent": group_success
        }), 200

    except Exception as e:
        logger.exception("Error broadcasting message")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# STATISTICS
# =============================================================================

@admin_bp.route('/stats', methods=['GET'])
def get_statistics():
    """Получить статистику"""
    try:
        db = get_db()

        daily_stats = db.get_daily_stats()
        service_stats = db.get_service_stats(days=7)

        return jsonify({
            "today": _clean_row(daily_stats),
            "weekly_by_service": _clean_rows(service_stats),
            "ramadan_mode": config.IS_RAMADAN
        }), 200

    except Exception as e:
        logger.exception("Error getting statistics")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/transactions', methods=['GET'])
def get_transactions():
    """Получить историю транзакций"""
    try:
        db = get_db()
        user_id = request.args.get('user_id')
        limit = request.args.get('limit', 100, type=int)

        transactions = db.get_transactions(user_id=user_id, limit=limit)

        return jsonify({
            "count": len(transactions),
            "transactions": _clean_rows(transactions)
        }), 200

    except Exception as e:
        logger.exception("Error getting transactions")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# SETTINGS
# =============================================================================

@admin_bp.route('/settings', methods=['GET'])
def get_settings():
    """Return current runtime settings for admin panel."""
    try:
        db = get_db()
        runtime = db.get_runtime_settings()

        response = {
            "is_ramadan": config.IS_RAMADAN,
            **runtime,
            # Legacy aliases for gradual UI rollout
            "cafe_commission": runtime["cafe_commission_percent"],
            "taxi_commission": runtime["taxi_commission"],
            "porter_commission": runtime["porter_commission"],
            "shopper_fee": runtime["shopper_service_fee"],
            "pharmacy_delivery_fee": runtime["pharmacy_delivery_fee"],
        }
        return jsonify(response), 200

    except Exception as e:
        logger.exception("Error getting settings")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/settings', methods=['POST'])
def update_settings():
    """Batch update runtime settings."""
    try:
        data = request.get_json(silent=True) or {}
        updates = data.get('updates')

        if not isinstance(updates, dict) or not updates:
            return jsonify({"error": "updates must be a non-empty object"}), 400

        unknown_keys = [key for key in updates.keys() if key not in RUNTIME_SETTING_DEFAULTS]
        if unknown_keys:
            return jsonify({"error": f"Unknown settings keys: {', '.join(unknown_keys)}"}), 400

        db = get_db()
        applied = db.set_runtime_settings(updates, source="admin_panel")

        return jsonify({
            "success": True,
            "settings": applied
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Error updating settings")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# CHATS (WhatsApp Inbox)
# =============================================================================

@admin_bp.route('/chats', methods=['GET'])
def list_chats():
    """Список чатов: по одному номеру, последнее сообщение и время."""
    try:
        db = get_db()
        chats = db.get_chat_list()
        return jsonify(_clean_rows(chats)), 200
    except Exception as e:
        logger.exception("Error listing chats")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/chats/<phone>', methods=['GET'])
def get_chat(phone):
    """История переписки с конкретным номером."""
    try:
        limit = min(int(request.args.get('limit', 150)), 500)
        offset = int(request.args.get('offset', 0))
        db = get_db()
        messages = db.get_chat_messages(phone, limit=limit, offset=offset)
        return jsonify(_clean_rows(messages)), 200
    except Exception as e:
        logger.exception("Error getting chat")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/chats/<phone>/block-status', methods=['GET'])
def chat_block_status(phone):
    """Статус блокировки пользователя."""
    try:
        db = get_db()
        status = db.get_block_status(phone)
        return jsonify(_clean_rows([status])[0]), 200
    except Exception as e:
        logger.exception("Error getting block status")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/chats/<phone>/block', methods=['POST'])
def block_chat_user(phone):
    """Заблокировать пользователя на указанное время (минуты) или навсегда."""
    try:
        from datetime import timedelta
        data = request.get_json() or {}
        duration = data.get('duration')  # минуты, None = навсегда
        until = None
        if duration:
            from datetime import datetime as _dt, timezone as _tz
            until = _dt.now(_tz.utc).replace(tzinfo=None) + timedelta(minutes=int(duration))
        db = get_db()
        db.block_user(phone, until)
        return jsonify({"success": True, "blocked_until": until.isoformat() if until else None}), 200
    except Exception as e:
        logger.exception("Error blocking user")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/chats/<phone>/unblock', methods=['POST'])
def unblock_chat_user(phone):
    """Разблокировать пользователя."""
    try:
        db = get_db()
        db.unblock_user(phone)
        return jsonify({"success": True}), 200
    except Exception as e:
        logger.exception("Error unblocking user")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/chats/<phone>/send', methods=['POST'])
def send_chat_message(phone):
    """Отправить сообщение пользователю из админ-панели."""
    try:
        data = request.get_json(silent=True) or {}
        message = (data.get('message') or '').strip()
        if not message:
            return jsonify({"error": "Message is required"}), 400
        if len(message) > 4000:
            return jsonify({"error": "Message is too long (max 4000 chars)"}), 400

        ok = send_whatsapp_plain(phone, message)
        if not ok:
            return jsonify({"error": "Failed to send WhatsApp message"}), 500

        # Всегда логируем исходящее сообщение — вебхук обрабатывает только входящие.
        db = get_db()
        db.save_message(phone=phone, direction='out', body=message, msg_type='text')

        return jsonify({"success": True}), 200
    except Exception as e:
        logger.exception("Error sending chat message from admin panel")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/settings/ramadan', methods=['POST'])
def toggle_ramadan_mode():
    """Переключить режим Рамазан"""
    try:
        data = request.get_json()
        enabled = data.get('enabled', False)

        mode_str = "включен" if enabled else "выключен"

        db = get_db()
        db.log_transaction(
            "RAMADAN_MODE_CHANGED",
            details=f"Ramadan mode {mode_str}"
        )

        return jsonify({
            "success": True,
            "message": f"Ramadan mode {mode_str}",
            "enabled": enabled
        }), 200

    except Exception as e:
        logger.exception("Error toggling ramadan mode")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/media/proxy')
def proxy_media():
    """Прокси для медиафайлов WhatsApp.
    Cloud API требует Bearer-токен, поэтому браузер не может запросить напрямую.
    Для Green API — прокси на прямой URL (также полезно для CORS).
    ?url=<media_url>
    """
    media_url = request.args.get('url', '').strip()
    if not media_url:
        return '', 404

    try:
        if media_url.startswith('cloud_media:'):
            # Cloud API: получаем download URL по media_id с авторизацией
            media_id = media_url.split(':', 1)[1]
            headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
            meta_resp = req_lib.get(
                f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{media_id}",
                headers=headers, timeout=20
            )
            if meta_resp.status_code != 200:
                return '', 502
            download_url = meta_resp.json().get("url")
            if not download_url:
                return '', 502
            dl_resp = req_lib.get(download_url, headers=headers, timeout=60)
            if dl_resp.status_code != 200:
                return '', 502
            content_type = dl_resp.headers.get('Content-Type', 'audio/ogg; codecs=opus')
            return Response(dl_resp.content, content_type=content_type)
        else:
            # Green API или прямой URL
            dl_resp = req_lib.get(media_url, timeout=60)
            if dl_resp.status_code != 200:
                return '', 502
            content_type = dl_resp.headers.get('Content-Type', 'audio/ogg')
            return Response(dl_resp.content, content_type=content_type)
    except Exception as e:
        logger.warning(f"Media proxy error: {e}")
        return '', 502
