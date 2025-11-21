import streamlit as st
import os
import re
import pandas as pd
from google.cloud import vision
from PIL import Image, ImageOps # ImageOps eklendi
import io
import json

# --- AYARLAR ---
if os.path.exists('google_key.json'):
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'google_key.json'
else:
    if "gcp_service_account" in st.secrets:
        key_dict = dict(st.secrets["gcp_service_account"])
        with open("google_key.json", "w") as f:
            json.dump(key_dict, f)
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'google_key.json'

def google_vision_ile_oku(image_bytes):
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
            # Regex: Yıldız, boşluk ve T harfi (TL için) temizliği
            rakamlar = re.findall(r'[*T]?\s*(\d+[.,]\d{2})', metin)
            if rakamlar: return rakamlar[-1].replace('*', '').replace('T', '')
            return None

        # TOPLAM TUTAR
        if ("toplam" in satir_kucuk or "top" in satir_kucuk) and "kdv" not in satir_kucuk:
            bulunan = para_bul(satir)
            if bulunan: veri["Toplam_Tutar"] = bulunan
            elif i + 1 < len(satirlar):
                bulunan_alt = para_bul(satirlar[i+1])
                if bulunan_alt: veri["Toplam_Tutar"] = bulunan_alt

        # KDV
        if "topkdv" in satir_kucuk or ("toplam" in satir_kucuk and "kdv" in satir_kucuk):
             bulunan_kdv = para_bul(satir)
             if bulunan_kdv: veri["Toplam_KDV"] = bulunan_kdv
             elif i + 1 < len(satirlar):
                bulunan_alt_kdv = para_bul(satirlar[i+1])
                if bulunan_alt_kdv: veri["Toplam_KDV"] = bulunan_alt_kdv
    return veri

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap Pro - Döndürme Modu", layout="wide", page_icon="🧾")

st.title("🧾 Fiş Okuyucu (Akıllı Döndürme)")
st.info("Eğer fiş yan duruyorsa, aşağıdaki butonlarla düzeltip öyle işleme alabilirsiniz.")

# Dosya Yükleme
yuklenen_dosya = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'])

# Session State (Döndürme açısını hafızada tutmak için)
if 'rotation' not in st.session_state:
    st.session_state.rotation = 0

if yuklenen_dosya:
    # Resmi Aç
    image = Image.open(yuklenen_dosya)
    
    # EXIF bilgisini kullanarak telefonun otomatik döndürmesini uygula
    image = ImageOps.exif_transpose(image)
    
    # Kullanıcının manuel döndürmesi
    image = image.rotate(st.session_state.rotation, expand=True)

    # 1. Resmi ve Butonları Göster
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.image(image, caption=f"Fiş Önizleme (Dönüş: {st.session_state.rotation}°)", width=400)
    
    with col2:
        st.write("### 🔄 Yön Ayarı")
        if st.button("Sola Döndür (90°)"):
            st.session_state.rotation += 90
            st.rerun() # Sayfayı yenile
            
        if st.button("Sağa Döndür (-90°)"):
            st.session_state.rotation -= 90
            st.rerun()

        st.write("---")
        # İşlem Butonu
        islem_yap = st.button("✅ ŞİMDİ OKU", type="primary")

    # 2. Okuma İşlemi (Kullanıcı 'Şimdi Oku'ya basınca başlar)
    if islem_yap:
        with st.spinner('Yapay zeka okuyor...'):
            # Resmi byte'a çevir (Döndürülmüş halini)
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='PNG')
            img_bytes = img_byte_arr.getvalue()

            metin = google_vision_ile_oku(img_bytes)
            
            if metin:
                veri = veriyi_anlamlandir(metin, yuklenen_dosya.name)
                
                # Sonuçları Göster
                st.success("İşlem Başarılı!")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("İşyeri", veri["Isyeri"])
                c2.metric("Tarih", veri["Tarih"])
                c3.metric("Tutar", veri["Toplam_Tutar"] + " TL")
                c4.metric("KDV", veri["Toplam_KDV"] + " TL")
                
                # Excel İndirme
                df = pd.DataFrame([veri])
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False)
                    
                st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="fis.xlsx")
                
                with st.expander("Ham Metni Gör"):
                    st.text(metin)
