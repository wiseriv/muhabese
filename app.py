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
st.set_page_config(page_title="Mihsap AI - Kategori", layout="wide", page_icon="🏷️")

# GÜVENLİK (ŞİFRE: 12345)
def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    if not st.session_state['giris_yapildi']:
        st.markdown("## 🔐 Mihsap AI Panel Girişi")
        if st.text_input("Şifre", type="password") == "12345":
            st.session_state['giris_yapildi'] = True
            st.rerun()
        st.stop()
giris_kontrol()

API_KEY = st.secrets.get("GEMINI_API_KEY")
if not API_KEY: st.error("API Key Eksik!"); st.stop()

# --- 1. DEDEKTİF MODÜLÜ ---
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

# --- 2. RESİM HAZIRLAMA ---
def resmi_hazirla(image_bytes):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

# --- 3. GEMINI ANALİZ (KATEGORİ EKLENDİ) ---
def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    dosya_adi = dosya_objesi.name
    try:
        base64_image = resmi_hazirla(dosya_objesi.getvalue())
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        
        # --- YENİ PROMPT: KATEGORİ İSTİYORUZ ---
        prompt_text = """
        Bu fişi analiz et ve aşağıdaki JSON formatında yanıt ver.
        "kategori" alanını harcamaya göre şunlardan biri seç: 
        [Gıda/Market, Akaryakıt/Ulaşım, Kırtasiye/Ofis, Teknoloji, Konaklama, Diğer]
        
        JSON Formatı:
        {
            "isyeri_adi": "İşyeri Adı",
            "fiş_no": "Fiş No",
            "tarih": "GG.AA.YYYY",
            "kategori": "Tahmin Edilen Kategori",
            "toplam_tutar": "00.00",
            "toplam_kdv": "00.00"
        }
        """
        
        payload = {
            "contents": [{"parts": [
                {"text": prompt_text},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]}]
        }

        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 429: return {"dosya_adi": dosya_adi, "hata": "Hız Sınırı!"}
        if response.status_code != 200: return {"dosya_adi": dosya_adi, "hata": f"Hata {response.status_code}"}

        metin = response.json()['candidates'][0]['content']['parts'][0]['text']
        metin = metin.replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_adi
        return veri

    except Exception as e:
        return {"dosya_adi": dosya_adi, "hata": str(e)}

# --- 4. MUHASEBE MOTORU ---
def muhasebe_fisne_cevir(df_ham):
    yevmiye_satirlari = []
    for index, row in df_ham.iterrows():
        try:
            toplam = float(str(row.get('toplam_tutar', 0)).replace(',', '.'))
            kdv = float(str(row.get('toplam_kdv', 0)).replace(',', '.'))
            matrah = toplam - kdv
            tarih = row.get('tarih', datetime.now().strftime('%d.%m.%Y'))
            kategori = row.get('kategori', 'Genel')
            
            # Açıklamaya Kategori bilgisini de ekleyelim
            aciklama = f"{kategori} - {row.get('isyeri_adi', 'Fiş')} ({row.get('fiş_no', '')})"
            
            # 1. GİDER (770)
            if matrah > 0:
                yevmiye_satirlari.append({
                    "Tarih": tarih, "Hesap Kodu": "770.01.001", 
                    "Açıklama": aciklama, "Borç": matrah, "Alacak": 0
                })
            
            # 2. KDV (191)
            if kdv > 0:
                yevmiye_satirlari.append({
                    "Tarih": tarih, "Hesap Kodu": "191.18.001", 
                    "Açıklama": "İndirilecek KDV", "Borç": kdv, "Alacak": 0
                })
                
            # 3. KASA (100)
            yevmiye_satirlari.append({
                "Tarih": tarih, "Hesap Kodu": "100.01.001", 
                "Açıklama": "Ödeme", "Borç": 0, "Alacak": toplam
            })
        except: continue
    return pd.DataFrame(yevmiye_satirlari)

# --- 5. VERİTABANI (SHEETS) ---
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
            rows.append([
                v.get("dosya_adi"), v.get("isyeri_adi"), v.get("fiş_no"),
                v.get("tarih"), v.get("kategori", "-"), # Kategori eklendi
                v.get("toplam_tutar"), v.get("toplam_kdv"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])
        sheet.append_rows(rows)
        return True
    except: return False

# --- ARAYÜZ ---
with st.sidebar:
    st.header("⚙️ Panel")
    modeller = modelleri_getir()
    model = st.selectbox("Model", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)
    st.info(f"Aktif Model: {model}")

st.title("🏷️ Mihsap AI - Kategori Uzmanı")
st.write("Fişleri okur, ürünleri analiz eder ve otomatik kategorize eder.")

dosyalar = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if dosyalar and st.button("🚀 Analiz ve Kategorize Et"):
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
        sheete_kaydet(tum_veriler)
        st.success("✅ İşlem Tamam!")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📊 Kategorili Liste")
            # Kategori sütununu öne alalım
            cols = ["kategori", "isyeri_adi", "tarih", "toplam_tutar", "toplam_kdv"]
            st.dataframe(df[[c for c in cols if c in df.columns]], use_container_width=True)
            
        with col2:
            st.subheader("💼 Muhasebe Fişi")
            df_muh = muhasebe_fisne_cevir(df
