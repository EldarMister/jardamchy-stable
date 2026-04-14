from __future__ import annotations

import ast
import importlib.util
import sys
import types
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"


def _load_module(path: Path, name: str):
    sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _load_config():
    return _load_module(SRC_DIR / "config.py", "test_config_poputka")


def _extract_namespace(path: Path, assign_names: set[str], func_names: set[str], namespace: dict) -> dict:
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    chunks = ["from __future__ import annotations"]

    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in assign_names:
                    chunks.append(ast.get_source_segment(source, node))
                    break
        elif isinstance(node, ast.FunctionDef) and node.name in func_names:
            chunks.append(ast.get_source_segment(source, node))

    exec("\n\n".join(chunks), namespace)
    return namespace


class FakeUser:
    def __init__(self, phone: str = "996555000111"):
        self.phone = phone
        self._temp: dict[str, object] = {}

    def set_temp_data(self, key, value):
        if value is None:
            self._temp.pop(key, None)
        else:
            self._temp[key] = value

    def get_temp_data(self, key, default=None):
        return self._temp.get(key, default)


class FakeOrderDB:
    def __init__(self):
        self.created_order: dict | None = None

    def create_order(self, **kwargs):
        self.created_order = kwargs
        return "GOPOP1"


class FakeCronDB:
    def __init__(self, orders: list[dict]):
        self.orders = orders
        self.cancelled: list[tuple[str, list[str]]] = []
        self.processed_timers: list[int] = []
        self.logs: list[tuple[str, str]] = []

    def get_expired_poputka_orders(self):
        return list(self.orders)

    def cancel_order_if_status(self, order_id, statuses):
        self.cancelled.append((order_id, list(statuses)))
        return True

    def mark_auction_processed(self, timer_id):
        self.processed_timers.append(timer_id)

    def log_transaction(self, action, order_id=None, details=None, **kwargs):
        self.logs.append((action, order_id))


class FakeAcceptDB:
    def __init__(self, order: dict):
        self.order = order
        self.cancelled: list[tuple[str, list[str]]] = []
        self.status_updates: list[tuple[str, str]] = []
        self.processed_timers: list[int] = []

    def get_order(self, order_id):
        if order_id == self.order["order_id"]:
            return self.order
        return None

    def cancel_order_if_status(self, order_id, statuses):
        self.cancelled.append((order_id, list(statuses)))
        return True

    def update_order_status(self, order_id, status, **kwargs):
        self.status_updates.append((order_id, status))

    def get_latest_auction_timer(self, order_id, service_type=None):
        return {"id": 91}

    def mark_auction_processed(self, timer_id):
        self.processed_timers.append(timer_id)


class FakeAcceptSuccessDB:
    def __init__(self, order: dict, balance: int = 500):
        self.order = order
        self.balance = balance
        self.balance_updates: list[tuple[str, int, str]] = []
        self.status_updates: list[tuple[str, str, dict]] = []
        self.processed_timers: list[int] = []
        self.logs: list[tuple[str, str, str]] = []

    def get_order(self, order_id):
        if order_id == self.order["order_id"]:
            return self.order
        return None

    def get_driver_balance(self, user_id):
        return self.balance

    def update_driver_balance(self, user_id, amount, reason=""):
        self.balance_updates.append((user_id, amount, reason))
        self.balance += amount

    def update_order_status(self, order_id, status, **kwargs):
        self.status_updates.append((order_id, status, kwargs))

    def get_driver(self, user_id):
        return {
            "name": "Ali",
            "phone": "996700111222",
            "car_model": "Honda Fit",
            "plate": "01KG123ABC",
        }

    def get_latest_auction_timer(self, order_id, service_type=None):
        return {"id": 91}

    def mark_auction_processed(self, timer_id):
        self.processed_timers.append(timer_id)

    def log_transaction(self, action, user_id, order_id):
        self.logs.append((action, user_id, order_id))


def _build_main_namespace(now_local: datetime):
    config = _load_config()
    sent_messages: list[tuple[str, str]] = []
    telegram_messages: list[tuple[str, str, list[dict], dict]] = []

    namespace = {
        "config": config,
        "datetime": datetime,
        "timedelta": timedelta,
        "_bishkek_now_naive": lambda: now_local,
        "send_whatsapp": lambda phone, text: sent_messages.append((phone, text)),
        "dispatch_telegram_group_notification": lambda chat_id, text, buttons, **kwargs: telegram_messages.append((chat_id, text, buttons, kwargs)),
    }
    _extract_namespace(
        SRC_DIR / "main.py",
        assign_names={"FLOW_STALE_TTL_MINUTES"},
        func_names={"_parse_poputka_client_date", "_parse_poputka_client_deadline", "_dispatch_poputka_to_group"},
        namespace=namespace,
    )
    return config, namespace, sent_messages, telegram_messages


def _build_cron_namespace(db, deleted_messages: list[tuple[str, int]]):
    config = _load_config()

    class Logger:
        @staticmethod
        def exception(*args, **kwargs):
            return None

        @staticmethod
        def info(*args, **kwargs):
            return None

    namespace = {
        "config": config,
        "get_db": lambda: db,
        "delete_telegram_message": lambda chat_id, message_id: deleted_messages.append((chat_id, message_id)),
        "logger": Logger(),
    }
    _extract_namespace(
        SRC_DIR / "cron_jobs.py",
        assign_names=set(),
        func_names={"check_poputka_timeouts"},
        namespace=namespace,
    )
    return namespace


def _build_telegram_namespace(now_local: datetime):
    config = _load_config()
    private_messages: list[tuple[str, str]] = []
    deleted_messages: list[tuple[str, int]] = []
    edited_messages: list[tuple[str, int, str, list]] = []
    callbacks: list[tuple[str | None, str | None]] = []
    client_messages: list[tuple[str, str]] = []

    class Logger:
        @staticmethod
        def exception(*args, **kwargs):
            return None

    namespace = {
        "config": config,
        "_bishkek_now_naive": lambda: now_local,
        "send_telegram_private": lambda user_id, text: private_messages.append((user_id, text)),
        "send_whatsapp": lambda phone, text: client_messages.append((phone, text)),
        "delete_telegram_message": lambda chat_id, message_id: deleted_messages.append((chat_id, message_id)),
        "edit_telegram_message": lambda chat_id, message_id, text, buttons=None: edited_messages.append((chat_id, message_id, text, buttons or [])),
        "_answer_callback": lambda callback_id, text=None: callbacks.append((callback_id, text)),
        "_format_phone_for_whatsapp": lambda phone: phone,
        "jsonify": lambda payload: payload,
        "logger": Logger(),
    }
    _extract_namespace(
        SRC_DIR / "telegram_handler.py",
        assign_names=set(),
        func_names={"handle_poputka_accept"},
        namespace=namespace,
    )
    return namespace, private_messages, deleted_messages, edited_messages, callbacks, client_messages


def test_poputka_deadline_parser_builds_exact_local_datetime_for_today():
    _, ns, _, _ = _build_main_namespace(datetime(2026, 4, 13, 10, 0, 0))

    date_text, deadline = ns["_parse_poputka_client_deadline"]("today 15:45")

    assert date_text is not None
    assert date_text.endswith("15:45")
    assert deadline == datetime(2026, 4, 13, 15, 45, 0)


def test_poputka_dispatch_uses_client_deadline_as_group_timeout():
    config, ns, sent_messages, telegram_messages = _build_main_namespace(datetime(2026, 4, 13, 10, 0, 0))
    user = FakeUser()
    db = FakeOrderDB()
    expires_at = datetime(2026, 4, 13, 12, 30, 0)

    user.set_temp_data("poputka_dest", "Osh")
    user.set_temp_data("poputka_date", "today 12:30")
    user.set_temp_data("poputka_seats", 2)
    user.set_temp_data("poputka_expires_at", expires_at.isoformat())

    ns["_dispatch_poputka_to_group"](user, db)

    assert db.created_order is not None
    assert db.created_order["service_type"] == config.SERVICE_POPUTKA
    assert db.created_order["expires_at"] == expires_at
    assert telegram_messages[-1][3]["timeout_seconds"] == 9000
    assert sent_messages[-1][1] == config.POPUTKA_CLIENT_SENT


def test_poputka_cron_cancels_order_and_deletes_group_message():
    db = FakeCronDB(
        orders=[
            {
                "order_id": "GOPOP1",
                "telegram_message_id": "77",
                "chat_id": "group-poputka",
                "timer_id": 5,
            }
        ]
    )
    deleted_messages: list[tuple[str, int]] = []
    ns = _build_cron_namespace(db, deleted_messages)

    assert ns["check_poputka_timeouts"]() is True
    assert db.cancelled == [("GOPOP1", ["PENDING", "AUCTION", "URGENT"])]
    assert db.processed_timers == [5]
    assert deleted_messages == [("group-poputka", 77)]
    assert db.logs == [("POPUTKA_CLIENT_DEADLINE_EXPIRED", "GOPOP1")]


def test_poputka_accept_rejects_expired_order():
    now_local = datetime(2026, 4, 13, 10, 0, 0)
    ns, private_messages, deleted_messages, edited_messages, callbacks, _ = _build_telegram_namespace(now_local)
    db = FakeAcceptDB(
        order={
            "order_id": "GOPOP1",
            "status": "PENDING",
            "expires_at": datetime(2026, 4, 13, 9, 55, 0),
        }
    )

    response, status = ns["handle_poputka_accept"](
        "poputka_accept_GOPOP1",
        "driver-1",
        "Ali",
        "group-chat",
        77,
        db,
        "cb-1",
    )

    assert status == 200
    assert response["status"] == "ok"
    assert db.cancelled == [("GOPOP1", ["PENDING", "AUCTION", "URGENT"])]
    assert db.processed_timers == [91]
    assert deleted_messages == [("group-chat", 77)]
    assert edited_messages == []
    assert private_messages
    assert callbacks == [("cb-1", None)]


def test_poputka_accept_uses_order_price_total_for_commission():
    now_local = datetime(2026, 4, 13, 10, 0, 0)
    ns, private_messages, _, edited_messages, callbacks, client_messages = _build_telegram_namespace(now_local)
    db = FakeAcceptSuccessDB(
        order={
            "order_id": "GOPOP2",
            "status": "PENDING",
            "expires_at": datetime(2026, 4, 13, 10, 30, 0),
            "client_phone": "996555000111",
            "address": "Шамалды-Сай — Ош",
            "details": "Түрү: адам\nКиши: 2",
            "price_total": 40,
        },
        balance=100,
    )

    response, status = ns["handle_poputka_accept"](
        "poputka_accept_GOPOP2",
        "driver-1",
        "Ali",
        "group-chat",
        77,
        db,
        "cb-2",
    )

    assert status == 200
    assert response["status"] == "ok"
    assert db.balance_updates == [("driver-1", -40, "Поputka order GOPOP2 commission")]
    assert db.status_updates == [("GOPOP2", _load_config().ORDER_STATUS_ACCEPTED, {"provider_id": "driver-1", "driver_commission": 40})]
    assert db.processed_timers == [91]
    assert db.logs == [("POPUTKA_ACCEPTED", "driver-1", "GOPOP2")]
    assert callbacks == [("cb-2", None)]
    assert edited_messages
    assert client_messages == [("996555000111", _load_config().POPUTKA_CLIENT_DRIVER_FOUND.format(
        driver_name="Ali",
        driver_phone="996700111222",
        car_info="Honda Fit 01KG123ABC",
    ))]
    assert any("40" in text for _, text in private_messages)
