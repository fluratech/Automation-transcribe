import os
import json
import time
import re
import threading
from flask import Flask, render_template, request, redirect, url_for, session, send_file
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- CONFIGURATION ---
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("WARNING: GEMINI_API_KEY not found in .env file")

genai.configure(api_key=API_KEY)

# AUTHENTICATION
USERNAME = "Syamraj"
PASSWORD = "AutomationHaHaHa"

# PATHS
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'data')
INPUT_FILE = os.path.join(UPLOAD_FOLDER, 'input_urls.txt')
OUTPUT_FILE = os.path.join(UPLOAD_FOLDER, 'extracted_questions.txt')

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# MODEL SETTINGS
MODEL_NAME = "gemini-3-pro-preview"

# Exact prompt from your refined_prompt.txt
SYSTEM_PROMPT = """### ROLE:
You are an expert Mathematics Data Analyst specializing in educational content extraction.

### TASK:
Analyze the provided video to extract the mathematical question discussed and format it into a specific JSON structure.

### CORE INSTRUCTIONS:
1. MATHEMATICAL NOTATION: Every mathematical equation, symbol, variable, or formula MUST be written in LaTeX use double slahes //
2. CHAPTER MAPPING: Assign the most appropriate `chapter_id` based strictly on this list:
   * 1: Sets, Relations and Functions
   * 2: Complex Numbers and Quadratic Equations
   * 3: Matrices and Determinants
   * 4: Permutations and Combinations
   * 5: Binomial Theorem
   * 6: Limit Continuity and Differentiability
   * 7: Integral Calculus
   * 8: Differential Equations
   * 9: Coordinate Geometry
   * 10: Vector Algebra
   * 11: 3D Geometry
   * 12: Statistics and Probability
   * 13: Trigonometry
3. FIELDS:
   * youtube_id: Extract the video ID from the URL provided.
   * question/answer: Use LaTeX for all math content.
   * options: Provide exact values/text for options A, B, C, and D.
   * image: Always leave as an empty string ("").
   * use the option index starting form 0 to show the correct option.

### OUTPUT SCHEMA (Strict JSON):
{ "question": "", "image_url": "", "options": [ { "text": "", "image": "" }, { "text": "", "image": "" }, { "text": "", "image": "" }, { "text": "", "image": "" } ], "correct_option_index": 0, "youtube_id": "", "chapter_id": 0 }
"""

# GLOBAL STATE & LOCKS
processing_status = {"active": False, "current": 0, "total": 0, "error": None}
file_lock = threading.Lock()

def extract_math_logic(urls):
    global processing_status
    processing_status["active"] = True
    processing_status["total"] = len(urls)
    processing_status["error"] = None
    
    # Configure model with safety settings to prevent blocking math diagrams
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    model = genai.GenerativeModel(
        model_name=MODEL_NAME, 
        system_instruction=SYSTEM_PROMPT,
        safety_settings=safety_settings
    )
    
    for index, url in enumerate(urls):
        processing_status["current"] = index + 1
        url = url.strip()
        if not url: continue
        
        success = False
        delay = 2 # Initial retry delay for errors
        
        # Retry loop (5 attempts)
        for attempt in range(5):
            try:
                # API Call
                response = model.generate_content(f"Analyze this math video and extract JSON: {url}")
                
                # Robust JSON Extraction (Finds the first { and last })
                text = response.text
                match = re.search(r'(\{.*\})', text, re.DOTALL)
                
                if match:
                    json_str = match.group(1)
                    result = json.loads(json_str)
                    
                    # Thread-safe file writing
                    with file_lock:
                        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                            f.write(json.dumps(result, ensure_ascii=False) + "\n")
                    
                    success = True
                    break
                else:
                    raise ValueError("No JSON object found in response")

            except Exception as e:
                error_msg = str(e)
                print(f"Attempt {attempt+1} failed for {url}: {error_msg}")
                
                if "429" in error_msg: # Rate Limit
                    time.sleep(delay * 2) # Exponential backoff
                    delay *= 2
                elif attempt == 4:
                    processing_status["error"] = f"Failed {url}: {error_msg}"
                else:
                    time.sleep(2) # Short wait for other errors
        
        # Stop everything if we failed after retries (likely quota limit)
        if not success:
            processing_status["active"] = False
            return

        # 1-MINUTE COOLDOWN (Skip for the last item)
        if index < len(urls) - 1:
            time.sleep(60)
            
    processing_status["active"] = False

# --- ROUTES ---

@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html', status=processing_status)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form.get('username')
        pw = request.form.get('password')
        if user == USERNAME and pw == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return render_template('index.html', status={"active":False, "error": "Invalid Credentials"}) # Simple feedback
    
    # Simple Login UI embedded to avoid extra file
    return '''
    <div style="max-width:300px; margin: 100px auto; font-family: sans-serif; text-align: center;">
        <h2>Login</h2>
        <form method="post">
            <input type="text" name="username" placeholder="Username" style="padding:10px; width:90%; margin-bottom:10px;"><br>
            <input type="password" name="password" placeholder="Password" style="padding:10px; width:90%; margin-bottom:10px;"><br>
            <input type="submit" value="Login" style="padding:10px 20px; background:#2196f3; color:white; border:none; cursor:pointer;">
        </form>
    </div>
    '''

@app.route('/upload', methods=['POST'])
def upload():
    if not session.get('logged_in'): return redirect(url_for('login'))
    
    if 'file' not in request.files:
        return redirect(url_for('index'))
        
    file = request.files['file']
    if file and file.filename:
        file.save(INPUT_FILE)
        
        # Read URLs immediately
        with open(INPUT_FILE, 'r') as f:
            urls = [line.strip() for line in f.readlines() if line.strip()]
            
        if urls:
            # Start background thread
            thread = threading.Thread(target=extract_math_logic, args=(urls,))
            thread.daemon = True # Ensures thread dies if main app dies
            thread.start()
            
    return redirect(url_for('index'))

@app.route('/download')
def download():
    if not session.get('logged_in'): return redirect(url_for('login'))
    
    if os.path.exists(OUTPUT_FILE):
        return send_file(OUTPUT_FILE, as_attachment=True)
    return "No output file generated yet."

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    # Use 0.0.0.0 for external access if hosting
    app.run(host='0.0.0.0', port=5000, debug=True)