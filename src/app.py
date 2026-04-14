"""
Application Factory для Business Assistant GO
Обновленная версия согласно ТЗ v2.0
"""

from flask import Flask, request, jsonify
import logging
import os
import threading
import time
from datetime import datetime

import config
from db import get_db
from telegram_handler import handle_telegram_webhook
from admin import admin_bp

# Настройка логирования
log_dir = os.path.dirname(config.LOG_FILE)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# BACKGROUND CRON SCHEDULER
# =============================================================================

def _cron_loop(app_instance):
    """Фоновый цикл для проверки таймаутов каждые 30 секунд"""
    from cron_jobs import run_all_cron_jobs
    from main import process_whatsapp_webhook_queue
    while True:
        try:
            with app_instance.app_context():
                processed_webhooks = process_whatsapp_webhook_queue(limit=20)
                if processed_webhooks:
                    logger.info("Processed %s queued WhatsApp webhooks", processed_webhooks)
                run_all_cron_jobs()
        except Exception as e:
            logger.exception("Cron loop error")
        time.sleep(30)


def create_app():
    """Фабрика приложения Flask"""
    app = Flask(__name__)
    
    # Регистрация blueprints
    app.register_blueprint(admin_bp)
    
    from menu import menu_bp
    app.register_blueprint(menu_bp)
    
    # Регистрация роутов
    from main import (
        handle_whatsapp,
        health_check,
        extract_whatsapp_queue_metadata,
        process_whatsapp_webhook_queue,
    )

    def _start_whatsapp_queue_worker(batch_size: int = 5) -> None:
        def _bg():
            with app.app_context():
                try:
                    processed = process_whatsapp_webhook_queue(limit=batch_size)
                    if processed:
                        logger.info("Processed %s queued WhatsApp webhooks in background", processed)
                except Exception:
                    logger.exception("Error in background WhatsApp queue worker")

        threading.Thread(target=_bg, daemon=True).start()

    def _enqueue_whatsapp_payload(payload: dict) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        try:
            metadata = extract_whatsapp_queue_metadata(payload)
            queue_id = get_db().enqueue_whatsapp_webhook(
                payload_json=payload,
                provider=metadata.get("provider"),
                sender_phone=metadata.get("sender_phone"),
                external_message_id=metadata.get("external_message_id"),
            )
            logger.info(
                "Queued WhatsApp webhook id=%s provider=%s sender=%s message_id=%s",
                queue_id,
                metadata.get("provider"),
                metadata.get("sender_phone"),
                metadata.get("external_message_id"),
            )
            _start_whatsapp_queue_worker()
            return True
        except Exception:
            logger.exception("Failed to enqueue WhatsApp webhook")
            return False

    @app.route('/whatsapp_webhook', methods=['POST'])
    def whatsapp_webhook():
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            if _enqueue_whatsapp_payload(payload):
                return jsonify({"status": "queued"}), 200
            return handle_whatsapp(request_json=payload)
        return handle_whatsapp(form_values=request.values)

    # WhatsApp Cloud API webhook (GET — верификация, POST — сообщения)
    @app.route('/webhook', methods=['GET'])
    def webhook_verify():
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == config.WHATSAPP_VERIFY_TOKEN:
            logger.info("WhatsApp Cloud API webhook verified")
            return challenge, 200
        logger.warning(f"Webhook verify failed: mode={mode} token={token}")
        return 'Forbidden', 403

    @app.route('/webhook', methods=['POST'])
    def webhook_receive():
        payload = request.get_json(silent=True) or {}
        if _enqueue_whatsapp_payload(payload):
            return jsonify({"status": "queued"}), 200
        return handle_whatsapp(request_json=payload)

    @app.route('/telegram_webhook', methods=['POST'])
    def telegram_webhook():
        return handle_telegram_webhook()

    @app.route('/health', methods=['GET'])
    def health():
        return health_check()
    
    # Инициализация БД при старте
    with app.app_context():
        try:
            db = get_db()
            logger.info("Database initialized successfully")
            try:
                from services import process_telegram_group_outbox

                recovered = process_telegram_group_outbox(limit=50)
                if recovered:
                    logger.info("Recovered %s pending Telegram group messages on startup", recovered)
            except Exception:
                logger.exception("Failed to process Telegram group outbox on startup")
            try:
                recovered_webhooks = process_whatsapp_webhook_queue(limit=50)
                if recovered_webhooks:
                    logger.info("Recovered %s pending WhatsApp webhooks on startup", recovered_webhooks)
            except Exception:
                logger.exception("Failed to process WhatsApp webhook queue on startup")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
    
    # Запуск фонового планировщика
    cron_thread = threading.Thread(target=_cron_loop, args=(app,), daemon=True)
    cron_thread.start()
    logger.info("Background cron scheduler started (every 30s)")
    
    return app


# Создание приложения для Gunicorn
app = create_app()

if __name__ == '__main__':
    logger.info("Starting Business Assistant GO server...")
    app.run(host='0.0.0.0', port=config.PORT, debug=config.FLASK_DEBUG)

# Production запуск (Railway):
# gunicorn --chdir src -w 2 -b 0.0.0.0:$PORT app:app
