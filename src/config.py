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

# Разнарабочий
RAZNARABOCHI_COMMISSION = 10  # Комиссия 10 сом за одного рабочего

# =============================================================================
# ТАЙМАУТЫ (в секундах)
# =============================================================================

CAFE_AUCTION_TIMEOUT = 120  # 2 минуты на аукцион кафе
PHARMACY_RESPONSE_TIMEOUT = 300  # 5 минут на ответ аптеки
TAXI_RESPONSE_TIMEOUT = 300  # 5 минут на ответ таксиста
RAZNARABOCHI_RESPONSE_TIMEOUT = 1800  # 30 минут на ответ разнорабочих
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
GROUP_POPUTKA_ID = os.getenv("GROUP_POPUTKA_ID", os.getenv("GROUP_TAXI_ID", "-100112233445"))
GROUP_RAZNARABOCHI_ID = os.getenv("GROUP_RAZNARABOCHI_ID", os.getenv("GROUP_PORTER_ID", "-100554433221"))

# ID закупщика (личные сообщения)
SHOPPER_TELEGRAM_ID = os.getenv("SHOPPER_TELEGRAM_ID", "")

# Тех поддержка
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "0226410410")
SUPPORT_TELEGRAM_ID = os.getenv("SUPPORT_TELEGRAM_ID", "")
MED_EJE_PHONE = os.getenv("MED_EJE_PHONE", "0 224 223 623")
MED_EJE_PHONE_2 = os.getenv("MED_EJE_PHONE_2", "0702 521 073")

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
OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY")
    or os.getenv("OPENAI_API_TOKEN")
    or os.getenv("OPENAI_TOKEN")
    or os.getenv("OPENAI_KEY")
    or ""
)
WHISPER_LANGUAGE = "ru"  # Русский язык
# Подсказка для Whisper — частые слова из контекста приложения помогают распознаванию
WHISPER_PROMPT = (
    "Такси, кафе, магазин, портер, муравей, груз, мука, "
    "Шамалды-Сай, базар, северная, южная, мечеть, школа, больница, "
    "заказ, цена, сом, нет, да, отмена, спасибо, "
    "светофор, от светофора, до светофора, "
    "Пушкина, Ленина, Адыл кафе, "
    "на трассу, на мертвый, "
    "номер 2, Магазин Миллион, миллиондон, миллионго, Глобус, от Глобуса, "
    "12-й, 12 чи, 12чи, он эки, "
    "номер 8, от Ленина, "
    "спортивная, Ак Кеме, центральная мечеть, двойняшки, Грин Хаус, ЖД, "
    "Салкын-Төр, Миң түркүн, Кумар аке, Адыр 2, Адыр, Адыр эки, Адыр экиден, Адыр экиге, "
    "Үй бүлөлүк магазин, Магазин 777, Көк магазин, "
    "Кеңешбек-Ата, Жээнмырза, Атакулов, Пионерская, Токтосун-Абайдуллаев, "
    "Фрунзе, Панфилова, Исанова, Кураева, Сергеева, Чынгыз Айтматов, "
    "Ыскак Раззаков, Тыныбекова, Орозбекова, Зулпукарова, Тарыкчиева, "
    "Солнечная, Нагорная, Горная, Лермонтова, Достук, Сыдыкова, "
    "Набережная, Дружба, Ынтымак, "
    "кайдан, кайда, базардан, базарга, ленинадан, ленинага, "
    "кочо, көчө, көчөсү, үй, уй, квартира, кв, "
    "такси керек, тамак керек, дары керек, жүк ташуу, желмаян, "
    "ташыш, ташуу, алып кет, алып бер, "
    "диван, холодильник, стиральная машина, кум, таш, мал, жыгач, уголь, ун, картошка, кирпич, цемент, жугору, арпа, буудай, жемиш, мешок, "
    "он, жыйырма, отуз, кырк, элүү, "
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
STATE_MED_EJE_MENU = "MED_EJE_MENU"

# Кафе
STATE_CAFE_ORDER = "CAFE_ORDER"
STATE_CAFE_ADDRESS = "CAFE_ADDRESS"
STATE_CAFE_PAYMENT = "CAFE_PAYMENT"
STATE_CAFE_WAIT_CONFIRM = "CAFE_WAIT_CONFIRM"
STATE_CAFE_DECLINE_REASON = "CAFE_DECLINE_REASON"
STATE_CAFE_OWN_COURIER_PHONE = "CAFE_OWN_COURIER_PHONE"

# Магазин
STATE_SHOP_LIST = "SHOP_LIST"
STATE_SHOP_ADDRESS = "SHOP_ADDRESS"
STATE_SHOP_CONFIRM = "SHOP_CONFIRM"
STATE_SHOP_WAIT_PRICE = "SHOP_WAIT_PRICE"
STATE_SHOP_DELIVERY_CHOICE = "SHOP_DELIVERY_CHOICE"

# Аптека
STATE_PHARMACY_WAIT_RX = "PHARMACY_WAIT_RX"             # Ждём название лекарства
STATE_PHARMACY_WAIT_PRICE = "PHARMACY_WAIT_PRICE"
STATE_PHARMACY_CONFIRM = "PHARMACY_CONFIRM"
STATE_PHARMACY_ADDRESS = "PHARMACY_ADDRESS"
STATE_PHARMACY_REORDER_CHOICE = "PHARMACY_REORDER_CHOICE"  # Повторить заказ: Ооба/Жок

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
STATE_POPUTKA_PHONE = "POPUTKA_PHONE"
STATE_POPUTKA_FROM = "POPUTKA_FROM"
STATE_POPUTKA_TO = "POPUTKA_TO"
STATE_POPUTKA_SEATS = "POPUTKA_SEATS"
STATE_POPUTKA_TIME = "POPUTKA_TIME"

# Клиентский запрос попутки (WhatsApp) — legacy, kept for backward compat
STATE_POPUTKA_CLIENT_DEST = "POPUTKA_CLIENT_DEST"
STATE_POPUTKA_CLIENT_DATE = "POPUTKA_CLIENT_DATE"
STATE_POPUTKA_CLIENT_SEATS = "POPUTKA_CLIENT_SEATS"

# 7 Область Такси (WhatsApp)
STATE_OBLAST_TYPE = "OBLAST_TYPE"       # Адам же жүк?
STATE_OBLAST_FROM = "OBLAST_FROM"       # Кайдан?
STATE_OBLAST_TO = "OBLAST_TO"           # Кайда?
STATE_OBLAST_PERSONS = "OBLAST_PERSONS" # Канча адам?
STATE_OBLAST_CARGO = "OBLAST_CARGO"     # Эмне жүк?
OBLAST_TYPE_PERSON_BUTTON_ID = "oblast_type_person"
OBLAST_TYPE_CARGO_BUTTON_ID = "oblast_type_cargo"

# Разнарабочий (WhatsApp)
STATE_RAZNARABOCHI_DESC = "RAZNARABOCHI_DESC"
STATE_RAZNARABOCHI_COUNT = "RAZNARABOCHI_COUNT"
STATE_RAZNARABOCHI_REORDER_CHOICE = "RAZNARABOCHI_REORDER_CHOICE"

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

# Способ доставки
DELIVERY_MODE_JARDAMCHY_GO = "JARDAMCHY_GO"
DELIVERY_MODE_SELF = "SELF"

# =============================================================================
# ТИПЫ УСЛУГ
# =============================================================================

SERVICE_CAFE = "cafe"
SERVICE_SHOP = "shop"
SERVICE_PHARMACY = "pharmacy"
SERVICE_TAXI = "taxi"
SERVICE_PORTER = "porter"
SERVICE_ANT = "ant"
SERVICE_MED_EJE = "med_eje"
SERVICE_SUPPORT = "support"
SERVICE_COMPUTER = "computer"
SERVICE_POPUTKA = "poputka"
SERVICE_PLUMBING = "plumbing"
SERVICE_MASTER = "master"
SERVICE_RAZNARABOCHI = "raznarabochi"
SERVICE_DIRECTORY = "directory"

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

WELCOME_MESSAGE = """👋 Салам! Мен *Жардамчы GO* - кызмат көрсөтүүчү ботмун!

📍 *Шамалды-Сай*

Төмөнкү кызматтар бар:

🍔 *1. Кафе* - даяр тамак
🛒 *2. Азык-түлүк* - доставка
🚕 *3. Такси* - тез жеткирүү
🚛 *4. Портер* - чоң жүк
🐜 *5. Желмаян* - майда жүк
👩‍⚕️ *6. Мед эже* - медициналык жардам
💻 *7. Компьютердик жардам*
🚐 *8. 7 область такси*
🔧 *9. Мастер кызматы*
👷 *10. Разнарабочий*
📖 *11. Маалымдама* - Шамалдуу-Сай дагы эң керектүү номерлер

Номер же сөзүн жаза бериңиз.

📞 *Суроо болсо* операторго чалыңыз: *0226 410 410*"""

PHARMACY_DISABLED_MESSAGE = """💊 *Аптека кызматы мындан ары жеткиликсиз.*

Башка кызмат тандаңыз."""

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

SHOP_ADDRESS_PROMPT = """📍 *Жеткирүү дареги*

Заказ кайда жеткирилсин? Даректи жазыңыз."""

SHOP_CONFIRM_PROMPT = """🛒 *Тастыктоо*

✅ Тастыктайсызбы?

Жазыңыз: *Ооба* же *Жок*"""

# Аптека
PHARMACY_PROMPT = """💊 *Дарыкана*

Дарынын атын жазыңыз"""

PHARMACY_SEARCHING = """💊 *Дары издеп жатабыз...*

Суроо дарыканаларга жөнөтүлдү.

⏱ Баа сунуштарын күтөбүз (5 мүнөт)..."""

PHARMACY_NO_RESPONSE_CLIENT = """😔 *Кечиресиз, жооп жок*

5 мүнөт ичинде эч бир дарыкана жооп берген жок.
Заказыңыз жокко чыгарылды.

🔄 Заказды кайра жөнөтүүнү каалайсызбы?"""

PHARMACY_NO_RESPONSE_TELEGRAM = """❌ *ЗАКАЗ ЖОККО ЧЫГАРЫЛДЫ*

Эч ким 5 мүнөт ичинде жооп берген жок."""

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

📍 *Дарек:* {address}
🚗 *Жеткирүү:* таксист менен сүйлөшүү

✅ Баары туурабы? Тастыктайсызбы?"""

CONFIRM_PHARMACY = """💊 *Дарыкана*

📝 *Суроо:* {order_details}

✅ Баары туурабы? Тастыктайсызбы?"""

CONFIRM_PORTER = """🚛 *Жүк ташуу*

🛣 *Маршрут:* {from_address} — {to_address}{cargo_line}

✅ Баары туурабы? Тастыктайсызбы?"""

CONFIRM_ANT = """🐜 *Желмаян*

🛣 *Маршрут:* {from_address} — {to_address}{cargo_line}

✅ Баары туурабы? Тастыктайсызбы?"""

ORDER_CANCELLED = """❌ *Заказ жокко чыгарылды*

Кайра заказ кылгыңыз келсе жазыңыз."""

ORDER_SENT_GENERIC = """✅ *Заказ жөнөтүлдү!*

⏱ Жооп күтөбүз..."""

MED_EJE_MESSAGE = """🩺 *Мед Эже - Медициналык жардам*

МЕДСЕСТРА НА ДОМ
Внутримышечно — 50
В/В струйно — 50
В/В капельно 100,0 (обычный) — 100
В/В капельно 100,0–200,0 (сложный) — 200
В/В капельно 400,0–500,0 — 250
Вызов — 200"""

MED_EJE_PHONE_MESSAGE = """🩺 *Мед Эже*

📞 *1.* {phone_1}
📞 *2.* {phone_2}"""

SUPPORT_TO_CLIENT = """🆘 *Колдоо кызматы*

📞 {support_phone}

Бизге чалыңыз же жазыңыз — жардам беребиз!"""

SUPPORT_TO_OPERATOR = """🆘 *Запрос помощи*

👤 Клиент: {client_phone}

Клиент запросил тех поддержку."""

COMPUTER_SERVICES_MESSAGE = """💻 *Компьютердик кызматтар*

🖨 *Полиграфия*
Визитка, баннер, стенд жасоо,самоклейка, көлөмдүү тамгалар, DTF.

📸 *Сүрөт жана видео*
Той видео, фотосессия, виньетка, жарнамалык видео, бизнес тартуу.

📞 *Байланыш:* 220 122 232"""

POPUTKA_COMMISSION = 20  # сом, комиссия за 1 человека / за груз

# =============================================================================
# 7 ОБЛАСТЬ ТАКСИ
# =============================================================================

OBLAST_TAXI_FIRST_MSG = """🚐 *7 область такси*

Адам же жүк?

👤 *Адам:* 20 сом / адам
📦 *Жүк:* 20 сом

Мисалы: *Шамалды-Сайдан Ошко 2 адам* же *Ошто телефон алып берүү*"""

OBLAST_TAXI_PERSON_ROUTE_PROMPT = """🚐 *7 область такси*

Кайдан кайда барасыз?

Мисалы: *Шамалды-Сайдан Ошко 2 адам*"""

OBLAST_TAXI_CARGO_ROUTE_PROMPT = """📦 *7 область такси — жүк*

Кайдан кайда жана эмне жеткирүү керек?

Мисалы: *Шамалды-Сайдан Ошко телефон алып баруу*"""

OBLAST_TAXI_FROM_PROMPT = """📍 Кайдан чыгасыз?

Мисалы: *Шамалды-Сай*, *Ош*, *Бишкек*"""

OBLAST_TAXI_TO_PROMPT = """📍 Кайда барасыз?

Мисалы: *Ош*, *Бишкек*, *Жалал-Абад*"""

OBLAST_TAXI_PERSONS_PROMPT = """👤 Канча адам?

Сан менен жазыңыз. Мисалы: *1*, *2*, *3*"""

OBLAST_TAXI_CARGO_PROMPT = """📦 Эмне жеткирүү керек?

Кыскача жазыңыз. Мисалы: *телефон*, *буюм*, *пакет*"""

OBLAST_TAXI_CONFIRM_PERSON = """🚐 *7 область такси*

📍 Кайдан: {from_addr}
📍 Кайда: {to_addr}
👤 Киши: {persons}
💰 Комиссия: {commission} сом

Ырастайсызбы?"""

OBLAST_TAXI_CONFIRM_CARGO = """📦 *7 область такси — жүк*

📍 Кайдан: {from_addr}
📍 Кайда: {to_addr}
📦 Жүк: {cargo_desc}
💰 Комиссия: {commission} сом

Ырастайсызбы?"""

OBLAST_TAXI_GROUP_PERSON = """🚐 *7 ОБЛАСТЬ ТАКСИ #{order_id}*

📍 Кайдан: {from_addr}
📍 Кайда: {to_addr}
👤 Киши: {persons}
💰 Комиссия: {commission} сом
📞 Телефон: {client_phone}"""

OBLAST_TAXI_GROUP_CARGO = """📦 *7 ОБЛАСТЬ ТАКСИ (ЖҮК) #{order_id}*

📍 Кайдан: {from_addr}
📍 Кайда: {to_addr}
📦 Жүк: {cargo_desc}
💰 Комиссия: {commission} сом
📞 Телефон: {client_phone}"""

OBLAST_TAXI_SENT = """✅ *Суроо жиберилди!*

Айдоочу табылганда сизге кабарлайбыз."""

# Legacy (kept for Telegram driver registration flow)
POPUTKA_CLIENT_DEST_PROMPT = OBLAST_TAXI_FIRST_MSG
POPUTKA_CLIENT_DATE_PROMPT = ""
POPUTKA_CLIENT_SEATS_PROMPT = ""
POPUTKA_CLIENT_CONFIRM = ""
POPUTKA_CLIENT_SENT = OBLAST_TAXI_SENT
POPUTKA_CLIENT_DRIVER_FOUND = """✅ *7 область такси табылды!*

👤 Айдоочу: {driver_name}
📞 Телефон: {driver_phone}
🚗 Унаа: {car_info}

Байланышыңыз!"""

POPUTKA_GROUP_MSG = OBLAST_TAXI_GROUP_PERSON

POPUTKA_MESSAGE = """🚐 *7 область такси*

Маршрут жана эмне керек экенин жазыңыз.

Оператор жардам берет.
📞 {support_phone}"""

PLUMBING_MESSAGE = """🔧 *Сантехника*

Опишите проблему и напишите адрес.

Оператор передаст заявку мастеру.
📞 {support_phone}"""

MASTER_MESSAGE = """🔧 *Мастер чакыруу*

Төмөнкү мастерлерге түздөн-түз чалсаңыз болот:

📞 *Мастер 1:* 0777 777 888
📞 *Мастер 2:* 0777 777 888
📞 *Мастер 3:* 0777 777 888
📞 *Мастер 4:* 0777 777 888
📞 *Мастер 5:* 0777 777 888
📞 *Мастер 6:* 0777 777 888
📞 *Мастер 7:* 0777 777 888
📞 *Мастер 8:* 0777 777 888
📞 *Мастер 9:* 0777 777 888
📞 *Мастер 10:* 0777 777 888
📞 *Мастер 11:* 0777 777 888
📞 *Мастер 12:* 0777 777 888
📞 *Мастер 13:* 0777 777 888
📞 *Мастер 14:* 0777 777 888
📞 *Мастер 15:* 0777 777 888"""

DIRECTORY_EMPTY_MSG = """📖 *Маалымдама*

Азырынча маалымат жок.

Администраторго кайрылыңыз."""

DIRECTORY_HEADER = """📖 *Маалымдама — Шамалдуу-Сай*

Шамалдуу-Сай дагы эң керектүү номерлер:

"""

RAZNARABOCHI_COMMISSION = 10  # дублируется для удобства шаблонов

RAZNARABOCHI_DESC_PROMPT = """👷 *Разнарабочий*

Эмне иш жана канча адам керек экенин жазыңыз.

Мисалы: *2 адам, көмүр түшүрүш керек* же *3 киши, жер казуу*"""

RAZNARABOCHI_JOB_PROMPT = """👷 *Разнарабочий*

Кандай иш экенин жазыңыз.

Мисалы: *1 фура ун түшүрүш керек*"""

RAZNARABOCHI_COUNT_PROMPT = """👷 *Разнарабочий*

Канча адам керек?

Мисалы: *5 адам*"""

RAZNARABOCHI_CONFIRM = """👷 *Разнарабочий — суроо*

📋 Иш: {desc}
👥 Керек адам: {workers_count}

Заказды жиберейинби?"""

RAZNARABOCHI_SENT = """✅ *Суроо жиберилди!*

Жумушчу табылганда сизге кабарлайбыз."""

RAZNARABOCHI_GROUP_MSG = """👷 *РАЗНАРАБОЧИЙ #{order_id}*

📋 Иш: {desc}
👥 Керек адам: {workers_count}
💰 Комиссия: {commission} сом"""

RAZNARABOCHI_WORKER_FOUND = """✅ *Жумушчу табылды!*

👤 Аты: {worker_name}
📞 Телефон: {worker_phone}

Байланышыңыз!"""

SPECIALIST_REQUEST_TO_OPERATOR = """🛎 *Новая заявка: {service_name}*

👤 Клиент: {client_phone}

Клиент выбрал новый раздел в WhatsApp-меню."""

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

Бир билдирүү менен жазыңыз:

📍 *КАЙДАН* жана *КАЙДА*?

Мисал:
Чынгыз Айтматовдон Достукка"""

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

🛣 *Маршрут:* {route}{cargo_line}

💵 *Цена / Баа:* Договорная / Сүйлөшүүлүү

🚛 *КТО ВОЗЬМЕТ? / КИМ АЛАТ?*"""

# Муравей
ANT_ORDER_TELEGRAM = """🐜 *МУРАВЕЙ / ЖЕЛМАЯН*

🛣 *Маршрут:* {route}{cargo_line}

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
    "ant": "🐜 Муравей (мелкие грузы)",
    "scooter": "🛵 Скутер (доставка)",
    "raznarabochi": "👷 Разнарабочий",
    "poputka": "🚘 Попутка"
}

# Типы водителей, которым разрешено брать только заказы доставки (не такси)
DELIVERY_ONLY_DRIVER_TYPES = {"scooter"}

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
🛵 *4.* Скутер (доставка еды/товаров)
👷 *5.* Разнарабочий (подработка)
🚘 *6.* Попутка (межгород/попутчики)

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

DRIVER_REG_CONFIRM_TEMPLATE_SCOOTER = """✅ *Проверьте данные:*

🛵 *Тип:* Скутер (доставка)
👤 *ФИО:* {name}
📞 *Телефон:* {phone}

⚠️ Скутеры могут брать только заказы доставки еды и товаров из магазина.

Всё верно? Напишите *Да* или *Нет*."""

DRIVER_REG_CONFIRM_TEMPLATE_RAZNARABOCHI = """✅ *Проверьте данные:*

👷 *Тип:* Разнарабочий
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
🚘 /poputka — Добавить ближайший выезд в список попуток
❓ /help — Это сообщение

*Как работать:*
1️⃣ Зарегистрируйтесь через /register
2️⃣ Пополните баланс (обратитесь к админу)
3️⃣ Вступите в рабочую группу
4️⃣ Берите заказы нажатием кнопки в группе
5️⃣ Комиссия списывается автоматически

📞 *Поддержка:* @admin_jardamchy"""

POPUTKA_DRIVER_START = """🚘 *Попутка*

Азыр сиздин жакынкы чыгууңузду WhatsApp тизмесине кошобуз.

*Кайдан* чыгасыз?"""

POPUTKA_DRIVER_PHONE_PROMPT = """🚘 *Попутка*

Телефон номериңизди жазыңыз же *Контактты бөлүшүү* баскычын басыңыз."""

POPUTKA_DRIVER_INVALID_PHONE = """⚠️ Телефон номери туура эмес.

Мисал: *0555123456*"""

POPUTKA_DRIVER_TO_PROMPT = """🚘 *Попутка*

*Кайда* барасыз?"""

POPUTKA_DRIVER_SEATS_PROMPT = """🚘 *Попутка*

Канча орун бар?

Сан менен гана жазыңыз.
Мисалы: 2"""

POPUTKA_DRIVER_TIME_PROMPT = """🚘 *Попутка*

Бүгүн *саат канчада* чыгасыз?

Формат: *HH:MM*
Мисалы: 19:00"""

POPUTKA_DRIVER_SUCCESS = """✅ *Попутка кошулду*

📍 Кайдан: {from_address}
📍 Кайда: {to_address}
👥 Орун: {seats}
🕒 Чыгуу: {departure_time}

Бул маалымат WhatsApp тизмесинде чыгуу убактысына чейин көрүнөт."""

POPUTKA_DRIVER_INVALID_TIME = """⚠️ Убакытты *HH:MM* форматында жана бүгүнкүгө гана жазыңыз.

Мисалы: 19:00"""

POPUTKA_DRIVER_INVALID_SEATS = """⚠️ Орун санын цифра менен жазыңыз.

Мисалы: 2"""

POPUTKA_LIST_EMPTY = """🚘 *Попутка*

Азыр жакын арада чыгуучу айдоочу жок.

Кийинчерээк кайра текшериңиз."""

POPUTKA_LIST_HEADER = """🚘 *Жакынкы попуткалар*

Төмөндө жакын арада чыгуучу айдоочулар:"""


