"""
Вспомогательные сервисы
Services Module for Business Assistant GO
Обновленная версия согласно ТЗ v2.0
"""

import json
import logging
import time
import re
import mimetypes
import os
from io import BytesIO
from datetime import datetime, timedelta
import requests
from typing import List, Dict, Optional, Tuple
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import unquote, urlencode, urlparse

import config

logger = logging.getLogger(__name__)


def _bishkek_now_naive() -> datetime:
    return datetime.utcnow() + timedelta(hours=6)

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


def _build_http_session() -> requests.Session:
    """Shared HTTP session to reuse TCP connections and reduce latency."""
    session = requests.Session()
    retries = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.15,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(pool_connections=24, pool_maxsize=48, max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_HTTP_SESSION = _build_http_session()


def _http_get(url: str, **kwargs):
    return _HTTP_SESSION.get(url, **kwargs)


def _http_post(url: str, **kwargs):
    return _HTTP_SESSION.post(url, **kwargs)


def _build_whatsapp_http_session() -> requests.Session:
    """
    Dedicated WhatsApp session with larger pool and safe retry policy.
    Retries only on connect-level failures to reduce duplicate message risk.
    """
    session = requests.Session()
    retries = Retry(
        total=2,
        connect=2,
        read=0,
        status=0,
        other=0,
        backoff_factor=0.2,
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=64,
        pool_maxsize=128,
        max_retries=retries,
        pool_block=False,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_WHATSAPP_HTTP_SESSION = _build_whatsapp_http_session()


def _wa_get(url: str, **kwargs):
    return _WHATSAPP_HTTP_SESSION.get(url, **kwargs)


def _wa_post(url: str, **kwargs):
    return _WHATSAPP_HTTP_SESSION.post(url, **kwargs)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _admin_upload_filename(image_url: str) -> Optional[str]:
    raw = (image_url or "").strip()
    if not raw:
        return None
    parsed_path = urlparse(raw).path if raw.startswith(("http://", "https://")) else raw
    parsed_path = unquote(parsed_path or "")
    marker = "/admin/uploads/"
    if marker not in parsed_path:
        return None
    filename = os.path.basename(parsed_path.split(marker, 1)[1])
    if not filename:
        return None
    return filename


def _local_admin_upload_path(image_url: str) -> Optional[str]:
    filename = _admin_upload_filename(image_url)
    if not filename:
        return None
    path = os.path.abspath(os.path.join(_repo_root(), "uploads", "admin", filename))
    upload_dir = os.path.abspath(os.path.join(_repo_root(), "uploads", "admin"))
    if not (path == upload_dir or path.startswith(upload_dir + os.sep)):
        return None
    return path if os.path.exists(path) else None


def _stored_admin_upload(image_url: str) -> Optional[dict]:
    filename = _admin_upload_filename(image_url)
    if not filename:
        return None
    try:
        from db import get_db

        stored = get_db().get_admin_upload_file(filename)
        return stored if stored and stored.get("data") else None
    except Exception:
        logger.exception("Failed to load admin upload from db filename=%s", filename)
        return None


def _guess_image_mime(filename: str) -> str:
    mime = mimetypes.guess_type(filename or "")[0]
    return mime if mime and mime.startswith("image/") else "image/jpeg"


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
    if "отмена" in message_lower and ("заказ" in message_lower or "доставка" in message_lower):
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
    return send_whatsapp_with_main_menu(phone, config.ORDER_CANCELLED)


def send_whatsapp_with_main_menu(phone: str, message: str) -> bool:
    """Send WhatsApp message with a dedicated main-menu button."""
    buttons = [{"id": WHATSAPP_MAIN_MENU_BUTTON_ID, "text": WHATSAPP_MAIN_MENU_BUTTON_TEXT}]
    if config.WHATSAPP_PROVIDER == "cloud":
        return send_whatsapp_buttons(phone, message, buttons, include_cancel=False)
    return send_whatsapp_plain(phone, message)



def _send_whatsapp_green(phone: str, message: str) -> bool:
    """Отправить сообщение через GREEN API"""
    try:
        url = f"{config.GREEN_API_URL}/sendMessage/{config.GREEN_API_TOKEN}"
        
        phone_clean = _clean_phone(phone)
        
        payload = {
            "chatId": f"{phone_clean}@c.us",
            "message": message
        }
        
        headers = {'Content-Type': 'application/json'}
        
        response = _wa_post(url, json=payload, headers=headers, timeout=30)
        
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
    """Отправить сообщение через Twilio"""
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
    """Отправить текстовое сообщение через WhatsApp Cloud API (Meta Graph API)"""
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
        response = _wa_post(url, json=payload, headers=headers, timeout=30)
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
    """Отправить интерактивное сообщение с кнопками через Cloud API (макс 3, заголовок макс 20 симв)"""
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
        response = _wa_post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            sent_id = (response.json().get('messages') or [{}])[0].get('id') or None
            try:
                from db import get_db as _get_db
                btn_titles = ' / '.join(b['reply']['title'] for b in cloud_buttons)
                _get_db().save_message(phone=phone, direction='out',
                                       body=f"{message}\n[Кнопки: {btn_titles}]",
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


def _upload_whatsapp_image_cloud(image_url: str) -> Optional[str]:
    local_path = _local_admin_upload_path(image_url)
    filename = os.path.basename(local_path) if local_path else "image.jpg"
    mime = _guess_image_mime(filename)

    try:
        if local_path:
            file_obj = open(local_path, "rb")
        elif stored := _stored_admin_upload(image_url):
            filename = stored.get("filename") or filename
            mime = stored.get("content_type") or _guess_image_mime(filename)
            file_obj = BytesIO(stored["data"])
        elif image_url.startswith(("http://", "https://")):
            download_resp = _wa_get(image_url, timeout=30)
            if download_resp.status_code != 200:
                logger.warning(
                    "WhatsApp image download failed status=%s url=%s body=%s",
                    download_resp.status_code,
                    image_url,
                    download_resp.text[:300],
                )
                return None
            content_type = (download_resp.headers.get("content-type") or "").split(";")[0].strip()
            if content_type.startswith("image/"):
                mime = content_type
            file_obj = BytesIO(download_resp.content)
        else:
            return None

        try:
            url = (
                f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}"
                f"/{config.WHATSAPP_PHONE_NUMBER_ID}/media"
            )
            headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
            files = {"file": (filename, file_obj, mime)}
            data = {"messaging_product": "whatsapp", "type": mime}
            response = _wa_post(url, headers=headers, data=data, files=files, timeout=60)
        finally:
            file_obj.close()

        if response.status_code == 200:
            media_id = response.json().get("id")
            if media_id:
                logger.info("WhatsApp image uploaded media_id=%s source=%s", media_id, "local" if local_path else "url")
                return media_id
        logger.warning("WhatsApp image upload failed status=%s body=%s", response.status_code, response.text[:500])
        return None
    except Exception:
        logger.exception("WhatsApp image upload exception")
        return None


def _send_whatsapp_image_id_cloud(phone: str, media_id: str, caption: str = "") -> bool:
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
            "image": {"id": media_id, "caption": caption},
        }
        response = _wa_post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            sent_id = (response.json().get("messages") or [{}])[0].get("id") or None
            try:
                from db import get_db as _get_db
                _get_db().save_message(
                    phone=phone,
                    direction="out",
                    body=caption or "[image]",
                    msg_type="image",
                    wa_message_id=sent_id,
                    media_url=f"cloud_media:{media_id}",
                )
            except Exception:
                pass
            logger.info("WhatsApp image sent by media id to=%s media_id=%s", phone, media_id)
            return True
        logger.warning("WhatsApp image send by id failed status=%s body=%s", response.status_code, response.text[:500])
        return False
    except Exception:
        logger.exception("WhatsApp image send by id exception")
        return False


def _send_whatsapp_image_cloud(phone: str, image_url: str, caption: str = "") -> bool:
    """Send an image through Cloud API. Local admin uploads are sent by media id."""
    try:
        if _admin_upload_filename(image_url):
            media_id = _upload_whatsapp_image_cloud(image_url)
            return _send_whatsapp_image_id_cloud(phone, media_id, caption) if media_id else False

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
        response = _wa_post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            sent_id = (response.json().get("messages") or [{}])[0].get("id") or None
            try:
                from db import get_db as _get_db
                _get_db().save_message(
                    phone=phone,
                    direction="out",
                    body=caption or "[image]",
                    msg_type="image",
                    wa_message_id=sent_id,
                    media_url=image_url,
                )
            except Exception:
                pass
            logger.info("WhatsApp image sent by link to=%s", phone)
            return True

        logger.warning("WhatsApp image send by link failed status=%s body=%s", response.status_code, response.text[:500])
        media_id = _upload_whatsapp_image_cloud(image_url)
        return _send_whatsapp_image_id_cloud(phone, media_id, caption) if media_id else False
    except Exception:
        logger.exception("WhatsApp image send exception")
        return False


def _download_cloud_media_bytes(media_id: str) -> Optional[bytes]:
    """Скачать медиа из Cloud API по media_id (требует Bearer токен)"""
    try:
        # Шаг 1: получаем download URL из media_id
        meta_url = f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{media_id}"
        headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
        meta_resp = _wa_get(meta_url, headers=headers, timeout=30)
        if meta_resp.status_code != 200:
            print(f"[Cloud API Media] Error getting URL for {media_id}: {meta_resp.text}")
            return None
        download_url = meta_resp.json().get("url")
        if not download_url:
            print(f"[Cloud API Media] No URL in response for {media_id}")
            return None

        # Шаг 2: скачиваем байты с авторизацией
        dl_resp = _wa_get(download_url, headers=headers, timeout=60)
        if dl_resp.status_code != 200:
            print(f"[Cloud API Media] Download failed {dl_resp.status_code}")
            return None
        content_type = (dl_resp.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type or content_type.startswith("text/"):
            snippet = (dl_resp.text or "")[:200]
            print(f"[Cloud API Media] Non-audio content type {content_type}: {snippet}")
            return None
        return dl_resp.content
    except Exception as e:
        print(f"[Cloud API Media] Exception: {e}")
        return None


def send_confirmation_buttons(phone: str) -> bool:
    """Отправить кнопки 'Да / Нет' для подтверждения заказа (Cloud API)"""
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
        response = _wa_post(api_url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            try:
                from db import get_db as _get_db
                sent_id = (response.json().get("messages") or [{}])[0].get("id") or None
                _get_db().save_message(
                    phone=phone, direction="out",
                    body=f"{message}\n[Кнопка: {btn_text} > {url}]",
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
        response = _wa_post(api_url, json=payload, timeout=30)
        return response.status_code == 200
    except Exception as e:
        print(f"[Green URL Button] Exception: {e}")
        return send_whatsapp_plain(phone, f"{message}\n{url}")


def send_whatsapp_buttons(phone: str, message: str, buttons: List[Dict], include_cancel: bool = True) -> bool:
    """Отправить интерактивное сообщение с кнопками в WhatsApp"""
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
    """Отправить кнопки через GREEN API"""
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
        
        response = _wa_post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 200
        
    except Exception as e:
        print(f"[GREEN API Buttons] Exception: {e}")
        return False


def _send_whatsapp_buttons_twilio(phone: str, message: str, buttons: List[Dict]) -> bool:
    """Отправить кнопки через Twilio (используем список с номерами)"""
    try:
        # Twilio не поддерживает нативные кнопки, отправляем как текст с нумерацией
        button_text = "\n\n"
        for idx, btn in enumerate(buttons, 1):
            button_text += f"{idx}. {btn['text']}\n"
        
        full_message = message + button_text + "\nОтветьте номером варианта."
        
        return _send_whatsapp_twilio(phone, full_message)
        
    except Exception as e:
        print(f"[Twilio Buttons] Exception: {e}")
        return False


def send_whatsapp_image(phone: str, image_url: str, caption: str = "") -> bool:
    """Отправить изображение в WhatsApp"""
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
    """Отправить изображение через GREEN API"""
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
        
        response = _wa_post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 200
        
    except Exception as e:
        print(f"[GREEN API Image] Exception: {e}")
        return False


def _send_whatsapp_image_twilio(phone: str, image_url: str, caption: str = "") -> bool:
    """Отправить изображение через Twilio"""
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
    """Отправить геолокацию в WhatsApp"""
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
            
            response = _wa_post(url, json=payload, headers=headers, timeout=30)
            return response.status_code == 200
        else:
            # Twilio не поддерживает отправку локации напрямую
            location_url = f"https://maps.google.com/?q={latitude},{longitude}"
            return send_whatsapp(phone, f"📍 Локация: {location_url}")
            
    except Exception as e:
        print(f"Error sending location: {e}")
        return False


# =============================================================================
# TELEGRAM SERVICES
# =============================================================================

def send_telegram_message(chat_id: str, message: str, 
                          buttons: Optional[List[Dict]] = None,
                          parse_mode: str = "Markdown") -> Optional[Dict]:
    """Отправить сообщение в Telegram"""
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
        
        last_error = None
        for attempt in range(3):
            response = _http_post(url, json=payload, timeout=30)
            if response.status_code == 200:
                return response.json().get("result")

            last_error = response.text
            retry_after = None
            if response.status_code == 429:
                try:
                    retry_after = (response.json().get("parameters") or {}).get("retry_after")
                except Exception:
                    retry_after = None

            if attempt < 2:
                sleep_for = retry_after if isinstance(retry_after, (int, float)) else (0.5 * (2 ** attempt))
                time.sleep(min(sleep_for, 5))
                continue

        print(f"Telegram error: {last_error}")
        return None
            
    except Exception as e:
        print(f"Exception sending Telegram message: {e}")
        return None


def send_telegram_contact_request(chat_id: str, message: str, button_text: str) -> Optional[Dict]:
    """Отправить сообщение с запросом контакта (reply keyboard)."""
    try:
        url = f"{config.TELEGRAM_API_URL}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": {
                "keyboard": [[{"text": button_text, "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
        }
        response = _http_post(url, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json().get("result")
        print(f"Telegram contact request error: {response.text}")
        return None
    except Exception as e:
        print(f"Exception sending Telegram contact request: {e}")
        return None


def send_telegram_group(chat_id: str, message: str, 
                        buttons: Optional[List[Dict]] = None) -> Optional[Dict]:
    """Отправить сообщение в Telegram группу"""
    return send_telegram_message(chat_id, message, buttons)


def send_telegram_private(telegram_id: str, message: str, 
                          buttons: Optional[List[Dict]] = None) -> Optional[Dict]:
    """Отправить личное сообщение в Telegram"""
    return send_telegram_message(telegram_id, message, buttons)


def _coerce_telegram_buttons(raw_buttons) -> List[Dict]:
    if not raw_buttons:
        return []
    if isinstance(raw_buttons, list):
        return raw_buttons
    if isinstance(raw_buttons, str):
        try:
            parsed = json.loads(raw_buttons)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _ensure_auction_timer(
    order_id: str,
    service_type: str,
    chat_id: str,
    message_id,
    timeout_seconds: Optional[int],
) -> None:
    """Создать таймер аукциона только один раз, даже если сообщение дошло через очередь."""
    if not order_id or not service_type or not timeout_seconds or message_id is None:
        return

    try:
        from db import get_db as _get_db

        db = _get_db()
        existing_timer = db.get_latest_auction_timer(order_id, service_type)
        if existing_timer:
            return

        db.create_auction_timer(
            order_id=order_id,
            service_type=service_type,
            telegram_message_id=str(message_id),
            chat_id=chat_id,
            timeout_seconds=int(timeout_seconds),
        )
    except Exception:
        logger.exception(
            "Failed to create auction timer for order_id=%s service_type=%s",
            order_id,
            service_type,
        )


def dispatch_telegram_group_notification(
    chat_id: str,
    message: str,
    buttons: Optional[List[Dict]] = None,
    *,
    order_id: str = None,
    service_type: str = None,
    timeout_seconds: Optional[int] = None,
    photo_url: str = None,
    parse_mode: str = "Markdown",
) -> Optional[Dict]:
    """
    Надёжная отправка в Telegram-группу.
    Если первая отправка не удалась, сохраняем сообщение в БД и cron дошлёт его позже.
    """
    result = (
        send_telegram_photo(chat_id, photo_url, message, buttons=buttons)
        if photo_url
        else send_telegram_message(chat_id, message, buttons, parse_mode=parse_mode)
    )
    if result:
        _ensure_auction_timer(
            order_id=order_id,
            service_type=service_type,
            chat_id=chat_id,
            message_id=result.get("message_id"),
            timeout_seconds=timeout_seconds,
        )
        return result

    try:
        from db import get_db as _get_db

        outbox_id = _get_db().enqueue_telegram_group_outbox(
            chat_id=chat_id,
            message_text=message,
            buttons=buttons,
            message_kind="photo" if photo_url else "message",
            photo_url=photo_url,
            parse_mode=parse_mode,
            order_id=order_id,
            service_type=service_type,
            timeout_seconds=int(timeout_seconds) if timeout_seconds is not None else None,
            last_error="Immediate Telegram send failed",
        )
        logger.warning(
            "Queued Telegram group message id=%s chat_id=%s order_id=%s service_type=%s",
            outbox_id,
            chat_id,
            order_id,
            service_type,
        )
    except Exception:
        logger.exception(
            "Failed to queue Telegram group message chat_id=%s order_id=%s service_type=%s",
            chat_id,
            order_id,
            service_type,
        )
    return None


def process_telegram_group_outbox(limit: int = 20, stale_after_seconds: int = 300) -> int:
    """Повторно отправить сообщения в Telegram-группы, которые не ушли сразу."""
    try:
        from db import get_db as _get_db

        db = _get_db()
        entries = db.claim_telegram_group_outbox(
            limit=limit,
            stale_after_seconds=stale_after_seconds,
        )
    except Exception:
        logger.exception("Failed to claim Telegram group outbox")
        return 0

    sent_count = 0
    for entry in entries:
        entry_id = entry["id"]
        chat_id = entry.get("chat_id")
        order_id = entry.get("order_id")
        service_type = entry.get("service_type")
        timeout_seconds = entry.get("timeout_seconds")

        try:
            existing_timer = None
            current_order = None
            if order_id and service_type:
                current_order = db.get_order(order_id)
                if not current_order:
                    db.mark_telegram_group_outbox_sent(entry_id, None)
                    logger.info(
                        "Telegram outbox id=%s skipped because order_id=%s no longer exists",
                        entry_id,
                        order_id,
                    )
                    continue

                if current_order.get("status") in (
                    config.ORDER_STATUS_CANCELLED,
                    config.ORDER_STATUS_COMPLETED,
                ):
                    db.mark_telegram_group_outbox_sent(entry_id, None)
                    logger.info(
                        "Telegram outbox id=%s skipped because order_id=%s already closed with status=%s",
                        entry_id,
                        order_id,
                        current_order.get("status"),
                    )
                    continue

                if (
                    service_type == config.SERVICE_POPUTKA
                    and current_order.get("expires_at")
                    and current_order["expires_at"] <= _bishkek_now_naive()
                ):
                    db.mark_telegram_group_outbox_sent(entry_id, None)
                    logger.info(
                        "Telegram outbox id=%s skipped because poputka order_id=%s already expired",
                        entry_id,
                        order_id,
                    )
                    continue

                existing_timer = db.get_latest_auction_timer(order_id, service_type)
            if existing_timer:
                db.mark_telegram_group_outbox_sent(entry_id, existing_timer.get("telegram_message_id"))
                logger.info(
                    "Telegram outbox id=%s skipped because timer already exists for order_id=%s service_type=%s",
                    entry_id,
                    order_id,
                    service_type,
                )
                continue

            buttons = _coerce_telegram_buttons(entry.get("buttons"))
            if entry.get("message_kind") == "photo":
                result = send_telegram_photo(
                    chat_id,
                    entry.get("photo_url") or "",
                    entry.get("message_text") or "",
                    buttons=buttons,
                )
            else:
                result = send_telegram_message(
                    chat_id,
                    entry.get("message_text") or "",
                    buttons,
                    parse_mode=entry.get("parse_mode") or "Markdown",
                )

            if result:
                message_id = result.get("message_id")
                _ensure_auction_timer(
                    order_id=order_id,
                    service_type=service_type,
                    chat_id=chat_id,
                    message_id=message_id,
                    timeout_seconds=timeout_seconds,
                )
                db.mark_telegram_group_outbox_sent(
                    entry_id,
                    str(message_id) if message_id is not None else None,
                )
                sent_count += 1
                continue

            attempts = int(entry.get("attempts") or 1)
            delay_seconds = min(300, 15 * (2 ** min(max(attempts - 1, 0), 4)))
            db.mark_telegram_group_outbox_retry(
                entry_id,
                last_error="Telegram resend failed",
                delay_seconds=delay_seconds,
            )
        except Exception as exc:
            attempts = int(entry.get("attempts") or 1)
            delay_seconds = min(300, 15 * (2 ** min(max(attempts - 1, 0), 4)))
            try:
                db.mark_telegram_group_outbox_retry(
                    entry_id,
                    last_error=str(exc),
                    delay_seconds=delay_seconds,
                )
            except Exception:
                logger.exception("Failed to update retry state for Telegram outbox id=%s", entry_id)
            logger.exception("Error processing Telegram outbox id=%s", entry_id)

    return sent_count


def answer_telegram_callback(callback_query_id: str, text: str = "",
                             show_alert: bool = False) -> bool:
    """Быстро подтвердить нажатие inline-кнопки в Telegram."""
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

        response = _http_post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Exception answering Telegram callback: {e}")
        return False


def send_telegram_photo(chat_id: str, photo_url: str, caption: str = "",
                        buttons: Optional[List[Dict]] = None) -> Optional[Dict]:
    """Отправить фото в Telegram. Поддерживает cloud_media: URL (Cloud API)."""
    try:
        # Cloud API фото — скачиваем байты и загружаем как файл
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

        response = _http_post(url, json=payload, timeout=30)

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
    """Загрузить фото в Telegram как multipart/form-data (для Cloud API медиа)"""
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

        response = _http_post(url, data=data, files=files, timeout=30)

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
    """Редактировать сообщение в Telegram"""
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
        
        response = _http_post(url, json=payload, timeout=30)
        return response.status_code == 200
        
    except Exception as e:
        print(f"Exception editing Telegram message: {e}")
        return False


def delete_telegram_message(chat_id: str, message_id: int) -> bool:
    """Удалить сообщение в Telegram"""
    try:
        url = f"{config.TELEGRAM_API_URL}/deleteMessage"
        
        payload = {
            "chat_id": chat_id,
            "message_id": message_id
        }
        
        response = _http_post(url, json=payload, timeout=30)
        return response.status_code == 200
        
    except Exception as e:
        print(f"Exception deleting Telegram message: {e}")
        return False


def send_telegram_broadcast(user_ids: List[str], message: str) -> Dict[str, bool]:
    """Рассылка сообщения нескольким пользователям"""
    results = {}
    for user_id in user_ids:
        result = send_telegram_private(user_id, message)
        results[user_id] = result is not None
    return results


# =============================================================================
# SPEECH-TO-TEXT SERVICES
# =============================================================================

def speech_to_text(audio_url: str) -> str:
    """Преобразовать голосовое сообщение в текст"""
    try:
        if not config.OPENAI_API_KEY:
            return "[Распознавание голоса недоступно - нет API ключа]"

        # Cloud API media — скачиваем через Bearer токен
        if audio_url.startswith("cloud_media:"):
            media_id = audio_url.split(":", 1)[1]
            audio_bytes = _download_cloud_media_bytes(media_id)
            if not audio_bytes:
                return "[Ошибка загрузки аудио]"
            return _transcribe_with_whisper_best(audio_bytes)

        # Обычный URL (Green API / Twilio) — прямая загрузка
        audio_response = _http_get(audio_url, timeout=30)
        if audio_response.status_code != 200:
            return "[Ошибка загрузки аудио]"
        content_type = (audio_response.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type or content_type.startswith("text/"):
            snippet = (audio_response.text or "")[:200]
            print(f"[STT] Audio download returned {content_type}: {snippet}")
            return "[Ошибка загрузки аудио]"
        return _transcribe_with_whisper_best(audio_response.content)

    except Exception as e:
        print(f"Exception in speech_to_text: {e}")
        return "[Ошибка распознавания голоса]"


def _transcribe_with_whisper_best(audio_content: bytes) -> str:
    """Transcribe with fixed ky+ru passes and choose the best domain-matching text."""
    preferred_lang = (config.WHISPER_LANGUAGE or "ru").strip().lower()
    lang_sequence = []
    for lang in (preferred_lang, "ky", "ru"):
        if lang and lang not in lang_sequence:
            lang_sequence.append(lang)

    candidates: List[Tuple[int, str, str]] = []
    for lang in lang_sequence:
        text = _transcribe_with_whisper_model(audio_content, language=lang, model="gpt-4o-transcribe")
        if not text or (text.startswith("[") and text.endswith("]")):
            text = _transcribe_with_whisper_model(audio_content, language=lang, model="whisper-1")
        if not text:
            continue
        cleaned = text.strip()
        if not cleaned:
            continue
        score = _score_transcription(cleaned)
        candidates.append((score, lang, cleaned))

    if not candidates:
        return "[Ошибка распознавания]"

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


def _transcribe_with_whisper_model(
    audio_content: bytes,
    language: str | None = None,
    model: str | None = None,
) -> str:
    """Transcribe audio with a specific OpenAI model."""
    try:
        model_name = model or "gpt-4o-transcribe"
        url = "https://api.openai.com/v1/audio/transcriptions"

        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}"
        }

        files = {
            'file': ('audio.ogg', audio_content, 'audio/ogg'),
            'model': (None, model_name),
            'language': (None, (language or config.WHISPER_LANGUAGE or "ru")),
            'prompt': (None, config.WHISPER_PROMPT),
        }

        response = _http_post(url, headers=headers, files=files, timeout=60)
        if response.status_code == 200:
            result = response.json()
            return result.get("text", "")
        print(f"Whisper API error ({model_name}) status={response.status_code}: {response.text}")
        return "[Ошибка распознавания]"
    except Exception as e:
        print(f"Exception in Whisper transcription ({model}): {e}")
        return "[Ошибка распознавания]"


def _transcribe_with_whisper(audio_content: bytes, language: str | None = None) -> str:
    """Транскрибировать аудио с помощью OpenAI Whisper"""
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

        response = _http_post(url, headers=headers, files=files, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            return result.get("text", "")
        else:
            print(f"Whisper API error: {response.text}")
            return "[Ошибка распознавания]"
            
    except Exception as e:
        print(f"Exception in Whisper transcription: {e}")
        return "[Ошибка распознавания]"


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def _clean_phone(phone: str) -> str:
    """Очистить номер телефона"""
    phone = phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    
    # Если номер начинается с whatsapp:, убираем
    if "whatsapp:" in phone:
        phone = phone.replace("whatsapp:", "")
    
    return phone


def format_phone(phone: str) -> str:
    """Форматировать номер телефона для отображения (формат: 0220 203 021)"""
    phone = _clean_phone(phone)

    # Если начинается с 996 → заменить на 0
    if phone.startswith("996"):
        phone = "0" + phone[3:]

    # Форматировать как XXXX XXX XXX (4 цифры, пробел, 3 цифры, пробел, 3 цифры)
    if len(phone) == 10:
        return f"{phone[:4]} {phone[4:7]} {phone[7:]}"

    return phone


def calculate_taxi_price(route: str) -> str:
    """Рассчитать примерную цену такси"""
    route_lower = route.lower()
    
    base_price = 100
    
    if any(word in route_lower for word in ["центр", "рынок", "базар", "center", "bazaar"]):
        return f"{base_price}-{base_price + 20}"
    elif any(word in route_lower for word in ["микрорайон", "мкр", "жилмассив", "microdistrict"]):
        return f"{base_price + 30}-{base_price + 50}"
    elif any(word in route_lower for word in ["за город", "село", "деревня", "village", "outskirts"]):
        return f"{base_price + 100}-{base_price + 200}"
    
    return f"{base_price}-{base_price + 50}"


def escape_markdown(text: str) -> str:
    """Экранировать специальные символы Markdown"""
    if not text:
        return ""
    
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    
    return text


def format_currency(amount: float) -> str:
    """Форматировать сумму валюты"""
    return f"{amount:,.0f}".replace(",", " ")


def truncate_text(text: str, max_length: int = 200) -> str:
    """Обрезать текст до указанной длины"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def detect_language(text: str) -> str:
    """Определить язык текста (ru/kg)"""
    # Простая эвристика - проверяем на кыргызские символы
    kyrgyz_chars = set('ңөү')
    
    for char in text.lower():
        if char in kyrgyz_chars:
            return 'kg'
    
    return 'ru'
