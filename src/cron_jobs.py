"""
Планировщик задач (Cron Jobs) для Business Assistant GO
Обновленная версия согласно ТЗ v2.0
"""

import logging
from datetime import datetime

import config
from db import get_db, get_runtime_setting
from services import (
    edit_telegram_message,
    send_telegram_group,
    delete_telegram_message,
    send_whatsapp,
    send_whatsapp_buttons,
    process_telegram_group_outbox,
)

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

            # Claim timer atomically to avoid duplicate processing in parallel cron runs.
            if not db.mark_auction_processed(auction['id']):
                continue
            
            order_id = auction['order_id']
            message_id = int(auction['telegram_message_id'])
            chat_id = auction['chat_id']
            
            # Получаем заказ
            order = db.get_order(order_id)
            if not order or order['status'] != config.ORDER_STATUS_PENDING:
                continue
            
            # Atomically switch to URGENT to avoid double handling.
            if not db.set_order_urgent_if_status(
                order_id,
                [config.ORDER_STATUS_PENDING, config.ORDER_STATUS_AUCTION],
            ):
                logger.info(f"Cafe order {order_id} already handled by another worker")
                continue
            
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
    """Автоотмена заказов аптеки (5 минут) — никто не ответил"""
    try:
        db = get_db()

        expired_auctions = db.get_expired_auctions()

        for auction in expired_auctions:
            if auction['service_type'] != config.SERVICE_PHARMACY:
                continue

            order_id = auction['order_id']
            message_id = auction.get('telegram_message_id')
            chat_id = auction.get('chat_id') or config.GROUP_PHARMACY_ID

            order = db.get_order(order_id)
            if not order or order['status'] != config.ORDER_STATUS_PENDING:
                db.mark_auction_processed(auction['id'])
                continue

            # Отменяем заказ в БД
            db.update_order_status(order_id, config.ORDER_STATUS_CANCELLED)

            # Обновляем сообщение в Telegram группе
            if message_id:
                try:
                    edit_telegram_message(
                        chat_id, int(message_id),
                        config.PHARMACY_NO_RESPONSE_TELEGRAM,
                        buttons=[]
                    )
                except Exception:
                    pass

            # Уведомляем клиента с кнопками Ооба/Жок
            client_phone = order.get('client_phone')
            if client_phone:
                reorder_buttons = [
                    {"id": "pharm_reorder_yes", "text": "✅ Ооба"},
                    {"id": "pharm_reorder_no", "text": "❌ Жок"},
                ]
                if not send_whatsapp_buttons(
                    client_phone,
                    config.PHARMACY_NO_RESPONSE_CLIENT,
                    reorder_buttons,
                    include_cancel=False
                ):
                    send_whatsapp(client_phone, config.PHARMACY_NO_RESPONSE_CLIENT)

                # Сохраняем запрос и ставим состояние для повтора
                client_user = db.get_user(client_phone)
                if client_user:
                    client_user.set_temp_data('pharmacy_reorder_request', order.get('details', ''))
                    client_user.set_state(config.STATE_PHARMACY_REORDER_CHOICE)

            db.mark_auction_processed(auction['id'])

            db.log_transaction(
                "PHARMACY_TIMEOUT_CANCELLED",
                order_id=order_id,
                details="Pharmacy order auto-cancelled after 5 min — no response"
            )

            logger.info(f"Pharmacy order {order_id} auto-cancelled — no response in 5 min")

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
        timeout_minutes = int(get_runtime_setting("pending_order_auto_cancel_timeout", config.PENDING_ORDER_AUTO_CANCEL_TIMEOUT)) // 60
        stale_orders = db.get_stale_pending_orders(timeout_minutes)

        for order in stale_orders:
            order_id = order['order_id']

            # Atomic cancel: if another worker already changed status, skip.
            if not db.cancel_order_if_status(
                order_id,
                [
                    config.ORDER_STATUS_PENDING,
                    config.ORDER_STATUS_AUCTION,
                    config.ORDER_STATUS_URGENT,
                ],
            ):
                continue

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
        timeout_minutes = int(get_runtime_setting("in_delivery_auto_complete_timeout", config.IN_DELIVERY_AUTO_COMPLETE_TIMEOUT)) // 60
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

    sent_from_outbox = process_telegram_group_outbox()
    if sent_from_outbox:
        logger.info("Telegram group outbox processed: sent=%s", sent_from_outbox)

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
