import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import requests
import base64

# --- AYARLAR ---
st.set_page_config(page_title="Mihsap AI - Dedektif", layout="wide", page_icon="🕵️‍♂️")
API_KEY = st.secrets.get("GEMINI_API_KEY")

if not API_KEY:
    st.error("Lütfen Secrets ayarlarından GEMINI_API_KEY'i ekleyin.")
    st.stop()

# --- 1. ADIM: MEVCUT MODELLERİ LİSTELE ---
def modelleri_getir():
    """Senin anahtarının erişebildiği modelleri Google'dan sorar."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            # Sadece içerik üretebilen (generateContent) modelleri filtrele
            uygun_modeller = []
            if 'models' in data:
                for m in data['models']:
                    if 'generateContent' in m.get('supportedGenerationMethods', []):
                        # Model isminin başındaki "models/" kısmını temizle veya olduğu gibi al
                        model_adi = m['name'].replace("models/", "")
                        uygun_modeller.append(model_adi)
            return uygun_modeller
        else:
            st.error(f"Model listesi alınamadı: {response.text}")
            return []
    except Exception as e:
        st.error(f"Bağlantı hatası: {e}")
        return []

# --- 2. ADIM: ANALİZ ET ---
def resmi_base64_yap(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')

def gemini_ile_analiz_et(image_bytes, secilen_model):
    # URL yapısı dinamik hale geldi
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
    
    headers = {'Content-Type': 'application/json'}
    base64_image = resmi_base64_yap(image_bytes)
    
    payload = {
        "contents": [{
            "parts": [
                {"text": """Bu fiş görüntüsünü analiz et. 
                Cevabı SADECE aşağıdaki formatta saf JSON olarak ver:
                {
                    "isyeri_adi": "İşyeri Adı",
                    "tarih": "GG.AA.YYYY",
                    "toplam_tutar": "00.00",
                    "toplam_kdv": "00.00"
                }"""},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            st.error(f"Google Hatası ({response.status_code}): {response.text}")
            return None
            
        sonuc_json = response.json()
        try:
            metin = sonuc_json['candidates'][0]['content']['parts'][0]['text']
            metin = metin.replace("```json", "").replace("```", "").strip()
            return json.loads(metin)
        except:
            st.warning("Veri döndü ama JSON formatında değil.")
            st.code(sonuc_json)
            return None

    except Exception as e:
        st.error(f"Hata: {e}")
        return None

# --- ARAYÜZ ---
with st.sidebar:
    st.header("🔍 Model Dedektifi")
    st.write("Google'a bağlanıp senin için açık olan modelleri çekiyorum...")
    
    # Modelleri Canlı Çek
    mevcut_modeller = modelleri_getir()
    
    if mevcut_modeller:
        secilen_model = st.selectbox("Bulunan Modeller", mevcut_modeller, index=0)
        st.success(f"Seçili: {secilen_model}")
    else:
        st.error("Hiçbir model bulunamadı! API Anahtarını kontrol et.")
        secilen_model = "gemini-1.5-flash" # Fallback

st.title("🕵️‍♂️ Mihsap AI - Dedektif Modu")
st.write(f"Şu an **{secilen_model}** modelini kullanarak deneme yapıyoruz.")

yuklenen_dosyalar = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        image = Image.open(dosya)
        buf = io.BytesIO()
        image = image.convert('RGB')
        image.save(buf, format='JPEG')
        
        sonuc = gemini_ile_analiz_et(buf.getvalue(), secilen_model)
        
        if sonuc:
            sonuc["dosya_adi"] = dosya.name
            tum_veriler.append(sonuc)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        cols = ["dosya_adi", "isyeri_adi", "tarih", "toplam_tutar", "toplam_kdv"]
        mevcut_cols = [c for c in cols if c in df.columns]
        st.dataframe(df[mevcut_cols], use_container_width=True)
