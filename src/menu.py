"""
Blueprint for Web Menu (Public & Admin)
"""
import os
import json
import urllib.parse
from flask import Blueprint, request, jsonify, send_from_directory, current_app
from db import get_db
import config
from services import send_telegram_group

# Путь к папке menu_panel (на один уровень выше src)
MENU_PANEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'menu_panel')

menu_bp = Blueprint('menu', __name__, url_prefix='/menu')

# =============================================================================
# STATIC FILES
# =============================================================================

@menu_bp.route('/', methods=['GET'])
def serve_menu_index():
    """Главная страница меню"""
    return send_from_directory(MENU_PANEL_DIR, 'index.html')

@menu_bp.route('/<path:filename>', methods=['GET'])
def serve_menu_static(filename):
    """Статические файлы меню (css, js, images)"""
    return send_from_directory(MENU_PANEL_DIR, filename)

# =============================================================================
# PUBLIC API
# =============================================================================

@menu_bp.route('/api/cafes', methods=['GET'])
def list_cafes_public():
    """Список активных кафе"""
    try:
        db = get_db()
        cafes = db.list_cafes(active_only=True)
        # Оставляем только нужные поля для фронта
        result = []
        for c in cafes:
            result.append({
                "id": c['id'],
                "name": c['name'],
                "address": c['address'],
                "phone": c['phone']
            })
        return jsonify(result), 200
    except Exception as e:
        current_app.logger.exception("Error listing cafes public")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/cafes/<int:cafe_id>/items', methods=['GET'])
def list_menu_items_public(cafe_id):
    """Меню конкретного кафе"""
    try:
        db = get_db()
        items = db.list_menu_items(cafe_id)
        settings = db.get_cafe_settings(cafe_id)
        global_pct = float(settings.get('global_discount_percent') or 0) if settings.get('global_discount_active') else 0

        available_items = []
        for i in items:
            if not i.get('is_available'):
                continue
            item = dict(i)
            item['category'] = i.get('category_name') or i.get('category')
            base_price = float(item['price'])
            final_price = base_price

            if item.get('discount_active') and item.get('discount_type'):
                if item['discount_type'] == 'percent':
                    final_price = base_price * (1 - float(item.get('discount_value', 0)) / 100)
                elif item['discount_type'] == 'amount':
                    final_price = base_price - float(item.get('discount_value', 0))
            elif global_pct > 0:
                final_price = base_price * (1 - global_pct / 100)

            final_price = max(round(final_price), 0)
            item['final_price'] = final_price
            if final_price < base_price:
                item['old_price'] = round(base_price)
            available_items.append(item)
        return jsonify(available_items), 200
    except Exception as e:
        current_app.logger.exception("Error listing menu items public")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/cafes/<int:cafe_id>/categories', methods=['GET'])
def list_categories_public(cafe_id):
    """Категории меню для кафе"""
    try:
        db = get_db()
        cats = db.list_categories(cafe_id)
        return jsonify(cats), 200
    except Exception as e:
        current_app.logger.exception("Error listing categories public")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/order', methods=['POST'])
def create_web_order():
    """Создание веб-заказа - только создание кода, без отправки в кафе"""
    import time
    
    timings = {}
    start_total = time.time()
    
    try:
        data = request.get_json()
        cafe_id = data.get('cafe_id')
        items = data.get('items')
        total_price = data.get('total_price')
        
        if not cafe_id or not items or not total_price:
            return jsonify({"error": "Missing data"}), 400
        
        t0 = time.time()
        db = get_db()
        timings['db_conn'] = int((time.time() - t0) * 1000)
        
        # Получаем инфо о кафе
        t0 = time.time()
        cafes = db.list_cafes(active_only=False)
        cafe = next((c for c in cafes if c['id'] == int(cafe_id)), None)
        cafe_name = cafe['name'] if cafe else "Unknown Cafe"
        timings['get_cafe'] = int((time.time() - t0) * 1000)

        # Создаем ТОЛЬКО веб-заказ (без отправки в кафе)
        t0 = time.time()
        order_code = db.create_web_order(cafe_id, cafe_name, items, float(total_price))
        timings['create_web_order'] = int((time.time() - t0) * 1000)
        
        # Формируем ответ
        msg_lines = [f"Заказ с сайта ✅", f"Кафе: {cafe_name}"]
        for item in items:
            msg_lines.append(f"{item['name']} x{item['count']}")
        msg_lines.append(f"Итого: {total_price} сом")
        msg_lines.append(f"Код: {order_code}")
        
        msg_text = "\n".join(msg_lines)
        encoded_text = urllib.parse.quote(msg_text)
        whatsapp_url = f"https://wa.me/{config.WHATSAPP_BOT_PHONE}?text={encoded_text}"
        
        timings['total'] = int((time.time() - start_total) * 1000)
        current_app.logger.info(f"Web order {order_code} created in {timings['total']}ms (awaiting confirmation)")
        
        return jsonify({
            "success": True, 
            "order_code": order_code, 
            "whatsapp_link": whatsapp_url,
            "_debug_timings_ms": timings
        }), 201
        
    except Exception as e:
        current_app.logger.exception("Web order creation failed")
        return jsonify({"error": str(e)}), 500

# =============================================================================
# ADMIN API for Menu
# =============================================================================

@menu_bp.route('/api/admin/items', methods=['GET'])
def admin_list_items():
    """Админка: список блюд кафе"""
    cafe_id = request.args.get('cafe_id')
    if not cafe_id:
        return jsonify({"error": "cafe_id required"}), 400
    try:
        db = get_db()
        items = db.list_menu_items(int(cafe_id))
        return jsonify(items), 200
    except Exception as e:
        current_app.logger.exception("Admin list items failed")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/admin/items', methods=['POST'])
def admin_add_item():
    """Админка: добавить блюдо"""
    try:
        data = request.get_json()
        cafe_id = data.get('cafe_id')
        name = data.get('name')
        price = data.get('price')
        category = data.get('category', 'Основное')
        category_id = data.get('category_id')
        image_url = data.get('image_url', None)
        description = data.get('description')
        
        if not cafe_id or not name or not price:
            return jsonify({"error": "Missing fields"}), 400
            
        db = get_db()
        success = db.add_menu_item(
            int(cafe_id), name, float(price), category,
            image_url=image_url, description=description, category_id=category_id
        )
        return jsonify({"success": success}), 201 if success else 500
    except Exception as e:
        current_app.logger.exception("Admin add item failed")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/admin/items/<int:item_id>', methods=['PUT'])
def admin_update_item(item_id):
    """Админка: обновить блюдо"""
    try:
        data = request.get_json()
        db = get_db()
        # Удаляем поля, которые нельзя менять или их нет
        fields = {k: v for k, v in data.items() if k in ['name', 'price', 'category', 'category_id', 'is_available', 'sort_order', 'image_url', 'description', 'discount_type', 'discount_value', 'discount_active']}
        if not fields:
            return jsonify({"error": "No valid fields"}), 400
            
        success = db.update_menu_item(item_id, **fields)
        return jsonify({"success": success}), 200
    except Exception as e:
        current_app.logger.exception("Admin update item failed")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/admin/items/<int:item_id>', methods=['DELETE'])
def admin_delete_item(item_id):
    """Админка: удалить блюдо"""
    try:
        db = get_db()
        success = db.delete_menu_item(item_id)
        return jsonify({"success": success}), 200
    except Exception as e:
        current_app.logger.exception("Admin delete item failed")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/admin/items/bulk', methods=['POST'])
def admin_bulk_add_items():
    """Админка: массовый импорт блюд из текста"""
    try:
        db = get_db()
        data = request.get_json()
        cafe_id = data.get('cafe_id')
        items = data.get('items', [])
        if not cafe_id or not items:
            return jsonify({"error": "cafe_id and items required"}), 400
        result = db.bulk_add_menu(int(cafe_id), items)
        return jsonify(result), 200
    except Exception as e:
        current_app.logger.exception("Bulk add items failed")
        return jsonify({"error": str(e)}), 500

# =============================================================================
# ADMIN API for Categories
# =============================================================================

@menu_bp.route('/api/admin/categories', methods=['GET'])
def admin_list_categories():
    cafe_id = request.args.get('cafe_id')
    if not cafe_id:
        return jsonify({"error": "cafe_id required"}), 400
    try:
        db = get_db()
        cats = db.list_categories(int(cafe_id))
        return jsonify(cats), 200
    except Exception as e:
        current_app.logger.exception("Admin list categories failed")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/admin/categories', methods=['POST'])
def admin_add_category():
    try:
        data = request.get_json()
        cafe_id = data.get('cafe_id')
        name = data.get('name')
        sort_order = data.get('sort_order', 0)
        if not cafe_id or not name:
            return jsonify({"error": "Missing fields"}), 400
        db = get_db()
        success = db.add_category(int(cafe_id), name, int(sort_order))
        return jsonify({"success": success}), 201 if success else 200
    except Exception as e:
        current_app.logger.exception("Admin add category failed")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/admin/categories/<int:category_id>', methods=['PUT'])
def admin_update_category(category_id):
    try:
        data = request.get_json()
        db = get_db()
        name = data.get('name')
        sort_order = data.get('sort_order')
        success = db.update_category(category_id, name=name, sort_order=sort_order)
        return jsonify({"success": success}), 200
    except Exception as e:
        current_app.logger.exception("Admin update category failed")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/admin/categories/<int:category_id>', methods=['DELETE'])
def admin_delete_category(category_id):
    try:
        db = get_db()
        success = db.delete_category(category_id)
        return jsonify({"success": success}), 200
    except Exception as e:
        current_app.logger.exception("Admin delete category failed")
        return jsonify({"error": str(e)}), 500

# =============================================================================
# ADMIN API for Cafe Settings (global discount)
# =============================================================================

@menu_bp.route('/api/admin/cafe-settings', methods=['GET'])
def admin_get_cafe_settings():
    cafe_id = request.args.get('cafe_id')
    if not cafe_id:
        return jsonify({"error": "cafe_id required"}), 400
    try:
        db = get_db()
        settings = db.get_cafe_settings(int(cafe_id))
        return jsonify(settings), 200
    except Exception as e:
        current_app.logger.exception("Admin get cafe settings failed")
        return jsonify({"error": str(e)}), 500

@menu_bp.route('/api/admin/cafe-settings', methods=['PUT'])
def admin_update_cafe_settings():
    try:
        data = request.get_json()
        cafe_id = data.get('cafe_id')
        if not cafe_id:
            return jsonify({"error": "cafe_id required"}), 400
        db = get_db()
        success = db.update_cafe_settings(
            int(cafe_id),
            global_discount_percent=float(data.get('global_discount_percent', 0)),
            global_discount_active=bool(data.get('global_discount_active', False))
        )
        return jsonify({"success": success}), 200
    except Exception as e:
        current_app.logger.exception("Admin update cafe settings failed")
        return jsonify({"error": str(e)}), 500
