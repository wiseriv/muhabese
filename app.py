import streamlit as st
import os
import re
import pandas as pd
from google.cloud import vision
from PIL import Image, ImageOps
import io
import json

# --- GÜVENLİK VE AYARLAR ---
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
    except Exception:
        return None

def veriyi_anlamlandir(ham_metin, dosya_adi):
    veri = {
        "Dosya Adı": dosya_adi,
        "Isyeri": "Bulunamadı",
        "Tarih": "Bulunamadı",
        "Toplam_Tutar": "0.00",
        "Toplam_KDV": "0.00",
        "Basari_Puani": 0
    }
    
    if not ham_metin: return veri
    
    satirlar = ham_metin.split('\n')
    if len(satirlar) > 0: veri["Isyeri"] = satirlar[0]

    # Tarih
    tarih_match = re.search(r'(\d{2}[./-]\d{2}[./-]\d{4})', ham_metin)
    if tarih_match: 
        veri["Tarih"] = tarih_match.group(1)
        veri["Basari_Puani"] += 1

    # --- YENİ PARASAL FONKSİYON (Daha Basit ve Güçlü) ---
    def rakam_temizle_ve_al(metin):
        # Regex sadece rakam yapısına odaklanır: 10,50 veya 1.000,00 gibi
        # Önündeki * T TL vs umursamaz, direkt rakamı cımbızlar.
        bulunanlar = re.findall(r'(\d+[.,]\d{2})', metin)
        if bulunanlar:
            # En sondaki rakamı al (Genelde tutarlar en sağdadır)
            tutar = bulunanlar[-1]
            return tutar
        return None

    for i in range(len(satirlar)):
        satir = satirlar[i]
        satir_kucuk = satir.lower()

        # --- TOPLAM TUTAR MANTIĞI ---
        if ("toplam" in satir_kucuk or "top" in satir_kucuk) and "kdv" not in satir_kucuk:
            
            # STRATEJİ 1: Aynı satırda var mı? (Migros Tipi)
            # Eğer burada bulursa, "break" yapıp çıkmaz, çünkü belki "Ara Toplam"dır.
            # Ama değişkene atar.
            bulunan = rakam_temizle_ve_al(satir)
            
            if bulunan: 
                veri["Toplam_Tutar"] = bulunan
                veri["Basari_Puani"] += 2
            
            # STRATEJİ 2: Aynı satırda YOKSA aşağıya bak (Doğan Büfe Tipi)
            else:
                # Aşağıdaki 3 satıra bak
                for j in range(1, 4):
                    if i + j < len(satirlar):
                        alt_satir = satirlar[i+j]
                        bulunan_alt = rakam_temizle_ve_al(alt_satir)
                        if bulunan_alt: 
                            veri["Toplam_Tutar"] = bulunan_alt
                            veri["Basari_Puani"] += 2
                            break # Alt satırda bulduysak aramayı kes

        # --- KDV MANTIĞI ---
        if "topkdv" in satir_kucuk or ("toplam" in satir_kucuk and "kdv" in satir_kucuk):
             bulunan_kdv = rakam_temizle_ve_al(satir)
             if bulunan_kdv: 
                 veri["Toplam_KDV"] = bulunan_kdv
             else:
                for j in range(1, 4):
                    if i + j < len(satirlar):
                        alt_satir = satirlar[i+j]
                        bulunan_alt_kdv = rakam_temizle_ve_al(alt_satir)
                        if bulunan_alt_kdv: 
                            veri["Toplam_KDV"] = bulunan_alt_kdv
                            break

    return veri

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap Pro - V4 Hibrit", layout="wide", page_icon="💎")

st.title("💎 Mihsap Klonu V4 (Hibrit Motor)")
st.write("Hem bitişik (Migros) hem ayrık (Doğan Büfe) formatları destekler.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        orijinal_resim = Image.open(dosya)
        orijinal_resim = ImageOps.exif_transpose(orijinal_resim)
        
        en_iyi_veri = None
        en_yuksek_puan = -1
        
        # Tüm açıları dene
        acilar = [0, 270, 90] 
        
        for aci in acilar:
            if aci == 0: islenen_resim = orijinal_resim
            else: islenen_resim = orijinal_resim.rotate(aci, expand=True)
            
            img_byte_arr = io.BytesIO()
            islenen_resim.save(img_byte_arr, format='JPEG')
            metin = google_vision_ile_oku(img_byte_arr.getvalue())
            
            if metin:
                analiz = veriyi_anlamlandir(metin, dosya.name)
                
                if analiz["Basari_Puani"] > en_yuksek_puan:
                    en_yuksek_puan = analiz["Basari_Puani"]
                    en_iyi_veri = analiz
                
                if en_yuksek_puan >= 3:
                    break
        
        if en_iyi_veri:
            tum_veriler.append(en_iyi_veri)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        
        st.write("### 📊 Sonuçlar")
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
            
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="muhasebe_final.xlsx", type="primary")
