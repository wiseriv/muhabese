import streamlit as st
import os
import re
import pandas as pd
from google.cloud import vision
import io
import json

# --- AYARLAR (BULUT VE YEREL UYUMLU) ---
# Eğer yerel bilgisayarda 'google_key.json' varsa onu kullan
if os.path.exists('google_key.json'):
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'google_key.json'
else:
    # Eğer dosya yoksa (Buluttaysak), Streamlit Secrets'tan bilgiyi alıp geçici dosya yarat
    # Bu sayede GitHub'a anahtar yüklemeden güvenle çalışırız.
    if "gcp_service_account" in st.secrets:
        key_dict = dict(st.secrets["gcp_service_account"])
        # Bilgileri geçici bir json dosyasına yaz
        with open("google_key.json", "w") as f:
            json.dump(key_dict, f)
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'google_key.json'

def google_vision_ile_oku(image_bytes):
    """Görüntüyü Google'a gönderir."""
    try:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        response = client.text_detection(image=image)
        texts = response.text_annotations
        
        if texts:
            return texts[0].description
        return None
    except Exception as e:
        st.error(f"API Hatası: {e}")
        return None

def veriyi_anlamlandir(ham_metin, dosya_adi):
    """Metinden verileri çeker. Dosya adını da kaydeder."""
    veri = {
        "Dosya Adı": dosya_adi,
        "Isyeri": "Bulunamadı",
        "Tarih": "Bulunamadı",
        "Toplam_Tutar": "0.00",
        "Toplam_KDV": "0.00"
    }
    
    satirlar = ham_metin.split('\n')
    if len(satirlar) > 0: veri["Isyeri"] = satirlar[0]

    tarih_match = re.search(r'(\d{2}[./-]\d{2}[./-]\d{4})', ham_metin)
    if tarih_match: veri["Tarih"] = tarih_match.group(1)

    for i in range(len(satirlar)):
        satir = satirlar[i]
        satir_kucuk = satir.lower()
        
        def para_bul(metin):
            rakamlar = re.findall(r'[*]?\s*(\d+[.,]\d{2})', metin)
            if rakamlar: return rakamlar[-1].replace('*', '')
            return None

        if ("toplam" in satir_kucuk or "top" in satir_kucuk) and "kdv" not in satir_kucuk:
            bulunan = para_bul(satir)
            if bulunan: veri["Toplam_Tutar"] = bulunan
            elif i + 1 < len(satirlar):
                bulunan_alt = para_bul(satirlar[i+1])
                if bulunan_alt: veri["Toplam_Tutar"] = bulunan_alt

        if "topkdv" in satir_kucuk or ("toplam" in satir_kucuk and "kdv" in satir_kucuk):
             bulunan_kdv = para_bul(satir)
             if bulunan_kdv: veri["Toplam_KDV"] = bulunan_kdv
             elif i + 1 < len(satirlar):
                bulunan_alt_kdv = para_bul(satirlar[i+1])
                if bulunan_alt_kdv: veri["Toplam_KDV"] = bulunan_alt_kdv
    return veri

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap Pro - Online", layout="wide", page_icon="🧾")

st.title("🧾 Fiş Okuyucu (Online)")
st.write("Bulut tabanlı OCR sistemi.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        bytes_data = dosya.getvalue()
        metin = google_vision_ile_oku(bytes_data)
        if metin:
            veri = veriyi_anlamlandir(metin, dosya.name)
            tum_veriler.append(veri)
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        st.write("### 📊 Sonuçlar")
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
            
        st.download_button(
            label="📥 Excel İndir",
            data=buffer.getvalue(),
            file_name="muhasebe_dokumu.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )