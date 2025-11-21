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
st.set_page_config(page_title="Mihsap AI - V10 Fix", layout="wide", page_icon="🔧")

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

# --- HATA AYIKLAYICI (DEBUGGER) ---
def temizle_ve_sayiya_cevir(deger):
    """Para birimlerini, virgülleri temizler ve float yapar."""
    if pd.isna(deger) or deger == "": return 0.0
    try:
        s = str(deger).replace("₺", "").replace("TL", "").strip()
        # 1.000,50 formatı -> 1000.50
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except:
        return 0.0

# --- GOOGLE SHEETS BAĞLANTISI ---
@st.cache_resource
def sheets_baglantisi_kur():
    if "gcp_service_account" not in st.secrets: 
        st.error("Secrets içinde [gcp_service_account] bulunamadı!")
        return None
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        return gspread.authorize(creds)
    except Exception as e: 
        st.error(f"Bağlantı Hatası: {e}")
        return None

def sheete_kaydet(veri_listesi):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Mihsap Veritabanı").sheet1
        rows = []
        for v in veri_listesi:
            rows.append([
                v.get("dosya_adi", "-"), 
                v.get("isyeri_adi", "-"), 
                v.get("fiş_no", "-"), 
                v.get("tarih", "-"), 
                v.get("kategori", "Diğer"), 
                str(v.get("toplam_tutar", "0")), # String olarak atalım, Sheet kendi anlasın
                str(v.get("toplam_kdv", "0")), 
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])
        sheet.append_rows(rows)
        return True
    except Exception as e: 
        st.error(f"Kayıt Hatası: {e}")
        return False

def sheetten_veri_cek():
    """Google Sheets'ten veriyi çeker ve sütunları standartlaştırır."""
    client = sheets_baglantisi_kur()
    if not client: return pd.DataFrame()
    try:
        sheet = client.open("Mihsap Veritabanı").sheet1
        data = sheet.get_all_records()
        
        if not data:
            st.warning("⚠️ Tablo boş görünüyor.")
            return pd.DataFrame()
            
        df = pd.DataFrame(data)
        
        # Sütun isimlerini temizle (Boşlukları sil, küçük harf yap)
        # Örn: "Toplam Tutar " -> "toplamtutar"
        df.columns = [c.strip().lower().replace(" ", "").replace("_", "") for c in df.columns]
        
        # Hangi sütun neye denk geliyor bulalım
        # Bizim aradıklarımız: isyeri, tarih, kategori, toplamtutar, kdv (veya toplamkdv)
        
        # Sayısal dönüşüm
        for col in df.columns:
            if "tutar" in col or "kdv" in col:
                df[col] = df[col].apply(temizle_ve_sayiya_cevir)
            
            if "tarih" in col:
                df['tarih_dt'] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')

        return df
    except Exception as e:
        st.error(f"Veri Çekme Hatası: {e}")
        return pd.DataFrame()

# --- GEMINI & RESİM İŞLEME ---
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
        
        prompt_text = """
        Bu fişi analiz et. Yanıt sadece JSON olsun.
        "kategori" alanını şunlardan biri seç: [Gıda, Akaryakıt, Kırtasiye, Teknoloji, Giyim, Diğer]
        JSON Formatı:
        {"isyeri_adi": "...", "fiş_no": "...", "tarih": "GG.AA.YYYY", "kategori": "...", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}
        """
        
        payload = {"contents": [{"parts": [{"text": prompt_text}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}]}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200: 
            return {"dosya_adi": dosya_adi, "hata": f"API Hatası {response.status_code}: {response.text}"}
            
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_adi
        return veri
    except Exception as e: 
        return {"dosya_adi": dosya_adi, "hata": f"Kod Hatası: {str(e)}"}

# --- ARAYÜZ ---
with st.sidebar:
    st.title("🔧 Mihsap Tamir Modu")
    modeller = modelleri_getir()
    model = st.selectbox("Model", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)
    if st.button("🧹 Önbelleği Temizle"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

tab1, tab2 = st.tabs(["📤 Fiş Yükle", "📊 Patron Paneli (Debug)"])

with tab1:
    st.header("Fiş İşlemleri")
    dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)
    
    if st.button("🚀 Analiz Et (V10)"):
        if not dosyalar:
            st.warning("⚠️ Lütfen önce fiş seçin.")
        else:
            tum_veriler = []
            hatalar = []
            bar = st.progress(0)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as executor:
                future_to_file = {executor.submit(gemini_ile_analiz_et, d, model): d for d in dosyalar}
                completed = 0
                for future in concurrent.futures.as_completed(future_to_file):
                    res = future.result()
                    if "hata" in res:
                        hatalar.append(res)
                    else:
                        tum_veriler.append(res)
                    completed += 1
                    bar.progress(completed / len(dosyalar))
                    time.sleep(0.5)
            
            if hatalar:
                st.error("Bazı dosyalarda hata oluştu:")
                st.table(pd.DataFrame(hatalar))
                
            if tum_veriler:
                df = pd.DataFrame(tum_veriler)
                st.success(f"✅ {len(tum_veriler)} fiş başarıyla okundu!")
                st.dataframe(df)
                
                # Veritabanına Kayıt
                with st.spinner("Google Sheets'e yazılıyor..."):
                    if sheete_kaydet(tum_veriler):
                        st.success("💾 Veritabanına Kaydedildi!")
                    else:
                        st.error("❌ Veritabanına yazılamadı. Ayarları kontrol et.")

with tab2:
    st.header("📊 Veritabanı Durumu")
    
    if st.button("🔄 Verileri Şimdi Çek"):
        df_db = sheetten_veri_cek()
        
        if not df_db.empty:
            # DEBUG BİLGİSİ: Sütunları göster
            st.info(f"Bulunan Sütunlar: {list(df_db.columns)}")
            
            # Sütun eşleştirme
            col_tutar = next((c for c in df_db.columns if "tutar" in c), None)
            col_kdv = next((c for c in df_db.columns if "kdv" in c), None)
            col_kat = next((c for c in df_db.columns if "kategori" in c), None)
            
            if col_tutar and col_kdv:
                total_spend = df_db[col_tutar].sum()
                total_kdv = df_db[col_kdv].sum()
                
                c1, c2 = st.columns(2)
                c1.metric("Toplam Harcama", f"{total_spend:,.2f} ₺")
                c2.metric("Toplam KDV", f"{total_kdv:,.2f} ₺")
                
                if col_kat:
                    fig = px.pie(df_db, values=col_tutar, names=col_kat, title="Kategori Dağılımı")
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("Kategori sütunu bulunamadı.")
            else:
                st.error("Tutar veya KDV sütunu bulunamadı. Google Sheets başlıklarını kontrol et.")
            
            st.dataframe(df_db)
        else:
            st.error("Veri çekilemedi veya tablo boş.")
