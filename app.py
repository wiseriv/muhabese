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
st.set_page_config(page_title="Mihsap AI - Pro", layout="wide", page_icon="💼")
API_KEY = st.secrets.get("GEMINI_API_KEY")

if not API_KEY:
    st.error("Secrets ayarı eksik!")
    st.stop()

# --- MUHASEBELEŞTİRME MOTORU (YENİ) ---
def muhasebe_fisne_cevir(df_ham):
    """
    Basit fiş listesini, Muhasebe Yevmiye Kaydına (Borç/Alacak) dönüştürür.
    Standart: 770 (Gider), 191 (KDV), 100 (Kasa)
    """
    yevmiye_satirlari = []
    
    for index, row in df_ham.iterrows():
        try:
            # Rakamları temizle ve sayıya çevir
            toplam = float(str(row.get('toplam_tutar', 0)).replace(',', '.'))
            kdv = float(str(row.get('toplam_kdv', 0)).replace(',', '.'))
            matrah = toplam - kdv
            
            tarih = row.get('tarih', datetime.now().strftime('%d.%m.%Y'))
            aciklama = f"{row.get('isyeri_adi', 'Fiş')} - {row.get('fiş_no', '')}"
            
            # SATIR 1: GİDER (Matrah) -> 770
            if matrah > 0:
                yevmiye_satirlari.append({
                    "Tarih": tarih,
                    "Hesap Kodu": "770.01.001",
                    "Açıklama": aciklama,
                    "Borç": matrah,
                    "Alacak": 0,
                    "Belge Türü": "FİŞ"
                })
            
            # SATIR 2: KDV -> 191
            if kdv > 0:
                yevmiye_satirlari.append({
                    "Tarih": tarih,
                    "Hesap Kodu": "191.18.001", # Varsayılan %18/20 kabul ettik
                    "Açıklama": "KDV",
                    "Borç": kdv,
                    "Alacak": 0,
                    "Belge Türü": ""
                })
                
            # SATIR 3: ÖDEME (Toplam) -> 100 Kasa
            yevmiye_satirlari.append({
                "Tarih": tarih,
                "Hesap Kodu": "100.01.001",
                "Açıklama": "Ödeme",
                "Borç": 0,
                "Alacak": toplam,
                "Belge Türü": ""
            })
            
        except:
            continue

    return pd.DataFrame(yevmiye_satirlari)

# --- GOOGLE SHEETS BAĞLANTISI ---
@st.cache_resource
def sheets_baglantisi_kur():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    if "gcp_service_account" not in st.secrets: return None
    try:
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
            rows.append([
                v.get("dosya_adi"), v.get("isyeri_adi"), v.get("fiş_no"),
                v.get("tarih"), v.get("toplam_tutar"), v.get("toplam_kdv"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])
        sheet.append_rows(rows)
        return True
    except: return False

# --- YARDIMCI ---
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
    try:
        base64_image = resmi_hazirla(dosya_objesi.getvalue())
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [{"parts": [
                {"text": """Bu fiş görüntüsünü analiz et. Cevabı saf JSON ver:
                {"isyeri_adi": "İşyeri", "fiş_no": "No", "tarih": "GG.AA.YYYY", "toplam_tutar": "00.00", "toplam_kdv": "00.00"}"""},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]}]
        }
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"hata": "Hata"}
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_objesi.name
        return veri
    except: return {"hata": "Okunamadı"}

# --- ARAYÜZ ---
with st.sidebar:
    st.header("⚙️ Panel")
    modeller = modelleri_getir()
    model = st.selectbox("Model", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)

st.title("💼 Mihsap AI - Müşavir Modu")

dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if dosyalar and st.button("🚀 Analiz Et"):
    tum_veriler = []
    bar = st.progress(0)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as executor:
        future_to_file = {executor.submit(gemini_ile_analiz_et, d, model): d for d in dosyalar}
        completed = 0
        for future in concurrent.futures.as_completed(future_to_file):
            res = future.result()
            if "hata" not in res: tum_veriler.append(res)
            completed += 1
            bar.progress(completed / len(dosyalar))
            time.sleep(0.5)

    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        
        # 1. Veritabanına Kaydet
        sheete_kaydet(tum_veriler)
        st.success("✅ Google Sheets'e kaydedildi.")

        # 2. Ekranı İkiye Böl
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📄 Basit Liste (Excel)")
            st.dataframe(df, use_container_width=True)
            
            buf1 = io.BytesIO()
            with pd.ExcelWriter(buf1, engine='openpyxl') as writer: df.to_excel(writer, index=False)
            st.download_button("📥 Basit Excel İndir", buf1.getvalue(), "basit_liste.xlsx")

        with col2:
            st.subheader("💼 Muhasebe Fişi (Luca/Zirve)")
            
            # Veriyi Muhasebe Formatına Çevir
            df_muhasebe = muhasebe_fisne_cevir(df)
            st.dataframe(df_muhasebe, use_container_width=True)
            
            buf2 = io.BytesIO()
            with pd.ExcelWriter(buf2, engine='openpyxl') as writer: df_muhasebe.to_excel(writer, index=False)
            st.download_button("📥 Muhasebe Fişi İndir", buf2.getvalue(), "muhasebe_fis_kaydi.xlsx", type="primary")
