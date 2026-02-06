import streamlit as st
import azure.cognitiveservices.speech as speechsdk
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential
import subprocess
import tempfile
import os
import json

# Azure credentials
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

def transcribe_audio_with_timing(audio_path):
    """Transcribe with timestamps per segment"""
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_config.speech_recognition_language = "en-US"
    speech_config.output_format = speechsdk.OutputFormat.Detailed
    
    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
    
    segments = []
    done = False
    
    def recognized(evt):
        if evt.result.text:
            # Offset is in 100-nanosecond units, convert to seconds
            offset_sec = evt.result.offset / 10000000.0
            duration_sec = evt.result.duration / 10000000.0
            segments.append({
                'text': evt.result.text,
                'start': offset_sec,
                'end': offset_sec + duration_sec,
                'duration': duration_sec
            })
    
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
    return segments

def translate_segments(segments, target_language):
    """Translate each segment separately"""
    credential = AzureKeyCredential(TRANSLATOR_KEY)
    client = TextTranslationClient(endpoint=TRANSLATOR_ENDPOINT, credential=credential, region=TRANSLATOR_REGION)
    target_lang_code = target_language.split('-')[0]
    
    translated_segments = []
    for seg in segments:
        response = client.translate(
            body=[{"text": seg['text']}],
            to_language=[target_lang_code],
            from_language="en"
        )
        translated_segments.append({
            'text': response[0].translations[0].text,
            'start': seg['start'],
            'end': seg['end'],
            'duration': seg['duration']
        })
    
    return translated_segments

def synthesize_segment(text, language, output_path):
    """Generate TTS for a single segment"""
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
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    result = synthesizer.speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise Exception(f"TTS failed: {result.reason}")

def create_timed_audio(segments, language, output_path, tmpdir):
    """Create audio with proper timing using ffmpeg"""
    segment_files = []
    filter_parts = []
    
    current_time = 0
    
    for i, seg in enumerate(segments):
        # Generate TTS for this segment
        segment_audio = os.path.join(tmpdir, f"segment_{i}.wav")
        synthesize_segment(seg['text'], language, segment_audio)
        segment_files.append(segment_audio)
        
        # Calculate silence needed before this segment
        silence_duration = seg['start'] - current_time
        
        if silence_duration > 0:
            # Add silence
            filter_parts.append(f"aevalsrc=0:d={silence_duration}[silence{i}];")
            filter_parts.append(f"[silence{i}][{i}:a]concat=n=2:v=0:a=1[a{i}];")
        else:
            filter_parts.append(f"[{i}:a]anull[a{i}];")
        
        # Get duration of generated audio
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', segment_audio],
            capture_output=True, text=True
        )
        duration = float(json.loads(result.stdout)['format']['duration'])
        current_time = seg['start'] + duration
    
    # Build ffmpeg command
    inputs = []
    for sf in segment_files:
        inputs.extend(['-i', sf])
    
    # Concatenate all segments
    concat_filter = ''.join(filter_parts)
    concat_inputs = ''.join([f"[a{i}]" for i in range(len(segments))])
    full_filter = f"{concat_filter}{concat_inputs}concat=n={len(segments)}:v=0:a=1[out]"
    
    cmd = ['ffmpeg'] + inputs + ['-filter_complex', full_filter, '-map', '[out]', output_path, '-y']
    subprocess.run(cmd, check=True, capture_output=True)

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
                
                st.write("🎤 Transcriptie met timing...")
                segments = transcribe_audio_with_timing(audio_path)
                
                # Show original text
                original_text = ' '.join([s['text'] for s in segments])
                st.text_area("Engels", original_text, height=100)
                
                st.write("🌍 Vertalen...")
                translated_segments = translate_segments(segments, target_lang)
                
                # Show translated text
                translated_text = ' '.join([s['text'] for s in translated_segments])
                st.text_area(f"Vertaald ({target_lang})", translated_text, height=100)
                
                st.write("🗣️ Spraak genereren met timing...")
                new_audio_path = os.path.join(tmpdir, "dubbed.wav")
                create_timed_audio(translated_segments, target_lang, new_audio_path, tmpdir)
                
                st.write("🎬 Video samenvoegen...")
                output_path = os.path.join(tmpdir, "output.mp4")
                merge_audio_video(video_path, new_audio_path, output_path)
                
                with open(output_path, "rb") as f:
                    st.download_button("⬇️ Download", f.read(), file_name=f"dubbed_{uploaded_file.name}", mime="video/mp4")
                
                st.success("✅ Klaar!")
                
    except Exception as e:
        st.error(f"❌ Error: {str(e)}")
        import traceback
        st.code(traceback.format_exc())
