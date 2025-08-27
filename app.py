import os
import shutil
import datetime
import re
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, abort
from printers import load_printers, save_printers
import mqtt_client
import psutil
import zipfile
import base64
import xml.etree.ElementTree as ET
from io import StringIO

app = Flask(__name__)

IMAGE_DIR = os.path.join(os.path.dirname(__file__), 'static', 'printers')
os.makedirs(IMAGE_DIR, exist_ok=True)

UPLOAD_DIR = os.path.expanduser("~/printserver/files")
THUMBNAILS_DIR = os.path.join(UPLOAD_DIR, "thumbnails")
HLAVNI_SLOZKA = os.path.join(UPLOAD_DIR, "hlavni_slozka")
os.makedirs(HLAVNI_SLOZKA, exist_ok=True)
os.makedirs(THUMBNAILS_DIR, exist_ok=True)

def generate_thumbnails_from_3mf(source_path, base_filename):
    """Extracts all plate thumbnails from a .3mf file."""
    with zipfile.ZipFile(source_path, 'r') as zf:
        # Find all images that match the pattern for thumbnails
        thumbnail_names = sorted([name for name in zf.namelist() if name.startswith('Metadata/plate_') and name.lower().endswith(('.png', '.jpg', '.jpeg')) and 'small' not in name and 'no_light' not in name])
        
        if not thumbnail_names:
            # Fallback to the single thumbnail if no plates are found
            if 'Metadata/thumbnail.png' in zf.namelist():
                thumbnail_names.append('Metadata/thumbnail.png')
            else:
                print(f"V souboru {base_filename} nebyla nalezena žádná miniatura.")
                return

        # Save all found thumbnails with unique names
        for i, thumb_name in enumerate(thumbnail_names):
            new_thumb_filename = f"{base_filename}_plate_{i+1}.png"
            thumbnail_path = os.path.join(THUMBNAILS_DIR, new_thumb_filename)
            thumbnail_data = zf.read(thumb_name)
            with open(thumbnail_path, 'wb') as f:
                f.write(thumbnail_data)
        print(f"Úspěšně vygenerováno {len(thumbnail_names)} miniatur pro {base_filename}")

def generate_thumbnail_from_gcode(source_path, base_filename):
    """Extracts a thumbnail from a .gcode file's Base64 comments."""
    thumbnail_path = os.path.join(THUMBNAILS_DIR, f"{base_filename}.png")
    with open(source_path, 'r', encoding='utf-8', errors='ignore') as f:
        in_thumbnail_block = False
        base64_data = ""
        for _ in range(2000): # Search in the beginning of the file
            line = f.readline()
            if not line: break
            if '; thumbnail begin' in line:
                in_thumbnail_block = True
                continue
            if '; thumbnail end' in line: break
            if in_thumbnail_block:
                base64_data += line.strip().lstrip('; ')
        if base64_data:
            image_data = base64.b64decode(base64_data)
            with open(thumbnail_path, 'wb') as f:
                f.write(image_data)
            print(f"Úspěšně vygenerována miniatura pro {base_filename}")

def generate_thumbnail(source_path):
    try:
        base_filename = os.path.basename(source_path)
        if source_path.lower().endswith('.3mf'):
            generate_thumbnails_from_3mf(source_path, base_filename)
        elif source_path.lower().endswith('.gcode'):
            generate_thumbnail_from_gcode(source_path, base_filename)
    except Exception as e:
        print(f"Chyba při generování miniatury pro {source_path}: {e}")

def parse_3mf_sliceinfo(zip_file):
    metadata = {"print_time": None, "material": None, "weight": None, "printer_model": None, "filament_length": None}
    try:
        sliceinfo_files = [n for n in zip_file.namelist() if n.lower().endswith("sliceinfo.config")]
        if not sliceinfo_files: return metadata
        xml_content = zip_file.read(sliceinfo_files[0]).decode('utf-8', 'ignore')
        root = ET.fromstring(xml_content)
        
        model_map = {"C11": "P1S", "C12": "P1S Combo", "C13": "A1", "C14": "A1 Combo"}

        for plate in root.findall("plate"):
            for meta in plate.findall("metadata"):
                key, value = meta.attrib.get("key", "").lower(), meta.attrib.get("value")
                if key == "prediction" and value:
                    try:
                        seconds = float(value)
                        h, m = int(seconds // 3600), int((seconds % 3600) // 60)
                        metadata["print_time"] = f"{h}h {m}m" if h > 0 else f"{m}m"
                    except: pass
                elif key == "weight" and value:
                    metadata["weight"] = f"{float(value):.2f}"
                elif key == "printer_model_id" and value:
                    metadata["printer_model"] = model_map.get(value, value)

            filament = plate.find("filament")
            if filament is not None:
                if filament.attrib.get("type"):
                    metadata["material"] = filament.attrib.get("type")
                if filament.attrib.get("length"):
                    try:
                        length_mm = float(filament.attrib.get("length"))
                        metadata["filament_length"] = f"{(length_mm / 1000):.2f}"
                    except: pass
    except Exception: pass
    return metadata

def parse_gcode_metadata(gcode_content_stream):
    metadata = {"layers": None, "nozzle_temp": None, "bed_temp": None, "nozzle_diameter": None, "layer_height": None}
    try:
        lines = gcode_content_stream.readlines()
        for i, line in enumerate(lines):
            if isinstance(line, bytes):
                line = line.decode('utf-8', 'ignore')
            
            line = line.strip()
            if "total layer number" in line:
                try: metadata["layers"] = int(re.search(r'(\d+)', line).group(1))
                except: pass
            elif "nozzle temperature:" in line:
                try: metadata["nozzle_temp"] = int(re.search(r'(\d+)', line).group(1))
                except: pass
            elif "bed temperature:" in line:
                try: metadata["bed_temp"] = int(re.search(r'(\d+)', line).group(1))
                except: pass
            elif "nozzle_diameter" in line:
                try: metadata["nozzle_diameter"] = float(re.search(r'([\d\.]+)', line).group(1))
                except: pass
            elif "layer_height" in line and "first_layer_height" not in line:
                try: metadata["layer_height"] = float(re.search(r'([\d\.]+)', line).group(1))
                except: pass
            
            if i > 500:
                break
    except Exception as e:
        print(f"Chyba při parsování G-kódu: {e}")
    return metadata

def get_all_metadata(abs_path):
    base_filename = os.path.basename(abs_path)
    
    thumbnail_urls = []
    # Find all thumbnails belonging to this file
    for thumb_file in sorted(os.listdir(THUMBNAILS_DIR)):
        if thumb_file.startswith(base_filename + "_plate_") or thumb_file == base_filename + ".png":
            url_path = thumb_file.replace("\\", "/")
            thumbnail_urls.append(f"/thumbnails/{url_path}")

    file_stat = os.stat(abs_path)
    size_kb = round(file_stat.st_size / 1024, 2)
    modified = datetime.datetime.fromtimestamp(file_stat.st_mtime).strftime('%d.%m.%Y %H:%M')

    metadata = { "print_time": None, "layers": None, "material": None, "weight": None, "nozzle_temp": None, "bed_temp": None, "printer_model": None, "filament_length": None, "nozzle_diameter": None, "layer_height": None }
    file_type = "Soubor"

    if abs_path.lower().endswith('.3mf'):
        file_type = "Tiskový soubor (.3mf)"
        with zipfile.ZipFile(abs_path, 'r') as zf:
            xml_meta = parse_3mf_sliceinfo(zf)
            metadata.update(xml_meta)
            gcode_files = [n for n in zf.namelist() if n.startswith('Metadata/plate_') and n.lower().endswith('.gcode')]
            if gcode_files:
                gcode_content = zf.read(gcode_files[0])
                gcode_stream = StringIO(gcode_content.decode('utf-8', 'ignore'))
                gcode_meta = parse_gcode_metadata(gcode_stream)
                metadata.update(gcode_meta)

    elif abs_path.lower().endswith(('.gcode', '.gcode.gz')):
        file_type = "G-Code (.gcode)"
        with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
            gcode_meta = parse_gcode_metadata(f)
            metadata.update(gcode_meta)

    note_content = ""
    note_path = abs_path + ".note"
    if os.path.exists(note_path):
        with open(note_path, 'r', encoding='utf-8') as f:
            note_content = f.read()

    return {
        "filename": base_filename,
        "full_path": os.path.relpath(abs_path, UPLOAD_DIR).replace("\\", "/"),
        "thumbnail_files": thumbnail_urls,
        "file_size_kb": size_kb,
        "modified": modified,
        "note": note_content,
        "file_type": file_type,
        **metadata
    }

@app.route('/')
def index():
    printers = load_printers()
    for printer in printers:
        printer["status"] = mqtt_client.check_status(printer["ip"], printer["access_code"])
    save_printers(printers)
    return render_template('index.html', printers=printers)

# ... (ostatní printer routes zůstávají stejné) ...
@app.route('/printer/add', methods=['POST'])
def add_printer():
    printers = load_printers()
    max_id = max([p["id"] for p in printers], default=0)
    new_printer = { "id": max_id + 1, "name": request.form.get("name", ""), "ip": request.form.get("ip", ""), "access_code": request.form.get("access_code", ""), "serial": request.form.get("serial", ""), "img": request.form.get("img", "default.png"), }
    printers.append(new_printer)
    save_printers(printers)
    return redirect(url_for('index'))

@app.route('/printer/<int:pid>')
def printer_detail(pid):
    printers = load_printers()
    printer = next((p for p in printers if p["id"] == pid), None)
    if not printer: return "Tiskárna nenalezena", 404
    return render_template('printer_detail.html', printer=printer)

@app.route('/printer/<int:pid>/update', methods=['POST'])
def printer_update(pid):
    printers = load_printers()
    for p in printers:
        if p["id"] == pid:
            p["name"] = request.form.get("name", p["name"])
            p["ip"] = request.form.get("ip", p["ip"])
            p["access_code"] = request.form.get("access_code", p["access_code"])
            p["serial"] = request.form.get("serial", p["serial"])
    save_printers(printers)
    return redirect(url_for('printer_detail', pid=pid))

@app.route('/printer/<int:pid>/cmd/<cmd>')
def printer_command(pid, cmd):
    return redirect(url_for('printer_detail', pid=pid))

@app.route('/printer/<int:pid>/upload_image', methods=['POST'])
def upload_printer_image(pid):
    printers = load_printers()
    printer = next((p for p in printers if p["id"] == pid), None)
    if not printer: return "Tiskárna nenalezena", 404
    if 'image' not in request.files: return redirect(url_for('printer_detail', pid=pid))
    file = request.files['image']
    if file.filename == '': return redirect(url_for('printer_detail', pid=pid))
    extension = os.path.splitext(file.filename)[1]
    filename = f"printer_{pid}{extension}"
    filepath = os.path.join(IMAGE_DIR, filename)
    file.save(filepath)
    printer['img'] = filename
    save_printers(printers)
    return redirect(url_for('printer_detail', pid=pid))

@app.route('/printer/<int:pid>/delete_image', methods=['POST'])
def delete_printer_image(pid):
    printers = load_printers()
    printer = next((p for p in printers if p["id"] == pid), None)
    if not printer: return "Tiskárna nenalezena", 404
    img = printer.get('img')
    if img and img != 'default.png':
        img_path = os.path.join(IMAGE_DIR, img)
        if os.path.exists(img_path): os.remove(img_path)
        printer['img'] = 'default.png'
        save_printers(printers)
    return redirect(url_for('printer_detail', pid=pid))

@app.route('/printer/<int:pid>/delete', methods=['POST'])
def delete_printer(pid):
    printers = load_printers()
    printer_to_delete = next((p for p in printers if p["id"] == pid), None)
    if not printer_to_delete: return "Tiskárna nenalezena", 404
    img = printer_to_delete.get('img')
    if img and img != 'default.png':
        img_path = os.path.join(IMAGE_DIR, img)
        if os.path.exists(img_path): os.remove(img_path)
    printers = [p for p in printers if p["id"] != pid]
    save_printers(printers)
    return redirect(url_for('index'))


@app.route('/files/')
def list_root_files():
    return render_template('files.html')

@app.route('/all_folders/')
def all_folders():
    folders = []
    for root, dirs, _ in os.walk(UPLOAD_DIR):
        for d in dirs:
            rel = os.path.relpath(os.path.join(root, d), UPLOAD_DIR)
            if rel == "thumbnails" or rel.startswith("thumbnails/"): continue
            folders.append("" if rel == "." else rel)
    folders = [f for f in folders if f != "thumbnails"]
    sorted_list = ["hlavni_slozka"] + sorted([f for f in folders if f != "hlavni_slozka"])
    return jsonify(folders=sorted_list)

@app.route('/files_in_folder/')
def files_in_folder():
    foldername = request.args.get('foldername', '')
    folder_path = os.path.join(UPLOAD_DIR, foldername)
    if not os.path.isdir(folder_path): return jsonify(files=[])
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f)) and not f.endswith('.note')]
    return jsonify(files=files)

@app.route('/download/<path:filepath>')
def download(filepath):
    file_path = os.path.join(UPLOAD_DIR, filepath)
    if not os.path.isfile(file_path): abort(404)
    return send_from_directory(UPLOAD_DIR, filepath, as_attachment=True)

@app.route('/upload/', methods=['POST'])
def upload():
    path = request.form.get('path', '')
    target_dir = os.path.join(UPLOAD_DIR, path) if path else UPLOAD_DIR
    os.makedirs(target_dir, exist_ok=True)
    file = request.files.get('file')
    if not file or file.filename == '': return jsonify(error="Neplatný soubor"), 400
    file_dest = os.path.join(target_dir, file.filename)
    file.save(file_dest)
    generate_thumbnail(file_dest)
    return jsonify(message="Soubor nahrán")

@app.route('/folders/', methods=['POST'])
def create_folder():
    foldername = request.form.get('foldername')
    if not foldername: return jsonify(error="Chybí název složky"), 400
    os.makedirs(os.path.join(UPLOAD_DIR, foldername), exist_ok=True)
    return jsonify(message="Složka vytvořena")

@app.route('/delete_folder/', methods=['POST'])
def delete_folder():
    foldername = request.form.get('foldername')
    path = os.path.join(UPLOAD_DIR, foldername)
    if os.path.isdir(path):
        shutil.rmtree(path)
        return jsonify(message="Složka smazána")
    return jsonify(error="Složka neexistuje"), 404

@app.route('/rename_folder/', methods=['POST'])
def rename_folder():
    source, target = request.form.get('source'), request.form.get('target')
    if not source or not target: return jsonify(error="Chybí data"), 400
    src = os.path.join(UPLOAD_DIR, source)
    tgt = os.path.join(UPLOAD_DIR, os.path.dirname(source), target)
    if os.path.isdir(src):
        os.rename(src, tgt)
        return jsonify(message="Složka přejmenována")
    return jsonify(error="Složka neexistuje"), 404

@app.route('/move/', methods=['POST'])
def move_file():
    filename, target_folder = request.form.get('filename'), request.form.get('target_folder')
    src = os.path.join(UPLOAD_DIR, filename)
    dest_dir = os.path.join(UPLOAD_DIR, target_folder)
    if not os.path.exists(src): return jsonify(error="Soubor neexistuje"), 404
    os.makedirs(dest_dir, exist_ok=True)
    shutil.move(src, os.path.join(dest_dir, os.path.basename(filename)))
    note_src, note_dst = src + ".note", os.path.join(dest_dir, os.path.basename(filename)) + ".note"
    if os.path.exists(note_src): shutil.move(note_src, note_dst)
    return jsonify(message="Soubor přesunut")

@app.route('/rename_file/', methods=['POST'])
def rename_file():
    old_name, new_name = request.form.get('old_name'), request.form.get('new_name')
    src = os.path.join(UPLOAD_DIR, old_name)
    dst = os.path.join(os.path.dirname(src), new_name)
    if not os.path.exists(src): return jsonify(error="Soubor neexistuje"), 404
    os.rename(src, dst)
    note_src, note_dst = src + ".note", dst + ".note"
    if os.path.exists(note_src): os.rename(note_src, note_dst)
    return jsonify(message="Soubor přejmenován")

@app.route('/save_note/', methods=['POST'])
def save_note():
    file_path, note = request.form.get('file_path'), request.form.get('note')
    note_path = os.path.join(UPLOAD_DIR, file_path) + ".note"
    os.makedirs(os.path.dirname(note_path), exist_ok=True)
    with open(note_path, "w", encoding="utf-8") as f: f.write(note)
    return jsonify(message="Poznámka uložena")

@app.route('/get_note/')
def get_note():
    file_path = request.args.get('file_path')
    note_path = os.path.join(UPLOAD_DIR, file_path) + ".note"
    if os.path.exists(note_path):
        with open(note_path, encoding="utf-8") as f: return jsonify(note=f.read())
    return jsonify(note="")

@app.route('/delete_file/', methods=['POST'])
def delete_file():
    filename = request.form.get('filename')
    file_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.isfile(file_path):
        os.remove(file_path)
        note_path = file_path + ".note"
        if os.path.exists(note_path): os.remove(note_path)
        return jsonify(message="Soubor smazán")
    return jsonify(error="Soubor neexistuje"), 404

@app.route('/disk_usage/')
def disk_usage():
    usage = psutil.disk_usage('.').percent
    return jsonify(disk_usage_percent=usage)

@app.route('/file_metadata/')
def file_metadata():
    file_path = request.args.get('file_path')
    abs_path = os.path.join(UPLOAD_DIR, file_path)
    if not os.path.isfile(abs_path):
        return jsonify(error="Soubor nenalezen"), 404
    return jsonify(get_all_metadata(abs_path))

# --- NOVÁ ROUTE PRO DETAIL SOUBORU ---
@app.route('/file_detail/<path:filepath>')
def file_detail_route(filepath):
    abs_path = os.path.join(UPLOAD_DIR, filepath)
    if not os.path.isfile(abs_path):
        return "Soubor nenalezen", 404
    
    metadata = get_all_metadata(abs_path)
    return render_template('file_detail.html', data=metadata)

@app.route('/thumbnails/<path:filename>')
def serve_thumbnail(filename):
    return send_from_directory(THUMBNAILS_DIR, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
