import os
import shutil
import datetime
import re
import json
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, abort
from printers import load_printers, save_printers
import mqtt_client
import psutil
import zipfile
import base64
import xml.etree.ElementTree as ET
from io import StringIO

app = Flask(__name__)

# --- Konfigurace ---
CONFIG = { "filament_price_per_kg": 550 }

IMAGE_DIR = os.path.join(os.path.dirname(__file__), 'static', 'printers')
os.makedirs(IMAGE_DIR, exist_ok=True)

UPLOAD_DIR = os.path.expanduser("~/printserver/files")
THUMBNAILS_DIR = os.path.join(UPLOAD_DIR, "thumbnails")
HLAVNI_SLOZKA = os.path.join(UPLOAD_DIR, "hlavni_slozka")
os.makedirs(HLAVNI_SLOZKA, exist_ok=True)
os.makedirs(THUMBNAILS_DIR, exist_ok=True)

def generate_thumbnails(source_path):
    try:
        base_filename = os.path.basename(source_path)
        if source_path.lower().endswith('.3mf'):
            with zipfile.ZipFile(source_path, 'r') as zf:
                thumbnail_names = sorted(
                    [name for name in zf.namelist() if name.startswith('Metadata/plate_') and name.lower().endswith(('.png', '.jpg', '.jpeg')) and 'small' not in name and 'no_light' not in name],
                    key=lambda x: int(re.search(r'plate_(\d+)', x).group(1)) if re.search(r'plate_(\d+)', x) else 0
                )
                if not thumbnail_names and 'Metadata/thumbnail.png' in zf.namelist():
                    thumbnail_names.append('Metadata/thumbnail.png')
                if not thumbnail_names: return
                for i, thumb_name in enumerate(thumbnail_names):
                    plate_index_match = re.search(r'plate_(\d+)', thumb_name)
                    plate_index = plate_index_match.group(1) if plate_index_match else (i + 1)
                    new_thumb_filename = f"{base_filename}_plate_{plate_index}.png"
                    thumbnail_path = os.path.join(THUMBNAILS_DIR, new_thumb_filename)
                    with open(thumbnail_path, 'wb') as f: f.write(zf.read(thumb_name))
    except Exception as e:
        print(f"Chyba při generování miniatury pro {source_path}: {e}")

def parse_gcode_header_params(gcode_content_stream):
    params = {}
    try:
        lines = gcode_content_stream.readlines()
        for line in lines[:2000]:
            if isinstance(line, bytes): line = line.decode('utf-8', 'ignore')
            line = line.strip()
            if line.startswith(';'):
                if "=" in line:
                    parts = line.lstrip('; ').split('=', 1)
                    if len(parts) == 2: params[parts[0].strip()] = parts[1].strip().strip('"')
                elif ":" in line:
                    parts = line.lstrip('; ').split(':', 1)
                    if len(parts) == 2: params[parts[0].strip()] = parts[1].strip()
    except Exception as e: print(f"Chyba při parsování G-kódu: {e}")
    return params

def seconds_to_hms(seconds_str):
    try:
        seconds = int(float(seconds_str))
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"
    except (ValueError, TypeError):
        return None

def get_full_metadata(abs_path):
    base_filename, file_stat = os.path.basename(abs_path), os.stat(abs_path)
    file_size_bytes = file_stat.st_size
    file_size_str = f"{round(file_size_bytes / 1024, 2)} kB" if file_size_bytes < 1024 * 1024 else f"{round(file_size_bytes / (1024 * 1024), 2)} MB"
    data = {"filename": base_filename, "full_path": os.path.relpath(abs_path, UPLOAD_DIR).replace("\\", "/"), "file_size": file_size_str, "modified": datetime.datetime.fromtimestamp(file_stat.st_mtime).strftime('%d.%m.%Y %H:%M'), "plates": [], "global_info": {}}
    
    note_path = abs_path + ".note"
    notes = {}
    if os.path.exists(note_path):
        with open(note_path, 'r', encoding='utf-8') as f:
            try: notes = json.load(f)
            except json.JSONDecodeError: f.seek(0); notes = {"plate_1": f.read()}

    if abs_path.lower().endswith('.3mf'):
        with zipfile.ZipFile(abs_path, 'r') as zf:
            plate_details, filaments_map = {}, {}
            
            if "Metadata/slice_info.config" in zf.namelist():
                try:
                    root = ET.fromstring(zf.read("Metadata/slice_info.config").decode('utf-8', 'ignore'))
                    model_map = {"C11": "P1S", "C12": "P1S Combo", "C13": "A1", "C14": "A1 Combo", "N2S": "A1"}
                    for meta in root.findall(".//metadata"):
                        if meta.attrib.get("key", "").lower() == "printer_model_id":
                            data["global_info"]["printer_model"] = model_map.get(meta.attrib.get("value"), meta.attrib.get("value"))
                    for filament_node in root.findall(".//filament"):
                        filaments_map[filament_node.attrib.get("id")] = { "type": filament_node.attrib.get("type"), "color": filament_node.attrib.get("color", "AAAAAA")[:6] }
                    for plate_node in root.findall("plate"):
                        idx_node = plate_node.find('.//metadata[@key="index"]')
                        if idx_node is not None:
                            idx = idx_node.attrib.get('value')
                            if idx:
                                plate_details[idx] = {
                                    'prediction': plate_node.find('.//metadata[@key="prediction"]').attrib.get('value') if plate_node.find('.//metadata[@key="prediction"]') is not None else None,
                                    'weight': plate_node.find('.//metadata[@key="weight"]').attrib.get('value') if plate_node.find('.//metadata[@key="weight"]') is not None else None,
                                    'filament_id': plate_node.find("filament").attrib.get("id") if plate_node.find("filament") is not None else None
                                }
                except Exception as e: print(f"Chyba při parsování slice_info.config: {e}")
            
            if "Metadata/model_settings.config" in zf.namelist():
                try:
                    root = ET.fromstring(zf.read("Metadata/model_settings.config").decode('utf-8', 'ignore'))
                    for plate_node in root.findall("plate"):
                        idx_node = plate_node.find('.//metadata[@key="plater_id"]')
                        name_node = plate_node.find('.//metadata[@key="plater_name"]')
                        if idx_node is not None and name_node is not None:
                            idx = idx_node.attrib.get('value')
                            name = name_node.attrib.get('value')
                            if idx and idx in plate_details:
                                plate_details[idx]['name'] = name
                except Exception as e: print(f"Chyba při parsování model_settings.config: {e}")

            gcode_files = sorted([n for n in zf.namelist() if n.startswith('Metadata/plate_') and n.lower().endswith('.gcode')], key=lambda x: int(re.search(r'plate_(\d+)', x).group(1)) if re.search(r'plate_(\d+)', x) else 0)
            
            data["file_type"] = "Projekt Bambu Studio" if not gcode_files else "Tiskový soubor (.3mf)"
            
            num_plates = len(gcode_files) if gcode_files else 1
            for i in range(1, num_plates + 1):
                idx = str(i)
                plate_meta = plate_details.get(idx, {})
                
                gcode_params = {}
                if gcode_files and i <= len(gcode_files):
                    gcode_file_name = gcode_files[i-1]
                    gcode_params = parse_gcode_header_params(StringIO(zf.read(gcode_file_name).decode('utf-8', 'ignore')))

                time_from_gcode = gcode_params.get('model printing time')
                time_from_xml = seconds_to_hms(plate_meta.get('prediction'))
                final_print_time = time_from_xml or time_from_gcode

                nozzle_temp_initial_str = str(gcode_params.get('nozzle_temperature_initial_layer') or gcode_params.get('nozzle_temperature', '0')).split(',')[0]
                nozzle_temp_other_str = str(gcode_params.get('nozzle_temperature', nozzle_temp_initial_str)).split(',')[0]

                bed_temp_initial_val = (gcode_params.get('textured_plate_temp_initial_layer') or gcode_params.get('hot_plate_temp_initial_layer') or gcode_params.get('cool_plate_temp_initial_layer') or gcode_params.get('bed_temperature_initial_layer'))
                bed_temp_other_val = (gcode_params.get('textured_plate_temp') or gcode_params.get('hot_plate_temp') or gcode_params.get('cool_plate_temp') or gcode_params.get('bed_temperature'))
                bed_temp_initial_str = str(bed_temp_initial_val or bed_temp_other_val or '0').split(',')[0]
                bed_temp_other_str = str(bed_temp_other_val or bed_temp_initial_str).split(',')[0]
                
                filaments_used = []
                types = gcode_params.get('filament_type', '').split(';')
                colors = gcode_params.get('filament_colour', '').split(';')
                weights_list = gcode_params.get('total filament weight [g]', '').split(',')
                lengths_list = gcode_params.get('total filament length [mm]', '').split(',')
                
                total_weight = 0
                total_length_mm = 0
                
                num_filaments = len(types)
                if num_filaments > 0 and types[0]:
                    for f_idx in range(num_filaments):
                        try:
                            weight = float(weights_list[f_idx]) if f_idx < len(weights_list) else 0
                            length_mm = float(lengths_list[f_idx]) if f_idx < len(lengths_list) else 0
                        except (ValueError, IndexError):
                            weight = 0
                            length_mm = 0
                        
                        total_weight += weight
                        total_length_mm += length_mm
                        
                        filaments_used.append({
                            "type": types[f_idx] if f_idx < len(types) else 'N/A',
                            "color": colors[f_idx].strip('#') if f_idx < len(colors) else 'AAAAAA',
                            "weight": f"{weight:.2f}",
                            "length": f"{length_mm / 1000:.2f}"
                        })

                final_plate_data = {
                    "plate_index": i, "plate_name": plate_meta.get("name") or f"Plát {i}",
                    "thumbnail": f"/thumbnails/{base_filename}_plate_{i}.png", "note": notes.get(f"plate_{i}", ""),
                    "print_time": final_print_time,
                    "weight": f"{total_weight:.2f}" if total_weight > 0 else None,
                    "filament_length": f"{total_length_mm / 1000:.2f}" if total_length_mm > 0 else None,
                    "nozzle_diameter": float(gcode_params.get('nozzle_diameter', 0)),
                    "layer_height": float(gcode_params.get('layer_height', 0)),
                    "layers": int(gcode_params.get('total layer number', 0)),
                    "bed_temp_initial": int(float(bed_temp_initial_str)),
                    "bed_temp_other": int(float(bed_temp_other_str)),
                    "nozzle_temp_initial": int(float(nozzle_temp_initial_str)),
                    "nozzle_temp_other": int(float(nozzle_temp_other_str)),
                    "plate_type": gcode_params.get('curr_bed_type', 'N/A').replace('_', ' ').title(),
                    "filaments_used": filaments_used,
                    "initial_layer_print_height": float(gcode_params.get('initial_layer_print_height', 0)),
                    "wall_loops": int(gcode_params.get('wall_loops', 0)),
                    "sparse_infill_pattern": gcode_params.get('sparse_infill_pattern', 'N/A'),
                    "sparse_infill_density": gcode_params.get('sparse_infill_density', 'N/A'),
                    "enable_support": "Ano" if gcode_params.get('enable_support') == '1' else "Ne",
                    "support_type": gcode_params.get('support_type', 'N/A'),
                    "brim_type": gcode_params.get('brim_type', 'none').replace('_', ' ').title(),
                    "brim_width": int(gcode_params.get('brim_width', 0))
                }
                if total_weight > 0:
                    cost = (total_weight / 1000) * CONFIG["filament_price_per_kg"]
                    final_plate_data['print_cost'] = f"{cost:.2f} Kč"
                data["plates"].append(final_plate_data)
    return data

def get_list_view_metadata(abs_path):
    base_filename, file_stat = os.path.basename(abs_path), os.stat(abs_path)
    data = {"name": base_filename, "path": os.path.relpath(abs_path, UPLOAD_DIR).replace("\\", "/"), "modified": datetime.datetime.fromtimestamp(file_stat.st_mtime).strftime('%d.%m.%Y %H:%M'), "thumbnail_files": [], "note": "", "file_type": "N/A", "printer_model": "", "nozzle_diameter": None}
    try:
        if abs_path.lower().endswith('.3mf'):
            with zipfile.ZipFile(abs_path, 'r') as zf:
                thumb_name = next((name for name in zf.namelist() if name.startswith('Metadata/plate_') and name.lower().endswith('.png') and 'small' not in name and 'no_light' not in name), None)
                if thumb_name:
                    plate_index = (re.search(r'plate_(\d+)', thumb_name) or [None, '1'])[1]
                    data["thumbnail_files"].append(f"/thumbnails/{base_filename}_plate_{plate_index}.png")
                
                printer_name = None
                if "Metadata/slice_info.config" in zf.namelist():
                    root = ET.fromstring(zf.read("Metadata/slice_info.config").decode('utf-8', 'ignore'))
                    model_map = {"C11": "P1S", "C12": "P1S Combo", "C13": "A1", "C14": "A1 Combo", "N2S": "A1"}
                    for meta in root.findall(".//metadata"):
                        if meta.attrib.get("key", "").lower() == "printer_model_id": printer_name = model_map.get(meta.attrib.get("value"), meta.attrib.get("value"))
                
                if not printer_name and "Metadata/model_settings.config" in zf.namelist():
                    config_content = zf.read("Metadata/model_settings.config").decode('utf-8', 'ignore')
                    match = re.search(r'printer_model\s*=\s*"([^"]+)"', config_content)
                    if match: printer_name = match.group(1)

                if printer_name: data["printer_model"] = printer_name.replace("Bambu Lab ", "")
                
                gcode_files = [n for n in zf.namelist() if n.lower().endswith('.gcode')]
                if not gcode_files: data["file_type"], data["printer_model"] = "Projekt Bambu Studio", ""
                else: 
                    data["file_type"] = "Tiskový soubor (.3mf)"
                    params = parse_gcode_header_params(StringIO(zf.read(gcode_files[0]).decode('utf-8', 'ignore')))
                    data["nozzle_diameter"] = params.get("nozzle_diameter")
        
        note_path = abs_path + ".note"
        if os.path.exists(note_path):
            with open(note_path, 'r', encoding='utf-8') as f:
                try: data["note"] = json.load(f).get("plate_1", "")
                except json.JSONDecodeError: pass
    except Exception as e:
        print(f"Chyba při rychlém čtení metadat pro {base_filename}: {e}")
        data["file_type"] = "Chyba při čtení"
    return data

def get_cached_list_view_metadata(abs_path):
    cache_path = abs_path + ".metadata_cache.json"
    if os.path.exists(cache_path):
        try:
            if os.path.getmtime(cache_path) > os.path.getmtime(abs_path):
                with open(cache_path, 'r', encoding='utf-8') as f: return json.load(f)
        except FileNotFoundError: pass
    metadata = get_list_view_metadata(abs_path)
    try:
        with open(cache_path, 'w', encoding='utf-8') as f: json.dump(metadata, f)
    except IOError as e: print(f"Chyba při zápisu do cache souboru {cache_path}: {e}")
    return metadata

# --- Routes ---
@app.route('/folder_contents/')
def folder_contents():
    foldername = request.args.get('foldername', 'hlavni_slozka')
    folder_path = os.path.join(UPLOAD_DIR, foldername)
    if not os.path.isdir(folder_path): return jsonify(files=[])
    all_files_data = []
    for filename in os.listdir(folder_path):
        if os.path.isfile(os.path.join(folder_path, filename)) and not filename.endswith(('.note', '.json')):
            all_files_data.append(get_cached_list_view_metadata(os.path.join(folder_path, filename)))
    return jsonify(files=all_files_data)

@app.route('/files/')
def list_root_files():
    return render_template('files.html')

@app.route('/file_detail/<path:filepath>')
def file_detail_route(filepath):
    abs_path = os.path.join(UPLOAD_DIR, filepath)
    if not os.path.isfile(abs_path): return "Soubor nenalezen", 404
    metadata = get_full_metadata(abs_path)
    return render_template('file_detail.html', data=metadata)

@app.route('/settings/')
def settings_page():
    return render_template('settings.html', current_config=CONFIG)

@app.route('/')
def index():
    printers = load_printers()
    for printer in printers:
        printer["status"] = mqtt_client.check_status(printer["ip"], printer["access_code"])
    save_printers(printers)
    return render_template('index.html', printers=printers)

@app.route('/all_folders/')
def all_folders():
    folders = set()
    for root, dirs, _ in os.walk(UPLOAD_DIR):
        if 'thumbnails' in dirs: dirs.remove('thumbnails')
        rel_path = os.path.relpath(root, UPLOAD_DIR)
        if rel_path != '.': folders.add(rel_path.replace("\\", "/"))
    folder_list = sorted(list(folders))
    final_list = ['hlavni_slozka'] + [f for f in folder_list if f != 'hlavni_slozka']
    return jsonify(folders=final_list)

@app.route('/upload/', methods=['POST'])
def upload():
    path = request.form.get('path', '')
    target_dir = os.path.join(UPLOAD_DIR, path)
    os.makedirs(target_dir, exist_ok=True)
    for file in request.files.getlist('file'):
        if file and file.filename != '':
            file_dest = os.path.join(target_dir, file.filename)
            file.save(file_dest)
            generate_thumbnails(file_dest)
            cache_path = file_dest + ".metadata_cache.json"
            if os.path.exists(cache_path): os.remove(cache_path)
    return jsonify(message="Soubory nahrány")

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

@app.route('/folders/', methods=['POST'])
def create_folder():
    foldername = request.form.get('foldername')
    if not foldername: return jsonify(error="Chybí název složky"), 400
    try:
        os.makedirs(os.path.join(UPLOAD_DIR, foldername), exist_ok=True)
        return jsonify(message="Složka vytvořena")
    except Exception as e:
        return jsonify(error=str(e)), 500

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
    source = request.form.get('source')
    target = request.form.get('target')
    if not source or not target: return jsonify(error="Chybí data"), 400
    src = os.path.join(UPLOAD_DIR, source)
    tgt = os.path.join(os.path.dirname(src), target)
    if os.path.isdir(src):
        os.rename(src, tgt)
        return jsonify(message="Složka přejmenována")
    return jsonify(error="Složka neexistuje"), 404

@app.route('/move/', methods=['POST'])
def move_file():
    filename = request.form.get('filename')
    target_folder = request.form.get('target_folder')
    src = os.path.join(UPLOAD_DIR, filename)
    dest_dir = os.path.join(UPLOAD_DIR, target_folder)
    if not os.path.exists(src): return jsonify(error="Soubor neexistuje"), 404
    os.makedirs(dest_dir, exist_ok=True)
    shutil.move(src, os.path.join(dest_dir, os.path.basename(filename)))
    for ext in [".note", ".metadata_cache.json"]:
        if os.path.exists(src + ext): shutil.move(src + ext, os.path.join(dest_dir, os.path.basename(src + ext)))
    return jsonify(message="Soubor přesunut")

@app.route('/rename_file/', methods=['POST'])
def rename_file():
    old_name_path = request.form.get('old_name')
    new_name_base = request.form.get('new_name')

    if not old_name_path or not new_name_base:
        return jsonify(error="Chybí starý nebo nový název souboru."), 400

    src = os.path.join(UPLOAD_DIR, old_name_path)
    dst = os.path.join(os.path.dirname(src), new_name_base)

    if not os.path.exists(src):
        return jsonify(error="Zdrojový soubor neexistuje."), 404

    try:
        os.rename(src, dst)

        if os.path.exists(src + ".note"):
            os.rename(src + ".note", dst + ".note")

        if os.path.exists(src + ".metadata_cache.json"):
            os.remove(src + ".metadata_cache.json")

        old_base_name = os.path.basename(old_name_path)
        new_base_name_for_thumb = os.path.basename(new_name_base)
        for thumb_file in os.listdir(THUMBNAILS_DIR):
            if thumb_file.startswith(old_base_name):
                new_thumb_name = thumb_file.replace(old_base_name, new_base_name_for_thumb, 1)
                os.rename(os.path.join(THUMBNAILS_DIR, thumb_file), os.path.join(THUMBNAILS_DIR, new_thumb_name))

        new_full_path = os.path.relpath(dst, UPLOAD_DIR).replace("\\", "/")
        return jsonify(message="Soubor úspěšně přejmenován.", new_path=new_full_path)

    except Exception as e:
        print(f"!!! KRITICKÁ CHYBA BĚHEM PŘEJMENOVÁNÍ: {e} !!!")
        if os.path.exists(dst) and not os.path.exists(src):
            os.rename(dst, src)
        return jsonify(error=f"Chyba na serveru při přejmenování: {e}"), 500

@app.route('/save_note/', methods=['POST'])
def save_note():
    file_path = request.form.get('file_path')
    plate_index = request.form.get('plate_index')
    note = request.form.get('note')
    note_path = os.path.join(UPLOAD_DIR, file_path) + ".note"
    notes = {}
    if os.path.exists(note_path):
        with open(note_path, 'r', encoding='utf-8') as f:
            try: notes = json.load(f)
            except json.JSONDecodeError: pass 
    notes[f"plate_{plate_index}"] = note
    with open(note_path, "w", encoding="utf-8") as f: json.dump(notes, f, ensure_ascii=False, indent=4)
    cache_path = os.path.join(UPLOAD_DIR, file_path) + ".metadata_cache.json"
    if os.path.exists(cache_path): os.remove(cache_path)
    return jsonify(message="Poznámka uložena")

@app.route('/get_note/')
def get_note():
    file_path = request.args.get('file_path')
    plate_index = request.args.get('plate_index')
    note_path = os.path.join(UPLOAD_DIR, file_path) + ".note"
    if os.path.exists(note_path):
        with open(note_path, 'r', encoding="utf-8") as f:
            try:
                notes = json.load(f)
                return jsonify(note=notes.get(f"plate_{plate_index}", ""))
            except json.JSONDecodeError: return jsonify(note="")
    return jsonify(note="")

@app.route('/delete_file/', methods=['POST'])
def delete_file():
    filename = request.form.get('filename')
    file_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.isfile(file_path):
        os.remove(file_path)
        for ext in [".note", ".metadata_cache.json"]:
            if os.path.exists(file_path + ext): os.remove(file_path + ext)
        base_name = os.path.basename(filename)
        for thumb_file in os.listdir(THUMBNAILS_DIR):
            if thumb_file.startswith(base_name):
                os.remove(os.path.join(THUMBNAILS_DIR, thumb_file))
        return jsonify(message="Soubor smazán")
    return jsonify(error="Soubor neexistuje"), 404

@app.route('/delete_multiple_files/', methods=['POST'])
def delete_multiple_files():
    files_to_delete = request.json.get('filenames', [])
    deleted_count = 0
    errors = []
    for filename in files_to_delete:
        file_path = os.path.join(UPLOAD_DIR, filename)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                for ext in [".note", ".metadata_cache.json"]:
                    if os.path.exists(file_path + ext): os.remove(file_path + ext)
                base_name = os.path.basename(filename)
                for thumb_file in os.listdir(THUMBNAILS_DIR):
                    if thumb_file.startswith(base_name):
                        os.remove(os.path.join(THUMBNAILS_DIR, thumb_file))
                deleted_count += 1
            except Exception as e:
                errors.append(f"Chyba při mazání souboru {filename}: {e}")
        else:
            errors.append(f"Soubor {filename} neexistuje.")
    if errors:
        return jsonify(message=f"Smazáno {deleted_count} souborů, ale vyskytly se chyby.", errors=errors), 500
    return jsonify(message=f"Úspěšně smazáno {deleted_count} souborů.")

@app.route('/disk_usage/')
def disk_usage():
    usage = psutil.disk_usage(UPLOAD_DIR).percent
    return jsonify(disk_usage_percent=usage)

@app.route('/thumbnails/<path:filename>')
def serve_thumbnail(filename):
    return send_from_directory(THUMBNAILS_DIR, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
