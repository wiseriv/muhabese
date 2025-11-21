import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import requests
import base64

# --- AYARLAR ---
# API Anahtarını al
API_KEY = st.secrets.get("GEMINI_API_KEY")

# --- YARDIMCI FONKSİYONLAR ---
def resmi_base64_yap(image_bytes):
    """Resmi Google'ın anlayacağı metin formatına (Base64) çevirir."""
    return base64.b64encode(image_bytes).decode('utf-8')

def gemini_ile_analiz_et(image_bytes):
    """Doğrudan HTTP isteği ile Google Gemini API'yi arar."""
    
    # 1. URL (Doğrudan Google'ın adresi)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={API_KEY}"
    
    # 2. Başlıklar
    headers = {'Content-Type': 'application/json'}
    
    # 3. Gövde (Veri)
    base64_image = resmi_base64_yap(image_bytes)
    
    payload = {
        "contents": [{
            "parts": [
                {
                    "text": """Bu fiş görüntüsünü analiz et. 
                    Cevabı SADECE aşağıdaki formatta saf JSON olarak ver (Markdown veya ```json kullanma):
                    {
                        "isyeri_adi": "İşyeri Adı",
                        "tarih": "GG.AA.YYYY",
                        "toplam_tutar": "00.00",
                        "toplam_kdv": "00.00"
                    }"""
                },
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64_image
                    }
                }
            ]
        }]
    }

    try:
        # İsteği gönder
        response = requests.post(url, headers=headers, json=payload)
        
        # Cevabı kontrol et
        if response.status_code != 200:
            st.error(f"Google Hatası ({response.status_code}): {response.text}")
            return None
            
        # Gelen veriyi çöz
        sonuc_json = response.json()
        try:
            # Google'ın karışık cevabının içinden metni cımbızla al
            metin = sonuc_json['candidates'][0]['content']['parts'][0]['text']
            
            # Temizlik (Bazen ```json ile gönderir)
            metin = metin.replace("```json", "").replace("```", "").strip()
            
            return json.loads(metin)
        except:
            st.error("Google cevap döndü ama formatı bozuk.")
            st.text(sonuc_json) # Hata ayıklama için ekrana bas
            return None

    except Exception as e:
        st.error(f"Bağlantı Hatası: {e}")
        return None

# --- ARAYÜZ ---
st.set_page_config(page_title="Mihsap AI - Direct", layout="wide", page_icon="⚡")

st.title("⚡ Mihsap AI (Direct API)")
st.write("Google kütüphanesi olmadan, doğrudan bağlantı modu.")

if not API_KEY:
    st.error("Lütfen Secrets ayarlarından GEMINI_API_KEY'i ekleyin.")
    st.stop()

yuklenen_dosyalar = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        image = Image.open(dosya)
        # JPEG'e çevir (Google JPEG sever)
        buf = io.BytesIO()
        image = image.convert('RGB') # PNG ise RGB yap
        image.save(buf, format='JPEG')
        
        sonuc = gemini_ile_analiz_et(buf.getvalue())
        
        if sonuc:
            sonuc["dosya_adi"] = dosya.name
            tum_veriler.append(sonuc)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        st.write("### 📊 Sonuçlar")
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="muhasebe_direct.xlsx")
