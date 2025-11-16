import requests
from langdetect import detect, DetectorFactory
import re
from datetime import datetime
import json

# Prevent langdetect non-deterministic behavior
DetectorFactory.seed = 0

# ============================================================
#                 CONFIGURATION
# ============================================================
try:
    from google.colab import userdata
    GROQ_API_KEY = userdata.get('GROQ_API_KEY')
except ImportError:
    GROQ_API_KEY = input("Enter your Groq API key: ").strip()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Active model (as of Nov 2025) - Fast, Free, High Quality
MODEL = "llama-3.3-70b-versatile"

# Fallback models in case of deprecation
FALLBACK_MODELS = [
    "llama3-70b-8192",
    "gemma2-9b-it",
    "mixtral-8x7b-32768"
]

LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "hi": "Hindi", "mr": "Marathi",
    "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ru": "Russian",
    "ar": "Arabic", "bn": "Bengali", "ta": "Tamil", "te": "Telugu"
}

# ============================================================
#                 METRICS TRACKING
# ============================================================
class TranslationMetrics:
    def __init__(self):
        self.queries = []
        self.start_time = datetime.now()
    
    def log_query(self, original_lang, original_text, english_translation, 
                  confidence, response_time):
        self.queries.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "language": original_lang,
            "original": original_text[:100],
            "translation": english_translation[:100],
            "confidence": confidence,
            "response_time": response_time
        })
    
    def show_summary(self):
        if not self.queries:
            print("\n📊 No queries processed yet.\n")
            return
        
        print("\n╔" + "═" * 68 + "╗")
        print("║" + " " * 18 + "📊 TRANSLATION METRICS SUMMARY" + " " * 19 + "║")
        print("╠" + "═" * 68 + "╣")
        print(f"║  Total Queries: {len(self.queries):<51}║")
        print(f"║  Session Duration: {(datetime.now() - self.start_time).seconds}s{' ' * (48 - len(str((datetime.now() - self.start_time).seconds)))}║")
        print("╠" + "═" * 68 + "╣")
        
        # Language breakdown
        lang_counts = {}
        for q in self.queries:
            lang = q["language"]
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        
        print("║  Language Distribution:" + " " * 44 + "║")
        for lang, count in sorted(lang_counts.items(), key=lambda x: x[1], reverse=True):
            spacing = 60 - len(f"    • {lang}: {count} queries")
            print(f"║    • {lang}: {count} queries{' ' * spacing}║")
        
        print("╠" + "═" * 68 + "╣")
        
        # Average metrics
        avg_conf = sum(q["confidence"] for q in self.queries) / len(self.queries)
        avg_time = sum(q["response_time"] for q in self.queries) / len(self.queries)
        
        conf_str = f"  Avg Translation Confidence: {avg_conf:.1f}%"
        time_str = f"  Avg Response Time: {avg_time:.2f}s"
        print(f"║{conf_str}{' ' * (68 - len(conf_str))}║")
        print(f"║{time_str}{' ' * (68 - len(time_str))}║")
        print("╚" + "═" * 68 + "╝\n")

metrics = TranslationMetrics()

# ============================================================
#                 MODEL VALIDATION & FALLBACK
# ============================================================
def get_active_model():
    """Check if preferred model is active, fallback if not"""
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        response = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=10)
        if response.status_code != 200:
            return MODEL
        
        available = [m['id'] for m in response.json().get('data', [])]
        
        if MODEL in available:
            return MODEL
        else:
            print(f"⚠️  Warning: Model '{MODEL}' not found. Trying fallbacks...")
            for fb in FALLBACK_MODELS:
                if fb in available:
                    print(f"✓ Success: Using fallback model: {fb}")
                    return fb
    except:
        pass
    print(f"⚠️  Warning: Using default fallback: {FALLBACK_MODELS[0]}")
    return FALLBACK_MODELS[0]

ACTIVE_MODEL = get_active_model()

# ============================================================
#                 LANGUAGE DETECTION
# ============================================================
def detect_language(text):
    """Detect language with enhanced accuracy"""
    if not text.strip():
        return "en"
    
    # Quick pre-checks for script-based languages
    if re.search(r"[\u0400-\u04FF]", text):  # Cyrillic
        return "ru"
    if re.search(r"[\u4e00-\u9fff]", text):  # Chinese
        return "zh"
    if re.search(r"[\uac00-\ud7af]", text):  # Korean
        return "ko"
    if re.search(r"[\u3040-\u30ff]", text):  # Japanese
        return "ja"
    
    # Enhanced Devanagari (Hindi/Marathi) detection
    if re.search(r"[\u0900-\u097F]", text):  # Devanagari script
        # Common Hindi words and patterns
        hindi_markers = ["है", "हैं", "का", "की", "के", "में", "को", "से", "ने", "और", "या", "हो","हे"]
        # Common Marathi words and patterns
        marathi_markers = ["आहे", "आहेत", "च्या", "ला", "ने", "मध्ये", "आणि", "किंवा"]
        
        hindi_count = sum(1 for marker in hindi_markers if marker in text)
        marathi_count = sum(1 for marker in marathi_markers if marker in text)
        
        # If clear markers found, use them
        if marathi_count > hindi_count and marathi_count > 0:
            return "mr"
        elif hindi_count > 0:
            return "hi"
        
        # Fallback to langdetect for Devanagari
        try:
            detected = detect(text)
            # langdetect returns 'hi' for both Hindi and Marathi sometimes
            # Default to Hindi as it's more common
            return detected if detected in ["hi", "mr"] else "hi"
        except:
            return "hi"  # Default to Hindi for Devanagari
    
    # For all other languages, use langdetect
    try:
        return detect(text)
    except:
        return "en"

# ============================================================
#                 GROQ API CALL
# ============================================================
def call_groq_api(messages, max_tokens=500):
    """Make API call to Groq with error handling and fallback"""
    global ACTIVE_MODEL
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": ACTIVE_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "top_p": 0.9
    }
    
    for attempt in range(3):
        try:
            response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            elif response.status_code == 400:
                error = response.json().get("error", {})
                msg = error.get("message", "")
                if "decommissioned" in msg or "not found" in msg:
                    print(f"⚠️  Error: Model {ACTIVE_MODEL} decommissioned. Trying fallback...")
                    ACTIVE_MODEL = FALLBACK_MODELS[0]
                    payload["model"] = ACTIVE_MODEL
                    continue
            # Print error
            try:
                print(f"\n❌ API Error Details: {response.json()}")
            except:
                print(f"\n❌ HTTP {response.status_code}: {response.text}")
            return f"API Error: {response.status_code}"
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                return f"Network Error: {str(e)}"
    return "API Error: Failed after retries"

# ============================================================
#                 TRANSLATION ENGINE
# ============================================================
def translate_to_english(text, lang_code):
    if lang_code == "en":
        return text.strip(), 100.0
    
    lang_name = LANGUAGE_NAMES.get(lang_code, lang_code.upper())
    
    messages = [
        {
            "role": "system",
            "content": "You are a professional translator for customer support. Translate accurately, naturally, and preserve intent, tone, and technical terms."
        },
        {
            "role": "user",
            "content": f"Translate this {lang_name} customer message to clear, natural English. Output ONLY the translation.\n\nText: {text}"
        }
    ]
    
    translation = call_groq_api(messages, max_tokens=300)
    
    # Confidence heuristic
    confidence = 80.0
    words = len(translation.split())
    if words >= 3:
        confidence = 90.0
    if words >= 6:
        confidence = 95.0
    if any(x in translation.lower() for x in ["error", "failed", "http"]):
        confidence = max(confidence - 15, 60)
    
    return translation, confidence

# ============================================================
#                 RESPONSE GENERATOR
# ============================================================
def generate_support_response(english_query):
    messages = [
        {
            "role": "system",
            "content": "You are a helpful, empathetic customer support agent. Be clear, concise, and provide actionable steps."
        },
        {
            "role": "user",
            "content": f"Customer says: {english_query}\n\nRespond professionally in 2-4 sentences with clear next steps."
        }
    ]
    
    return call_groq_api(messages, max_tokens=400)

# ============================================================
#                 MAIN QUERY PROCESSOR
# ============================================================
def process_query(user_input):
    start_time = datetime.now()
    
    print("\n╔" + "═" * 68 + "╗")
    print("║" + " " * 24 + "📥 PROCESSING QUERY" + " " * 25 + "║")
    print("╚" + "═" * 68 + "╝")
    
    # Original Query Box
    print("\n┌─ 💬 Original Query " + "─" * 47 + "┐")
    for line in user_input.split('\n'):
        print(f"│ {line:<66} │")
    print("└" + "─" * 68 + "┘")
    
    # Language Detection
    lang_code = detect_language(user_input)
    lang_name = LANGUAGE_NAMES.get(lang_code, lang_code.upper())
    print(f"\n🌍 Detected Language: {lang_name}")
    
    # Translation
    print("\n⏳ Translating to English...")
    english_translation, confidence = translate_to_english(user_input, lang_code)
    
    print("\n┌─ ✅ English Translation " + "─" * 42 + "┐")
    print(f"│ Confidence: {confidence:.1f}%" + " " * 54 + "│")
    print("├" + "─" * 68 + "┤")
    for line in english_translation.split('\n'):
        print(f"│ {line:<66} │")
    print("└" + "─" * 68 + "┘")
    
    # Support Response
    print("\n💡 Generating Support Response...")
    support_response = generate_support_response(english_translation)
    
    print("\n┌─ 🤖 Support Response " + "─" * 45 + "┐")
    # Wrap text to fit in box
    words = support_response.split()
    line = ""
    for word in words:
        if len(line + word) <= 64:
            line += word + " "
        else:
            print(f"│ {line.strip():<66} │")
            line = word + " "
    if line.strip():
        print(f"│ {line.strip():<66} │")
    print("└" + "─" * 68 + "┘")
    
    response_time = (datetime.now() - start_time).total_seconds()
    print(f"\n⏱️  Total Response Time: {response_time:.2f}s")
    
    metrics.log_query(lang_name, user_input, english_translation, confidence, response_time)
    
    print("\n" + "─" * 70 + "\n")
    
    return {
        "original": user_input,
        "language": lang_name,
        "english": english_translation,
        "confidence": confidence,
        "response": support_response,
        "time": response_time
    }

# ============================================================
#                 INTERACTIVE INTERFACE
# ============================================================
def main():
    print("\n╔" + "═" * 68 + "╗")
    print("║" + " " * 12 + "🌎 REAL-TIME MULTILINGUAL QUERY HANDLER" + " " * 17 + "║")
    print("║" + " " * 18 + "Powered by Groq API (Free & Fast)" + " " * 17 + "║")
    print("╚" + "═" * 68 + "╝")
    print("\n📌 Features:")
    print("   ✓ Real-time translation to English")
    print("   ✓ AI-powered support responses")
    print("   ✓ Performance metrics tracking")
    print("   ✓ 16+ language support")
    print("\n⌨️  Commands:")
    print("   • Type your query in any language")
    print("   • 'metrics' - View performance statistics")
    print("   • 'test' - Run demo queries")
    print("   • 'exit' - Quit program")
    print("\n" + "─" * 70)
    
    if not GROQ_API_KEY or GROQ_API_KEY.strip() == "":
        print("\n❌ ERROR: Groq API key not found!")
        print("\n📍 For Google Colab:")
        print("   1. Click the 🔑 Keys icon in the left sidebar")
        print("   2. Add secret: Name = GROQ_API_KEY, Value = your_key")
        print("   3. Enable 'Notebook access'")
        print("\n   Get free API key: https://console.groq.com/keys\n")
        return
    
    while True:
        try:
            user_input = input("\n💬 You: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() == "exit":
                print("\n👋 Goodbye!\n")
                break
            
            if user_input.lower() == "metrics":
                metrics.show_summary()
                continue
            
            if user_input.lower() == "test":
                print("\n🧪 Running demo queries...")
                demo_queries = [
                    "¿Cómo puedo cambiar mi contraseña?",
                    "मेरा ऑर्डर कहाँ है?",
                    "Le produit est défectueux",
                    "Quero alterar minha senha."
                ]
                for query in demo_queries:
                    process_query(query)
                continue
            
            process_query(user_input)
            
        except KeyboardInterrupt:
            print("\n\n👋 Exiting...\n")
            break
        except Exception as e:
            print(f"\n❌ Error: {str(e)}\n")

# ============================================================
#                 RUN
# ============================================================
if __name__ == "__main__":
    main()
