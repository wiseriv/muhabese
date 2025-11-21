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
import zipfile

# --- 1. AYARLAR VE GÜVENLİK ---
st.set_page_config(page_title="Mihsap AI - Enterprise", layout="wide", page_icon="🏢")

def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    if not st.session_state['giris_yapildi']:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 Mihsap AI | Giriş")
            with st.form("login"):
                sifre = st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş"):
                    if sifre == "12345":
                        st.session_state['giris_yapildi'] = True
                        st.rerun()
                    else: st.error("Hatalı Şifre")
        st.stop()
giris_kontrol()

API_KEY = st.secrets.get("GEMINI_API_KEY")
if not API_KEY: st.error("API Key Eksik!"); st.stop()

# --- 2. HESAP PLANI AYARLARI (VARSAYILAN) ---
if 'hesap_kodlari' not in st.session_state:
    st.session_state['hesap_kodlari'] = {
        "Gıda": "770.01", "Ulaşım": "770.02", "Kırtasiye": "770.03", 
        "Teknoloji": "770.04", "Konaklama": "770.05", "Diğer": "770.99",
        "KDV": "191.18", "Kasa": "100.01", "Banka": "102.01"
    }

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
    hk = st.session_state['hesap_kodlari'] # Ayarlardan çek
    yevmiye = []
    for index, row in df_ham.iterrows():
        try:
            toplam = temizle_ve_sayiya_cevir(row.get('toplam_tutar', 0))
            kdv = temizle_ve_sayiya_cevir(row.get('toplam_kdv', 0))
            matrah = toplam - kdv
            tarih = str(row.get('tarih', datetime.now().strftime('%d.%m.%Y')))
            kategori = row.get('kategori', 'Diğer')
            
            # Kategoriye göre hesap kodu seç
            gider_kodu = hk.get(kategori, hk["Diğer"])
            
            aciklama = f"{kategori} - {row.get('isyeri_adi', 'Evrak')}"
            
            if matrah > 0: yevmiye.append({"Tarih": tarih, "Hesap Kodu": gider_kodu, "Açıklama": aciklama, "Borç": matrah, "Alacak": 0})
            if kdv > 0: yevmiye.append({"Tarih": tarih, "Hesap Kodu": hk["KDV"], "Açıklama": "KDV", "Borç": kdv, "Alacak": 0})
            
            # Ekstre ise 102 (Banka), Fiş ise 100 (Kasa)
            alacak_hesabi = hk["Banka"] if "Ekstre" in str(row.get('dosya_adi','')) else hk["Kasa"]
            yevmiye.append({"Tarih": tarih, "Hesap Kodu": alacak_hesabi, "Açıklama": "Ödeme", "Borç": 0, "Alacak": toplam})
        except: continue
    return pd.DataFrame(yevmiye)

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
        return df
    except: return pd.DataFrame()

# --- 5. GEMINI CORE ---
@st.cache_data
def modelleri_getir():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        flash = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" in m['name']]
        return flash + [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" not in m['name']]
    except: return []

def dosyayi_hazirla(uploaded_file):
    bytes_data = uploaded_file.getvalue()
    mime_type = uploaded_file.type
    if mime_type == "application/pdf":
        return base64.b64encode(bytes_data).decode('utf-8'), mime_type
    else:
        img = Image.open(io.BytesIO(bytes_data)).convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode('utf-8'), "image/jpeg"

def gemini_ile_analiz_et(dosya_objesi, secilen_model, mod="fis"):
    try:
        base64_data, mime_type = dosyayi_hazirla(dosya_objesi)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        
        if mod == "fis":
            prompt = """Bu belgeyi analiz et. JSON dön:
            {"isyeri_adi": "...", "fiş_no": "...", "tarih": "GG.AA.YYYY", "kategori": "Gıda/Ulaşım/Kırtasiye/Teknoloji/Konaklama/Diğer", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}
            Tarih formatı Gün.Ay.Yıl olsun."""
        else: # EKSTRE MODU
            prompt = """Bu bir kredi kartı ekstresidir. İçindeki tüm harcamaları satır satır ayıkla.
            JSON Formatı (Liste içinde nesneler):
            [
              {"isyeri_adi": "İşyeri A", "tarih": "GG.AA.YYYY", "kategori": "Gıda", "toplam_tutar": "100.00", "toplam_kdv": "0"},
              {"isyeri_adi": "İşyeri B", "tarih": "GG.AA.YYYY", "kategori": "Ulaşım", "toplam_tutar": "50.00", "toplam_kdv": "0"}
            ]
            Sadece harcamaları al, ödemeleri ve devreden bakiyeyi alma.
            """

        payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64_data}}]}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"hata": "API Hatası"}
        
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        
        if isinstance(veri, list):
            for v in veri: v["dosya_adi"] = f"Ekstre_{dosya_objesi.name}"
            return veri
        else:
            veri["dosya_adi"] = dosya_objesi.name
            return veri
            
    except Exception as e: return {"hata": str(e)}

# --- 6. ARAYÜZ ---
with st.sidebar:
    st.markdown("### 🏢 Mihsap Enterprise")
    modeller = modelleri_getir()
    model = st.selectbox("Model", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)
    
    if st.button("❌ Temizle"):
        if 'analiz_sonuclari' in st.session_state: del st.session_state['analiz_sonuclari']
        st.session_state['uploader_key'] = st.session_state.get('uploader_key', 0) + 1
        st.rerun()

# ASİSTAN SEKMESİ KALDIRILDI, SADECE 3 SEKME KALDI
tab1, tab2, tab3 = st.tabs(["📤 Fiş/Fatura", "💳 Kredi Kartı Ekstresi", "⚙️ Ayarlar"])

# --- TAB 1: FİŞ ---
with tab1:
    st.header("Fiş & Fatura İşleme")
    dosyalar = st.file_uploader("Dosya Yükle", type=['jpg','png','pdf'], accept_multiple_files=True, key=f"up1_{st.session_state.get('uploader_key',0)}")
    if dosyalar and st.button("🚀 Fişleri Analiz Et"):
        tum_veriler = []
        bar = st.progress(0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as executor:
            future_to_file = {executor.submit(gemini_ile_analiz_et, d, model, "fis"): d for d in dosyalar}
            completed = 0
            for future in concurrent.futures.as_completed(future_to_file):
                res = future.result()
                if "hata" not in res: tum_veriler.append(res)
                completed += 1
                bar.progress(completed / len(dosyalar))
        
        if tum_veriler:
            df = pd.DataFrame(tum_veriler)
            sheete_kaydet(tum_veriler)
            st.success("✅ Kaydedildi")
            c1, c2 = st.columns(2)
            with c1: st.dataframe(df)
            with c2:
                df_muh = muhasebe_fisne_cevir(df)
                st.dataframe(df_muh)
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='openpyxl') as writer: df_muh.to_excel(writer, index=False)
                st.download_button("📥 Fiş İndir", buf.getvalue(), "muhasebe.xlsx", "primary")

# --- TAB 2: EKSTRE ---
with tab2:
    st.header("Kredi Kartı Ekstresi Çözümleme")
    st.info("PDF veya Resim formatındaki ekstreleri yükleyin. Tüm satırlar ayrıştırılacaktır.")
    ekstreler = st.file_uploader("Ekstre Yükle", type=['pdf','jpg','png'], accept_multiple_files=True, key=f"up2_{st.session_state.get('uploader_key',0)}")
    
    if ekstreler and st.button("💳 Ekstreyi Parçala"):
        tum_satirlar = []
        with st.spinner("Yapay zeka ekstreyi okuyor (Bu işlem biraz sürebilir)..."):
            for d in ekstreler:
                res = gemini_ile_analiz_et(d, model, "ekstre")
                if isinstance(res, list): tum_satirlar.extend(res)
                elif "hata" in res: st.error(f"{d.name}: {res['hata']}")
        
        if tum_satirlar:
            df_ekstre = pd.DataFrame(tum_satirlar)
            st.success(f"✅ Toplam {len(tum_satirlar)} işlem bulundu!")
            sheete_kaydet(tum_satirlar)
            st.dataframe(df_ekstre, use_container_width=True)
            
            df_muh_ekstre = muhasebe_fisne_cevir(df_ekstre)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as writer: df_muh_ekstre.to_excel(writer, index=False)
            st.download_button("📥 Ekstre Muhasebe Fişi İndir", buf.getvalue(), "ekstre_muhasebe.xlsx", "primary")

# --- TAB 3: AYARLAR ---
with tab3:
    st.header("⚙️ Hesap Planı Ayarları")
    col1, col2 = st.columns(2)
    yeni_kodlar = st.session_state['hesap_kodlari'].copy()
    
    with col1:
        yeni_kodlar["Gıda"] = st.text_input("Gıda Kodu", yeni_kodlar["Gıda"])
        yeni_kodlar["Ulaşım"] = st.text_input("Ulaşım/Akaryakıt Kodu", yeni_kodlar["Ulaşım"])
        yeni_kodlar["Kırtasiye"] = st.text_input("Kırtasiye Kodu", yeni_kodlar["Kırtasiye"])
        yeni_kodlar["KDV"] = st.text_input("İndirilecek KDV (191)", yeni_kodlar["KDV"])
        
    with col2:
        yeni_kodlar["Teknoloji"] = st.text_input("Teknoloji Kodu", yeni_kodlar["Teknoloji"])
        yeni_kodlar["Konaklama"] = st.text_input("Konaklama Kodu", yeni_kodlar["Konaklama"])
        yeni_kodlar["Diğer"] = st.text_input("Diğer Giderler", yeni_kodlar["Diğer"])
        yeni_kodlar["Kasa"] = st.text_input("Kasa Hesabı (100)", yeni_kodlar["Kasa"])
        yeni_kodlar["Banka"] = st.text_input("Banka Hesabı (102)", yeni_kodlar["Banka"])

    if st.button("💾 Ayarları Kaydet"):
        st.session_state['hesap_kodlari'] = yeni_kodlar
        st.success("Ayarlar güncellendi!")
