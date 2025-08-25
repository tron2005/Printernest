import re
import zipfile
import os
import xml.etree.ElementTree as ET
from urllib.parse import quote

UPLOAD_DIR = os.path.expanduser("~/printserver/files")
THUMBNAILS_DIR = os.path.join(UPLOAD_DIR, "thumbnails")
os.makedirs(THUMBNAILS_DIR, exist_ok=True)

def parse_bambulab_gcode_metadata_extended(file_path: str) -> dict:
    metadata = {
        "print_time": None,
        "material": None,
        "build_plate": None,
        "layers": None,
        "filament_length": None
    }
    time_pattern = re.compile(r";\s*model printing time:\s*([\dhms\s]+)", re.I)
    layers_pattern = re.compile(r";\s*total layer number:\s*(\d+)", re.I)
    filament_length_pattern = re.compile(r";\s*total filament length \[mm\]\s*:\s*([\d\.]+)", re.I)
    material_pattern = re.compile(r";\s*filament_type\s*=\s*([\w\s]+)", re.I)
    build_plate_pattern = re.compile(r";\s*curr_bed_type\s*=\s*([\w\s]+)", re.I)
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in range(200):
                line = f.readline()
                if not line:
                    break
                if time_match := time_pattern.search(line):
                    time_str = time_match.group(1).strip()
                    h = m = s = 0
                    for part in time_str.split():
                        if 'h' in part: h = int(part.replace('h',''))
                        elif 'm' in part: m = int(part.replace('m',''))
                        elif 's' in part: s = int(part.replace('s',''))
                    total_minutes = h * 60 + m + (1 if s >= 30 else 0)
                    metadata["print_time"] = f"{total_minutes} min"
                elif layers_match := layers_pattern.search(line):
                    metadata["layers"] = int(layers_match.group(1))
                elif filament_length_match := filament_length_pattern.search(line):
                    metadata["filament_length"] = filament_length_match.group(1)
                elif material_match := material_pattern.search(line):
                    metadata["material"] = material_match.group(1).strip()
                elif build_plate_match := build_plate_pattern.search(line):
                    metadata["build_plate"] = build_plate_match.group(1).strip()
                if all(metadata.values()):
                    break
    except Exception as e:
        metadata["error"] = str(e)
    return metadata

def parse_sliceinfo_config_xml(xml_content: str) -> dict:
    metadata = {"print_time": None,"material": None,"build_plate": None,"weight": None,"printer_model": None}
    try:
        root = ET.fromstring(xml_content)
        for plate in root.findall("plate"):
            for meta in plate.findall("metadata"):
                key = meta.attrib.get("key","").lower()
                value = meta.attrib.get("value")
                if key == "prediction" and value:
                    try:
                        m = int(float(value)//60)
                        metadata["print_time"]=f"{m} min"
                    except: pass
                elif key == "weight" and value:
                    metadata["weight"]=value
                elif key == "bed_type" and value:
                    metadata["build_plate"]=value
                elif key == "printer_model_id" and value:
                    model_map={"C12":"P1S","C13":"A1"}
                    metadata["printer_model"] = model_map.get(value,value)
            filament = plate.find("filament")
            if filament is not None:
                t=filament.attrib.get("type")
                if t: metadata["material"]=t
    except Exception as e:
        metadata["error"]=str(e)
    return metadata

def parse_3mf_metadata(file_path: str):
    result={"print_time":None,"material":None,"build_plate":None,"layers":None,"filament_length":None,"weight":None,"printer_model":None,"thumbnail_files":[]}
    if not zipfile.is_zipfile(file_path):
        return result
    try:
        with zipfile.ZipFile(file_path,'r') as z:
            img_exts=[".png",".jpg",".jpeg"]
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            thumb_dir = THUMBNAILS_DIR
            os.makedirs(thumb_dir, exist_ok=True)
            for name in z.namelist():
                if name.startswith("Metadata/plate_") and any(name.lower().endswith(ext) for ext in img_exts):
                    save_name = f"{base_name}_{os.path.basename(name)}"
                    tp = os.path.join(thumb_dir, save_name)
                    with open(tp,"wb") as f:
                        f.write(z.read(name))
                    result["thumbnail_files"].append(tp)
            sli = [n for n in z.namelist() if n.lower().endswith("sliceinfo.config")]
            if sli:
                xml=z.read(sli[0]).decode('utf-8','ignore')
                meta=parse_sliceinfo_config_xml(xml)
                for k,v in meta.items():
                    if v: result[k]=v
            gcs=[n for n in z.namelist() if n.startswith("Metadata/plate_") and n.lower().endswith(".gcode")]
            for g in gcs:
                gc=z.read(g).decode('utf-8','ignore')
                tmp=os.path.join(UPLOAD_DIR,"tmp_gcode.gcode")
                with open(tmp,"w",encoding="utf-8") as f:
                    f.write(gc)
                m=parse_bambulab_gcode_metadata_extended(tmp)
                for k in ["print_time","material","build_plate","layers","filament_length"]:
                    if m.get(k): result[k]=m[k]
                os.remove(tmp)
        return result
    except Exception as e:
        result["error"]=str(e)
        return result
