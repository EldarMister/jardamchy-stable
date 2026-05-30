import os

def patch_file(path, old, new):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    if old in content:
        content = content.replace(old, new, 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Patched {path}")
    else:
        print(f"Could not find target in {path}")

# 1. db.py
db_old_1 = """                cur.execute("CREATE INDEX IF NOT EXISTS idx_rental_listings_rooms_price ON rental_listings(rooms, price)")"""
db_new_1 = """                cur.execute("CREATE INDEX IF NOT EXISTS idx_rental_listings_rooms_price ON rental_listings(rooms, price)")
                
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS app_files (
                        id VARCHAR(50) PRIMARY KEY,
                        mime_type VARCHAR(100) NOT NULL,
                        data BYTEA NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')"""

db_old_2 = """    def add_menu_item"""
db_new_2 = """    def save_file(self, file_id: str, mime_type: str, data: bytes) -> bool:
        try:
            with self.get_cursor() as cur:
                cur.execute(
                    "INSERT INTO app_files (id, mime_type, data) VALUES (%s, %s, %s)",
                    (file_id, mime_type, data)
                )
            return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("Error saving file")
            return False

    def get_file(self, file_id: str):
        try:
            with self.get_cursor() as cur:
                cur.execute("SELECT mime_type, data FROM app_files WHERE id = %s", (file_id,))
                row = cur.fetchone()
                if row:
                    return {"mime_type": row["mime_type"], "data": bytes(row["data"])}
            return None
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("Error getting file")
            return None

    def add_menu_item"""

patch_file('src/db.py', db_old_1, db_new_1)
patch_file('src/db.py', db_old_2, db_new_2)

# 2. admin.py
admin_old = """# =============================================================================
# DASHBOARD
# ============================================================================="""
admin_new = """import uuid

# =============================================================================
# FILE UPLOADS
# =============================================================================

@admin_bp.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
            
        file_id = str(uuid.uuid4())
        mime_type = file.content_type
        data = file.read()
        
        db = get_db()
        if db.save_file(file_id, mime_type, data):
            # Return URL
            return jsonify({'url': f'/admin/files/{file_id}'}), 200
        else:
            return jsonify({'error': 'Failed to save file'}), 500
    except Exception as e:
        logger.exception('Error uploading file')
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/files/<file_id>', methods=['GET'])
def serve_file(file_id):
    try:
        db = get_db()
        file_data = db.get_file(file_id)
        if not file_data:
            return 'File not found', 404
        return Response(file_data['data'], mimetype=file_data['mime_type'])
    except Exception as e:
        logger.exception('Error serving file')
        return 'Internal error', 500

# =============================================================================
# DASHBOARD
# ============================================================================="""

patch_file('src/admin.py', admin_old, admin_new)

# 3. index.html
html_old_1 = """<textarea id="catalog-entry-photos" rows="3" placeholder="https://example.com/photo.jpg"></textarea>"""
html_new_1 = """<textarea id="catalog-entry-photos" rows="3" placeholder="https://example.com/photo.jpg"></textarea>
                    <br><input type="file" multiple accept="image/*" onchange="uploadFiles(event, 'catalog-entry-photos')" style="margin-top:5px;">"""

html_old_2 = """<textarea id="rental-photos" rows="3" placeholder="https://example.com/photo.jpg"></textarea>"""
html_new_2 = """<textarea id="rental-photos" rows="3" placeholder="https://example.com/photo.jpg"></textarea>
                    <br><input type="file" multiple accept="image/*" onchange="uploadFiles(event, 'rental-photos')" style="margin-top:5px;">"""

patch_file('admin_panel/index.html', html_old_1, html_new_1)
patch_file('admin_panel/index.html', html_old_2, html_new_2)

# 4. app.js
js_old = """function numOrNull(id) {"""
js_new = """async function uploadFiles(event, textareaId) {
    const files = event.target.files;
    if (!files.length) return;
    
    toast('Загрузка...', 'info');
    let uploadedCount = 0;
    const textarea = document.getElementById(textareaId);
    let currentUrls = readPhotoTextarea(textareaId);
    
    for (let i = 0; i < files.length; i++) {
        const formData = new FormData();
        formData.append('file', files[i]);
        
        try {
            const res = await fetch('/admin/upload', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (data.url) {
                currentUrls.push(data.url);
                uploadedCount++;
            } else {
                toast('Ошибка загрузки: ' + (data.error || 'unknown'), 'error');
            }
        } catch (e) {
            toast('Ошибка сети: ' + e.message, 'error');
        }
    }
    
    if (uploadedCount > 0) {
        textarea.value = currentUrls.join('\\n');
        toast(`Загружено фото: ${uploadedCount}`, 'success');
    }
    event.target.value = '';
}

function numOrNull(id) {"""

patch_file('admin_panel/app.js', js_old, js_new)
