"""
Р’СЃРїРѕРјРѕРіР°С‚РµР»СЊРЅС‹Рµ СЃРµСЂРІРёСЃС‹
Services Module for Business Assistant GO
РћР±РЅРѕРІР»РµРЅРЅР°СЏ РІРµСЂСЃРёСЏ СЃРѕРіР»Р°СЃРЅРѕ РўР— v2.0
"""

import json
import re
import requests
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlencode

import config

WHATSAPP_CANCEL_BUTTON_ID = "btn_cancel_global"
WHATSAPP_CANCEL_BUTTON_TEXT = "❌ Отмена"
WHATSAPP_MAIN_MENU_BUTTON_ID = "btn_main_menu"
WHATSAPP_MAIN_MENU_BUTTON_TEXT = "🏠 Башкы меню"
NO_CANCEL_MESSAGE_PHRASES = (
    "заказ отмен",
    "заказ отменё",
    "заказ жокко",
    "доставка отмен",
    "доставка жокко",
)

STT_SERVICE_KEYWORDS = (
    "такси", "taxi", "унаа", "машина", "кайдан", "кайда",
    "кафе", "тамак", "оокат", "меню", "жейм",
    "магазин", "дүкөн", "продукт", "товар",
    "аптека", "дарыкана", "дары", "лекарство",
    "портер", "жүк", "жук", "груз", "ташыш", "ташуу",
    "муравей", "желмаян",
    # Типы груза — помогают выбрать лучшую транскрипцию
    "диван", "холодильник", "мебель", "кум", "таш", "мал", "жыгач", "уголь",
    "ун", "жугору", "арпа", "буудай", "жемиш", "мешок", "кирпич", "цемент",
)
STT_ADDRESS_KEYWORDS = (
    # Основные ориентиры
    "базар", "жд", "микрорайон", "адыр", "ынтымак",
    # Улицы / районы — основные
    "северная", "южная", "пушкина", "ленина", "аксы",
    "орозбекова", "набережная", "достук",
    "айтматов", "раззаков", "панфилова", "фрунзе",
    # Улицы — дополнительные (из нормализации NLU)
    "нагорная", "солнечная", "горная", "лермонтова",
    "сыдыкова", "дружба", "зулпукарова", "тарыкчиева",
    "тыныбекова", "исанова", "кураева", "сергеева",
    "пионерская", "токтосун",
    # Местные ориентиры и магазины
    "салкын", "миң", "кумар", "кеңешбек", "жээнмырза", "атакулов",
    "миллион", "самурай", "777", "глобус", "ак кеме",
)
STT_NOISE_MARKERS = (
    "[ошибка", "[распозна", "[error", "[recognition"
)


# =============================================================================
# WHATSAPP SERVICES (GREEN API + Twilio)
# =============================================================================

def _with_cancel_button(buttons: List[Dict]) -> List[Dict]:
    """Add global cancel button if there is free slot (max 3 buttons)."""
    safe_buttons = list(buttons or [])
    if any((btn or {}).get("id") == WHATSAPP_CANCEL_BUTTON_ID for btn in safe_buttons):
        return safe_buttons[:3]
    if len(safe_buttons) < 3:
        safe_buttons.append({"id": WHATSAPP_CANCEL_BUTTON_ID, "text": WHATSAPP_CANCEL_BUTTON_TEXT})
    return safe_buttons[:3]

def _is_plain_whatsapp_message(message: str) -> bool:
    """Messages that must be sent without the global cancel button."""
    message_text = (message or "").strip()
    if not message_text:
        return True
    if message_text == (config.WELCOME_MESSAGE or "").strip():
        return True
    if message_text == (config.ORDER_CANCELLED or "").strip():
        return True
    message_lower = message_text.lower()
    if any(phrase in message_lower for phrase in NO_CANCEL_MESSAGE_PHRASES):
        return True
    # Auto-cancel and similar cancellation notifications for orders/deliveries.
    if "отмен" in message_lower and ("заказ" in message_lower or "доставка" in message_lower):
        return True
    return False

def send_whatsapp(phone: str, message: str) -> bool:
    """Send message to WhatsApp"""
    if _is_plain_whatsapp_message(message):
        return send_whatsapp_plain(phone, message)
    return send_whatsapp_buttons(
        phone,
        message,
        [{"id": WHATSAPP_CANCEL_BUTTON_ID, "text": WHATSAPP_CANCEL_BUTTON_TEXT}]
    )


def send_whatsapp_plain(phone: str, message: str) -> bool:
    """Send plain text WhatsApp message without interactive buttons."""
    if config.WHATSAPP_PROVIDER == "cloud":
        return _send_whatsapp_cloud(phone, message)
    if config.WHATSAPP_PROVIDER == "twilio":
        return _send_whatsapp_twilio(phone, message)
    return _send_whatsapp_green(phone, message)


def send_order_cancelled_with_main_menu(phone: str) -> bool:
    """Send cancelled-order message with a dedicated main-menu button."""
    buttons = [{"id": WHATSAPP_MAIN_MENU_BUTTON_ID, "text": WHATSAPP_MAIN_MENU_BUTTON_TEXT}]
    if config.WHATSAPP_PROVIDER == "cloud":
        return send_whatsapp_buttons(phone, config.ORDER_CANCELLED, buttons, include_cancel=False)
    return send_whatsapp_plain(phone, config.ORDER_CANCELLED)



def _send_whatsapp_green(phone: str, message: str) -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ С‡РµСЂРµР· GREEN API"""
    try:
        url = f"{config.GREEN_API_URL}/sendMessage/{config.GREEN_API_TOKEN}"
        
        phone_clean = _clean_phone(phone)
        
        payload = {
            "chatId": f"{phone_clean}@c.us",
            "message": message
        }
        
        headers = {'Content-Type': 'application/json'}
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            print(f"[GREEN API] Message sent to {phone}")
            return True
        else:
            print(f"[GREEN API] Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"[GREEN API] Exception: {e}")
        return False


def _send_whatsapp_twilio(phone: str, message: str) -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ С‡РµСЂРµР· Twilio"""
    try:
        from twilio.rest import Client
        
        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        
        phone_clean = _clean_phone(phone)
        if not phone_clean.startswith('+'):
            phone_clean = '+' + phone_clean
        
        message = client.messages.create(
            from_=f"whatsapp:{config.TWILIO_PHONE_NUMBER}",
            body=message,
            to=f"whatsapp:{phone_clean}"
        )
        
        print(f"[Twilio] Message sent to {phone}, SID: {message.sid}")
        return True
        
    except Exception as e:
        print(f"[Twilio] Exception: {e}")
        return False


def _send_whatsapp_cloud(phone: str, message: str) -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ С‚РµРєСЃС‚РѕРІРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ С‡РµСЂРµР· WhatsApp Cloud API (Meta Graph API)"""
    try:
        url = (
            f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}"
            f"/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {
            "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        phone_clean = _clean_phone(phone)
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_clean,
            "type": "text",
            "text": {"body": message, "preview_url": False},
        }
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            sent_id = (response.json().get('messages') or [{}])[0].get('id') or None
            try:
                from db import get_db as _get_db
                _get_db().save_message(phone=phone, direction='out', body=message,
                                       msg_type='text', wa_message_id=sent_id)
            except Exception:
                pass
            print(f"[Cloud API] Message sent to {phone}")
            return True
        else:
            print(f"[Cloud API] Error: {response.text}")
            return False
    except Exception as e:
        print(f"[Cloud API] Exception: {e}")
        return False


def _send_whatsapp_buttons_cloud(phone: str, message: str, buttons: List[Dict]) -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РёРЅС‚РµСЂР°РєС‚РёРІРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ СЃ РєРЅРѕРїРєР°РјРё С‡РµСЂРµР· Cloud API (РјР°РєСЃ 3, Р·Р°РіРѕР»РѕРІРѕРє РјР°РєСЃ 20 СЃРёРјРІ)"""
    try:
        url = (
            f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}"
            f"/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {
            "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        phone_clean = _clean_phone(phone)

        cloud_buttons = []
        for btn in buttons[:3]:
            btn_id = (btn.get("id") or btn.get("text") or "btn")[:256]
            btn_title = btn.get("text", "")[:20]
            cloud_buttons.append({"type": "reply", "reply": {"id": btn_id, "title": btn_title}})

        payload = {
            "messaging_product": "whatsapp",
            "to": phone_clean,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": message},
                "action": {"buttons": cloud_buttons},
            },
        }
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            sent_id = (response.json().get('messages') or [{}])[0].get('id') or None
            try:
                from db import get_db as _get_db
                btn_titles = ' / '.join(b['reply']['title'] for b in cloud_buttons)
                _get_db().save_message(phone=phone, direction='out',
                                       body=f"{message}\n[РљРЅРѕРїРєРё: {btn_titles}]",
                                       msg_type='interactive', wa_message_id=sent_id)
            except Exception:
                pass
            return True
        else:
            print(f"[Cloud API Buttons] Error: {response.text}, fallback to plain text")
            return _send_whatsapp_cloud(phone, message)
    except Exception as e:
        print(f"[Cloud API Buttons] Exception: {e}")
        return False


def _send_whatsapp_image_cloud(phone: str, image_url: str, caption: str = "") -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РёР·РѕР±СЂР°Р¶РµРЅРёРµ С‡РµСЂРµР· Cloud API РїРѕ РїСѓР±Р»РёС‡РЅРѕРјСѓ URL"""
    try:
        url = (
            f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}"
            f"/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {
            "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        phone_clean = _clean_phone(phone)
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_clean,
            "type": "image",
            "image": {"link": image_url, "caption": caption},
        }
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 200
    except Exception as e:
        print(f"[Cloud API Image] Exception: {e}")
        return False


def _download_cloud_media_bytes(media_id: str) -> Optional[bytes]:
    """РЎРєР°С‡Р°С‚СЊ РјРµРґРёР° РёР· Cloud API РїРѕ media_id (С‚СЂРµР±СѓРµС‚ Bearer С‚РѕРєРµРЅ)"""
    try:
        # РЁР°Рі 1: РїРѕР»СѓС‡Р°РµРј download URL РёР· media_id
        meta_url = f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{media_id}"
        headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
        meta_resp = requests.get(meta_url, headers=headers, timeout=30)
        if meta_resp.status_code != 200:
            print(f"[Cloud API Media] Error getting URL for {media_id}: {meta_resp.text}")
            return None
        download_url = meta_resp.json().get("url")
        if not download_url:
            print(f"[Cloud API Media] No URL in response for {media_id}")
            return None

        # РЁР°Рі 2: СЃРєР°С‡РёРІР°РµРј Р±Р°Р№С‚С‹ СЃ Р°РІС‚РѕСЂРёР·Р°С†РёРµР№
        dl_resp = requests.get(download_url, headers=headers, timeout=60)
        if dl_resp.status_code != 200:
            print(f"[Cloud API Media] Download failed {dl_resp.status_code}")
            return None
        return dl_resp.content
    except Exception as e:
        print(f"[Cloud API Media] Exception: {e}")
        return None


def send_confirmation_buttons(phone: str) -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РєРЅРѕРїРєРё 'Р”Р° / РќРµС‚' РґР»СЏ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ Р·Р°РєР°Р·Р° (Cloud API)"""
    if config.WHATSAPP_PROVIDER != "cloud":
        return False
    buttons = [
        {"id": "confirm_yes", "text": "✅ Ооба"},
        {"id": "confirm_no", "text": "❌ Жок"},
    ]
    return _send_whatsapp_buttons_cloud(phone, "Тастыктайсызбы?", buttons)


def send_whatsapp_url_button(phone: str, message: str, btn_text: str, url: str) -> bool:
    """Отправить сообщение с кнопкой-ссылкой в WhatsApp.
    Cloud API: тип cta_url (одна URL-кнопка).
    Green API: templateButtons с urlButton.
    Fallback: plain text с URL.
    """
    if config.WHATSAPP_PROVIDER == "cloud":
        return _send_whatsapp_cta_url_cloud(phone, message, btn_text, url)
    if config.WHATSAPP_PROVIDER == "green":
        return _send_whatsapp_url_button_green(phone, message, btn_text, url)
    # twilio / fallback
    return send_whatsapp_plain(phone, f"{message}\n{url}")


def _send_whatsapp_cta_url_cloud(phone: str, message: str, btn_text: str, url: str) -> bool:
    """Cloud API: интерактивное сообщение типа cta_url (одна кнопка-ссылка)."""
    try:
        api_url = (
            f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}"
            f"/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {
            "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        phone_clean = _clean_phone(phone)
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_clean,
            "type": "interactive",
            "interactive": {
                "type": "cta_url",
                "body": {"text": message},
                "action": {
                    "name": "cta_url",
                    "parameters": {
                        "display_text": btn_text[:20],
                        "url": url,
                    },
                },
            },
        }
        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            try:
                from db import get_db as _get_db
                sent_id = (response.json().get("messages") or [{}])[0].get("id") or None
                _get_db().save_message(
                    phone=phone, direction="out",
                    body=f"{message}\n[Кнопка: {btn_text} → {url}]",
                    msg_type="interactive", wa_message_id=sent_id,
                )
            except Exception:
                pass
            return True
        print(f"[Cloud CTA URL] Error {response.status_code}: {response.text}")
        return send_whatsapp_plain(phone, f"{message}\n{url}")
    except Exception as e:
        print(f"[Cloud CTA URL] Exception: {e}")
        return send_whatsapp_plain(phone, f"{message}\n{url}")


def _send_whatsapp_url_button_green(phone: str, message: str, btn_text: str, url: str) -> bool:
    """Green API: templateButtons с urlButton."""
    try:
        api_url = f"{config.GREEN_API_URL}/sendTemplateButtons/{config.GREEN_API_TOKEN}"
        phone_clean = _clean_phone(phone)
        payload = {
            "chatId": f"{phone_clean}@c.us",
            "message": message,
            "templateButtons": [
                {
                    "index": 1,
                    "urlButton": {"displayText": btn_text, "url": url},
                    "callButton": None,
                    "quickReplyButton": None,
                }
            ],
        }
        response = requests.post(api_url, json=payload, timeout=30)
        return response.status_code == 200
    except Exception as e:
        print(f"[Green URL Button] Exception: {e}")
        return send_whatsapp_plain(phone, f"{message}\n{url}")


def send_whatsapp_buttons(phone: str, message: str, buttons: List[Dict], include_cancel: bool = True) -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РёРЅС‚РµСЂР°РєС‚РёРІРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ СЃ РєРЅРѕРїРєР°РјРё РІ WhatsApp"""
    try:
        if include_cancel:
            buttons = _with_cancel_button(buttons)
        if config.WHATSAPP_PROVIDER == "cloud":
            return _send_whatsapp_buttons_cloud(phone, message, buttons)
        elif config.WHATSAPP_PROVIDER == "twilio":
            return _send_whatsapp_buttons_twilio(phone, message, buttons)
        else:
            return _send_whatsapp_buttons_green(phone, message, buttons)
    except Exception as e:
        print(f"Error sending WhatsApp buttons: {e}")
        return False


def _send_whatsapp_buttons_green(phone: str, message: str, buttons: List[Dict]) -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РєРЅРѕРїРєРё С‡РµСЂРµР· GREEN API"""
    try:
        url = f"{config.GREEN_API_URL}/sendTemplateButtons/{config.GREEN_API_TOKEN}"
        
        phone_clean = _clean_phone(phone)
        
        template_buttons = []
        for idx, btn in enumerate(buttons):
            template_buttons.append({
                "index": idx,
                "urlButton": None,
                "callButton": None,
                "quickReplyButton": {
                    "displayText": btn["text"],
                    "id": btn.get("id", f"btn_{idx}")
                }
            })
        
        payload = {
            "chatId": f"{phone_clean}@c.us",
            "message": message,
            "templateButtons": template_buttons
        }
        
        headers = {'Content-Type': 'application/json'}
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 200
        
    except Exception as e:
        print(f"[GREEN API Buttons] Exception: {e}")
        return False


def _send_whatsapp_buttons_twilio(phone: str, message: str, buttons: List[Dict]) -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РєРЅРѕРїРєРё С‡РµСЂРµР· Twilio (РёСЃРїРѕР»СЊР·СѓРµРј СЃРїРёСЃРѕРє СЃ РЅРѕРјРµСЂР°РјРё)"""
    try:
        # Twilio РЅРµ РїРѕРґРґРµСЂР¶РёРІР°РµС‚ РЅР°С‚РёРІРЅС‹Рµ РєРЅРѕРїРєРё, РѕС‚РїСЂР°РІР»СЏРµРј РєР°Рє С‚РµРєСЃС‚ СЃ РЅСѓРјРµСЂР°С†РёРµР№
        button_text = "\n\n"
        for idx, btn in enumerate(buttons, 1):
            button_text += f"{idx}. {btn['text']}\n"
        
        full_message = message + button_text + "\nРћС‚РІРµС‚СЊС‚Рµ РЅРѕРјРµСЂРѕРј РІР°СЂРёР°РЅС‚Р°."
        
        return _send_whatsapp_twilio(phone, full_message)
        
    except Exception as e:
        print(f"[Twilio Buttons] Exception: {e}")
        return False


def send_whatsapp_image(phone: str, image_url: str, caption: str = "") -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РёР·РѕР±СЂР°Р¶РµРЅРёРµ РІ WhatsApp"""
    try:
        if config.WHATSAPP_PROVIDER == "cloud":
            return _send_whatsapp_image_cloud(phone, image_url, caption)
        elif config.WHATSAPP_PROVIDER == "twilio":
            return _send_whatsapp_image_twilio(phone, image_url, caption)
        else:
            return _send_whatsapp_image_green(phone, image_url, caption)
    except Exception as e:
        print(f"Error sending WhatsApp image: {e}")
        return False


def _send_whatsapp_image_green(phone: str, image_url: str, caption: str = "") -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РёР·РѕР±СЂР°Р¶РµРЅРёРµ С‡РµСЂРµР· GREEN API"""
    try:
        url = f"{config.GREEN_API_URL}/sendFileByUrl/{config.GREEN_API_TOKEN}"
        
        phone_clean = _clean_phone(phone)
        
        payload = {
            "chatId": f"{phone_clean}@c.us",
            "urlFile": image_url,
            "fileName": "image.jpg",
            "caption": caption
        }
        
        headers = {'Content-Type': 'application/json'}
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 200
        
    except Exception as e:
        print(f"[GREEN API Image] Exception: {e}")
        return False


def _send_whatsapp_image_twilio(phone: str, image_url: str, caption: str = "") -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РёР·РѕР±СЂР°Р¶РµРЅРёРµ С‡РµСЂРµР· Twilio"""
    try:
        from twilio.rest import Client
        
        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        
        phone_clean = _clean_phone(phone)
        if not phone_clean.startswith('+'):
            phone_clean = '+' + phone_clean
        
        message = client.messages.create(
            from_=f"whatsapp:{config.TWILIO_PHONE_NUMBER}",
            body=caption,
            media_url=[image_url],
            to=f"whatsapp:{phone_clean}"
        )
        
        return True
        
    except Exception as e:
        print(f"[Twilio Image] Exception: {e}")
        return False


def send_whatsapp_location(phone: str, latitude: float, longitude: float, 
                           name: str = "", address: str = "") -> bool:
    """РћС‚РїСЂР°РІРёС‚СЊ РіРµРѕР»РѕРєР°С†РёСЋ РІ WhatsApp"""
    try:
        if config.WHATSAPP_PROVIDER == "green":
            url = f"{config.GREEN_API_URL}/sendLocation/{config.GREEN_API_TOKEN}"
            
            phone_clean = _clean_phone(phone)
            
            payload = {
                "chatId": f"{phone_clean}@c.us",
                "latitude": latitude,
                "longitude": longitude,
                "name": name,
                "address": address
            }
            
            headers = {'Content-Type': 'application/json'}
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            return response.status_code == 200
        else:
            # Twilio РЅРµ РїРѕРґРґРµСЂР¶РёРІР°РµС‚ РѕС‚РїСЂР°РІРєСѓ Р»РѕРєР°С†РёРё РЅР°РїСЂСЏРјСѓСЋ
            location_url = f"https://maps.google.com/?q={latitude},{longitude}"
            return send_whatsapp(phone, f"рџ“Ќ Р›РѕРєР°С†РёСЏ: {location_url}")
            
    except Exception as e:
        print(f"Error sending location: {e}")
        return False


# =============================================================================
# TELEGRAM SERVICES
# =============================================================================

def send_telegram_message(chat_id: str, message: str, 
                          buttons: Optional[List[Dict]] = None,
                          parse_mode: str = "Markdown") -> Optional[Dict]:
    """РћС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ РІ Telegram"""
    try:
        url = f"{config.TELEGRAM_API_URL}/sendMessage"
        
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode
        }
        
        if buttons:
            inline_keyboard = []
            for btn in buttons:
                inline_keyboard.append([{
                    "text": btn["text"],
                    "callback_data": btn["callback"]
                }])
            
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
        
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code == 200:
            return response.json().get("result")
        else:
            print(f"Telegram error: {response.text}")
            return None
            
    except Exception as e:
        print(f"Exception sending Telegram message: {e}")
        return None


def send_telegram_group(chat_id: str, message: str, 
                        buttons: Optional[List[Dict]] = None) -> Optional[Dict]:
    """РћС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ РІ Telegram РіСЂСѓРїРїСѓ"""
    return send_telegram_message(chat_id, message, buttons)


def send_telegram_private(telegram_id: str, message: str, 
                          buttons: Optional[List[Dict]] = None) -> Optional[Dict]:
    """РћС‚РїСЂР°РІРёС‚СЊ Р»РёС‡РЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ РІ Telegram"""
    return send_telegram_message(telegram_id, message, buttons)


def answer_telegram_callback(callback_query_id: str, text: str = "",
                             show_alert: bool = False) -> bool:
    """Р‘С‹СЃС‚СЂРѕ РїРѕРґС‚РІРµСЂРґРёС‚СЊ РЅР°Р¶Р°С‚РёРµ inline-РєРЅРѕРїРєРё РІ Telegram."""
    try:
        if not callback_query_id:
            return False

        url = f"{config.TELEGRAM_API_URL}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id,
        }
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True

        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Exception answering Telegram callback: {e}")
        return False


def send_telegram_photo(chat_id: str, photo_url: str, caption: str = "",
                        buttons: Optional[List[Dict]] = None) -> Optional[Dict]:
    """РћС‚РїСЂР°РІРёС‚СЊ С„РѕС‚Рѕ РІ Telegram. РџРѕРґРґРµСЂР¶РёРІР°РµС‚ cloud_media: URL (Cloud API)."""
    try:
        # Cloud API С„РѕС‚Рѕ вЂ” СЃРєР°С‡РёРІР°РµРј Р±Р°Р№С‚С‹ Рё Р·Р°РіСЂСѓР¶Р°РµРј РєР°Рє С„Р°Р№Р»
        if photo_url.startswith("cloud_media:"):
            media_id = photo_url.split(":", 1)[1]
            photo_bytes = _download_cloud_media_bytes(media_id)
            if photo_bytes:
                return _send_telegram_photo_bytes(chat_id, photo_bytes, caption, buttons)
            return None

        url = f"{config.TELEGRAM_API_URL}/sendPhoto"

        payload = {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "Markdown"
        }

        if buttons:
            inline_keyboard = []
            for btn in buttons:
                inline_keyboard.append([{
                    "text": btn["text"],
                    "callback_data": btn["callback"]
                }])
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

        response = requests.post(url, json=payload, timeout=30)

        if response.status_code == 200:
            return response.json().get("result")
        else:
            print(f"Telegram photo error: {response.text}")
            return None

    except Exception as e:
        print(f"Exception sending Telegram photo: {e}")
        return None


def _send_telegram_photo_bytes(chat_id: str, photo_bytes: bytes, caption: str = "",
                                buttons: Optional[List[Dict]] = None) -> Optional[Dict]:
    """Р—Р°РіСЂСѓР·РёС‚СЊ С„РѕС‚Рѕ РІ Telegram РєР°Рє multipart/form-data (РґР»СЏ Cloud API РјРµРґРёР°)"""
    try:
        url = f"{config.TELEGRAM_API_URL}/sendPhoto"

        data: dict = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "Markdown",
        }

        if buttons:
            inline_keyboard = []
            for btn in buttons:
                inline_keyboard.append([{
                    "text": btn["text"],
                    "callback_data": btn["callback"]
                }])
            data["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})

        files = {"photo": ("photo.jpg", photo_bytes, "image/jpeg")}

        response = requests.post(url, data=data, files=files, timeout=30)

        if response.status_code == 200:
            return response.json().get("result")
        else:
            print(f"Telegram photo bytes error: {response.text}")
            return None

    except Exception as e:
        print(f"Exception sending Telegram photo bytes: {e}")
        return None


def edit_telegram_message(chat_id: str, message_id: int, 
                          new_text: str, buttons: Optional[List[Dict]] = None) -> bool:
    """Р РµРґР°РєС‚РёСЂРѕРІР°С‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ РІ Telegram"""
    try:
        url = f"{config.TELEGRAM_API_URL}/editMessageText"
        
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "Markdown"
        }
        
        if buttons is not None:
            inline_keyboard = []
            for btn in buttons:
                inline_keyboard.append([{
                    "text": btn["text"],
                    "callback_data": btn["callback"]
                }])
            
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
        
        response = requests.post(url, json=payload, timeout=30)
        return response.status_code == 200
        
    except Exception as e:
        print(f"Exception editing Telegram message: {e}")
        return False


def delete_telegram_message(chat_id: str, message_id: int) -> bool:
    """РЈРґР°Р»РёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ РІ Telegram"""
    try:
        url = f"{config.TELEGRAM_API_URL}/deleteMessage"
        
        payload = {
            "chat_id": chat_id,
            "message_id": message_id
        }
        
        response = requests.post(url, json=payload, timeout=30)
        return response.status_code == 200
        
    except Exception as e:
        print(f"Exception deleting Telegram message: {e}")
        return False


def send_telegram_broadcast(user_ids: List[str], message: str) -> Dict[str, bool]:
    """Р Р°СЃСЃС‹Р»РєР° СЃРѕРѕР±С‰РµРЅРёСЏ РЅРµСЃРєРѕР»СЊРєРёРј РїРѕР»СЊР·РѕРІР°С‚РµР»СЏРј"""
    results = {}
    for user_id in user_ids:
        result = send_telegram_private(user_id, message)
        results[user_id] = result is not None
    return results


# =============================================================================
# SPEECH-TO-TEXT SERVICES
# =============================================================================

def speech_to_text(audio_url: str) -> str:
    """РџСЂРµРѕР±СЂР°Р·РѕРІР°С‚СЊ РіРѕР»РѕСЃРѕРІРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ РІ С‚РµРєСЃС‚"""
    try:
        if not config.OPENAI_API_KEY:
            return "[Р Р°СЃРїРѕР·РЅР°РІР°РЅРёРµ РіРѕР»РѕСЃР° РЅРµРґРѕСЃС‚СѓРїРЅРѕ - РЅРµС‚ API РєР»СЋС‡Р°]"

        # Cloud API media вЂ” СЃРєР°С‡РёРІР°РµРј С‡РµСЂРµР· Bearer С‚РѕРєРµРЅ
        if audio_url.startswith("cloud_media:"):
            media_id = audio_url.split(":", 1)[1]
            audio_bytes = _download_cloud_media_bytes(media_id)
            if not audio_bytes:
                return "[РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё Р°СѓРґРёРѕ]"
            return _transcribe_with_whisper_best(audio_bytes)

        # РћР±С‹С‡РЅС‹Р№ URL (Green API / Twilio) вЂ” РїСЂСЏРјР°СЏ Р·Р°РіСЂСѓР·РєР°
        audio_response = requests.get(audio_url, timeout=30)
        if audio_response.status_code != 200:
            return "[РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё Р°СѓРґРёРѕ]"
        return _transcribe_with_whisper_best(audio_response.content)

    except Exception as e:
        print(f"Exception in speech_to_text: {e}")
        return "[РћС€РёР±РєР° СЂР°СЃРїРѕР·РЅР°РІР°РЅРёСЏ РіРѕР»РѕСЃР°]"


def _transcribe_with_whisper_best(audio_content: bytes) -> str:
    """Transcribe with fixed ky+ru passes and choose the best domain-matching text."""
    preferred_lang = (config.WHISPER_LANGUAGE or "ru").strip().lower()
    lang_sequence = []
    for lang in (preferred_lang, "ky", "ru"):
        if lang and lang not in lang_sequence:
            lang_sequence.append(lang)

    candidates: List[Tuple[int, str, str]] = []
    for lang in lang_sequence:
        text = _transcribe_with_whisper(audio_content, language=lang)
        if not text:
            continue
        cleaned = text.strip()
        if not cleaned:
            continue
        score = _score_transcription(cleaned)
        candidates.append((score, lang, cleaned))

    if not candidates:
        return "[РћС€РёР±РєР° СЂР°СЃРїРѕР·РЅР°РІР°РЅРёСЏ]"

    # Sort by domain score, then by text length.
    candidates.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
    best = candidates[0]
    print(f"[STT] Best transcript language={best[1]} score={best[0]}")
    return best[2]


def _score_transcription(text: str) -> int:
    normalized = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if not normalized:
        return -100
    if any(marker in normalized for marker in STT_NOISE_MARKERS):
        return -80

    score = 0
    token_count = len(re.findall(r"[a-zа-яёүөңқһі0-9]+", normalized, flags=re.IGNORECASE))
    if token_count >= 3:
        score += 10
    if token_count >= 6:
        score += 8

    for kw in STT_SERVICE_KEYWORDS:
        if kw in normalized:
            score += 7
    for kw in STT_ADDRESS_KEYWORDS:
        if kw in normalized:
            score += 8

    # Route patterns (addresses + direction markers) are highly valuable.
    if re.search(r"\b(кайдан|кайда|откуда|куда|от|до)\b", normalized):
        score += 15
    if re.search(r"\b(дан|ден|тан|тен|нан|нен|дон|дөн|га|ге|ка|ке|го|гө|ко|кө)\b", normalized):
        score += 10

    # House numbers / route separators usually indicate useful address content.
    if re.search(r"\b\d{1,4}\b", normalized):
        score += 12
    if " - " in normalized or " — " in normalized:
        score += 10

    return score


def _transcribe_with_whisper(audio_content: bytes, language: str | None = None) -> str:
    """РўСЂР°РЅСЃРєСЂРёР±РёСЂРѕРІР°С‚СЊ Р°СѓРґРёРѕ СЃ РїРѕРјРѕС‰СЊСЋ OpenAI Whisper"""
    try:
        url = "https://api.openai.com/v1/audio/transcriptions"
        
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}"
        }
        
        files = {
            'file': ('audio.ogg', audio_content, 'audio/ogg'),
            'model': (None, 'gpt-4o-mini-transcribe'),
            'language': (None, (language or config.WHISPER_LANGUAGE or "ru")),
            'prompt': (None, config.WHISPER_PROMPT),
        }

        response = requests.post(url, headers=headers, files=files, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            return result.get("text", "")
        else:
            print(f"Whisper API error: {response.text}")
            return "[РћС€РёР±РєР° СЂР°СЃРїРѕР·РЅР°РІР°РЅРёСЏ]"
            
    except Exception as e:
        print(f"Exception in Whisper transcription: {e}")
        return "[РћС€РёР±РєР° СЂР°СЃРїРѕР·РЅР°РІР°РЅРёСЏ]"


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def _clean_phone(phone: str) -> str:
    """РћС‡РёСЃС‚РёС‚СЊ РЅРѕРјРµСЂ С‚РµР»РµС„РѕРЅР°"""
    phone = phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    
    # Р•СЃР»Рё РЅРѕРјРµСЂ РЅР°С‡РёРЅР°РµС‚СЃСЏ СЃ whatsapp:, СѓР±РёСЂР°РµРј
    if "whatsapp:" in phone:
        phone = phone.replace("whatsapp:", "")
    
    return phone


def format_phone(phone: str) -> str:
    """Р¤РѕСЂРјР°С‚РёСЂРѕРІР°С‚СЊ РЅРѕРјРµСЂ С‚РµР»РµС„РѕРЅР° РґР»СЏ РѕС‚РѕР±СЂР°Р¶РµРЅРёСЏ (С„РѕСЂРјР°С‚: 0220 203 021)"""
    phone = _clean_phone(phone)

    # Р•СЃР»Рё РЅР°С‡РёРЅР°РµС‚СЃСЏ СЃ 996 в†’ Р·Р°РјРµРЅРёС‚СЊ РЅР° 0
    if phone.startswith("996"):
        phone = "0" + phone[3:]

    # Р¤РѕСЂРјР°С‚РёСЂРѕРІР°С‚СЊ РєР°Рє XXXX XXX XXX (4 С†РёС„СЂС‹, РїСЂРѕР±РµР», 3 С†РёС„СЂС‹, РїСЂРѕР±РµР», 3 С†РёС„СЂС‹)
    if len(phone) == 10:
        return f"{phone[:4]} {phone[4:7]} {phone[7:]}"

    return phone


def calculate_taxi_price(route: str) -> str:
    """Р Р°СЃСЃС‡РёС‚Р°С‚СЊ РїСЂРёРјРµСЂРЅСѓСЋ С†РµРЅСѓ С‚Р°РєСЃРё"""
    route_lower = route.lower()
    
    base_price = 100
    
    if any(word in route_lower for word in ["С†РµРЅС‚СЂ", "СЂС‹РЅРѕРє", "Р±Р°Р·Р°СЂ", "center", "bazaar"]):
        return f"{base_price}-{base_price + 20}"
    elif any(word in route_lower for word in ["РјРёРєСЂРѕСЂР°Р№РѕРЅ", "РјРєСЂ", "Р¶РёР»РјР°СЃСЃРёРІ", "microdistrict"]):
        return f"{base_price + 30}-{base_price + 50}"
    elif any(word in route_lower for word in ["Р·Р° РіРѕСЂРѕРґ", "СЃРµР»Рѕ", "РґРµСЂРµРІРЅСЏ", "village", "outskirts"]):
        return f"{base_price + 100}-{base_price + 200}"
    
    return f"{base_price}-{base_price + 50}"


def escape_markdown(text: str) -> str:
    """Р­РєСЂР°РЅРёСЂРѕРІР°С‚СЊ СЃРїРµС†РёР°Р»СЊРЅС‹Рµ СЃРёРјРІРѕР»С‹ Markdown"""
    if not text:
        return ""
    
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    
    return text


def format_currency(amount: float) -> str:
    """Р¤РѕСЂРјР°С‚РёСЂРѕРІР°С‚СЊ СЃСѓРјРјСѓ РІР°Р»СЋС‚С‹"""
    return f"{amount:,.0f}".replace(",", " ")


def truncate_text(text: str, max_length: int = 200) -> str:
    """РћР±СЂРµР·Р°С‚СЊ С‚РµРєСЃС‚ РґРѕ СѓРєР°Р·Р°РЅРЅРѕР№ РґР»РёРЅС‹"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def detect_language(text: str) -> str:
    """РћРїСЂРµРґРµР»РёС‚СЊ СЏР·С‹Рє С‚РµРєСЃС‚Р° (ru/kg)"""
    # РџСЂРѕСЃС‚Р°СЏ СЌРІСЂРёСЃС‚РёРєР° - РїСЂРѕРІРµСЂСЏРµРј РЅР° РєС‹СЂРіС‹Р·СЃРєРёРµ СЃРёРјРІРѕР»С‹
    kyrgyz_chars = set('ТЈУ©ТЇ')
    
    for char in text.lower():
        if char in kyrgyz_chars:
            return 'kg'
    
    return 'ru'
