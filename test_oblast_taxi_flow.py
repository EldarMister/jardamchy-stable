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
    return _load_module(SRC_DIR / "config.py", "test_config_oblast")


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


class FakeDB:
    def __init__(self):
        self.created_order: dict | None = None

    def create_order(self, **kwargs):
        self.created_order = kwargs
        return "GOOBL1"


def _build_main_harness():
    config = _load_config()
    sent_messages: list[tuple[str, str]] = []
    confirm_messages: list[tuple[str, str]] = []
    back_prompts: list[tuple[str, str]] = []
    button_prompts: list[tuple[str, str, list[dict]]] = []
    telegram_messages: list[tuple[str, str, list[dict], dict]] = []

    def parse_user_message(message: str):
        text = (message or "").strip()
        base = {
            "from_address": None,
            "to_address": None,
            "cargo_type": None,
            "order_details": None,
        }
        mapping = {
            "Шамалды-Сайдан Ошко 2 адам": {
                "from_address": "Шамалды-Сай",
                "to_address": "Ош",
            },
            "Шамалды-Сайдан Ошко телефон салыш керек": {
                "from_address": "Шамалды-Сай",
                "to_address": "Ош",
                "cargo_type": "телефон",
            },
            "Ошто телефон алып берүү": {
                "to_address": "Ош",
                "cargo_type": "телефон",
            },
        }
        return {**base, **mapping.get(text, {})}

    namespace = {
        "config": config,
        "WHATSAPP_MAIN_MENU_BUTTON_ID": "btn_main_menu",
        "_reset_unknown_fallback": lambda user: None,
        "parse_user_message": parse_user_message,
        "send_whatsapp": lambda phone, text: sent_messages.append((phone, text)),
        "send_whatsapp_buttons": lambda phone, text, buttons, include_cancel=False: button_prompts.append((phone, text, buttons)) or True,
        "_send_back_prompt": lambda phone, text: back_prompts.append((phone, text)),
        "_send_confirm_with_buttons": lambda phone, text: confirm_messages.append((phone, text)),
        "dispatch_telegram_group_notification": lambda chat_id, text, buttons, **kwargs: telegram_messages.append((chat_id, text, buttons, kwargs)),
        "jsonify": lambda payload: payload,
        "_canonicalize_address_value": lambda text: (text or "").strip(),
        "_canonicalize_optional_address": lambda text: (text or "").strip() if text else None,
        "_is_vague_address": lambda text: len((text or "").strip()) < 2,
        "_addresses_equal": lambda a, b: (a or "").strip().lower() == (b or "").strip().lower(),
    }
    assign_names = {
        "ADDRESS_CASE_SUFFIXES",
        "KY_NUM_TOKEN_ALIASES",
        "KY_NUM_UNITS",
        "KY_NUM_TENS",
        "KY_NUM_SCALE",
        "FLOW_STALE_TTL_MINUTES",
        "OBLAST_KIND_PERSON",
        "OBLAST_KIND_CARGO",
        "OBLAST_PERSON_MARKERS",
        "OBLAST_CARGO_MARKERS",
        "OBLAST_CARGO_FILLERS",
    }
    func_names = {
        "_clear_oblast_temp_data",
        "_send_oblast_type_prompt",
        "_send_oblast_route_prompt",
        "_get_oblast_commission",
        "_normalize_loose_text",
        "_strip_address_case_suffix",
        "_normalize_kyrgyz_number_aliases",
        "_parse_kyrgyz_number_sequence",
        "_convert_kyrgyz_numbers_to_digits",
        "_extract_oblast_persons_count",
        "_extract_oblast_route",
        "_detect_oblast_kind",
        "_extract_oblast_cargo_desc",
        "_send_oblast_confirm",
        "_prompt_oblast_for_missing_data",
        "handle_oblast_taxi_request",
        "_start_poputka_client_flow",
        "_dispatch_poputka_to_group",
    }
    _extract_namespace(SRC_DIR / "main.py", assign_names, func_names, namespace)
    return config, namespace, sent_messages, confirm_messages, back_prompts, button_prompts, telegram_messages


def test_start_poputka_flow_shows_type_buttons():
    config, ns, _, _, _, button_prompts, _ = _build_main_harness()
    user = FakeUser(config)

    response, status = ns["_start_poputka_client_flow"](user)

    assert status == 200
    assert response["status"] == "ok"
    assert user.current_state == config.STATE_OBLAST_TYPE
    assert user.get_temp_data("service_type") == config.SERVICE_POPUTKA
    assert button_prompts[-1][1] == config.OBLAST_TAXI_FIRST_MSG
    assert [button["id"] for button in button_prompts[-1][2]][:2] == [
        config.OBLAST_TYPE_PERSON_BUTTON_ID,
        config.OBLAST_TYPE_CARGO_BUTTON_ID,
    ]


def test_oblast_person_button_asks_for_route():
    config, ns, _, _, back_prompts, _, _ = _build_main_harness()
    user = FakeUser(config)
    user.set_state(config.STATE_OBLAST_TYPE)

    response, status = ns["handle_oblast_taxi_request"](user, "", FakeDB(), selected_kind=ns["OBLAST_KIND_PERSON"])

    assert status == 200
    assert response["status"] == "ok"
    assert user.get_temp_data("oblast_kind") == ns["OBLAST_KIND_PERSON"]
    assert back_prompts[-1][1] == config.OBLAST_TAXI_PERSON_ROUTE_PROMPT


def test_oblast_person_text_goes_straight_to_confirm_with_per_person_commission():
    config, ns, _, confirm_messages, _, _, _ = _build_main_harness()
    user = FakeUser(config)
    user.set_state(config.STATE_OBLAST_TYPE)

    response, status = ns["handle_oblast_taxi_request"](user, "Шамалды-Сайдан Ошко 2 адам", FakeDB())

    assert status == 200
    assert response["status"] == "ok"
    assert user.current_state == config.STATE_CONFIRM_ORDER
    assert user.get_temp_data("oblast_kind") == ns["OBLAST_KIND_PERSON"]
    assert user.get_temp_data("oblast_from") == "Шамалды-Сай"
    assert user.get_temp_data("oblast_to") == "Ош"
    assert user.get_temp_data("oblast_persons") == 2
    assert "40 сом" in confirm_messages[-1][1]


def test_oblast_cargo_text_goes_straight_to_confirm_with_flat_commission():
    config, ns, _, confirm_messages, _, _, _ = _build_main_harness()
    user = FakeUser(config)
    user.set_state(config.STATE_OBLAST_TYPE)

    response, status = ns["handle_oblast_taxi_request"](user, "Шамалды-Сайдан Ошко телефон салыш керек", FakeDB())

    assert status == 200
    assert response["status"] == "ok"
    assert user.current_state == config.STATE_CONFIRM_ORDER
    assert user.get_temp_data("oblast_kind") == ns["OBLAST_KIND_CARGO"]
    assert user.get_temp_data("oblast_from") == "Шамалды-Сай"
    assert user.get_temp_data("oblast_to") == "Ош"
    assert user.get_temp_data("oblast_cargo") == "телефон"
    assert "20 сом" in confirm_messages[-1][1]


def test_oblast_partial_cargo_asks_missing_from_address():
    config, ns, sent_messages, _, _, _, _ = _build_main_harness()
    user = FakeUser(config)
    user.set_state(config.STATE_OBLAST_TYPE)

    response, status = ns["handle_oblast_taxi_request"](user, "Ошто телефон алып берүү", FakeDB())

    assert status == 200
    assert response["status"] == "ok"
    assert user.current_state == config.STATE_OBLAST_FROM
    assert user.get_temp_data("oblast_kind") == ns["OBLAST_KIND_CARGO"]
    assert user.get_temp_data("oblast_to") == "Ош"
    assert user.get_temp_data("oblast_cargo") == "телефон"
    assert sent_messages[-1][1] == config.OBLAST_TAXI_FROM_PROMPT


def test_oblast_dispatch_stores_price_total_for_person_order():
    config, ns, sent_messages, _, _, _, telegram_messages = _build_main_harness()
    user = FakeUser(config)
    db = FakeDB()
    user.set_temp_data("oblast_kind", "person")
    user.set_temp_data("oblast_from", "Шамалды-Сай")
    user.set_temp_data("oblast_to", "Ош")
    user.set_temp_data("oblast_persons", 2)

    ns["_dispatch_poputka_to_group"](user, db)

    assert db.created_order is not None
    assert db.created_order["price"] == 40
    assert db.created_order["address"] == "Шамалды-Сай — Ош"
    assert "40 сом" in telegram_messages[-1][1]
    assert sent_messages[-1][1] == config.POPUTKA_CLIENT_SENT


def test_oblast_dispatch_stores_price_total_for_cargo_order():
    config, ns, sent_messages, _, _, _, telegram_messages = _build_main_harness()
    user = FakeUser(config)
    db = FakeDB()
    user.set_temp_data("oblast_kind", "cargo")
    user.set_temp_data("oblast_from", "Ош")
    user.set_temp_data("oblast_to", "Шамалды-Сай")
    user.set_temp_data("oblast_cargo", "телефон")

    ns["_dispatch_poputka_to_group"](user, db)

    assert db.created_order is not None
    assert db.created_order["price"] == 20
    assert db.created_order["address"] == "Ош — Шамалды-Сай"
    assert "20 сом" in telegram_messages[-1][1]
    assert sent_messages[-1][1] == config.POPUTKA_CLIENT_SENT
