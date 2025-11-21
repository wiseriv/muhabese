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
import plotly.express as px 

# --- AYARLAR ---
st.set_page_config(page_title="Mihsap AI - Live", layout="wide", page_icon="📈")

# GÜVENLİK
def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    if not st.session_state['giris_yapildi']:
        st.markdown("## 🔐 Panel Girişi")
        if st.text_input("Şifre", type="password") == "12345":
            st.session_state['giris_yapildi'] = True
            st.rerun()
        st.stop()
giris_kontrol()

API_KEY = st.secrets.get("GEMINI_API_KEY")
if not API_KEY: st.error("API Key Eksik!"); st.stop()

# --- GOOGLE SHEETS BAĞLANTISI VE VERİ ÇEKME ---
@st.cache_resource
def sheets_baglantisi_kur():
    if "gcp_service_account" not in st.secrets: return None
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        return gspread.authorize(creds)
    except: return None

def sheete_kaydet(veri_listesi):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Mihsap Veritabanı").sheet1
        rows = []
        for v in veri_listesi:
            rows.append([v.get("dosya_adi"), v.get("isyeri_adi"), v.get("fiş_no"), v.get("tarih"), v.get("kategori", "-"), v.get("toplam_tutar"), v.get("toplam_kdv"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        sheet.append_rows(rows)
        return True
    except: return False

def sheetten_veri_cek():
    """Google Sheets'teki TÜM veriyi okur ve DataFrame yapar."""
    client = sheets_baglantisi_kur()
    if not client: return pd.DataFrame()
    try:
        sheet = client.open("Mihsap Veritabanı").sheet1
        data = sheet.get_all_records() # Tüm tabloyu sözlük olarak çek
        df = pd.DataFrame(data)
        
        # Sütun isimlerini standartlaştıralım (Google Sheets'te ne yazdıysan o gelir)
        # Bizim beklediğimiz: 'Toplam Tutar', 'KDV', 'Kategori', 'Tarih'
        
        # Sayısal verileri temizle (Virgül/Nokta)
        if 'Toplam Tutar' in df.columns:
            df['toplam_tutar'] = df['Toplam Tutar'].astype(str).str.replace('.', '').str.replace(',', '.').astype(float)
        elif 'toplam_tutar' in df.columns: # Eski başlıklarsa
             df['toplam_tutar'] = df['toplam_tutar'].astype(str).str.replace(',', '.').astype(float)

        # Tarih formatını düzelt
        if 'Tarih' in df.columns:
            df['tarih_dt'] = pd.to_datetime(df['Tarih'], dayfirst=True, errors='coerce')
            
        return df
    except Exception as e:
        st.error(f"Veri Çekme Hatası: {e}")
        return pd.DataFrame()

# --- DİĞER YARDIMCI FONKSİYONLAR ---
@st.cache_data
def modelleri_getir():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        flash = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" in m['name']]
        diger = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" not in m['name']]
        return flash + diger
    except: return []

def resmi_hazirla(image_bytes):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    dosya_adi = dosya_objesi.name
    try:
        base64_image = resmi_hazirla(dosya_objesi.getvalue())
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        prompt_text = """Bu fişi analiz et. JSON yanıt ver: {"isyeri_adi": "Ad", "fiş_no": "No", "tarih": "YYYY-AA-GG", "kategori": "Gıda/Akaryakıt/Ofis/Teknoloji/Diğer", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}"""
        payload = {"contents": [{"parts": [{"text": prompt_text}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"dosya_adi": dosya_adi, "hata": f"Hata {response.status_code}"}
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_adi
        return veri
    except Exception as e: return {"dosya_adi": dosya_adi, "hata": str(e)}

def muhasebe_fisne_cevir(df_ham):
    yevmiye_satirlari = []
    for index, row in df_ham.iterrows():
        try:
            toplam = float(str(row.get('toplam_tutar', 0)).replace(',', '.'))
            kdv = float(str(row.get('toplam_kdv', 0)).replace(',', '.'))
            matrah = toplam - kdv
            tarih = row.get('tarih', datetime.now().strftime('%d.%m.%Y'))
            aciklama = f"{row.get('kategori', '')} - {row.get('isyeri_adi', '')}"
            if matrah > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "770.01", "Açıklama": aciklama, "Borç": matrah, "Alacak": 0})
            if kdv > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "191.18", "Açıklama": "KDV", "Borç": kdv, "Alacak": 0})
            yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "100.01", "Açıklama": "Ödeme", "Borç": 0, "Alacak": toplam})
        except: continue
    return pd.DataFrame(yevmiye_satirlari)

# --- ARAYÜZ ---
with st.sidebar:
    st.title("Mihsap AI")
    modeller = modelleri_getir()
    model = st.selectbox("Model", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)
    if st.button("🔄 Verileri Yenile"):
        st.cache_data.clear() # Cache temizle
        st.rerun()

tab1, tab2 = st.tabs(["📤 Fiş Yükle", "📊 Patron Paneli (Canlı)"])

with tab1:
    st.header("Fiş İşlemleri")
    dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)
    if dosyalar and st.button("🚀 Analiz Et"):
        tum_veriler = []
