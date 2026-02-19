"""
Планировщик задач (Cron Jobs) для Business Assistant GO
Обновленная версия согласно ТЗ v2.0
"""

import logging
from datetime import datetime

import config
from db import get_db
from services import edit_telegram_message, send_telegram_group, delete_telegram_message, send_whatsapp

logger = logging.getLogger(__name__)


# =============================================================================
# CAFE AUCTION TIMEOUT
# =============================================================================

def check_cafe_timeouts():
    """Проверка таймаутов кафе (2 минуты)"""
    try:
        db = get_db()
        
        # Получаем истекшие аукционы
        expired_auctions = db.get_expired_auctions()
        
        for auction in expired_auctions:
            if auction['service_type'] != config.SERVICE_CAFE:
                continue
            
            order_id = auction['order_id']
            message_id = int(auction['telegram_message_id'])
            chat_id = auction['chat_id']
            
            # Получаем заказ
            order = db.get_order(order_id)
            if not order or order['status'] != config.ORDER_STATUS_PENDING:
                db.mark_auction_processed(auction['id'])
                continue
            
            # Помечаем заказ как срочный
            db.set_order_urgent(order_id)
            
            # Обновляем сообщение в группе
            updated_msg = config.CAFE_ORDER_URGENT.format(
                order_id=order_id,
                order_details=order.get('details', '')[:100],
                address=order.get('address', ''),
                payment=config.PAYMENT_METHODS.get(order.get('payment_method'), 'Наличные'),
                phone=order.get('client_phone', '')
            )
            
            buttons = [
                {"text": "🚨 ВЗЯТЬ СРОЧНО!", "callback": f"cafe_accept_{order_id}"},
                {"text": "❌ Отказать", "callback": f"cafe_decline_{order_id}"}
            ]
            
            edit_telegram_message(chat_id, message_id, updated_msg, buttons)
            
            # Помечаем аукцион как обработанный
            db.mark_auction_processed(auction['id'])
            
            db.log_transaction(
                "CAFE_AUCTION_TIMEOUT",
                order_id=order_id,
                details="Cafe auction expired, marked as urgent"
            )
            
            logger.info(f"Cafe auction timeout for order {order_id}")
        
        return True
        
    except Exception as e:
        logger.exception("Error checking cafe timeouts")
        return False


# =============================================================================
# TAXI TIMEOUT
# =============================================================================

def check_taxi_timeouts():
    """Незабранные заказы такси — удалить через 5 минут"""
    try:
        db = get_db()
        
        expired_auctions = db.get_expired_auctions()
        
        for auction in expired_auctions:
            if auction['service_type'] != config.SERVICE_TAXI:
                continue
            
            order_id = auction['order_id']
            message_id = int(auction['telegram_message_id'])
            chat_id = auction['chat_id']
            
            # Удаляем сообщение из группы
            delete_telegram_message(chat_id, message_id)
            
            db.mark_auction_processed(auction['id'])
            
            db.log_transaction(
                "TAXI_ORDER_EXPIRED",
                order_id=order_id,
                details="Taxi order not taken in 5 min, message deleted"
            )
            
            logger.info(f"Taxi order {order_id} expired — message deleted from group")
        
        return True
        
    except Exception as e:
        logger.exception("Error checking taxi timeouts")
        return False


# =============================================================================
# ACCEPTED ORDER CLEANUP (30 min)
# =============================================================================

def check_accepted_order_timeouts():
    """Забранные заказы — удалить сообщение 'ЗАКАЗ ЗАБРАН' через 30 минут"""
    try:
        db = get_db()
        
        expired_auctions = db.get_expired_auctions()
        
        for auction in expired_auctions:
            if auction['service_type'] != 'taxi_accepted':
                continue
            
            message_id = int(auction['telegram_message_id'])
            chat_id = auction['chat_id']
            order_id = auction.get('order_id', '')
            
            # Удаляем сообщение из группы
            delete_telegram_message(chat_id, message_id)
            
            db.mark_auction_processed(auction['id'])
            
            db.log_transaction(
                "TAXI_ACCEPTED_EXPIRED",
                order_id=order_id,
                details="Accepted order message deleted after 30 min"
            )
            
            logger.info(f"Accepted order {order_id} — message deleted after 30 min")
        
        return True
        
    except Exception as e:
        logger.exception("Error checking accepted order timeouts")
        return False


# =============================================================================
# PHARMACY TIMEOUT
# =============================================================================

def check_pharmacy_timeouts():
    """Проверка таймаутов аптек (3 минуты)"""
    try:
        db = get_db()
        
        expired_auctions = db.get_expired_auctions()
        
        for auction in expired_auctions:
            if auction['service_type'] != config.SERVICE_PHARMACY:
                continue
            
            order_id = auction['order_id']
            
            order = db.get_order(order_id)
            if not order or order['status'] != config.ORDER_STATUS_PENDING:
                db.mark_auction_processed(auction['id'])
                continue
            
            # Отправляем напоминание
            reminder_msg = f"""💊 *ЗАКАЗ #{order_id} - НАПОМИНАНИЕ*

⏱ Прошло больше 3 минут!
Клиент всё ещё ждет предложения.

📞 *Клиент:* {order.get('client_phone', '')}
📋 *Запрос:* {order.get('details', '')[:100]}

💊 *КТО ПРЕДЛОЖИТ ЦЕНУ?*"""
            
            buttons = [{
                "text": "💊 Указать цену",
                "callback": f"pharm_bid_{order_id}"
            }]
            
            send_telegram_group(config.GROUP_PHARMACY_ID, reminder_msg, buttons)
            
            db.mark_auction_processed(auction['id'])
            
            db.log_transaction(
                "PHARMACY_TIMEOUT_REMINDER",
                order_id=order_id,
                details="Pharmacy timeout reminder sent"
            )
            
            logger.info(f"Pharmacy timeout reminder for order {order_id}")
        
        return True
        
    except Exception as e:
        logger.exception("Error checking pharmacy timeouts")
        return False


# =============================================================================
# AUTO-CANCEL PENDING ORDERS (20 minutes)
# =============================================================================

def check_pending_order_timeouts():
    """Автоотмена заказов PENDING/AUCTION/URGENT старше 20 минут"""
    try:
        db = get_db()
        timeout_minutes = config.PENDING_ORDER_AUTO_CANCEL_TIMEOUT // 60
        stale_orders = db.get_stale_pending_orders(timeout_minutes)

        for order in stale_orders:
            order_id = order['order_id']

            # Перепроверяем статус (защита от гонки)
            current = db.get_order(order_id)
            if not current or current['status'] not in (
                config.ORDER_STATUS_PENDING,
                config.ORDER_STATUS_AUCTION,
                config.ORDER_STATUS_URGENT,
            ):
                continue

            # Отменяем заказ
            db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED)

            # Удаляем сообщение из Telegram-группы если есть
            msg_id = order.get('telegram_message_id')
            chat_id = order.get('chat_id')
            if msg_id and chat_id:
                try:
                    delete_telegram_message(chat_id, int(msg_id))
                except Exception:
                    pass

            # Уведомляем клиента в WhatsApp
            client_phone = order.get('client_phone')
            if client_phone:
                send_whatsapp(
                    client_phone,
                    f"❌ Ваш заказ #{order_id} был автоматически отменён — "
                    f"никто не принял его в течение {timeout_minutes} минут."
                )

            db.log_transaction(
                "AUTO_CANCEL_TIMEOUT",
                order_id=order_id,
                details=f"Order auto-cancelled after {timeout_minutes} min with no executor"
            )
            logger.info(f"Order {order_id} auto-cancelled after {timeout_minutes} min")

        return True

    except Exception as e:
        logger.exception("Error checking pending order timeouts")
        return False


# =============================================================================
# AUTO-COMPLETE IN_DELIVERY ORDERS (30 minutes)
# =============================================================================

def check_in_delivery_auto_complete():
    """Автозавершение заказов IN_DELIVERY старше 30 минут после назначения водителя."""
    try:
        db = get_db()
        timeout_minutes = config.IN_DELIVERY_AUTO_COMPLETE_TIMEOUT // 60
        stale_orders = db.get_stale_in_delivery_orders(timeout_minutes)

        for order in stale_orders:
            order_id = order['order_id']

            current = db.get_order(order_id)
            if not current or current.get('status') != config.ORDER_STATUS_IN_DELIVERY:
                continue
            if not current.get('driver_id'):
                continue

            db.update_order_status(
                order_id,
                config.ORDER_STATUS_COMPLETED,
                completed_at=datetime.now()
            )

            db.log_transaction(
                "AUTO_COMPLETE_IN_DELIVERY_TIMEOUT",
                order_id=order_id,
                details=f"Order auto-completed after {timeout_minutes} min in IN_DELIVERY"
            )
            logger.info(f"Order {order_id} auto-completed after {timeout_minutes} min in IN_DELIVERY")

        return True

    except Exception:
        logger.exception("Error checking in-delivery auto-complete timeouts")
        return False


# =============================================================================
# MAIN CRON RUNNER
# =============================================================================

def run_all_cron_jobs():
    """Запуск всех cron-задач"""
    logger.info("Running cron jobs...")
    
    check_cafe_timeouts()
    check_taxi_timeouts()
    check_pharmacy_timeouts()
    check_accepted_order_timeouts()
    check_pending_order_timeouts()
    check_in_delivery_auto_complete()

    logger.info("Cron jobs completed")


# =============================================================================
# CLI INTERFACE
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "cafe":
            check_cafe_timeouts()
        elif command == "pharmacy":
            check_pharmacy_timeouts()
        elif command == "taxi":
            check_taxi_timeouts()
        elif command == "pending":
            check_pending_order_timeouts()
        elif command == "in_delivery":
            check_in_delivery_auto_complete()
        elif command == "all":
            run_all_cron_jobs()
        else:
            print("Unknown command. Use: cafe, pharmacy, taxi, pending, in_delivery, or all")
    else:
        run_all_cron_jobs()
