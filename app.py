import streamlit as st
import azure.cognitiveservices.speech as speechsdk
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential
import subprocess
import tempfile
import os

# Azure credentials - straks in Streamlit secrets
SPEECH_KEY = st.secrets.get("SPEECH_KEY", "temp-key")
SPEECH_REGION = st.secrets.get("SPEECH_REGION", "westeurope")
TRANSLATOR_KEY = st.secrets.get("TRANSLATOR_KEY", "temp-key")
TRANSLATOR_ENDPOINT = st.secrets.get("TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com")
TRANSLATOR_REGION = st.secrets.get("TRANSLATOR_REGION", "westeurope")

st.title("🎬 Video Dubbing voor Hans")
st.write("Upload video, kies taal, download result")

target_lang = st.selectbox(
    "Naar welke taal?",
    ["pt-PT", "pt-BR", "es-ES", "fr-FR", "de-DE", "nl-NL"],
    format_func=lambda x: {
        "pt-PT": "Portugees (Portugal)",
        "pt-BR": "Portugees (Brazilië)", 
        "es-ES": "Spaans",
        "fr-FR": "Frans",
        "de-DE": "Duits",
        "nl-NL": "Nederlands"
    }[x]
)

uploaded_file = st.file_uploader("Upload video", type=["mp4", "mov", "avi"])

def extract_audio(video_path, audio_path):
    cmd = ['ffmpeg', '-i', video_path, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio_path, '-y']
    subprocess.run(cmd, check=True, capture_output=True)

def transcribe_audio(audio_path):
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_config.speech_recognition_language = "en-US"
    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
    
    all_text = []
    done = False
    
    def recognized(evt):
        all_text.append(evt.result.text)
    
    def stop_cb(evt):
        nonlocal done
        done = True
    
    speech_recognizer.recognized.connect(recognized)
    speech_recognizer.session_stopped.connect(stop_cb)
    speech_recognizer.canceled.connect(stop_cb)
    speech_recognizer.start_continuous_recognition()
    
    import time
    while not done:
        time.sleep(0.5)
    
    speech_recognizer.stop_continuous_recognition()
    return ' '.join(all_text)

def translate_text(text, target_language):
    credential = AzureKeyCredential(TRANSLATOR_KEY)
    client = TextTranslationClient(endpoint=TRANSLATOR_ENDPOINT, credential=credential, region=TRANSLATOR_REGION)
    target_lang_code = target_language.split('-')[0]
    response = client.translate(body=[{"text": text}], to_language=[target_lang_code], from_language="en")
    return response[0].translations[0].text

def synthesize_speech(text, language, output_path):
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    voices = {
        "pt-PT": "pt-PT-DuarteNeural",
        "pt-BR": "pt-BR-AntonioNeural",
        "es-ES": "es-ES-AlvaroNeural",
        "fr-FR": "fr-FR-HenriNeural",
        "de-DE": "de-DE-ConradNeural",
        "nl-NL": "nl-NL-MaartenNeural"
    }
    speech_config.speech_synthesis_voice_name = voices.get(language)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)
    speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    result = speech_synthesizer.speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise Exception(f"TTS failed: {result.reason}")

def merge_audio_video(video_path, audio_path, output_path):
    cmd = ['ffmpeg', '-i', video_path, '-i', audio_path, '-c:v', 'copy', '-map', '0:v:0', '-map', '1:a:0', '-shortest', output_path, '-y']
    subprocess.run(cmd, check=True, capture_output=True)

if uploaded_file and st.button("🚀 Start Dubbing"):
    try:
        with st.spinner("Bezig..."):
            with tempfile.TemporaryDirectory() as tmpdir:
                video_path = os.path.join(tmpdir, "input.mp4")
                with open(video_path, "wb") as f:
                    f.write(uploaded_file.read())
                
                st.write("📹 Audio extracten...")
                audio_path = os.path.join(tmpdir, "audio.wav")
                extract_audio(video_path, audio_path)
                
                st.write("🎤 Transcriptie...")
                transcription = transcribe_audio(audio_path)
                st.text_area("Engels", transcription, height=100)
                
                st.write("🌍 Vertalen...")
                translation = translate_text(transcription, target_lang)
                st.text_area(f"Vertaald ({target_lang})", translation, height=100)
                
                st.write("🗣️ Spraak genereren...")
                new_audio_path = os.path.join(tmpdir, "dubbed.wav")
                synthesize_speech(translation, target_lang, new_audio_path)
                
                st.write("🎬 Mergen...")
                output_path = os.path.join(tmpdir, "output.mp4")
                merge_audio_video(video_path, new_audio_path, output_path)
                
                with open(output_path, "rb") as f:
                    st.download_button("⬇️ Download", f.read(), file_name=f"dubbed_{uploaded_file.name}", mime="video/mp4")
                
                st.success("✅ Klaar!")
                
    except Exception as e:
        st.error(f"❌ Error: {str(e)}")
