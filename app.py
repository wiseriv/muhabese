import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import requests
import base64
import concurrent.futures
import time
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- AYARLAR ---
st.set_page_config(page_title="Mihsap AI - Database", layout="wide", page_icon="🗃️")
API_KEY = st.secrets.get("GEMINI_API_KEY")

if not API_KEY:
    st.error("Lütfen Secrets ayarlarından GEMINI_API_KEY'i ekleyin.")
    st.stop()

# --- GOOGLE SHEETS BAĞLANTISI ---
@st.cache_resource
def sheets_baglantisi_kur():
    """Google Sheets'e bağlanır."""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # Secrets'tan servis hesabı bilgilerini al
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    else:
        st.error("Google Cloud Service Account bilgileri Secrets'ta bulunamadı!")
        return None

def sheete_kaydet(veri_listesi):
    """Verileri Google E-Tabloya ekler."""
    try:
        client = sheets_baglantisi_kur()
        if not client: return
        
        # Tabloyu aç (İsmi tam olarak 'Mihsap Veritabanı' olmalı)
        sheet = client.open("Mihsap Veritabanı").sheet1
        
        rows_to_add = []
        for v in veri_listesi:
            row = [
                v.get("dosya_adi", "-"),
                v.get("isyeri_adi", "-"),
                v.get("fiş_no", "-"),
                v.get("tarih", "-"),
                v.get("toplam_tutar", "0"),
                v.get("toplam_kdv", "0"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S") # İşlenme zamanı
            ]
            rows_to_add.append(row)
            
        # Toplu ekle (Hızlıdır)
        sheet.append_rows(rows_to_add)
        st.toast(f"✅ {len(veri_listesi)} kayıt veritabanına işlendi!", icon="💾")
        
    except Exception as e:
        st.error(f"Veritabanı Hatası: {e}")
        st.info("İpucu: Tabloyu servis hesabıyla (client_email) paylaştın mı?")

# --- YARDIMCI FONKSİYONLAR ---
@st.cache_data
def modelleri_getir():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            flash = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" in m['name']]
            diger = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" not in m['name']]
            return flash + diger
        return []
    except:
        return []

def resmi_hazirla(image_bytes):
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    dosya_adi = dosya_objesi.name
    try:
        base64_image = resmi_hazirla(dosya_objesi.getvalue())
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [{
                "parts": [
                    {"text": """Bu fiş görüntüsünü analiz et. 
                    Cevabı SADECE aşağıdaki formatta saf JSON olarak ver:
                    {
                        "isyeri_adi": "İşyeri Adı",
                        "fiş_no": "Fiş No",
                        "tarih": "GG.AA.YYYY",
                        "toplam_tutar": "00.00",
                        "toplam_kdv": "00.00"
                    }"""},
                    {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
                ]
            }]
        }
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"dosya_adi": dosya_adi, "hata": f"Hata ({response.status_code})"}
        
        metin = response.json()['candidates'][0]['content']['parts'][0]['text']
        metin = metin.replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_adi
        return veri
    except Exception as e:
        return {"dosya_adi": dosya_adi, "hata": str(e)}

# --- ARAYÜZ ---
with st.sidebar:
    st.header("⚙️ Ayarlar")
    mevcut_modeller = modelleri_getir()
    secilen_model = st.selectbox("Model", mevcut_modeller, index=0) if mevcut_modeller else "gemini-1.5-flash"
    isci_sayisi = st.slider("Hız", 1, 5, 3)
    st.divider()
    st.markdown("[📂 Veritabanını Aç (Google Sheets)](https://docs.google.com/spreadsheets)") # Buraya kendi sheet linkini koyabilirsin

st.title("🗃️ Mihsap AI - Veritabanı Modu")
st.write("Okunan fişler otomatik olarak Google E-Tablo'ya kaydedilir.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    if st.button("🚀 Başlat ve Kaydet"):
        tum_veriler = []
        bar = st.progress(0)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=isci_sayisi) as executor:
            future_to_file = {executor.submit(gemini_ile_analiz_et, d, secilen_model): d for d in yuklenen_dosyalar}
            completed = 0
            for future in concurrent.futures.as_completed(future_to_file):
                sonuc = future.result()
                if "hata" not in sonuc:
                    tum_veriler.append(sonuc)
                completed += 1
                bar.progress(completed / len(yuklenen_dosyalar))
                time.sleep(0.5)

        if tum_veriler:
            # 1. Ekrana Bas
            df = pd.DataFrame(tum_veriler)
            cols = ["dosya_adi", "isyeri_adi", "fiş_no", "tarih", "toplam_tutar", "toplam_kdv"]
            st.dataframe(df[[c for c in cols if c in df.columns]], use_container_width=True)
            
            # 2. Google Sheets'e Kaydet (OTOMATİK)
            with st.spinner("Veritabanına işleniyor..."):
                sheete_kaydet(tum_veriler)
                
            st.success("İşlem Tamam! Veriler Google Sheets'e yollandı.")
