import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import google.generativeai as genai

# --- AYARLAR ---
if "GEMINI_API_KEY" in st.secrets:
    os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]

try:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
except Exception as e:
    st.error(f"API Anahtarı Hatası: {e}")

def gemini_ile_analiz_et(image_bytes, model_adi):
    try:
        model = genai.GenerativeModel(model_adi)
        
        image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]

        prompt = """
        Bu fiş görüntüsünü analiz et. 
        Cevabı SADECE aşağıdaki formatta JSON olarak ver:
        {
            "isyeri_adi": "İşyeri Adı",
            "tarih": "GG.AA.YYYY",
            "toplam_tutar": "00.00",
            "toplam_kdv": "00.00"
        }
        """
        
        # Gemini Pro Vision (Eski sürüm) config ayarı gerekebilir
        response = model.generate_content([prompt, image_parts[0]])
        
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        if text.startswith("```"): text = text[3:-3]
        
        return json.loads(text)

    except Exception as e:
        st.error(f"Model ({model_adi}) Hatası: {e}")
        return None

# --- ARAYÜZ ---
st.set_page_config(page_title="Mihsap AI - Kararlı Sürüm", layout="wide", page_icon="🛡️")

with st.sidebar:
    st.header("⚙️ Model Seçimi")
    # BURASI ÖNEMLİ: En garanti çalışan modelleri en başa koyduk
    model_listesi = [
        "gemini-pro-vision",  # EN GARANTİ ÇALIŞAN (Resim okuma yeteneği olan eski sürüm)
        "gemini-1.5-flash",   # Yeni sürüm (Kütüphane güncellenirse çalışır)
        "gemini-1.5-pro",     # Yeni güçlü sürüm
        "gemini-pro"          # Sadece metin (Bazen resim yemez ama listede dursun)
    ]
    secilen_model = st.selectbox("Model Seç", model_listesi)
    st.info(f"Seçili: {secilen_model}")

st.title("🛡️ Mihsap AI (Kararlı Mod)")
st.write("Fişinizi yükleyin. Önerilen Model: **gemini-pro-vision**")

yuklenen_dosyalar = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        image = Image.open(dosya)
        buf = io.BytesIO()
        image.save(buf, format='JPEG')
        
        sonuc = gemini_ile_analiz_et(buf.getvalue(), secilen_model)
        
        if sonuc:
            sonuc["dosya_adi"] = dosya.name
            tum_veriler.append(sonuc)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        cols = ["dosya_adi", "isyeri_adi", "tarih", "toplam_tutar", "toplam_kdv"]
        st.dataframe(df[[c for c in cols if c in df.columns]], use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="muhasebe.xlsx")
