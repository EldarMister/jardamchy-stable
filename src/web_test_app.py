"""
Lightweight web test harness for Jardamchy GO WhatsApp bot logic.

This app intentionally reuses src/main.py handlers and replaces only external
edges: WhatsApp sends, Telegram dispatches, and PostgreSQL access.
"""

from __future__ import annotations

import contextvars
import hashlib
import itertools
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

import config
import main
import services


STATIC_DIR = Path(__file__).resolve().parent / "web_test_static"
DEFAULT_TEST_PHONE = "996700000001"

_active_outbox: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "web_test_outbox",
    default=None,
)
_active_events: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "web_test_events",
    default=None,
)
_web_test_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "web_test_active",
    default=False,
)
_patch_install_lock = Lock()
_patch_installed = False
_original_edges: dict[str, Any] = {}


class WebTestUser:
    def __init__(
        self,
        db: "WebTestDB",
        phone: str,
        name: str = "",
        current_state: str = config.STATE_IDLE,
        temp_data: dict[str, Any] | None = None,
        language: str = "ru",
        updated_at: datetime | None = None,
    ):
        self._db = db
        self.phone = phone
        self.name = name
        self.current_state = current_state
        self.temp_data = temp_data or {}
        self.language = language
        self.updated_at = updated_at or datetime.now(timezone.utc)

    def set_state(self, state: str) -> None:
        self.current_state = state
        self.updated_at = datetime.now(timezone.utc)
        self._db.set_user_state(self.phone, state)

    def set_temp_data(self, key: str, value: Any) -> None:
        if value is None:
            self.temp_data.pop(key, None)
        else:
            self.temp_data[key] = value
        self.updated_at = datetime.now(timezone.utc)
        self._db.set_user_temp_data(self.phone, key, value)

    def get_temp_data(self, key: str, default: Any = None) -> Any:
        return self.temp_data.get(key, default)

    def clear_temp_data(self) -> None:
        self.temp_data = {}
        self.updated_at = datetime.now(timezone.utc)
        self._db.clear_user_temp_data(self.phone)


class WebTestDB:
    def __init__(self):
        self._lock = Lock()
        self._order_seq = itertools.count(1)
        self.users: dict[str, WebTestUser] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = []
        self.transactions: list[dict[str, Any]] = []
        self.web_orders: dict[str, dict[str, Any]] = {
            "W12345": {
                "order_code": "W12345",
                "cafe_id": 1,
                "cafe_name": "Demo Cafe",
                "items_json": [
                    {"name": "Плов", "count": 1},
                    {"name": "Чай", "count": 1},
                ],
                "total_price": 280,
                "status": "PENDING",
            }
        }

    def reset(self, phone: str | None = None) -> None:
        with self._lock:
            if phone:
                self.users.pop(phone, None)
            else:
                self.users.clear()
                self.orders.clear()
                self.messages.clear()
                self.transactions.clear()
                self._order_seq = itertools.count(1)

    def get_user(self, phone: str) -> WebTestUser:
        with self._lock:
            user = self.users.get(phone)
            if user is None:
                user = WebTestUser(self, phone)
                self.users[phone] = user
                self.log_transaction("USER_CREATED", phone, details="web-test user")
            return user

    def set_user_state(self, phone: str, state: str) -> bool:
        user = self.users.get(phone)
        if user:
            user.current_state = state
            user.updated_at = datetime.now(timezone.utc)
        return True

    def set_user_temp_data(self, phone: str, key: str, value: Any) -> bool:
        user = self.users.get(phone)
        if user:
            if value is None:
                user.temp_data.pop(key, None)
            else:
                user.temp_data[key] = value
            user.updated_at = datetime.now(timezone.utc)
        return True

    def clear_user_temp_data(self, phone: str) -> bool:
        user = self.users.get(phone)
        if user:
            user.temp_data = {}
            user.updated_at = datetime.now(timezone.utc)
        return True

    def save_message(self, **kwargs) -> bool:
        self.messages.append({**kwargs, "created_at": datetime.now(timezone.utc).isoformat()})
        return True

    def get_block_status(self, phone: str) -> dict[str, Any]:
        return {"is_blocked": False, "reason": None}

    def update_last_welcome(self, phone: str) -> bool:
        return True

    def can_send_welcome(self, phone: str, cooldown_seconds: int = 600) -> bool:
        return True

    def get_runtime_setting(self, key: str, default: float | None = None) -> float:
        try:
            from db import RUNTIME_SETTING_DEFAULTS

            return RUNTIME_SETTING_DEFAULTS.get(key, default)
        except Exception:
            return default

    def get_runtime_settings(self) -> dict[str, float]:
        return {}

    def create_order(self, **kwargs) -> str:
        order_id = f"WEB{next(self._order_seq):05d}"
        order = {
            "order_id": order_id,
            "status": config.ORDER_STATUS_PENDING,
            "created_at": datetime.now(timezone.utc),
            "price_total": kwargs.get("price", 0) or kwargs.get("price_total", 0) or 0,
            **kwargs,
        }
        self.orders[order_id] = order
        self.log_transaction("ORDER_CREATED", kwargs.get("client_phone"), order_id, details=kwargs)
        return order_id

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        return self.orders.get(order_id)

    def get_pending_order(self, client_phone: str, service_type: str | None = None):
        return self._latest_order(client_phone, service_type, {config.ORDER_STATUS_PENDING, config.ORDER_STATUS_AUCTION})

    def get_latest_active_order(self, client_phone: str, service_type: str | None = None):
        return self._latest_order(
            client_phone,
            service_type,
            {
                config.ORDER_STATUS_PENDING,
                config.ORDER_STATUS_AUCTION,
                config.ORDER_STATUS_ACCEPTED,
                config.ORDER_STATUS_READY,
                config.ORDER_STATUS_IN_DELIVERY,
                config.ORDER_STATUS_URGENT,
            },
        )

    def _latest_order(self, client_phone: str, service_type: str | None, statuses: set[str]):
        matches = [
            order
            for order in self.orders.values()
            if order.get("client_phone") == client_phone
            and order.get("status") in statuses
            and (service_type is None or order.get("service_type") == service_type)
        ]
        return matches[-1] if matches else None

    def update_order_status(self, order_id: str, status: str, **kwargs) -> bool:
        order = self.orders.get(order_id)
        if not order:
            return False
        order["status"] = status
        order.update(kwargs)
        return True

    def get_latest_auction_timer(self, order_id: str, service_type: str | None = None):
        return None

    def mark_auction_processed(self, timer_id) -> bool:
        return True

    def update_driver_balance(self, *args, **kwargs) -> bool:
        return True

    def add_cafe_balance(self, *args, **kwargs):
        return True, 0

    def get_web_order(self, order_code: str) -> dict[str, Any] | None:
        return self.web_orders.get((order_code or "").upper())

    def update_web_order_status(self, order_code: str, status: str, **kwargs) -> bool:
        order = self.web_orders.get((order_code or "").upper())
        if not order:
            return False
        order["status"] = status
        order.update(kwargs)
        return True

    def list_master_categories(self, active_only: bool = True) -> list[dict[str, Any]]:
        return []

    def list_masters(self, active_only: bool = True) -> list[dict[str, Any]]:
        return []

    def list_directory(self, active_only: bool = True) -> list[dict[str, Any]]:
        return []

    def claim_whatsapp_webhooks(self, *args, **kwargs) -> list[dict[str, Any]]:
        return []

    def mark_whatsapp_webhook_processed(self, *args, **kwargs) -> bool:
        return True

    def mark_whatsapp_webhook_retry(self, *args, **kwargs) -> bool:
        return True

    def log_transaction(self, action: str, user_id: str | None = None, order_id: str | None = None, **kwargs) -> None:
        self.transactions.append(
            {
                "action": action,
                "user_id": user_id,
                "order_id": order_id,
                "details": kwargs,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )


web_test_db = WebTestDB()


def _append_outgoing(kind: str, phone: str, message: str, buttons: list[dict[str, Any]] | None = None, **extra) -> bool:
    outbox = _active_outbox.get()
    if outbox is not None:
        outbox.append(
            {
                "kind": kind,
                "phone": phone,
                "text": message or "",
                "buttons": buttons or [],
                **extra,
            }
        )
    web_test_db.save_message(phone=phone, direction="out", body=message or "", msg_type=kind)
    return True


def _mock_send_whatsapp(phone: str, message: str) -> bool:
    return _append_outgoing("text", phone, message)


def _mock_send_whatsapp_buttons(
    phone: str,
    message: str,
    buttons: list[dict[str, Any]],
    include_cancel: bool = True,
) -> bool:
    safe_buttons = list(buttons or [])
    if include_cancel:
        safe_buttons = services._with_cancel_button(safe_buttons)
    return _append_outgoing("buttons", phone, message, safe_buttons)


def _mock_send_whatsapp_image(phone: str, image_url: str, caption: str = "") -> bool:
    return _append_outgoing("image", phone, caption, image_url=image_url)


def _mock_send_order_cancelled_with_main_menu(phone: str) -> bool:
    return _mock_send_whatsapp_buttons(
        phone,
        config.ORDER_CANCELLED,
        [{"id": services.WHATSAPP_MAIN_MENU_BUTTON_ID, "text": services.WHATSAPP_MAIN_MENU_BUTTON_TEXT}],
        include_cancel=False,
    )


def _mock_dispatch_telegram_group_notification(chat_id: str, message: str, buttons=None, **kwargs) -> bool:
    events = _active_events.get()
    if events is not None:
        events.append(
            {
                "type": "telegram_group_mock",
                "chat_id": chat_id,
                "text": message,
                "buttons": buttons or [],
                "meta": kwargs,
            }
        )
    return True


def _mock_send_telegram_private(user_id: str, message: str, buttons=None) -> bool:
    events = _active_events.get()
    if events is not None:
        events.append(
            {
                "type": "telegram_private_mock",
                "user_id": user_id,
                "text": message,
                "buttons": buttons or [],
            }
        )
    return True


def _is_web_test_request() -> bool:
    return bool(_web_test_active.get())


def _patch_external_edges() -> None:
    global _patch_installed
    with _patch_install_lock:
        if _patch_installed:
            return
        _original_edges.update(
            {
                "get_db": main.get_db,
                "get_runtime_setting": main.get_runtime_setting,
                "send_whatsapp": main.send_whatsapp,
                "send_whatsapp_buttons": main.send_whatsapp_buttons,
                "send_whatsapp_image": main.send_whatsapp_image,
                "send_order_cancelled_with_main_menu": main.send_order_cancelled_with_main_menu,
                "send_confirmation_buttons": main.send_confirmation_buttons,
                "dispatch_telegram_group_notification": main.dispatch_telegram_group_notification,
                "send_telegram_private": main.send_telegram_private,
            }
        )

        def get_db_proxy():
            if _is_web_test_request():
                return web_test_db
            return _original_edges["get_db"]()

        def get_runtime_setting_proxy(*args, **kwargs):
            if _is_web_test_request():
                return web_test_db.get_runtime_setting(*args, **kwargs)
            return _original_edges["get_runtime_setting"](*args, **kwargs)

        def send_whatsapp_proxy(*args, **kwargs):
            if _is_web_test_request():
                return _mock_send_whatsapp(*args, **kwargs)
            return _original_edges["send_whatsapp"](*args, **kwargs)

        def send_whatsapp_buttons_proxy(*args, **kwargs):
            if _is_web_test_request():
                return _mock_send_whatsapp_buttons(*args, **kwargs)
            return _original_edges["send_whatsapp_buttons"](*args, **kwargs)

        def send_whatsapp_image_proxy(*args, **kwargs):
            if _is_web_test_request():
                return _mock_send_whatsapp_image(*args, **kwargs)
            return _original_edges["send_whatsapp_image"](*args, **kwargs)

        def send_order_cancelled_with_main_menu_proxy(*args, **kwargs):
            if _is_web_test_request():
                return _mock_send_order_cancelled_with_main_menu(*args, **kwargs)
            return _original_edges["send_order_cancelled_with_main_menu"](*args, **kwargs)

        def send_confirmation_buttons_proxy(phone: str, *args, **kwargs):
            if _is_web_test_request():
                return _mock_send_whatsapp_buttons(
                    phone,
                    "РўР°СЃС‚С‹РєС‚Р°Р№СЃС‹Р·Р±С‹?",
                    [{"id": "confirm_yes", "text": "вњ… РћРѕР±Р°"}, {"id": "confirm_no", "text": "вќЊ Р–РѕРє"}],
                    include_cancel=False,
                )
            return _original_edges["send_confirmation_buttons"](phone, *args, **kwargs)

        def dispatch_telegram_group_notification_proxy(*args, **kwargs):
            if _is_web_test_request():
                return _mock_dispatch_telegram_group_notification(*args, **kwargs)
            return _original_edges["dispatch_telegram_group_notification"](*args, **kwargs)

        def send_telegram_private_proxy(*args, **kwargs):
            if _is_web_test_request():
                return _mock_send_telegram_private(*args, **kwargs)
            return _original_edges["send_telegram_private"](*args, **kwargs)

        main.get_db = get_db_proxy
        main.get_runtime_setting = get_runtime_setting_proxy
        main.send_whatsapp = send_whatsapp_proxy
        main.send_whatsapp_buttons = send_whatsapp_buttons_proxy
        main.send_whatsapp_image = send_whatsapp_image_proxy
        main.send_order_cancelled_with_main_menu = send_order_cancelled_with_main_menu_proxy
        main.send_confirmation_buttons = send_confirmation_buttons_proxy
        main.dispatch_telegram_group_notification = dispatch_telegram_group_notification_proxy
        main.send_telegram_private = send_telegram_private_proxy
        _patch_installed = True
    return
    main.get_db = lambda: web_test_db
    main.get_runtime_setting = web_test_db.get_runtime_setting
    main.send_whatsapp = _mock_send_whatsapp
    main.send_whatsapp_buttons = _mock_send_whatsapp_buttons
    main.send_whatsapp_image = _mock_send_whatsapp_image
    main.send_order_cancelled_with_main_menu = _mock_send_order_cancelled_with_main_menu
    main.send_confirmation_buttons = lambda phone: _mock_send_whatsapp_buttons(
        phone,
        "Тастыктайсызбы?",
        [{"id": "confirm_yes", "text": "✅ Ооба"}, {"id": "confirm_no", "text": "❌ Жок"}],
        include_cancel=False,
    )
    main.dispatch_telegram_group_notification = _mock_dispatch_telegram_group_notification
    main.send_telegram_private = _mock_send_telegram_private


def _phone_for_session(session_id: str | None) -> str:
    if not session_id:
        return DEFAULT_TEST_PHONE
    digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()
    return "9967" + str(int(digest[:8], 16) % 100000000).zfill(8)


def _cloud_text_payload(phone: str, text: str, message_id: str) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": phone,
                                    "id": message_id,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


def _cloud_button_payload(phone: str, button_id: str, title: str, message_id: str) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": phone,
                                    "id": message_id,
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": button_id, "title": title or button_id},
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


def register_web_test_routes(
    app: Flask,
    *,
    page_route: str = "/web-test",
    static_prefix: str = "/web-test/static",
    api_prefix: str = "/web-test/api",
    endpoint_prefix: str = "web_test",
) -> None:
    _patch_external_edges()

    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    def static_files(filename: str):
        return send_from_directory(STATIC_DIR, filename)

    def health():
        return jsonify(
            {
                "status": "ok",
                "mode": "web-test",
                "uses_openai_when_configured": bool(config.OPENAI_API_KEY),
            }
        )

    def reset():
        data = request.get_json(silent=True) or {}
        phone = _phone_for_session(data.get("session_id"))
        web_test_db.reset(phone)
        return jsonify({"status": "ok", "phone": phone})

    def chat():
        data = request.get_json(silent=True) or {}
        text = (data.get("message") or "").strip()
        button_id = (data.get("button_id") or "").strip()
        button_title = (data.get("button_title") or "").strip()
        session_id = (data.get("session_id") or "default").strip()
        phone = _phone_for_session(session_id)

        if not text and not button_id:
            return jsonify({"status": "error", "message": "empty message"}), 400

        outbox: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        outbox_token = _active_outbox.set(outbox)
        events_token = _active_events.set(events)
        active_token = _web_test_active.set(True)
        try:
            message_id = f"web-{datetime.now(timezone.utc).timestamp()}-{len(web_test_db.messages)}"
            if button_id:
                payload = _cloud_button_payload(phone, button_id, button_title or text, message_id)
            else:
                payload = _cloud_text_payload(phone, text, message_id)
            result = main.handle_whatsapp(request_json=payload)
            status_code = result[1] if isinstance(result, tuple) and len(result) > 1 else 200
        finally:
            _web_test_active.reset(active_token)
            _active_outbox.reset(outbox_token)
            _active_events.reset(events_token)

        user = web_test_db.get_user(phone)
        return jsonify(
            {
                "status": "ok",
                "handler_status": status_code,
                "phone": phone,
                "state": user.current_state,
                "temp_data": user.temp_data,
                "messages": outbox,
                "mock_events": events,
            }
        )

    app.add_url_rule(page_route, f"{endpoint_prefix}_index", index, methods=["GET"])
    app.add_url_rule(f"{static_prefix}/<path:filename>", f"{endpoint_prefix}_static", static_files, methods=["GET"])
    app.add_url_rule(f"{api_prefix}/health", f"{endpoint_prefix}_health", health, methods=["GET"])
    app.add_url_rule(f"{api_prefix}/reset", f"{endpoint_prefix}_reset", reset, methods=["POST"])
    app.add_url_rule(f"{api_prefix}/chat", f"{endpoint_prefix}_chat", chat, methods=["POST"])
    if "web_test_favicon" not in app.view_functions:
        app.add_url_rule("/favicon.ico", "web_test_favicon", lambda: ("", 204), methods=["GET"])


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    register_web_test_routes(
        app,
        page_route="/",
        static_prefix="/web-test",
        api_prefix="/api",
        endpoint_prefix="web_test_root",
    )
    register_web_test_routes(app)

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("WEB_TEST_PORT", "5050"))
    debug = os.getenv("WEB_TEST_DEBUG", "false").lower() == "true"
    app.run(host="127.0.0.1", port=port, debug=debug)
