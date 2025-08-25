{\rtf1\ansi\ansicpg1250\cocoartf2822
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 from fastapi import FastAPI, File, UploadFile\
import shutil\
import os\
\
UPLOAD_DIR = "uploads"\
os.makedirs(UPLOAD_DIR, exist_ok=True)\
\
app = FastAPI()\
\
@app.get("/")\
def home():\
    return \{"status": "Server b\uc0\u283 \'9e\'ed"\}\
\
@app.post("/upload/")\
async def upload_gcode(file: UploadFile = File(...)):\
    file_path = os.path.join(UPLOAD_DIR, file.filename)\
    with open(file_path, "wb") as buffer:\
        shutil.copyfileobj(file.file, buffer)\
    return \{"message": f"Soubor \{file.filename\} ulo\'9een"\}\
}