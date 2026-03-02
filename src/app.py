"""
Application Factory для Business Assistant GO
Обновленная версия согласно ТЗ v2.0
"""

from flask import Flask, request, jsonify, copy_current_request_context
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

def _cron_loop():
    """Фоновый цикл для проверки таймаутов каждые 30 секунд"""
    from cron_jobs import run_all_cron_jobs
    while True:
        try:
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
    from main import handle_whatsapp, health_check

    @app.route('/whatsapp_webhook', methods=['POST'])
    def whatsapp_webhook():
        return handle_whatsapp()

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
        @copy_current_request_context
        def _bg():
            try:
                handle_whatsapp()
            except Exception:
                logger.exception("Error in background whatsapp handler")
        threading.Thread(target=_bg, daemon=True).start()
        return jsonify({"status": "ok"}), 200

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
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
    
    # Запуск фонового планировщика
    cron_thread = threading.Thread(target=_cron_loop, daemon=True)
    cron_thread.start()
    logger.info("Background cron scheduler started (every 30s)")
    
    return app


# Создание приложения для Gunicorn
app = create_app()

if __name__ == '__main__':
    logger.info("Starting Business Assistant GO server...")
    logger.info(f"Ramadan mode: {config.IS_RAMADAN}")
    app.run(host='0.0.0.0', port=config.PORT, debug=config.FLASK_DEBUG)

# Production запуск (Railway):
# gunicorn --chdir src -w 2 -b 0.0.0.0:$PORT app:app
