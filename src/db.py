"""
Модуль работы с базой данных (PostgreSQL)
Database Module for Business Assistant GO
Обновленная версия согласно ТЗ v2.0
"""

import json
import uuid
import math
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

import config

RUNTIME_SETTING_DEFAULTS: Dict[str, float] = {
    "cafe_commission_percent": float(config.CAFE_COMMISSION_PERCENT),
    "taxi_commission": float(config.TAXI_COMMISSION),
    "porter_commission": float(config.PORTER_COMMISSION),
    "ant_commission": float(config.ANT_COMMISSION),
    "shopper_commission": float(config.SHOPPER_COMMISSION),
    "taxi_shop_commission": float(config.TAXI_SHOP_COMMISSION),
    "taxi_pharmacy_commission": float(config.TAXI_PHARMACY_COMMISSION),
    "pharmacy_commission_percent": float(config.PHARMACY_COMMISSION_PERCENT),
    "taxi_custom_price_commission": float(config.TAXI_CUSTOM_PRICE_COMMISSION),
    "shop_delivery_fee": float(config.SHOP_DELIVERY_FEE),
    "shopper_service_fee": float(config.SHOPPER_SERVICE_FEE),
    "pharmacy_delivery_fee": float(config.PHARMACY_DELIVERY_FEE),
    "taxi_custom_price_min": float(config.TAXI_CUSTOM_PRICE_MIN),
    "taxi_custom_price_threshold": float(config.TAXI_CUSTOM_PRICE_THRESHOLD),
    "cafe_auction_timeout": float(config.CAFE_AUCTION_TIMEOUT),
    "pharmacy_response_timeout": float(config.PHARMACY_RESPONSE_TIMEOUT),
    "taxi_response_timeout": float(config.TAXI_RESPONSE_TIMEOUT),
    "taxi_accepted_timeout": float(config.TAXI_ACCEPTED_TIMEOUT),
    "pending_order_auto_cancel_timeout": float(config.PENDING_ORDER_AUTO_CANCEL_TIMEOUT),
    "in_delivery_auto_complete_timeout": float(config.IN_DELIVERY_AUTO_COMPLETE_TIMEOUT),
}

RUNTIME_TIMEOUT_KEYS = {
    "cafe_auction_timeout",
    "pharmacy_response_timeout",
    "taxi_response_timeout",
    "taxi_accepted_timeout",
    "pending_order_auto_cancel_timeout",
    "in_delivery_auto_complete_timeout",
}


def _normalize_runtime_value(key: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Invalid value type for {key}: bool is not allowed")

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid value type for {key}: must be numeric")

    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"Invalid value for {key}: NaN/Infinity is not allowed")
    if number < 0:
        raise ValueError(f"Invalid value for {key}: must be >= 0")

    if key in RUNTIME_TIMEOUT_KEYS:
        return float(int(number))
    return float(number)


class Database:
    """Класс для работы с PostgreSQL"""
    
    def __init__(self):
        self.pool = None
        self._connect()
        self._init_tables()
    
    def _connect(self):
        """Подключение к PostgreSQL с пулом соединений"""
        try:
            self.pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=config.DATABASE_URL
            )
        except Exception as e:
            print(f"Error connecting to PostgreSQL: {e}")
            raise
    
    @contextmanager
    def get_cursor(self, commit=False):
        """Контекстный менеджер для работы с курсором"""
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            yield cursor
            if commit:
                conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            self.pool.putconn(conn)
    
    def _init_tables(self):
        """Инициализация таблиц базы данных"""
        with self.get_cursor(commit=True) as cur:
            # Serialize schema init across multiple workers
            cur.execute("SELECT pg_advisory_lock(741852963)")
            try:
                # Таблица пользователей (WhatsApp)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        phone VARCHAR(20) UNIQUE NOT NULL,
                        name VARCHAR(100),
                        current_state VARCHAR(50) DEFAULT 'IDLE',
                        temp_data JSONB DEFAULT '{}',
                        language VARCHAR(10) DEFAULT 'ru',
                        last_welcome_at TIMESTAMP DEFAULT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Migration: add last_welcome_at if missing
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'users' AND column_name = 'last_welcome_at'
                        ) THEN
                            ALTER TABLE users ADD COLUMN last_welcome_at TIMESTAMP DEFAULT NULL;
                        END IF;
                    END $$;
                """)
            
                # Таблица заказов
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id SERIAL PRIMARY KEY,
                        order_id VARCHAR(20) UNIQUE NOT NULL,
                        service_type VARCHAR(20) NOT NULL,
                        status VARCHAR(30) DEFAULT 'PENDING',
                        client_phone VARCHAR(20) NOT NULL,
                        details TEXT,
                        address VARCHAR(255),
                        payment_method VARCHAR(20),
                        price_total DECIMAL(10, 2) DEFAULT 0,
                        commission DECIMAL(10, 2) DEFAULT 0,
                        provider_id VARCHAR(50),
                        driver_id VARCHAR(50),
                        driver_assigned_at TIMESTAMP,
                        driver_commission DECIMAL(10, 2) DEFAULT 0,
                        cargo_type VARCHAR(50),
                        ready_time INTEGER,
                        is_urgent BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        completed_at TIMESTAMP
                    )
                """)
            
                # Таблица водителей (такси, портер)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS drivers (
                        id SERIAL PRIMARY KEY,
                        telegram_id VARCHAR(50) UNIQUE NOT NULL,
                        name VARCHAR(100),
                        phone VARCHAR(20),
                        car_model VARCHAR(100),
                        plate VARCHAR(20),
                        driver_type VARCHAR(20) DEFAULT 'taxi',
                        balance DECIMAL(10, 2) DEFAULT 0,
                        is_active BOOLEAN DEFAULT TRUE,
                        is_blocked BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            
                # Таблица кафе
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cafes (
                        id SERIAL PRIMARY KEY,
                        telegram_id VARCHAR(50) UNIQUE NOT NULL,
                        name VARCHAR(100) NOT NULL,
                        phone VARCHAR(20),
                        address VARCHAR(255),
                        debt DECIMAL(10, 2) DEFAULT 0,
                        commission_percent INTEGER DEFAULT 5,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Категории меню по кафе
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cafe_categories (
                        id SERIAL PRIMARY KEY,
                        cafe_id INTEGER REFERENCES cafes(id) ON DELETE CASCADE,
                        name VARCHAR(100) NOT NULL,
                        sort_order INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (cafe_id, name)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cafe_categories_cafe ON cafe_categories(cafe_id)")
            
                # Позиции меню кафе
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS menu_items (
                        id SERIAL PRIMARY KEY,
                        cafe_id INTEGER REFERENCES cafes(id),
                        name VARCHAR(200) NOT NULL,
                        price DECIMAL(10, 2) NOT NULL,
                        category VARCHAR(100) DEFAULT 'Основное',
                        category_id INTEGER REFERENCES cafe_categories(id),
                        is_available BOOLEAN DEFAULT TRUE,
                        sort_order INTEGER DEFAULT 0,
                        image_url TEXT DEFAULT NULL,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Migration: add image_url if missing
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'menu_items' AND column_name = 'image_url'
                        ) THEN
                            ALTER TABLE menu_items ADD COLUMN image_url TEXT DEFAULT NULL;
                        END IF;
                    END $$;
                """)
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'orders' AND column_name = 'driver_assigned_at'
                        ) THEN
                            ALTER TABLE orders ADD COLUMN driver_assigned_at TIMESTAMP;
                        END IF;
                    END $$;
                """)
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'orders' AND column_name = 'driver_commission'
                        ) THEN
                            ALTER TABLE orders ADD COLUMN driver_commission DECIMAL(10,2) DEFAULT 0;
                        END IF;
                    END $$;
                """)
                # Migration: add description if missing
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'menu_items' AND column_name = 'description'
                        ) THEN
                            ALTER TABLE menu_items ADD COLUMN description TEXT;
                        END IF;
                    END $$;
                """)
                # Migration: add category_id if missing
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'menu_items' AND column_name = 'category_id'
                        ) THEN
                            ALTER TABLE menu_items ADD COLUMN category_id INTEGER REFERENCES cafe_categories(id);
                        END IF;
                    END $$;
                """)

                # Migration: add discount fields to menu_items
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'menu_items' AND column_name = 'discount_type'
                        ) THEN
                            ALTER TABLE menu_items ADD COLUMN discount_type VARCHAR(10) DEFAULT NULL;
                            ALTER TABLE menu_items ADD COLUMN discount_value DECIMAL(10,2) DEFAULT 0;
                            ALTER TABLE menu_items ADD COLUMN discount_active BOOLEAN DEFAULT FALSE;
                        END IF;
                    END $$;
                """)

                # Глобальные настройки кафе (скидки и пр.)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cafe_settings (
                        id SERIAL PRIMARY KEY,
                        cafe_id INTEGER REFERENCES cafes(id) ON DELETE CASCADE UNIQUE,
                        global_discount_percent DECIMAL(5,2) DEFAULT 0,
                        global_discount_active BOOLEAN DEFAULT FALSE
                    )
                """)

                # Веб-заказы (корзины с сайта)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS web_orders (
                        id SERIAL PRIMARY KEY,
                        order_code VARCHAR(10) UNIQUE NOT NULL,
                        cafe_id INTEGER REFERENCES cafes(id),
                        cafe_name VARCHAR(100),
                        items_json JSONB NOT NULL,
                        total_price DECIMAL(10, 2) NOT NULL,
                        status VARCHAR(20) DEFAULT 'PENDING',
                        client_phone VARCHAR(20),
                        address VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            
                # Таблица аптек
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS pharmacies (
                        id SERIAL PRIMARY KEY,
                        telegram_id VARCHAR(50) UNIQUE NOT NULL,
                        name VARCHAR(100) NOT NULL,
                        phone VARCHAR(20),
                        address VARCHAR(255),
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            
                # Таблица закупщиков
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS shoppers (
                        id SERIAL PRIMARY KEY,
                        telegram_id VARCHAR(50) UNIQUE NOT NULL,
                        name VARCHAR(100),
                        phone VARCHAR(20),
                        balance DECIMAL(10, 2) DEFAULT 0,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            
                # Таблица транзакций (логи)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id SERIAL PRIMARY KEY,
                        action VARCHAR(50) NOT NULL,
                        user_id VARCHAR(50),
                        order_id VARCHAR(20),
                        amount DECIMAL(10, 2),
                        details TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Runtime-настройки комиссий/таймаутов с применением без рестарта
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS runtime_settings (
                        key TEXT PRIMARY KEY,
                        value NUMERIC NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS runtime_settings_history (
                        id SERIAL PRIMARY KEY,
                        key TEXT NOT NULL,
                        old_value NUMERIC,
                        new_value NUMERIC NOT NULL,
                        source TEXT DEFAULT 'system',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_runtime_settings_history_key ON runtime_settings_history(key)")
            
                # Таблица для отслеживания таймаутов (аукционы)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS auction_timers (
                        id SERIAL PRIMARY KEY,
                        order_id VARCHAR(20) NOT NULL,
                        service_type VARCHAR(20) NOT NULL,
                        telegram_message_id VARCHAR(50),
                        chat_id VARCHAR(50),
                        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP NOT NULL,
                        is_processed BOOLEAN DEFAULT FALSE
                    )
                """)
            
                # Таблица цен от аптек (временная)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS pharmacy_bids (
                        id SERIAL PRIMARY KEY,
                        order_id VARCHAR(20) NOT NULL,
                        pharmacy_id VARCHAR(50) NOT NULL,
                        price DECIMAL(10, 2) NOT NULL,
                        is_selected BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            
                # Индексы для оптимизации
                cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_client ON orders(client_phone)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_auction_timers ON auction_timers(expires_at, is_processed)")

                # Заполняем дефолты runtime-настроек, если ключей еще нет
                self._seed_runtime_settings(cur)
            
                # Таблица Telegram-сессий (для регистрации водителей)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS telegram_sessions (
                        id SERIAL PRIMARY KEY,
                        telegram_id VARCHAR(50) UNIQUE NOT NULL,
                        state VARCHAR(50) DEFAULT 'IDLE',
                        temp_data JSONB DEFAULT '{}',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_telegram_sessions ON telegram_sessions(telegram_id)")
            finally:
                cur.execute("SELECT pg_advisory_unlock(741852963)")

    # ==========================================================================
    # RUNTIME SETTINGS
    # ==========================================================================

    def _seed_runtime_settings(self, cur) -> None:
        for key, default_value in RUNTIME_SETTING_DEFAULTS.items():
            cur.execute(
                """INSERT INTO runtime_settings (key, value, updated_at)
                   VALUES (%s, %s, CURRENT_TIMESTAMP)
                   ON CONFLICT (key) DO NOTHING""",
                (key, _normalize_runtime_value(key, default_value))
            )

    def get_runtime_settings(self) -> Dict[str, float]:
        settings: Dict[str, float] = {
            key: (int(value) if key in RUNTIME_TIMEOUT_KEYS else float(value))
            for key, value in RUNTIME_SETTING_DEFAULTS.items()
        }
        with self.get_cursor() as cur:
            cur.execute("SELECT key, value FROM runtime_settings")
            for row in cur.fetchall():
                key = row['key']
                if key not in settings:
                    continue
                value = float(row['value'])
                settings[key] = int(value) if key in RUNTIME_TIMEOUT_KEYS else value
        return settings

    def get_runtime_setting(self, key: str, default: float = None) -> float:
        fallback = RUNTIME_SETTING_DEFAULTS.get(key, 0.0) if default is None else default
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT value FROM runtime_settings WHERE key = %s",
                (key,)
            )
            row = cur.fetchone()
            if not row:
                return int(fallback) if key in RUNTIME_TIMEOUT_KEYS else fallback
            value = float(row['value'])
            return int(value) if key in RUNTIME_TIMEOUT_KEYS else value

    def set_runtime_settings(self, updates: Dict[str, float], source: str = "admin_panel") -> Dict[str, float]:
        if not isinstance(updates, dict) or not updates:
            raise ValueError("updates must be a non-empty object")

        normalized_updates: Dict[str, float] = {}
        for key, raw_value in updates.items():
            if key not in RUNTIME_SETTING_DEFAULTS:
                raise ValueError(f"Unknown setting key: {key}")
            normalized_updates[key] = _normalize_runtime_value(key, raw_value)

        changed_entries = []
        applied: Dict[str, float] = {}

        with self.get_cursor(commit=True) as cur:
            cur.execute(
                "SELECT key, value FROM runtime_settings WHERE key = ANY(%s)",
                (list(normalized_updates.keys()),)
            )
            existing = {row['key']: float(row['value']) for row in cur.fetchall()}

            for key, new_value in normalized_updates.items():
                old_value = existing.get(key, RUNTIME_SETTING_DEFAULTS[key])

                cur.execute(
                    """INSERT INTO runtime_settings (key, value, updated_at)
                       VALUES (%s, %s, CURRENT_TIMESTAMP)
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP""",
                    (key, new_value)
                )

                applied[key] = int(new_value) if key in RUNTIME_TIMEOUT_KEYS else new_value

                if float(old_value) != float(new_value):
                    cur.execute(
                        """INSERT INTO runtime_settings_history (key, old_value, new_value, source, updated_at)
                           VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                        (key, old_value, new_value, source)
                    )
                    changed_entries.append({
                        "key": key,
                        "old_value": int(old_value) if key in RUNTIME_TIMEOUT_KEYS else float(old_value),
                        "new_value": int(new_value) if key in RUNTIME_TIMEOUT_KEYS else float(new_value),
                    })

        if changed_entries:
            self.log_transaction(
                action="SETTINGS_UPDATED",
                details=json.dumps({
                    "source": source,
                    "updates": changed_entries
                }, ensure_ascii=False)
            )

        return applied
    
    # ==========================================================================
    # USERS METHODS
    # ==========================================================================
    
    def get_user(self, phone: str) -> Optional['User']:
        """Получить пользователя по номеру телефона"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE phone = %s",
                (phone,)
            )
            row = cur.fetchone()
            
            if row:
                return User(
                    phone=row['phone'],
                    name=row['name'] or '',
                    current_state=row['current_state'],
                    temp_data=dict(row['temp_data']) if row['temp_data'] else {},
                    language=row['language']
                )
            
            # Создаем нового пользователя
            return self.create_user(phone)
    
    def create_user(self, phone: str, name: str = "") -> 'User':
        """Создать нового пользователя"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO users (phone, name, current_state, temp_data)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (phone) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                   RETURNING *""",
                (phone, name, config.STATE_IDLE, '{}')
            )
            row = cur.fetchone()
            
            self.log_transaction("USER_CREATED", phone, details=f"New user: {phone}")
            
            return User(
                phone=row['phone'],
                name=row['name'] or '',
                current_state=row['current_state'],
                temp_data={},
                language=row['language']
            )
    
    def update_user(self, user: 'User') -> bool:
        """Обновить данные пользователя"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE users 
                   SET current_state = %s, temp_data = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE phone = %s""",
                (user.current_state, json.dumps(user.temp_data), user.phone)
            )
            return cur.rowcount > 0
    
    def set_user_state(self, phone: str, state: str) -> bool:
        """Установить состояние пользователя"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE users SET current_state = %s, updated_at = CURRENT_TIMESTAMP WHERE phone = %s",
                (state, phone)
            )
            return cur.rowcount > 0
    
    def set_user_temp_data(self, phone: str, key: str, value: Any) -> bool:
        """Установить временные данные пользователя"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE users 
                   SET temp_data = temp_data || %s::jsonb, updated_at = CURRENT_TIMESTAMP
                   WHERE phone = %s""",
                (json.dumps({key: value}), phone)
            )
            return cur.rowcount > 0
    
    def clear_user_temp_data(self, phone: str) -> bool:
        """Очистить временные данные пользователя"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE users SET temp_data = '{}', updated_at = CURRENT_TIMESTAMP WHERE phone = %s",
                (phone,)
            )
            return cur.rowcount > 0
    
    def can_send_welcome(self, phone: str, cooldown_seconds: int = 600) -> bool:
        """Проверить, можно ли отправить приветствие (rate limiting)"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT last_welcome_at 
                   FROM users 
                   WHERE phone = %s""",
                (phone,)
            )
            row = cur.fetchone()
            if not row or not row['last_welcome_at']:
                return True
            
            from datetime import datetime, timezone
            last_welcome = row['last_welcome_at']
            if isinstance(last_welcome, str):
                from datetime import datetime
                last_welcome = datetime.fromisoformat(last_welcome.replace('Z', '+00:00'))
            
            # Делаем last_welcome aware если он naive
            if last_welcome.tzinfo is None:
                from datetime import timezone
                last_welcome = last_welcome.replace(tzinfo=timezone.utc)
            
            now = datetime.now(timezone.utc)
            delta = (now - last_welcome).total_seconds()
            return delta >= cooldown_seconds
    
    def update_last_welcome(self, phone: str) -> bool:
        """Обновить время последнего приветствия"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE users 
                   SET last_welcome_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP 
                   WHERE phone = %s""",
                (phone,)
            )
            return cur.rowcount > 0
    
    # ==========================================================================
    # ORDERS METHODS
    # ==========================================================================
    
    def create_order(self, client_phone: str, service_type: str, details: str,
                     address: str = None, payment_method: str = None,
                     cargo_type: str = None, price: float = 0) -> str:
        """Создать новый заказ"""
        order_id = f"GO{datetime.now().strftime('%y%m%d%H%M%S')}"
        
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO orders 
                   (order_id, service_type, client_phone, details, address, 
                    payment_method, cargo_type, price_total, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (order_id, service_type, client_phone, details, address,
                 payment_method, cargo_type, price, config.ORDER_STATUS_PENDING)
            )
            
            self.log_transaction(
                action="ORDER_CREATED",
                user_id=client_phone,
                order_id=order_id,
                details=f"Service: {service_type}"
            )
            
            return order_id
    
    def get_order(self, order_id: str) -> Optional[Dict]:
        """Получить заказ по ID"""
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    
    def get_pending_order(self, client_phone: str, service_type: str = None) -> Optional[Dict]:
        """Получить ожидающий заказ клиента"""
        with self.get_cursor() as cur:
            if service_type:
                cur.execute(
                    """SELECT * FROM orders 
                       WHERE client_phone = %s AND service_type = %s 
                       AND status IN ('PENDING', 'AUCTION')
                       ORDER BY created_at DESC LIMIT 1""",
                    (client_phone, service_type)
                )
            else:
                cur.execute(
                    """SELECT * FROM orders 
                       WHERE client_phone = %s 
                       AND status IN ('PENDING', 'AUCTION')
                       ORDER BY created_at DESC LIMIT 1""",
                    (client_phone,)
                )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_latest_active_order(self, client_phone: str, service_type: str = None) -> Optional[Dict]:
        """Последний активный заказ клиента (не завершён и не отменён)"""
        active_statuses = [
            config.ORDER_STATUS_PENDING,
            config.ORDER_STATUS_AUCTION,
            config.ORDER_STATUS_ACCEPTED,
            config.ORDER_STATUS_IN_DELIVERY,
            config.ORDER_STATUS_READY,
            config.ORDER_STATUS_URGENT,
        ]
        with self.get_cursor() as cur:
            if service_type:
                cur.execute(
                    """SELECT * FROM orders
                       WHERE client_phone = %s AND service_type = %s AND status = ANY(%s)
                       ORDER BY created_at DESC LIMIT 1""",
                    (client_phone, service_type, active_statuses)
                )
            else:
                cur.execute(
                    """SELECT * FROM orders
                       WHERE client_phone = %s AND status = ANY(%s)
                       ORDER BY created_at DESC LIMIT 1""",
                    (client_phone, active_statuses)
                )
            row = cur.fetchone()
            return dict(row) if row else None
    
    def get_stale_pending_orders(self, minutes: int = 20) -> List[Dict]:
        """Заказы застрявшие в PENDING/AUCTION/URGENT дольше N минут"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT o.*, at.telegram_message_id, at.chat_id
                   FROM orders o
                   LEFT JOIN LATERAL (
                       SELECT telegram_message_id, chat_id
                       FROM auction_timers
                       WHERE order_id = o.order_id
                       ORDER BY created_at ASC
                       LIMIT 1
                   ) at ON TRUE
                   WHERE o.status IN ('PENDING', 'AUCTION', 'URGENT')
                     AND o.created_at <= CURRENT_TIMESTAMP - INTERVAL '%s minutes'
                   ORDER BY o.created_at ASC""",
                (minutes,)
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows] if rows else []

    def get_stale_in_delivery_orders(self, minutes: int = 30) -> List[Dict]:
        """Заказы в IN_DELIVERY дольше N минут с момента назначения водителя."""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT *
                   FROM orders
                   WHERE status = %s
                     AND driver_id IS NOT NULL
                     AND COALESCE(driver_assigned_at, updated_at) <= CURRENT_TIMESTAMP - INTERVAL '%s minutes'
                   ORDER BY COALESCE(driver_assigned_at, updated_at) ASC""",
                (config.ORDER_STATUS_IN_DELIVERY, minutes)
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows] if rows else []

    def update_order_status(self, order_id: str, status: str,
                           provider_id: str = None, driver_id: str = None,
                           price: float = None, ready_time: int = None,
                           driver_assigned_at: datetime = None,
                           driver_commission: float = None,
                           completed_at: datetime = None,
                           address: str = None) -> bool:
        """Обновить статус заказа"""
        with self.get_cursor(commit=True) as cur:
            updates = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
            params = [status]
            
            if provider_id:
                updates.append("provider_id = %s")
                params.append(provider_id)
            if driver_id:
                updates.append("driver_id = %s")
                params.append(driver_id)
            if price is not None:
                updates.append("price_total = %s")
                params.append(price)
            if ready_time is not None:
                updates.append("ready_time = %s")
                params.append(ready_time)
            if driver_assigned_at is not None:
                updates.append("driver_assigned_at = %s")
                params.append(driver_assigned_at)
            if driver_commission is not None:
                updates.append("driver_commission = %s")
                params.append(driver_commission)
            if completed_at is not None:
                updates.append("completed_at = %s")
                params.append(completed_at)
            if address is not None:
                updates.append("address = %s")
                params.append(address)
            
            params.append(order_id)
            
            cur.execute(
                f"UPDATE orders SET {', '.join(updates)} WHERE order_id = %s",
                params
            )
            
            if cur.rowcount > 0:
                self.log_transaction(
                    action=f"ORDER_{status}",
                    order_id=order_id,
                    details=f"Status changed to {status}"
                )
            
            return cur.rowcount > 0

    def assign_order_to_driver(
        self,
        order_id: str,
        status: str,
        driver_id: str,
        allowed_statuses: Optional[List[str]] = None,
        driver_assigned_at: datetime = None,
        driver_commission: float = None
    ) -> Optional[Dict]:
        """Атомарно назначить водителя, если заказ ещё свободен."""
        if not allowed_statuses:
            allowed_statuses = [
                config.ORDER_STATUS_PENDING,
                config.ORDER_STATUS_AUCTION
            ]

        with self.get_cursor(commit=True) as cur:
            updates = ["status = %s", "driver_id = %s", "updated_at = CURRENT_TIMESTAMP"]
            params = [status, driver_id]

            if driver_assigned_at is not None:
                updates.append("driver_assigned_at = %s")
                params.append(driver_assigned_at)
            if driver_commission is not None:
                updates.append("driver_commission = %s")
                params.append(driver_commission)

            params.append(order_id)
            params.append(allowed_statuses)

            cur.execute(
                f"""UPDATE orders SET {', '.join(updates)}
                    WHERE order_id = %s
                      AND driver_id IS NULL
                      AND status = ANY(%s)
                    RETURNING *""",
                params
            )
            row = cur.fetchone()
            if row:
                self.log_transaction(
                    action=f"ORDER_{status}",
                    order_id=order_id,
                    details=f"Driver assigned: {driver_id}"
                )
                return dict(row)
            return None
    
    def set_order_urgent(self, order_id: str) -> bool:
        """Пометить заказ как срочный (таймаут)"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE orders SET is_urgent = TRUE, status = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE order_id = %s""",
                (config.ORDER_STATUS_URGENT, order_id)
            )
            return cur.rowcount > 0
    
    def is_order_taken(self, order_id: str) -> bool:
        """Проверить, занят ли заказ"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT status FROM orders 
                   WHERE order_id = %s AND status IN ('ACCEPTED', 'IN_DELIVERY', 'READY')""",
                (order_id,)
            )
            return cur.fetchone() is not None
    
    def complete_order(self, order_id: str) -> bool:
        """Завершить заказ"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE orders SET status = %s, completed_at = CURRENT_TIMESTAMP
                   WHERE order_id = %s""",
                (config.ORDER_STATUS_COMPLETED, order_id)
            )
            return cur.rowcount > 0
    
    # ==========================================================================
    # DRIVERS METHODS
    # ==========================================================================
    
    def get_driver(self, telegram_id: str) -> Optional[Dict]:
        """Получить информацию о водителе"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT * FROM drivers WHERE telegram_id = %s AND is_active = TRUE",
                (telegram_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    
    def get_driver_by_phone(self, phone: str) -> Optional[Dict]:
        """Получить водителя по телефону"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT * FROM drivers WHERE phone = %s AND is_active = TRUE",
                (phone,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    
    def add_driver(self, telegram_id: str, name: str, phone: str = None,
                   car_model: str = None, plate: str = None, 
                   driver_type: str = 'taxi') -> bool:
        """Добавить нового водителя"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO drivers (telegram_id, name, phone, car_model, plate, driver_type)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (telegram_id) DO UPDATE 
                   SET name = EXCLUDED.name, phone = EXCLUDED.phone,
                       car_model = EXCLUDED.car_model, plate = EXCLUDED.plate,
                       driver_type = EXCLUDED.driver_type,
                       is_active = TRUE, updated_at = CURRENT_TIMESTAMP""",
                (telegram_id, name, phone, car_model, plate, driver_type)
            )
            self.log_transaction("DRIVER_ADDED", telegram_id, details=f"Driver: {name}")
            return True
    
    def update_driver_info(self, telegram_id: str, **fields) -> bool:
        """Обновить информацию о водителе (name, phone, car_model, plate)"""
        allowed = {'name', 'phone', 'car_model', 'plate'}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        
        if not updates:
            return False
        
        set_parts = [f"{k} = %s" for k in updates]
        set_parts.append("updated_at = CURRENT_TIMESTAMP")
        values = list(updates.values()) + [telegram_id]
        
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE drivers SET {', '.join(set_parts)} WHERE telegram_id = %s",
                values
            )
            if cur.rowcount > 0:
                self.log_transaction("DRIVER_UPDATED", telegram_id, 
                                     details=f"Updated: {', '.join(updates.keys())}")
                return True
            return False
    
    def remove_driver(self, telegram_id: str) -> bool:
        """Удалить (деактивировать) водителя"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE drivers SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                   WHERE telegram_id = %s""",
                (telegram_id,)
            )
            self.log_transaction("DRIVER_REMOVED", telegram_id)
            return cur.rowcount > 0
    
    def update_driver_balance(self, telegram_id: str, amount: float, 
                             reason: str = "") -> Tuple[bool, float]:
        """Обновить баланс водителя"""
        with self.get_cursor(commit=True) as cur:
            # Получаем текущий баланс
            cur.execute(
                "SELECT balance FROM drivers WHERE telegram_id = %s",
                (telegram_id,)
            )
            row = cur.fetchone()
            
            if not row:
                return False, 0
            
            new_balance = float(row['balance']) + amount
            
            # Проверяем минимальный баланс
            if new_balance < config.MIN_DRIVER_BALANCE:
                return False, float(row['balance'])
            
            cur.execute(
                """UPDATE drivers SET balance = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE telegram_id = %s""",
                (new_balance, telegram_id)
            )
            
            self.log_transaction(
                action="BALANCE_UPDATE",
                user_id=telegram_id,
                amount=amount,
                details=f"New balance: {new_balance}. Reason: {reason}"
            )
            
            return True, new_balance
    
    def get_driver_balance(self, telegram_id: str) -> float:
        """Получить баланс водителя"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT balance FROM drivers WHERE telegram_id = %s",
                (telegram_id,)
            )
            row = cur.fetchone()
            return float(row['balance']) if row else 0
    
    def can_driver_take_order(self, telegram_id: str) -> bool:
        """Проверить, может ли водитель взять заказ"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT balance, is_active, is_blocked FROM drivers 
                   WHERE telegram_id = %s""",
                (telegram_id,)
            )
            row = cur.fetchone()
            
            if not row:
                return False
            
            if not row['is_active'] or row['is_blocked']:
                return False
            
            balance = float(row['balance'])
            return balance >= config.MIN_DRIVER_BALANCE
    
    def list_drivers(self, driver_type: str = None, active_only: bool = True) -> List[Dict]:
        """Получить список водителей"""
        with self.get_cursor() as cur:
            query = "SELECT * FROM drivers WHERE 1=1"
            params = []
            
            if driver_type:
                query += " AND driver_type = %s"
                params.append(driver_type)
            
            if active_only:
                query += " AND is_active = TRUE"
            
            query += " ORDER BY name"
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    # ==========================================================================
    # CAFE METHODS
    # ==========================================================================
    
    def get_cafe(self, telegram_id: str) -> Optional[Dict]:
        """Получить информацию о кафе"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT * FROM cafes WHERE telegram_id = %s AND is_active = TRUE",
                (telegram_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_cafe_by_id(self, cafe_id: int) -> Optional[Dict]:
        """Получить кафе по ID (PK)"""
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM cafes WHERE id = %s", (cafe_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    
    def add_cafe(self, telegram_id: str, name: str, phone: str = None,
                 address: str = None) -> bool:
        """Добавить новое кафе"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO cafes (telegram_id, name, phone, address)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (telegram_id) DO UPDATE 
                   SET name = EXCLUDED.name, phone = EXCLUDED.phone,
                       address = EXCLUDED.address, is_active = TRUE""",
                (telegram_id, name, phone, address)
            )
            self.log_transaction("CAFE_ADDED", telegram_id, details=f"Cafe: {name}")
            return True
    
    def update_cafe_info(self, telegram_id: str, **fields) -> bool:
        """Обновить данные кафе (name, phone, address, commission_percent, is_active)"""
        allowed = {'name', 'phone', 'address', 'commission_percent', 'is_active', 'telegram_id'}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}

        if not updates:
            return False

        set_parts = [f"{k} = %s" for k in updates]
        values = list(updates.values()) + [telegram_id]

        with self.get_cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE cafes SET {', '.join(set_parts)} WHERE telegram_id = %s",
                values
            )
            if cur.rowcount > 0:
                self.log_transaction("CAFE_UPDATED", telegram_id, details=",".join(updates.keys()))
                return True
            return False
    
    def remove_cafe(self, telegram_id: str) -> bool:
        """Полностью удалить кафе и связанные данные меню/веб-заказов."""
        with self.get_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM cafes WHERE telegram_id = %s", (telegram_id,))
            cafe = cur.fetchone()
            if not cafe:
                return False

            cafe_id = int(cafe['id'])

            # menu_items has FK to cafes and can block delete; clean dependencies first.
            cur.execute("DELETE FROM menu_items WHERE cafe_id = %s", (cafe_id,))
            cur.execute("DELETE FROM web_orders WHERE cafe_id = %s", (cafe_id,))
            cur.execute("DELETE FROM cafe_categories WHERE cafe_id = %s", (cafe_id,))
            cur.execute("DELETE FROM cafe_settings WHERE cafe_id = %s", (cafe_id,))
            cur.execute(
                """DELETE FROM cafes WHERE id = %s""",
                (cafe_id,)
            )
            if cur.rowcount > 0:
                self.log_transaction("CAFE_REMOVED", telegram_id)
                return True
            return False
    
    def update_cafe_debt(self, telegram_id: str, order_amount: float) -> Tuple[bool, float]:
        """Обновить долг кафе (добавить комиссию)"""
        cafe_commission_percent = self.get_runtime_setting(
            "cafe_commission_percent",
            config.CAFE_COMMISSION_PERCENT
        )
        commission = order_amount * (cafe_commission_percent / 100)
        
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE cafes 
                   SET debt = debt + %s
                   WHERE telegram_id = %s
                   RETURNING debt""",
                (commission, telegram_id)
            )
            row = cur.fetchone()
            
            if row:
                self.log_transaction(
                    action="CAFE_DEBT_ADDED",
                    user_id=telegram_id,
                    amount=commission,
                    details=f"Order amount: {order_amount}, Commission: {commission}"
                )
                return True, float(row['debt'])
            
            return False, 0
    
    def get_cafe_debt(self, telegram_id: str) -> float:
        """Получить долг кафе"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT debt FROM cafes WHERE telegram_id = %s",
                (telegram_id,)
            )
            row = cur.fetchone()
            return float(row['debt']) if row else 0

    def adjust_cafe_debt(self, telegram_id: str, amount: float, reason: str = "") -> Tuple[bool, float]:
        """
        Ручная корректировка долга кафе.
        amount > 0: погашение долга (debt уменьшается)
        amount < 0: увеличение долга (debt увеличивается)
        """
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE cafes
                   SET debt = GREATEST(debt - %s, 0)
                   WHERE telegram_id = %s
                   RETURNING debt""",
                (amount, telegram_id)
            )
            row = cur.fetchone()
            if not row:
                return False, 0

            new_debt = float(row['debt'])
            action = "CAFE_DEBT_REDUCED" if amount > 0 else "CAFE_DEBT_INCREASED"
            self.log_transaction(
                action=action,
                user_id=telegram_id,
                amount=abs(float(amount)),
                details=f"Reason: {reason}"
            )
            return True, new_debt
    
    def list_cafes(self, active_only: bool = True) -> List[Dict]:
        """Получить список кафе"""
        with self.get_cursor() as cur:
            query = "SELECT * FROM cafes"
            if active_only:
                query += " WHERE is_active = TRUE"
            query += " ORDER BY name"
            cur.execute(query)
            return [dict(row) for row in cur.fetchall()]
    
    # ==========================================================================
    # PHARMACY METHODS
    # ==========================================================================
    
    def get_pharmacy(self, telegram_id: str) -> Optional[Dict]:
        """Получить информацию об аптеке"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT * FROM pharmacies WHERE telegram_id = %s AND is_active = TRUE",
                (telegram_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    
    def add_pharmacy_bid(self, order_id: str, pharmacy_id: str, price: float) -> bool:
        """Добавить ценовое предложение от аптеки"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO pharmacy_bids (order_id, pharmacy_id, price)
                   VALUES (%s, %s, %s)""",
                (order_id, pharmacy_id, price)
            )
            return True
    
    def get_pharmacy_bids(self, order_id: str) -> List[Dict]:
        """Получить все ценовые предложения для заказа"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT pb.*, p.name as pharmacy_name 
                   FROM pharmacy_bids pb
                   JOIN pharmacies p ON pb.pharmacy_id = p.telegram_id
                   WHERE pb.order_id = %s
                   ORDER BY pb.price ASC""",
                (order_id,)
            )
            return [dict(row) for row in cur.fetchall()]
    
    def select_pharmacy_bid(self, order_id: str, bid_id: int) -> Optional[Dict]:
        """Выбрать предложение аптеки"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE pharmacy_bids SET is_selected = TRUE
                   WHERE id = %s AND order_id = %s
                   RETURNING *""",
                (bid_id, order_id)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    
    # ==========================================================================
    # SHOPPER METHODS
    # ==========================================================================
    
    def get_shopper(self, telegram_id: str = None) -> Optional[Dict]:
        """Получить информацию о закупщике"""
        with self.get_cursor() as cur:
            if telegram_id:
                cur.execute(
                    "SELECT * FROM shoppers WHERE telegram_id = %s AND is_active = TRUE",
                    (telegram_id,)
                )
            else:
                # Возвращаем первого активного закупщика
                cur.execute(
                    "SELECT * FROM shoppers WHERE is_active = TRUE LIMIT 1"
                )
            row = cur.fetchone()
            return dict(row) if row else None
    
    def add_shopper(self, telegram_id: str, name: str, phone: str = None) -> bool:
        """Добавить закупщика"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO shoppers (telegram_id, name, phone)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (telegram_id) DO UPDATE 
                   SET name = EXCLUDED.name, phone = EXCLUDED.phone, is_active = TRUE""",
                (telegram_id, name, phone)
            )
            self.log_transaction("SHOPPER_ADDED", telegram_id, details=f"Shopper: {name}")
            return True
    
    # ==========================================================================
    # AUCTION TIMER METHODS
    # ==========================================================================
    
    def create_auction_timer(self, order_id: str, service_type: str,
                            telegram_message_id: str, chat_id: str,
                            timeout_seconds: int) -> bool:
        """Создать таймер аукциона"""
        expires_at = datetime.now() + timedelta(seconds=timeout_seconds)
        
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO auction_timers 
                   (order_id, service_type, telegram_message_id, chat_id, expires_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (order_id, service_type, telegram_message_id, chat_id, expires_at)
            )
            return True
    
    def get_expired_auctions(self) -> List[Dict]:
        """Получить истекшие аукционы"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT * FROM auction_timers 
                   WHERE expires_at <= CURRENT_TIMESTAMP 
                   AND is_processed = FALSE"""
            )
            return [dict(row) for row in cur.fetchall()]

    def get_latest_auction_timer(self, order_id: str, service_type: str = None) -> Optional[Dict]:
        """Получить последний таймер по заказу (независимо от is_processed — нужен message_id для редактирования)"""
        with self.get_cursor() as cur:
            if service_type:
                cur.execute(
                    """SELECT * FROM auction_timers
                       WHERE order_id = %s AND service_type = %s
                       ORDER BY id DESC LIMIT 1""",
                    (order_id, service_type)
                )
            else:
                cur.execute(
                    """SELECT * FROM auction_timers
                       WHERE order_id = %s
                       ORDER BY id DESC LIMIT 1""",
                    (order_id,)
                )
            row = cur.fetchone()
            return dict(row) if row else None
    
    def mark_auction_processed(self, timer_id: int) -> bool:
        """Пометить аукцион как обработанный"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE auction_timers SET is_processed = TRUE WHERE id = %s",
                (timer_id,)
            )
            return cur.rowcount > 0
    
    # ==========================================================================
    # TRANSACTION LOG METHODS
    # ==========================================================================
    
    def log_transaction(self, action: str, user_id: str = None,
                       order_id: str = None, amount: float = None,
                       details: str = "") -> bool:
        """Записать транзакцию в лог"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO transactions (action, user_id, order_id, amount, details)
                   VALUES (%s, %s, %s, %s, %s)""",
                (action, user_id, order_id, amount, details)
            )
            return True
    
    def get_transactions(self, user_id: str = None, limit: int = 100) -> List[Dict]:
        """Получить историю транзакций"""
        with self.get_cursor() as cur:
            if user_id:
                cur.execute(
                    """SELECT * FROM transactions 
                       WHERE user_id = %s 
                       ORDER BY created_at DESC LIMIT %s""",
                    (user_id, limit)
                )
            else:
                cur.execute(
                    """SELECT * FROM transactions 
                       ORDER BY created_at DESC LIMIT %s""",
                    (limit,)
                )
            return [dict(row) for row in cur.fetchall()]
    
    # ==========================================================================
    # STATISTICS METHODS
    # ==========================================================================
    
    def get_daily_stats(self, date: datetime = None) -> Dict:
        """Получить статистику за день"""
        if date is None:
            date = datetime.now()
        
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT 
                       COUNT(*) as total_orders,
                       COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) as completed,
                       COUNT(CASE WHEN status = 'CANCELLED' THEN 1 END) as cancelled,
                       SUM(CASE WHEN status = 'COMPLETED' THEN price_total ELSE 0 END) as total_revenue,
                       SUM(commission) as total_commission
                   FROM orders 
                   WHERE DATE(created_at) = DATE(%s)""",
                (date,)
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    
    def get_service_stats(self, days: int = 7) -> List[Dict]:
        """Получить статистику по услугам"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT 
                       service_type,
                       COUNT(*) as count,
                       SUM(price_total) as revenue,
                       SUM(commission) as commission
                   FROM orders 
                   WHERE created_at >= CURRENT_DATE - INTERVAL '%s days'
                   GROUP BY service_type""",
                (days,)
            )
            return [dict(row) for row in cur.fetchall()]
    
    # ==========================================================================
    # TELEGRAM SESSION METHODS (для регистрации водителей)
    # ==========================================================================
    
    def get_telegram_session(self, telegram_id: str) -> Optional[Dict]:
        """Получить сессию Telegram-пользователя"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT * FROM telegram_sessions WHERE telegram_id = %s",
                (telegram_id,)
            )
            row = cur.fetchone()
            if row:
                result = dict(row)
                if isinstance(result.get('temp_data'), str):
                    result['temp_data'] = json.loads(result['temp_data'])
                return result
            return None
    
    def create_telegram_session(self, telegram_id: str) -> Dict:
        """Создать новую Telegram-сессию"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO telegram_sessions (telegram_id, state, temp_data)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (telegram_id) DO UPDATE 
                   SET state = 'IDLE', temp_data = '{}', updated_at = CURRENT_TIMESTAMP
                   RETURNING *""",
                (telegram_id, 'IDLE', '{}')
            )
            row = cur.fetchone()
            result = dict(row)
            if isinstance(result.get('temp_data'), str):
                result['temp_data'] = json.loads(result['temp_data'])
            return result
    
    def set_telegram_session_state(self, telegram_id: str, state: str) -> bool:
        """Установить состояние Telegram-сессии"""
        with self.get_cursor(commit=True) as cur:
            # Шаг 1: Убедиться, что сессия существует (создать с пустыми данными, если нет)
            cur.execute(
                """INSERT INTO telegram_sessions (telegram_id, state, temp_data)
                   VALUES (%s, 'IDLE', '{}'::jsonb)
                   ON CONFLICT (telegram_id) DO NOTHING""",
                (telegram_id,)
            )

            # Шаг 2: Обновить только state, НЕ трогая temp_data
            cur.execute(
                """UPDATE telegram_sessions
                   SET state = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE telegram_id = %s""",
                (state, telegram_id)
            )
            return cur.rowcount > 0
    
    def set_telegram_session_data(self, telegram_id: str, key: str, value: Any) -> bool:
        """Установить данные Telegram-сессии"""
        with self.get_cursor(commit=True) as cur:
            # Убедимся что сессия существует
            cur.execute(
                """INSERT INTO telegram_sessions (telegram_id, temp_data)
                   VALUES (%s, %s::jsonb)
                   ON CONFLICT (telegram_id) DO UPDATE 
                   SET temp_data = COALESCE(telegram_sessions.temp_data, '{}'::jsonb) || %s::jsonb,
                       updated_at = CURRENT_TIMESTAMP""",
                (telegram_id, json.dumps({key: value}), json.dumps({key: value}))
            )
            
            if cur.rowcount == 0:
                logger.error(f"Failed to set session data for {telegram_id}: {key}={value}")
                
            return cur.rowcount > 0
    
    def clear_telegram_session(self, telegram_id: str) -> bool:
        """Очистить Telegram-сессию (сбросить в IDLE)"""
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """UPDATE telegram_sessions 
                   SET state = 'IDLE', temp_data = '{}', updated_at = CURRENT_TIMESTAMP
                   WHERE telegram_id = %s""",
                (telegram_id,)
            )
            return cur.rowcount > 0
    
    def get_telegram_session_data(self, telegram_id: str, key: str, default=None) -> Any:
        """Получить данные из Telegram-сессии"""
        session = self.get_telegram_session(telegram_id)
        if session and session.get('temp_data'):
            return session['temp_data'].get(key, default)
        return default
    
    # ==========================================================================
    # DRIVER STATISTICS
    # ==========================================================================
    
    def get_driver_order_stats(self, telegram_id: str) -> Dict:
        """Получить статистику заказов водителя"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT 
                       COUNT(*) as total_orders,
                       COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) as completed,
                       COUNT(CASE WHEN status = 'CANCELLED' THEN 1 END) as cancelled,
                       COUNT(CASE WHEN DATE(created_at) = CURRENT_DATE THEN 1 END) as today
                   FROM orders 
                   WHERE driver_id = %s""",
                (telegram_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else {
                'total_orders': 0, 'completed': 0, 
                'cancelled': 0, 'today': 0
            }

    # ==========================================================================
    # MENU ITEMS
    # ==========================================================================
    
    # --------- Categories ----------
    def list_categories(self, cafe_id: int) -> List[Dict]:
        """Список категорий кафе"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT * FROM cafe_categories
                   WHERE cafe_id = %s
                   ORDER BY sort_order ASC, name ASC""",
                (cafe_id,)
            )
            return [dict(row) for row in cur.fetchall()]

    def get_category(self, category_id: int) -> Optional[Dict]:
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM cafe_categories WHERE id = %s", (category_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def add_category(self, cafe_id: int, name: str, sort_order: int = 0) -> bool:
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO cafe_categories (cafe_id, name, sort_order)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (cafe_id, name) DO NOTHING""",
                (cafe_id, name, sort_order)
            )
            return cur.rowcount > 0

    def update_category(self, category_id: int, name: str = None, sort_order: int = None) -> bool:
        fields = []
        values = []
        if name is not None:
            fields.append("name = %s")
            values.append(name)
        if sort_order is not None:
            fields.append("sort_order = %s")
            values.append(sort_order)
        if not fields:
            return False
        values.append(category_id)
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE cafe_categories SET {', '.join(fields)} WHERE id = %s",
                tuple(values)
            )
            return cur.rowcount > 0

    def delete_category(self, category_id: int) -> bool:
        with self.get_cursor(commit=True) as cur:
            # Сначала отвяжем блюда, чтобы не было конфликтов FK
            cur.execute("UPDATE menu_items SET category_id = NULL WHERE category_id = %s", (category_id,))
            cur.execute("DELETE FROM cafe_categories WHERE id = %s", (category_id,))
            return cur.rowcount > 0

    # --------- Items ----------
    def list_menu_items(self, cafe_id: int) -> List[Dict]:
        """Получить список позиций меню кафе"""
        with self.get_cursor() as cur:
            cur.execute(
                """SELECT mi.*, cc.name AS category_name, cc.sort_order AS category_sort
                   FROM menu_items mi
                   LEFT JOIN cafe_categories cc ON mi.category_id = cc.id
                   WHERE mi.cafe_id = %s 
                   ORDER BY COALESCE(cc.sort_order, 9999) ASC, mi.sort_order ASC, mi.id ASC""",
                (cafe_id,)
            )
            rows = cur.fetchall()
            return [dict(row) for row in rows]
            
    def add_menu_item(self, cafe_id: int, name: str, price: float, category: str = "Основное",
                     image_url: str = None, description: str = None, category_id: int = None) -> bool:
        """Добавить позицию в меню"""
        resolved_category = category or "Основное"
        if category_id:
            cat = self.get_category(category_id)
            resolved_category = cat['name'] if cat else category

        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO menu_items (cafe_id, name, price, category, category_id, image_url, description)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (cafe_id, name, price, resolved_category, category_id, image_url, description)
            )
            return cur.rowcount > 0

    def update_menu_item(self, item_id: int, **fields: Any) -> bool:
        """Обновить позицию меню"""
        if not fields:
            return False

        # If category_id passed, also sync category name
        if 'category_id' in fields and fields['category_id']:
            cat = self.get_category(fields['category_id'])
            if cat:
                fields['category'] = cat['name']

        set_parts = []
        values = []
        for key, value in fields.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
            
        values.append(item_id)
        
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE menu_items SET {', '.join(set_parts)} WHERE id = %s",
                tuple(values)
            )
            return cur.rowcount > 0

    def delete_menu_item(self, item_id: int) -> bool:
        """Удалить позицию меню"""
        with self.get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM menu_items WHERE id = %s", (item_id,))
            return cur.rowcount > 0

    def get_menu_item(self, item_id: int) -> Optional[Dict]:
        """Получить позицию меню по ID"""
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM menu_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    # ==========================================================================
    # CAFE SETTINGS (global discount)
    # ==========================================================================

    def get_cafe_settings(self, cafe_id: int) -> Dict:
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM cafe_settings WHERE cafe_id = %s", (cafe_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
            return {"cafe_id": cafe_id, "global_discount_percent": 0, "global_discount_active": False}

    def update_cafe_settings(self, cafe_id: int, global_discount_percent: float = 0, global_discount_active: bool = False) -> bool:
        with self.get_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO cafe_settings (cafe_id, global_discount_percent, global_discount_active)
                VALUES (%s, %s, %s)
                ON CONFLICT (cafe_id) DO UPDATE SET
                    global_discount_percent = EXCLUDED.global_discount_percent,
                    global_discount_active = EXCLUDED.global_discount_active
            """, (cafe_id, global_discount_percent, global_discount_active))
            return True

    # ==========================================================================
    # WEB ORDERS
    # ==========================================================================
    
    def create_web_order(self, cafe_id: int, cafe_name: str, 
                         items: List[Dict], total_price: float) -> str:
        """Создать веб-заказ и вернуть код (W12345)"""
        import random
        # Генерируем уникальный код W + 5 цифр
        while True:
            code = f"W{random.randint(10000, 99999)}"
            with self.get_cursor() as cur:
                cur.execute("SELECT 1 FROM web_orders WHERE order_code = %s", (code,))
                if not cur.fetchone():
                    break
        
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO web_orders 
                   (order_code, cafe_id, cafe_name, items_json, total_price, status)
                   VALUES (%s, %s, %s, %s, %s, 'PENDING')""",
                (code, cafe_id, cafe_name, json.dumps(items), total_price)
            )
        return code

    def get_web_order(self, order_code: str) -> Optional[Dict]:
        """Получить веб-заказ по коду"""
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM web_orders WHERE order_code = %s", (order_code,))
            row = cur.fetchone()
            if row:
                data = dict(row)
                return data
            return None
            
    def update_web_order_status(self, order_code: str, status: str, 
                                client_phone: str = None, address: str = None) -> bool:
        """Обновить статус и данные веб-заказа"""
        fields = ["status = %s"]
        values = [status]
        
        if client_phone:
            fields.append("client_phone = %s")
            values.append(client_phone)
        if address:
            fields.append("address = %s")
            values.append(address)
            
        values.append(order_code)
        
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE web_orders SET {', '.join(fields)} WHERE order_code = %s",
                tuple(values)
            )
            return cur.rowcount > 0


class User:
    """Класс представляющий пользователя"""
    
    def __init__(self, phone: str, name: str = "", 
                 current_state: str = config.STATE_IDLE, 
                 temp_data: Dict = None, language: str = "ru"):
        self.phone = phone
        self.name = name
        self.current_state = current_state
        self.temp_data = temp_data or {}
        self.language = language
    
    def set_state(self, state: str):
        """Установить состояние пользователя"""
        try:
            self.current_state = state
            db = get_db()
            db.set_user_state(self.phone, state)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error setting user state for {self.phone}: {e}")
            raise

    def set_temp_data(self, key: str, value: Any):
        """Установить временные данные"""
        try:
            self.temp_data[key] = value
            db = get_db()
            db.set_user_temp_data(self.phone, key, value)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error setting user temp_data for {self.phone}: {e}")
            raise
    
    def get_temp_data(self, key: str, default=None) -> Any:
        """Получить временные данные"""
        return self.temp_data.get(key, default)
    
    def clear_temp_data(self):
        """Очистить временные данные"""
        self.temp_data = {}
        db = get_db()
        db.clear_user_temp_data(self.phone)


# Singleton instance
_db_instance = None

def get_db() -> Database:
    """Получить экземпляр базы данных (Singleton)"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


def get_runtime_settings() -> Dict[str, float]:
    return get_db().get_runtime_settings()


def get_runtime_setting(key: str, default: float = None) -> float:
    return get_db().get_runtime_setting(key, default)
