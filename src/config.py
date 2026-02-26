"""
Конфигурация и Константы для Бизнес Жардамчы ГО
Business Assistant GO - Configuration
Автор: @Eldar
"""

import os
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ (АДМИНИСТРИРОВАНИЕ)
# =============================================================================

# Режим Рамазан (глобальный переключатель)
IS_RAMADAN = os.getenv("IS_RAMADAN", "False").lower() == "true"

# Администраторы (Telegram IDs через запятую)
ADMIN_TELEGRAM_IDS = os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")

# =============================================================================
# КОМИССИИ И ТАРИФЫ
# =============================================================================

# Кафе
CAFE_COMMISSION_PERCENT = 5  # 5% от суммы заказа (всегда, без скидок)

# Магазин
SHOP_DELIVERY_FEE = 150  # Стоимость доставки из магазина (с клиента и таксисту)
SHOPPER_SERVICE_FEE = 200  # (legacy) Услуга закупщика
SHOPPER_COMMISSION = 10  # (legacy) комиссия с курьера-закупщика
TAXI_SHOP_COMMISSION = 10  # 10 сом комиссия с таксиста за доставку из магазина

# Аптека
PHARMACY_COMMISSION_PERCENT = 5  # 5% от суммы заказа
PHARMACY_DELIVERY_FEE = 100  # Доставка (с клиента)
TAXI_PHARMACY_COMMISSION = 10  # 10 сом комиссия с таксиста за доставку аптеки

# Такси
TAXI_COMMISSION = 10  # Фиксированная комиссия 10 сом
TAXI_PRICE_RANGE = "100-150"  # Диапазон цен
TAXI_CUSTOM_PRICE_MIN = 70        # Минимальная цена клиента
TAXI_CUSTOM_PRICE_THRESHOLD = 0   # Не используется
TAXI_CUSTOM_PRICE_COMMISSION = 0  # Не используется

# Портер
PORTER_COMMISSION = 20  # Комиссия с портер 20 сом

# Муравей
ANT_COMMISSION = 10  # Комиссия с муравья 10 сом

# =============================================================================
# ТАЙМАУТЫ (в секундах)
# =============================================================================

CAFE_AUCTION_TIMEOUT = 120  # 2 минуты на аукцион кафе
PHARMACY_RESPONSE_TIMEOUT = 180  # 3 минуты на ответ аптеки
TAXI_RESPONSE_TIMEOUT = 300  # 5 минут на ответ таксиста
TAXI_ACCEPTED_TIMEOUT = 1800  # 30 минут — удалить сообщение забранного заказа
PENDING_ORDER_AUTO_CANCEL_TIMEOUT = 1200  # 20 минут — автоотмена если никто не забрал
IN_DELIVERY_AUTO_COMPLETE_TIMEOUT = 1800  # 30 минут — автозавершение после взятия водителем

# =============================================================================
# ID TELEGRAM ГРУПП И ПОЛЬЗОВАТЕЛЕЙ
# =============================================================================

GROUP_CAFE_ID = os.getenv("GROUP_CAFE_ID", "-100123456789")
GROUP_PHARMACY_ID = os.getenv("GROUP_PHARMACY_ID", "-100987654321")
GROUP_TAXI_ID = os.getenv("GROUP_TAXI_ID", "-100112233445")
GROUP_PORTER_ID = os.getenv("GROUP_PORTER_ID", "-100554433221")
GROUP_ANT_ID = os.getenv("GROUP_ANT_ID", os.getenv("GROUP_PORTER_ID", "-100554433221"))  # По умолчанию = группа портеров
GROUP_SHOP_ID = os.getenv("GROUP_SHOP_ID", "-100667788990")

# ID закупщика (личные сообщения)
SHOPPER_TELEGRAM_ID = os.getenv("SHOPPER_TELEGRAM_ID", "")

# Тех поддержка
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "0226410410")
SUPPORT_TELEGRAM_ID = os.getenv("SUPPORT_TELEGRAM_ID", "")

# =============================================================================
# API КЛЮЧИ
# =============================================================================

# WhatsApp (Cloud API, Twilio или GREEN API)
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "cloud")  # "cloud", "green" или "twilio"

# WhatsApp Cloud API Settings (Meta Graph API)
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "jardamchy_verify")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")

# GREEN API Settings (legacy)
GREEN_API_INSTANCE = os.getenv("GREEN_API_INSTANCE", "")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN", "")
GREEN_API_URL = f"https://api.green-api.com/waInstance{GREEN_API_INSTANCE}"

# Twilio Settings (legacy)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

# Telegram Bot API
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Google Sheets (для легаси данных или бэкапа)
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "")
GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")

# OpenAI Whisper (Speech-to-Text)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
WHISPER_LANGUAGE = "ru"  # Русский язык
# Подсказка для Whisper — частые слова из контекста приложения помогают распознаванию
WHISPER_PROMPT = (
    "Такси, кафе, аптека, магазин, портер, муравей, груз, мука, "
    "Шамалды-Сай, базар, северная, южная, мечеть, школа, больница, "
    "заказ, цена, сом, нет, да, отмена, спасибо, "
    "светофор, от светофора, до светофора, "
    "Пушкина, Ленина, Адыл кафе, "
    "на трассу, на мертвый, "
    "номер 2, Магазин Миллион, миллиондон, миллионго, Глобус, от Глобуса, "
    "12-й, 12 чи, 12чи, он эки, "
    "номер 8, от Ленина, "
    "спортивная, Ак Кеме, центральная мечеть, двойняшки, Грин Хаус, ЖД, "
    "Миң түркүн, Кумар аке, Адыр 2, Адыр, "
    "Үй бүлөлүк магазин, Магазин 777, Көк магазин, "
    "Кеңешбек-Ата, Жээнмырза, Атакулов, "
    "Самурай, больница көчөсү"
)

# PostgreSQL Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/business_assistant")

# =============================================================================
# СТАТУСЫ ПОЛЬЗОВАТЕЛЯ (STATE MACHINE)
# =============================================================================

# Основные состояния
STATE_IDLE = "IDLE"
STATE_MENU = "MENU"

# Кафе
STATE_CAFE_ORDER = "CAFE_ORDER"
STATE_CAFE_ADDRESS = "CAFE_ADDRESS"
STATE_CAFE_PAYMENT = "CAFE_PAYMENT"
STATE_CAFE_WAIT_CONFIRM = "CAFE_WAIT_CONFIRM"
STATE_CAFE_DECLINE_REASON = "CAFE_DECLINE_REASON"

# Магазин
STATE_SHOP_LIST = "SHOP_LIST"
STATE_SHOP_CONFIRM = "SHOP_CONFIRM"
STATE_SHOP_WAIT_PRICE = "SHOP_WAIT_PRICE"
STATE_SHOP_DELIVERY_CHOICE = "SHOP_DELIVERY_CHOICE"

# Аптека
STATE_PHARMACY_WAIT_RX = "PHARMACY_WAIT_RX"             # Ждём название лекарства
STATE_PHARMACY_WAIT_PRICE = "PHARMACY_WAIT_PRICE"
STATE_PHARMACY_CONFIRM = "PHARMACY_CONFIRM"
STATE_PHARMACY_ADDRESS = "PHARMACY_ADDRESS"

# Такси
STATE_TAXI_ROUTE = "TAXI_ROUTE"
STATE_TAXI_REORDER_CHOICE = "TAXI_REORDER_CHOICE"  # Повторить отменённый заказ: Да/Нет

# Портер
STATE_PORTER_CARGO_TYPE = "PORTER_CARGO_TYPE"
STATE_PORTER_ROUTE = "PORTER_ROUTE"

# Муравей
STATE_ANT_ROUTE = "ANT_ROUTE"

# Подтверждение заказа (универсальное)
STATE_CONFIRM_ORDER = "CONFIRM_ORDER"

# Регистрация водителя (Telegram)
STATE_DRIVER_REG_TYPE = "DRIVER_REG_TYPE"           # Выбор типа (такси/портер/муравей)
STATE_DRIVER_REG_NAME = "DRIVER_REG_NAME"           # Ввод имени
STATE_DRIVER_REG_PHONE = "DRIVER_REG_PHONE"         # Ввод телефона
STATE_DRIVER_REG_CAR = "DRIVER_REG_CAR"             # Ввод марки авто

# Веб-заказ
STATE_WEB_ORDER_ADDRESS = "WEB_ORDER_ADDRESS"         # Ввод адреса для веб-заказа

# =============================================================================
# НОМЕРА И ССЫЛКИ
# =============================================================================
WHATSAPP_BOT_PHONE = os.getenv("WHATSAPP_BOT_PHONE", "996220310310")
MENU_LINK = os.getenv("MENU_LINK", "http://localhost:5000/menu/")

STATE_DRIVER_REG_PLATE = "DRIVER_REG_PLATE"         # Ввод госномера
STATE_DRIVER_REG_CONFIRM = "DRIVER_REG_CONFIRM"     # Подтверждение

# =============================================================================
# СТАТУСЫ ЗАКАЗОВ
# =============================================================================

ORDER_STATUS_PENDING = "PENDING"           # Ожидает исполнителя
ORDER_STATUS_AUCTION = "AUCTION"           # На аукционе
ORDER_STATUS_ACCEPTED = "ACCEPTED"         # Принят исполнителем
ORDER_STATUS_READY = "READY"               # Готов к выдаче
ORDER_STATUS_IN_DELIVERY = "IN_DELIVERY"   # В доставке
ORDER_STATUS_COMPLETED = "COMPLETED"       # Завершен
ORDER_STATUS_CANCELLED = "CANCELLED"       # Отменен
ORDER_STATUS_URGENT = "URGENT"             # Срочный (таймаут)

# =============================================================================
# ТИПЫ УСЛУГ
# =============================================================================

SERVICE_CAFE = "cafe"
SERVICE_SHOP = "shop"
SERVICE_PHARMACY = "pharmacy"
SERVICE_TAXI = "taxi"
SERVICE_PORTER = "porter"
SERVICE_ANT = "ant"
SERVICE_SUPPORT = "support"

# =============================================================================
# ТИПЫ ГРУЗОВ (ПОРТЕР)
# =============================================================================

CARGO_TYPES = {
    "furniture": "🪑 Мебель",
    "trash": "🗑️ Мусор",
    "construction": "🏗️ Стройматериалы",
    "livestock": "🐄 Скот",
    "groceries": "🥬 Продукты / Азык-түлүк",
    "bags": "🎒 Вещи / Баштыктар",
    "appliances": "📺 Техника / Техника",
    "other": "📦 Другое"
}

# =============================================================================
# МЕТОДЫ ОПЛАТЫ
# =============================================================================

PAYMENT_CASH = "cash"
PAYMENT_MBANK = "mbank"
PAYMENT_OPTIMA = "optima"

PAYMENT_METHODS = {
    PAYMENT_CASH: "💵 Наличные",
    PAYMENT_MBANK: "📱 МБанк",
    PAYMENT_OPTIMA: "🏦 Оптима"
}

# =============================================================================
# ВРЕМЯ ГОТОВНОСТИ КАФЕ (минуты)
# =============================================================================

CAFE_READY_TIMES = [10, 15, 20, 30, 45, 60]

# =============================================================================
# СООБЩЕНИЯ БОТА (Русский / Кыргызский)
# =============================================================================

WELCOME_MESSAGE = """👋 Салам! Мен *Жардамчы ГО* - сиздин жардамчыңыз!

🏙 *г. Шамалдуу-Сай*

Кызмат тандаңыз:

🍔 *1. Кафе* - Тамак заказ
🚚 *2. Азык-түлүк* - Жеткирүү
💊 *3. Аптека* - Дары
🚖 *4. Такси* - Такси чакыруу
🚛 *5. Портер* - Жүк ташуу
🐜 *6. Муравей* - Желмаян
🆘 *7. Жардам/Оператор* - 0226410410

Номер же атын жазыңыз."""

# Кафе
CAFE_PROMPT = """🍔 *Кафе*

Эмне заказ кылгыңыз келет?

Тамактардын тизмесин жазыңыз же менюнун сүрөтүн жөнөтүңүз."""

CAFE_ADDRESS_PROMPT = """📍 *Жеткирүү дареги*

Заказ кайда жеткирилсин?"""

CAFE_PAYMENT_PROMPT = """💳 *Төлөө ыкмасы*

Төлөө ыкмасын тандаңыз:

1️⃣ 💵 Накталай
2️⃣ 📱 МБанк
3️⃣ 🏦 Оптима"""

CAFE_ORDER_SENT = """✅ *Заказ жөнөтүлдү!*

🔍 Кафе издеп жатабыз...

⏱ Тастыктоону күтөбүз..."""

# Кафе
CAFE_DECLINE_PROMPT = """❌ *Заказдан баш тартуу*

Бир билдирүү менен себепти жазыңыз.

Мисал: "Таавук жок, башка тамак тандаңыз"."""

CAFE_DECLINE_CLIENT = """❌ *Заказ #{order_id}*

Тилекке каршы, кафе баш тартты:
{reason}

Сураныч, башка заказ бериңиз."""

# Магазин
SHOP_PROMPT = """🚚 *Жеткирүү*

Продукттардын тизмесин жөнөтүңүз.

Мисал:
- Нан
- Сүт 1л
- Жумуртка 10 даана
- Өсүмдүк майы"""

SHOP_CONFIRM_PROMPT = """🛒 *Тастыктоо*

✅ Тастыктайсызбы?

Жазыңыз: *Ооба* же *Жок*"""

# Аптека
PHARMACY_PROMPT = """💊 *Дарыкана*

Дарынын атын жазыңыз"""

PHARMACY_SEARCHING = """💊 *Дары издеп жатабыз...*

Суроо дарыканаларга жөнөтүлдү.

⏱ Баа сунуштарын күтөбүз (3 мүнөт)..."""

# Такси
TAXI_PROMPT = """🚖 *Такси*

Бир билдирүү менен жазыңыз:

📍 *КАЙДАН* жана *КАЙДА*?

Мисал:
Базардан Ак-Булак микрорайонуна"""

TAXI_PRICE_INFO = """🚖 *Такси*

🔍 Унаа издеп жатабыз..."""

# =============================================================================
# СООБЩЕНИЯ ПОДТВЕРЖДЕНИЯ ЗАКАЗА
# =============================================================================

CONFIRM_TAXI = """🚖 *Такси*

🛣 *Маршрут:* {from_address} — {to_address}

✅ Маршрут туурабы? Тастыктайсызбы?"""

CONFIRM_CAFE = """🍔 *Кафе*

📋 *Заказ:* {order_details}
📍 *Дарек:* {address}
🚗 *Жеткирүү:* таксист менен сүйлөшүү

✅ Баары туурабы? Тастыктайсызбы?"""

CONFIRM_SHOP = """🚚 *Жеткирүү*

📋 *Тизме:*
{order_details}

🚗 *Жеткирүү:* таксист менен сүйлөшүү

✅ Баары туурабы? Тастыктайсызбы?"""

CONFIRM_PHARMACY = """💊 *Дарыкана*

📝 *Суроо:* {order_details}

✅ Баары туурабы? Тастыктайсызбы?"""

CONFIRM_PORTER = """🚛 *Жүк ташуу*

🛣 *Маршрут:* {from_address} — {to_address}

✅ Баары туурабы? Тастыктайсызбы?"""

CONFIRM_ANT = """🐜 *Желмаян*

🛣 *Маршрут:* {from_address} — {to_address}

✅ Баары туурабы? Тастыктайсызбы?"""

ORDER_CANCELLED = """❌ *Заказ жокко чыгарылды*

Кайра заказ кылгыңыз келсе жазыңыз."""

ORDER_SENT_GENERIC = """✅ *Заказ жөнөтүлдү!*

⏱ Жооп күтөбүз..."""

SUPPORT_TO_CLIENT = """🆘 *Колдоо кызматы*

📞 {support_phone}

Бизге чалыңыз же жазыңыз — жардам беребиз!"""

SUPPORT_TO_OPERATOR = """🆘 *Запрос помощи*

👤 Клиент: {client_phone}

Клиент запросил тех поддержку."""

BLOCKED_MESSAGE = """🚫 *Бөгөттөлдүңүз*

Колдоо кызматына кайрылыңыз: {support_phone}"""

# Сообщение при неточном адресе
VAGUE_ADDRESS_PROMPT = """⚠️ *Дарек өтө жалпы*

Такталыраак даректи жазыңыз.

Мисал:
Ленина көч. 25 / Северная 12, 5-үй"""

# Портер
PORTER_CARGO_PROMPT = """🚛 *Жүк ташуу*

Эмне ташыйбыз?

Кыскача жазыңыз, мисалы: мебель, таш, кум, мал, продукты..."""

PORTER_ROUTE_PROMPT = """🚛 *Маршрут*

Маршрутту жазыңыз:

📍 Кайдан - Кайда?"""

# Муравей
ANT_PROMPT = """🐜 *Желмаян*

Эмне ташыш керек жана кайдан-кайда?

Бир билдирүү менен жазыңыз:
Мисал: Ун ташыш керек, базардан үйгө"""

ANT_ROUTE_PROMPT = """🐜 *Маршрут*

Маршрутту жазыңыз:

📍 Кайдан - Кайда?"""

# =============================================================================
# СООБЩЕНИЯ ДЛЯ ИСПОЛНИТЕЛЕЙ (Telegram)
# =============================================================================

# Кафе
CAFE_ORDER_TELEGRAM = """🍔 *НОВЫЙ ЗАКАЗ / ЖАҢЫ ЗАКАЗ* #{order_id}

📋 *Заказ / Заказ:*
{order_details}

📍 *Адрес / Дарек:* {address}
💳 *Оплата / Төлөө:* {payment}
📞 *Клиент / Кардар:* {phone}

⏱ *Таймер: 2 минуты / Таймер: 2 мүнөт*"""

CAFE_ORDER_URGENT = """🔥 *СРОЧНЫЙ ЗАКАЗ! / ШАШЫЛЫШ ЗАКАЗ!* #{order_id}

⚠️ Никто не взял заказ!
Эч ким заказды алган жок!

📋 *Заказ / Заказ:*
{order_details}

📍 *Адрес / Дарек:* {address}
💳 *Оплата / Төлөө:* {payment}
📞 *Клиент / Кардар:* {phone}

🚨 *КТО ВОЗЬМЕТ? / КИМ АЛАТ?*"""

# Такси
TAXI_ORDER_TELEGRAM = """🚖 *НОВЫЙ ПАССАЖИР / ЖАҢЫ ЖОЛУЧУ*

🛣 *Маршрут:*
{route}

💵 *Цена / Баа:* {price} сом (договорная)
{commission_info}

🚖 *КТО СВОБОДЕН? / КИМ БОШ?*"""

# Аптека
PHARMACY_ORDER_TELEGRAM = """💊 *ИЩУТ ЛЕКАРСТВО / ДАРЫ ИЗДЕП ЖАТАТ*

📝 *Запрос / Суроо:*
{request}

📞 *Клиент / Кардар:* {phone}

💊 *У КОГО ЕСТЬ? / КИМДЕ БАР?*"""

# Портер
PORTER_ORDER_TELEGRAM = """🚛 *НОВЫЙ ГРУЗ / ЖАҢЫ ЖҮК*

🛣 *Маршрут:* {route}

💵 *Цена / Баа:* Договорная / Сүйлөшүүлүү

🚛 *КТО ВОЗЬМЕТ? / КИМ АЛАТ?*"""

# Муравей
ANT_ORDER_TELEGRAM = """🐜 *МУРАВЕЙ / ЖЕЛМАЯН*

🛣 *Маршрут:* {route}

💵 *Цена / Баа:* Договорная / Сүйлөшүүлүү
📞 *Клиент / Кардар:* {phone}

🐜 *КТО ВОЗЬМЕТ? / КИМ АЛАТ?*"""

# =============================================================================
# ЛОГИРОВАНИЕ
# =============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "logs/business_assistant.log"
PORT = int(os.getenv("PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# =============================================================================
# ЛИМИТЫ И ОГРАНИЧЕНИЯ
# =============================================================================

# Минимальный баланс для приёма заказов (сом)
MIN_DRIVER_BALANCE = 10

# Максимальный долг кафе
MAX_CAFE_DEBT = 10000

# Максимальное время хранения заказа в архиве (дней)
ARCHIVE_DAYS = 30

# =============================================================================
# ТИПЫ ВОДИТЕЛЕЙ
# =============================================================================

DRIVER_TYPES = {
    "taxi": "🚖 Такси",
    "porter": "🚛 Портер (грузоперевозки)",
    "ant": "🐜 Муравей (мелкие грузы)"
}

# =============================================================================
# СООБЩЕНИЯ РЕГИСТРАЦИИ ВОДИТЕЛЕЙ (Telegram)
# =============================================================================

DRIVER_WELCOME = """👋 *Добро пожаловать в Жардамчы ГО!*

Выберите действие из кнопок ниже:"""

DRIVER_WELCOME_BUTTONS = [
    {"text": "📝 Регистрация", "callback": "cmd_register"},
    {"text": "💰 Баланс", "callback": "cmd_balance"},
    {"text": "👤 Профиль", "callback": "cmd_profile"},
    {"text": "📊 Статистика", "callback": "cmd_stats"},
    {"text": "❓ Помощь", "callback": "cmd_help"},
]

DRIVER_REG_TYPE_PROMPT = """📝 *Регистрация водителя*

Выберите тип работы:

🚖 *1.* Такси
🚛 *2.* Портер (грузоперевозки)
🐜 *3.* Муравей (мелкие грузы)

Напишите номер или нажмите кнопку."""

DRIVER_REG_NAME_PROMPT = """👤 *Введите ваше ФИО:*

Пример: Райымов Элдар"""

DRIVER_REG_PHONE_PROMPT = """📞 *Введите ваш номер телефона:*

Пример: 0555123456"""

DRIVER_REG_CAR_PROMPT_TAXI = """🚗 *Введите марку и модель авто:*

Пример: Toyota Camry"""

DRIVER_REG_CAR_PROMPT_PORTER = """🚛 *Введите марку и модель транспорта:*

Пример: Hyundai Porter / Газель"""

DRIVER_REG_CAR_PROMPT_ANT = """🐜 *Введите марку и модель транспорта:*

Пример: Daewoo Damas / Labo"""

DRIVER_REG_PLATE_PROMPT = """🔢 *Введите государственный номер:*

Пример: 04 123 AAA"""

DRIVER_REG_CONFIRM_TEMPLATE = """✅ *Проверьте данные:*

{type_emoji} *Тип:* {driver_type}
👤 *ФИО:* {name}
📞 *Телефон:* {phone}
🚗 *Транспорт:* {car_model}
🔢 *Госномер:* {plate}

Всё верно? Напишите *Да* или *Нет*."""

DRIVER_REG_CONFIRM_TEMPLATE_ANT = """✅ *Проверьте данные:*

{type_emoji} *Тип:* {driver_type}
👤 *ФИО:* {name}
📞 *Телефон:* {phone}

Всё верно? Напишите *Да* или *Нет*."""

DRIVER_REG_SUCCESS = """🎉 *Регистрация завершена!*

✅ Вы успешно зарегистрированы как {driver_type}.

💰 *Ваш баланс:* {balance} сом
⚠️ Для приёма заказов пополните баланс.

📌 *ВАЖНО:* Вступите в рабочую группу, чтобы видеть заказы:
{group_link}

💡 Команды:
/balance — Проверить баланс
/profile — Мой профиль"""

DRIVER_REG_ALREADY = """⚠️ *Вы уже зарегистрированы!*

{type_emoji} *Тип:* {driver_type}
👤 *ФИО:* {name}
📞 *Телефон:* {phone}
🚗 *Транспорт:* {car_model}
🔢 *Госномер:* {plate}
💰 *Баланс:* {balance} сом

Хотите обновить данные? Напишите /update"""

DRIVER_BALANCE_MSG = """💰 *Ваш баланс:* {balance} сом

{status}

📌 Для пополнения обратитесь к администратору."""

DRIVER_PROFILE_MSG = """👤 *Мой профиль*

{type_emoji} *Тип:* {driver_type}
👤 *ФИО:* {name}
📞 *Телефон:* {phone}
🚗 *Транспорт:* {car_model}
🔢 *Госномер:* {plate}
💰 *Баланс:* {balance} сом
📅 *Зарегистрирован:* {created_at}

📌 /update — Обновить данные
📌 /balance — Баланс"""

DRIVER_NOT_REGISTERED = """❌ *Вы не зарегистрированы!*

Для регистрации напишите /register"""

DRIVER_HELP_MSG = """❓ *Помощь по боту*

*Доступные команды:*

📝 /register — Регистрация нового водителя
💰 /balance — Проверить баланс
👤 /profile — Мой профиль
🔄 /update — Обновить данные
📊 /stats — Моя статистика
❓ /help — Это сообщение

*Как работать:*
1️⃣ Зарегистрируйтесь через /register
2️⃣ Пополните баланс (обратитесь к админу)
3️⃣ Вступите в рабочую группу
4️⃣ Берите заказы нажатием кнопки в группе
5️⃣ Комиссия списывается автоматически

📞 *Поддержка:* @admin_jardamchy"""


