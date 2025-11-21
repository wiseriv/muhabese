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

# --- 1. SAYFA AYARLARI ---
st.set_page_config(page_title="Mihsap AI - Pro", layout="wide", page_icon="💼")

# --- 2. GÜVENLİK ---
def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    if not st.session_state['giris_yapildi']:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 Mihsap AI | Giriş")
            if st.button("Giriş Yap (Demo Modu)"): # Şifreyi kaldırdım hızlanmak için, istersen ekle
                st.session_state['giris_yapildi'] = True
                st.rerun()
        st.stop()
giris_kontrol()

API_KEY = st.secrets.get("GEMINI_API_KEY")
if not API_KEY: st.error("API Key Eksik!"); st.stop()

# --- 3. YARDIMCI MOTORLAR ---
def temizle_ve_sayiya_cevir(deger):
    if pd.isna(deger) or deger == "": return 0.0
    try:
        s = str(deger).replace("₺", "").replace("TL", "").strip()
        if "," in s and "." in s: s = s.replace(".", "").replace(",", ".")
        elif "," in s: s = s.replace(",", ".")
        return float(s)
    except: return 0.0

def muhasebe_fisne_cevir(df_ham):
    yevmiye_satirlari = []
    for index, row in df_ham.iterrows():
        try:
            toplam = temizle_ve_sayiya_cevir(row.get('toplam_tutar', 0))
            kdv = temizle_ve_sayiya_cevir(row.get('toplam_kdv', 0))
            matrah = toplam - kdv
            tarih = row.get('tarih', datetime.now().strftime('%d.%m.%Y'))
            aciklama = f"{row.get('kategori', 'Genel')} - {row.get('isyeri_adi', 'Evrak')}"
            
            if matrah > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "770.01", "Açıklama": aciklama, "Borç": matrah, "Alacak": 0})
            if kdv > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "191.18", "Açıklama": "KDV", "Borç": kdv, "Alacak": 0})
            yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "100.01", "Açıklama": "Ödeme", "Borç": 0, "Alacak": toplam})
        except: continue
    return pd.DataFrame(yevmiye_satirlari)

# --- 4. VERİTABANI ---
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
                v.get("dosya_adi", "-"), v.get("isyeri_adi", "-"), v.get("fiş_no", "-"), 
                v.get("tarih", "-"), v.get("kategori", "Diğer"), 
                str(v.get("toplam_tutar", "0")), str(v.get("toplam_kdv", "0")), 
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])
        sheet.append_rows(rows)
        return True
    except: return False

def sheetten_veri_cek():
    client = sheets_baglantisi_kur()
    if not client: return pd.DataFrame()
    try:
        sheet = client.open("Mihsap Veritabanı").sheet1
        data = sheet.get_all_records()
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        df.columns = [c.strip().lower().replace(" ", "").replace("_", "") for c in df.columns]
        for col in df.columns:
            if "tutar" in col or "kdv" in col: df[col] = df[col].apply(temizle_ve_sayiya_cevir)
            if "tarih" in col: df['tarih_dt'] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')
        return df
    except: return pd.DataFrame()

# --- 5. GEMINI (PDF GÜNCELLEMESİ) ---
@st.cache_data
def modelleri_getir():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        flash = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" in m['name']]
        return flash + [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" not in m['name']]
    except: return []

# YENİ FONKSİYON: Hem Resmi Hem PDF'i Hazırlar
def dosyayi_hazirla(uploaded_file):
    bytes_data = uploaded_file.getvalue()
    mime_type = uploaded_file.type # Dosyanın türü (image/jpeg veya application/pdf)

    # Eğer PDF ise: Olduğu gibi Base64 yap (Küçültme yapılmaz)
    if mime_type == "application/pdf":
        return base64.b64encode(bytes_data).decode('utf-8'), mime_type
    
    # Eğer Resim ise: Küçült ve öyle Base64 yap
    else:
        img = Image.open(io.BytesIO(bytes_data)).convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode('utf-8'), "image/jpeg"

def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    try:
        # Dosya türüne göre hazırla
        base64_data, mime_type = dosyayi_hazirla(dosya_objesi)
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        
        prompt = """Bu belgeyi (fiş, fatura veya e-arşiv) analiz et. JSON formatında dön:
        {"isyeri_adi": "...", "fiş_no": "...", "tarih": "GG.AA.YYYY", "kategori": "Gıda/Akaryakıt/Ofis/Teknoloji/Hizmet/Diğer", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}
        Not: e-Fatura ise 'Ödenecek Tutar'ı baz al."""
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": base64_data}}
                ]
            }]
        }
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"hata": f"Hata: {response.text}"}
        
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_objesi.name
        return veri
    except Exception as e: return {"hata": str(e)}

# --- 6. ARAYÜZ ---
with st.sidebar:
    st.markdown("### 💼 Mihsap AI Pro")
    modeller = modelleri_getir()
    secilen_model = st.selectbox("Model", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)

tab1, tab2 = st.tabs(["📤 Evrak Yükle (Fiş/PDF)", "📊 Raporlar"])

with tab1:
    st.header("Evrak İşleme Merkezi")
    st.info("Artık Fiş (JPG/PNG) ve e-Fatura (PDF) yükleyebilirsiniz!")
    
    # PDF'i de kabul etmesi için type listesini güncelledik
    dosyalar = st.file_uploader("Dosyaları Buraya Bırakın", type=['jpg', 'png', 'jpeg', 'pdf'], accept_multiple_files=True)
    
    if dosyalar and st.button("🚀 İşlemi Başlat", type="primary"):
        tum_veriler = []
        bar = st.progress(0)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as executor:
            future_to_file = {executor.submit(gemini_ile_analiz_et, d, secilen_model): d for d in dosyalar}
            completed = 0
            for future in concurrent.futures.as_completed(future_to_file):
                res = future.result()
                if "hata" not in res: tum_veriler.append(res)
                else: st.error(f"{future_to_file[future].name}: {res['hata']}")
                completed += 1
                bar.progress(completed / len(dosyalar))
                time.sleep(0.5)
        
        if tum_veriler:
            df = pd.DataFrame(tum_veriler)
            st.success(f"{len(tum_veriler)} evrak işlendi.")
            sheete_kaydet(tum_veriler)
            
            c1, c2 = st.columns(2)
            with c1:
                st.dataframe(df, use_container_width=True)
            with c2:
                df_muh = muhasebe_fisne_cevir(df)
                st.dataframe(df_muh, use_container_width=True)
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='openpyxl') as writer: df_muh.to_excel(writer, index=False)
                st.download_button("📥 Muhasebe Fişi İndir", buf.getvalue(), "muhasebe.xlsx", type="primary")

with tab2:
    st.header("Yönetim Paneli")
    if st.button("🔄 Güncelle"): st.rerun()
    df_db = sheetten_veri_cek()
    if not df_db.empty:
        col_tutar = next((c for c in df_db.columns if "tutar" in c), None)
        col_kat = next((c for c in df_db.columns if "kategori" in c), None)
        if col_tutar:
            c1, c2 = st.columns(2)
            c1.metric("Toplam Harcama", f"{df_db[col_tutar].sum():,.2f} ₺")
            c2.metric("Kayıt Sayısı", len(df_db))
            if col_kat:
                fig = px.pie(df_db, values=col_tutar, names=col_kat, title="Kategori Dağılımı", hole=0.4)
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_db, use_container_width=True)
