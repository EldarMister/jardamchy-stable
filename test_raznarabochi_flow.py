from __future__ import annotations

import ast
import importlib.util
import sys
import types
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
    return _load_module(SRC_DIR / "config.py", "test_config")


def _extract_namespace(path: Path, assign_names: set[str], func_names: set[str], namespace: dict) -> dict:
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    chunks = ["from __future__ import annotations", "import re"]

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
    def __init__(self, config_module):
        self.phone = "996555000111"
        self.current_state = config_module.STATE_IDLE
        self._temp: dict[str, object] = {}

    def clear_temp_data(self):
        self._temp.clear()

    def set_temp_data(self, key, value):
        if value is None:
            self._temp.pop(key, None)
        else:
            self._temp[key] = value

    def get_temp_data(self, key, default=None):
        return self._temp.get(key, default)

    def set_state(self, state):
        self.current_state = state


class FakeRaznaDB:
    def __init__(self):
        self.created_order: dict | None = None

    def create_order(self, **kwargs):
        self.created_order = kwargs
        return "GO123"


class FakeTelegramDB:
    def __init__(self, order: dict, balance: int = 500):
        self.order = order
        self.balance = balance
        self.balance_updates: list[tuple[str, int, str]] = []
        self.status_updates: list[tuple[str, str, str]] = []
        self.timer_processed: list[int] = []
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

    def update_order_status(self, order_id, status, provider_id=None):
        self.status_updates.append((order_id, status, provider_id))

    def get_driver(self, user_id):
        return {"name": "Ali", "phone": "996700111222"}

    def get_latest_auction_timer(self, order_id, service_type):
        return {"id": 77}

    def mark_auction_processed(self, timer_id):
        self.timer_processed.append(timer_id)

    def log_transaction(self, action, user_id, order_id):
        self.logs.append((action, user_id, order_id))


def _build_main_harness():
    config = _load_config()
    sent_messages: list[tuple[str, str]] = []
    confirm_messages: list[tuple[str, str]] = []
    telegram_messages: list[tuple[str, str, list[dict], dict]] = []

    def send_whatsapp(phone, text):
        sent_messages.append((phone, text))

    def send_confirm(phone, text):
        confirm_messages.append((phone, text))

    def dispatch_group(chat_id, text, buttons, **kwargs):
        telegram_messages.append((chat_id, text, buttons, kwargs))

    namespace = {
        "config": config,
        "send_whatsapp": send_whatsapp,
        "_send_confirm_with_buttons": send_confirm,
        "dispatch_telegram_group_notification": dispatch_group,
        "_reset_unknown_fallback": lambda user: None,
        "jsonify": lambda payload: payload,
    }
    assign_names = {
        "ADDRESS_CASE_SUFFIXES",
        "KY_NUM_TOKEN_ALIASES",
        "KY_NUM_UNITS",
        "KY_NUM_TENS",
        "KY_NUM_SCALE",
        "FLOW_STALE_TTL_MINUTES",
        "RAZNARABOCHI_PERSON_PATTERN",
        "RAZNARABOCHI_DESC_FILLERS",
        "RAZNARABOCHI_RU_NUMBER_WORDS",
        "RAZNARABOCHI_COUNT_ONLY_TOKENS",
    }
    func_names = {
        "_normalize_loose_text",
        "_strip_address_case_suffix",
        "_normalize_kyrgyz_number_aliases",
        "_parse_kyrgyz_number_sequence",
        "_convert_kyrgyz_numbers_to_digits",
        "_extract_price",
        "_normalize_raznarabochi_text",
        "_is_valid_raznarabochi_workers_count",
        "_extract_raznarabochi_count_match",
        "_cleanup_raznarabochi_desc",
        "_is_simple_raznarabochi_count_answer",
        "_extract_raznarabochi_request",
        "_prime_raznarabochi_flow",
        "_get_raznarabochi_workers_count",
        "_build_raznarabochi_confirm_msg",
        "_send_raznarabochi_next_prompt",
        "_handle_raznarabochi_input",
        "_dispatch_raznarabochi_to_group",
    }
    _extract_namespace(SRC_DIR / "main.py", assign_names, func_names, namespace)
    return config, namespace, sent_messages, confirm_messages, telegram_messages


def _build_telegram_harness():
    config = _load_config()
    private_messages: list[tuple[str, str]] = []
    client_messages: list[tuple[str, str]] = []
    edits: list[tuple[str, int, str, list]] = []
    callbacks: list[tuple[str | None, str | None]] = []

    class Logger:
        @staticmethod
        def exception(*args, **kwargs):
            return None

    namespace = {
        "config": config,
        "send_telegram_private": lambda user_id, text: private_messages.append((user_id, text)),
        "send_whatsapp": lambda phone, text: client_messages.append((phone, text)),
        "edit_telegram_message": lambda chat_id, message_id, text, buttons=None: edits.append((chat_id, message_id, text, buttons or [])),
        "_answer_callback": lambda callback_id, text=None: callbacks.append((callback_id, text)),
        "_format_phone_for_whatsapp": lambda phone: phone,
        "jsonify": lambda payload: payload,
        "logger": Logger(),
    }
    _extract_namespace(
        SRC_DIR / "telegram_handler.py",
        assign_names=set(),
        func_names={"handle_raznarabochi_accept"},
        namespace=namespace,
    )
    return config, namespace, private_messages, client_messages, edits, callbacks


def test_raznarabochi_combined_message_goes_straight_to_confirm():
    config, ns, sent_messages, confirm_messages, _ = _build_main_harness()
    user = FakeUser(config)
    db = FakeRaznaDB()
    message = "\u043c\u0430\u0433\u0430 10 \u0431\u0430\u043b\u0430 \u043a\u0435\u0440\u0435\u043a 1 \u0444\u0443\u0440\u0430 \u0443\u043d \u0442\u0443\u0448\u0443\u0440\u0443\u0448 \u043a\u0435\u0440\u0435\u043a"

    ns["_prime_raznarabochi_flow"](user)
    ns["_handle_raznarabochi_input"](user, message, db, allow_bare_count=False)

    assert sent_messages == []
    assert user.current_state == config.STATE_CONFIRM_ORDER
    assert user.get_temp_data("raznarabochi_workers_count") == 10
    assert user.get_temp_data("raznarabochi_desc") == "\u0031 \u0444\u0443\u0440\u0430 \u0443\u043d \u0442\u0443\u0448\u0443\u0440\u0443\u0448 \u043a\u0435\u0440\u0435\u043a"
    assert confirm_messages[-1][1] == config.RAZNARABOCHI_CONFIRM.format(
        desc="\u0031 \u0444\u0443\u0440\u0430 \u0443\u043d \u0442\u0443\u0448\u0443\u0440\u0443\u0448 \u043a\u0435\u0440\u0435\u043a",
        workers_count=10,
    )


def test_raznarabochi_asks_only_for_missing_count_and_then_combines_data():
    config, ns, sent_messages, confirm_messages, _ = _build_main_harness()
    user = FakeUser(config)
    db = FakeRaznaDB()
    job_text = "\u0031 \u0444\u0443\u0440\u0430 \u0443\u043d \u0442\u0443\u0448\u0443\u0440\u0443\u0448 \u043a\u0435\u0440\u0435\u043a"

    ns["_prime_raznarabochi_flow"](user)
    ns["_handle_raznarabochi_input"](user, job_text, db, allow_bare_count=False)

    assert user.current_state == config.STATE_RAZNARABOCHI_COUNT
    assert user.get_temp_data("raznarabochi_desc") == job_text
    assert sent_messages[-1][1] == config.RAZNARABOCHI_COUNT_PROMPT

    ns["_handle_raznarabochi_input"](user, "\u0035 \u0430\u0434\u0430\u043c", db, allow_bare_count=True)

    assert user.current_state == config.STATE_CONFIRM_ORDER
    assert user.get_temp_data("raznarabochi_workers_count") == 5
    assert confirm_messages[-1][1] == config.RAZNARABOCHI_CONFIRM.format(
        desc=job_text,
        workers_count=5,
    )


def test_raznarabochi_dispatch_uses_total_commission_in_order_and_group_message():
    config, ns, sent_messages, _, telegram_messages = _build_main_harness()
    user = FakeUser(config)
    db = FakeRaznaDB()
    job_text = "\u0031 \u0444\u0443\u0440\u0430 \u0443\u043d \u0442\u0443\u0448\u0443\u0440\u0443\u0448 \u043a\u0435\u0440\u0435\u043a"

    user.set_temp_data("raznarabochi_desc", job_text)
    user.set_temp_data("raznarabochi_workers_count", 5)
    ns["_dispatch_raznarabochi_to_group"](user, db)

    assert db.created_order is not None
    assert db.created_order["cargo_type"] == "5"
    assert db.created_order["price"] == 50
    assert telegram_messages[-1][1] == config.RAZNARABOCHI_GROUP_MSG.format(
        order_id="GO123",
        desc=job_text,
        workers_count=5,
        commission=50,
    )
    assert telegram_messages[-1][2] == [{"text": "\U0001f477 \u0417\u0430\u043a\u0430\u0437\u0434\u044b \u0430\u043b\u0443\u0443 (50 \u0441\u043e\u043c)", "callback": "razna_accept_GO123"}]
    assert sent_messages[-1][1] == config.RAZNARABOCHI_SENT


def test_telegram_accept_charges_total_commission_for_all_requested_workers():
    config, ns, private_messages, client_messages, edits, callbacks = _build_telegram_harness()
    order = {
        "order_id": "GO123",
        "status": config.ORDER_STATUS_PENDING,
        "cargo_type": "5",
        "price_total": 50,
        "details": "\u0031 \u0444\u0443\u0440\u0430 \u0443\u043d \u0442\u0443\u0448\u0443\u0440\u0443\u0448 \u043a\u0435\u0440\u0435\u043a",
        "client_phone": "996700123456",
    }
    db = FakeTelegramDB(order=order, balance=120)

    response, status = ns["handle_raznarabochi_accept"](
        "razna_accept_GO123",
        "worker-1",
        "Ali",
        "group-chat",
        77,
        db,
        "cb-1",
    )

    assert status == 200
    assert response["status"] == "ok"
    assert db.balance_updates == [("worker-1", -50, "Raznarabochi order GO123 commission")]
    assert db.status_updates == [("GO123", config.ORDER_STATUS_ACCEPTED, "worker-1")]
    assert db.timer_processed == [77]
    assert db.logs == [("RAZNARABOCHI_ACCEPTED", "worker-1", "GO123")]
    assert "\U0001f465 \u041a\u0435\u0440\u0435\u043a \u0430\u0434\u0430\u043c: 5" in private_messages[-1][1]
    assert "\U0001f4b0 \u0411\u0430\u043b\u0430\u043d\u0441\u044b\u04a3\u044b\u0437\u0434\u0430\u043d 50 \u0441\u043e\u043c \u0430\u043b\u044b\u043d\u0434\u044b" in private_messages[-1][1]
    assert "\U0001f465 \u041a\u0435\u0440\u0435\u043a \u0430\u0434\u0430\u043c: 5" in edits[-1][2]
    assert "\U0001f4b0 \u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f: 50 \u0441\u043e\u043c" in edits[-1][2]
    assert client_messages[-1][1] == config.RAZNARABOCHI_WORKER_FOUND.format(
        worker_name="Ali",
        worker_phone="996700111222",
    )
    assert callbacks == [("cb-1", None)]
