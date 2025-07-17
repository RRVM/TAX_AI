import os
import uuid
import re
import json
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import PyPDF2
import psycopg2
import tax_calculator

# Load environment variables from .env
load_dotenv()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.urandom(24)
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
DB_URL = os.getenv('DB_URL')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Helper: check allowed file
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Helper: extract text from PDF (text-based only)
def extract_text_from_pdf(pdf_path):
    text = ""
    with open(pdf_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() or ""
    print("[DEBUG] Extracted PDF text:\n", text)
    return text

# Helper: extract fields using Gemini AI
def extract_fields_with_gemini(text):
    if not GEMINI_API_KEY:
        print("[DEBUG] No Gemini API key found.")
        return None
    prompt = (
        "Extract the following fields from this salary slip text. "
        "Return ONLY a plain JSON object with these keys: "
        "gross_salary, basic_salary, hra_received, rent_paid, deduction_80c, deduction_80d, standard_deduction, professional_tax, tds. "
        "If a value is not present, use an empty string. Do NOT include any code block markers, preamble, or explanation.\n"
        "Example: {\"gross_salary\": \"100000\", \"basic_salary\": \"50000\", ...}\n\n"
        f"Salary slip text:\n{text}\n"
    )
    try:
        response = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}]
            },
            timeout=30
        )
        print("[DEBUG] Gemini API status:", response.status_code)
        print("[DEBUG] Gemini API response:", response.text)
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
        if not candidates:
            print("[DEBUG] No candidates in Gemini response.")
            return None
        text_response = candidates[0]["content"]["parts"][0]["text"]
        print("[DEBUG] Gemini text response:", text_response)
        # Remove code block markers if present
        cleaned = text_response.strip()
        if cleaned.startswith('```json'):
            cleaned = cleaned[len('```json'):].strip()
        if cleaned.startswith('```'):
            cleaned = cleaned[len('```'):].strip()
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3].strip()
        try:
            parsed = json.loads(cleaned)
            print("[DEBUG] Parsed Gemini JSON:", parsed)
            return parsed
        except Exception as e:
            print("[DEBUG] Error parsing Gemini JSON:", e)
            return None
    except Exception as e:
        print(f"Gemini extraction failed: {e}")
        return None

AI_LOG_FILE = 'ai_conversation_log.json'

# Helper: Gemini follow-up question
def gemini_followup_question(user_data):
    prompt = (
        "Given the following user tax and salary data, generate a single, smart, contextual follow-up question that will help you give better investment or tax-saving advice.\n"
        "User data (JSON):\n" + json.dumps(user_data) + "\n"
        "Return ONLY the question, no preamble."
    )
    try:
        response = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30
        )
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
        if not candidates:
            return "What are your main financial goals for this year?"
        text_response = candidates[0]["content"]["parts"][0]["text"]
        return text_response.strip()
    except Exception as e:
        print(f"[DEBUG] Gemini follow-up question error: {e}")
        return "What are your main financial goals for this year?"

# Helper: Gemini personalized suggestions
def gemini_suggestions(user_data, user_answer):
    prompt = (
        "Given the following user tax and salary data, and their answer to your follow-up question, provide personalized, actionable investment and tax-saving suggestions. "
        "Format your response as a short, readable list or card.\n"
        "User data (JSON):\n" + json.dumps(user_data) + "\n"
        "User answer: " + user_answer + "\n"
        "Suggestions:"
    )
    try:
        response = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30
        )
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
        if not candidates:
            return "Could not generate suggestions."
        text_response = candidates[0]["content"]["parts"][0]["text"]
        return text_response.strip()
    except Exception as e:
        print(f"[DEBUG] Gemini suggestions error: {e}")
        return "Could not generate suggestions."

# Helper: Load/save conversation log
def load_conversation():
    try:
        with open(AI_LOG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_conversation(entry):
    log = load_conversation()
    log.append(entry)
    with open(AI_LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log, f, indent=2)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'GET':
        return render_template('form.html', extracted=None)
    # POST: handle file upload, data review, or AI Q&A
    if 'pdf_file' in request.files:
        file = request.files['pdf_file']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            session_id = str(uuid.uuid4())
            session['session_id'] = session_id
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
            file.save(save_path)
            # Extract text from PDF
            text = extract_text_from_pdf(save_path)
            # Extract fields using Gemini AI
            extracted = extract_fields_with_gemini(text)
            if not extracted:
                flash('Could not extract data from your salary slip automatically. Please fill in the fields manually.')
                extracted = {
                    'gross_salary': '',
                    'basic_salary': '',
                    'hra_received': '',
                    'rent_paid': '',
                    'deduction_80c': '',
                    'deduction_80d': '',
                    'standard_deduction': '',
                    'professional_tax': '',
                    'tds': '',
                    'selected_regime': 'new',
                }
            # Clean up PDF after extraction
            try:
                os.remove(save_path)
            except Exception:
                pass
            return render_template('form.html', extracted=extracted)
        else:
            flash('Invalid file type. Please upload a PDF.')
            return redirect(url_for('upload'))

    # If this is an AI Q&A POST, try to get data/results from session
    if request.form.get('get_ai') or request.form.get('user_answer'):
        session_data = session.get('results_data')
        session_comparison = session.get('results_comparison')
        if session_data and session_comparison:
            data = session_data
            comparison = session_comparison
        else:
            # fallback: parse from form (should not happen in normal flow)
            data = {
                'gross_salary': request.form.get('gross_salary', ''),
                'basic_salary': request.form.get('basic_salary', ''),
                'hra_received': request.form.get('hra_received', ''),
                'rent_paid': request.form.get('rent_paid', ''),
                'deduction_80c': request.form.get('deduction_80c', ''),
                'deduction_80d': request.form.get('deduction_80d', ''),
                'standard_deduction': request.form.get('standard_deduction', ''),
                'professional_tax': request.form.get('professional_tax', ''),
                'tds': request.form.get('tds', ''),
                'selected_regime': request.form.get('selected_regime', 'new'),
            }
            comparison = tax_calculator.calculate_tax_comparison(data)
    else:
        # Normal review/edit form submission
        data = {
            'gross_salary': request.form.get('gross_salary', ''),
            'basic_salary': request.form.get('basic_salary', ''),
            'hra_received': request.form.get('hra_received', ''),
            'rent_paid': request.form.get('rent_paid', ''),
            'deduction_80c': request.form.get('deduction_80c', ''),
            'deduction_80d': request.form.get('deduction_80d', ''),
            'standard_deduction': request.form.get('standard_deduction', ''),
            'professional_tax': request.form.get('professional_tax', ''),
            'tds': request.form.get('tds', ''),
            'selected_regime': request.form.get('selected_regime', 'new'),
        }
        salary_period = request.form.get('salary_period', 'monthly')
        # If monthly, multiply all relevant fields by 12
        if salary_period == 'monthly':
            for k in ['gross_salary', 'basic_salary', 'hra_received', 'rent_paid', 'deduction_80c', 'deduction_80d', 'professional_tax', 'tds']:
                try:
                    data[k] = float(data[k]) * 12 if data[k] else 0
                except Exception:
                    data[k] = 0
        else:
            for k in ['gross_salary', 'basic_salary', 'hra_received', 'rent_paid', 'deduction_80c', 'deduction_80d', 'professional_tax', 'tds']:
                try:
                    data[k] = float(data[k]) if data[k] else 0
                except Exception:
                    data[k] = 0
        session_id = session.get('session_id', str(uuid.uuid4()))
        # Calculate tax
        comparison = tax_calculator.calculate_tax_comparison(data)
        # Save to Supabase (unchanged)
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            # Save to UserFinancials
            cur.execute('''
                INSERT INTO UserFinancials (
                    session_id, gross_salary, basic_salary, hra_received, rent_paid, deduction_80c, deduction_80d, standard_deduction, professional_tax, tds, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    gross_salary=EXCLUDED.gross_salary,
                    basic_salary=EXCLUDED.basic_salary,
                    hra_received=EXCLUDED.hra_received,
                    rent_paid=EXCLUDED.rent_paid,
                    deduction_80c=EXCLUDED.deduction_80c,
                    deduction_80d=EXCLUDED.deduction_80d,
                    standard_deduction=EXCLUDED.standard_deduction,
                    professional_tax=EXCLUDED.professional_tax,
                    tds=EXCLUDED.tds,
                    created_at=NOW()
            ''', (
                session_id, data['gross_salary'], data['basic_salary'], data['hra_received'], data['rent_paid'],
                data['deduction_80c'], data['deduction_80d'], data['standard_deduction'], data['professional_tax'], data['tds']
            ))
            # Save to TaxComparison
            cur.execute('''
                INSERT INTO TaxComparison (
                    session_id, tax_old_regime, tax_new_regime, best_regime, selected_regime, created_at
                ) VALUES (%s,%s,%s,%s,%s, NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    tax_old_regime=EXCLUDED.tax_old_regime,
                    tax_new_regime=EXCLUDED.tax_new_regime,
                    best_regime=EXCLUDED.best_regime,
                    selected_regime=EXCLUDED.selected_regime,
                    created_at=NOW()
            ''', (
                session_id, comparison['tax_old_regime'], comparison['tax_new_regime'], comparison['best_regime'], data['selected_regime']
            ))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            flash(f'Error saving to database: {e}')
        # Store data/results in session for AI Q&A
        session['results_data'] = data
        session['results_comparison'] = comparison
    # AI Q&A logic
    ai_question = None
    ai_suggestions = None
    user_answer = None
    if request.form.get('get_ai'):
        ai_question = gemini_followup_question(data)
    elif request.form.get('user_answer'):
        ai_question = request.form.get('ai_question')
        user_answer = request.form.get('user_answer')
        ai_suggestions = gemini_suggestions(data, user_answer)
    return render_template(
        'results.html',
        data=data,
        tax_old_regime=comparison['tax_old_regime'],
        tax_new_regime=comparison['tax_new_regime'],
        selected_regime=data['selected_regime'],
        ai_question=ai_question,
        ai_suggestions=ai_suggestions,
        user_answer=user_answer
    )

@app.route('/ask', methods=['GET', 'POST'])
def ask():
    # Get user data from session or last submission
    user_data = session.get('last_user_data')
    if not user_data:
        flash('No user data found. Please complete the tax calculation first.')
        return redirect(url_for('index'))
    conversation = load_conversation()
    if request.method == 'GET':
        # Generate follow-up question using Gemini
        question = gemini_followup_question(user_data)
        return render_template('ask.html', question=question, suggestions=None, user_answer=None, conversation=conversation)
    else:
        # POST: user answered the question
        user_answer = request.form.get('user_answer', '')
        question = request.form.get('question') or gemini_followup_question(user_data)
        suggestions = gemini_suggestions(user_data, user_answer)
        # Log the Q&A
        entry = {
            'question': question,
            'user_answer': user_answer,
            'suggestions': suggestions
        }
        save_conversation(entry)
        return render_template('ask.html', question=question, suggestions=suggestions, user_answer=user_answer, conversation=load_conversation())

# After tax results, redirect to /ask and store user data in session
@app.route('/to_advisor', methods=['POST'])
def to_advisor():
    # Collect user data from form or session
    user_data = {
        'gross_salary': request.form.get('gross_salary', ''),
        'basic_salary': request.form.get('basic_salary', ''),
        'hra_received': request.form.get('hra_received', ''),
        'rent_paid': request.form.get('rent_paid', ''),
        'deduction_80c': request.form.get('deduction_80c', ''),
        'deduction_80d': request.form.get('deduction_80d', ''),
        'standard_deduction': request.form.get('standard_deduction', ''),
        'professional_tax': request.form.get('professional_tax', ''),
        'tds': request.form.get('tds', ''),
        'selected_regime': request.form.get('selected_regime', 'new'),
    }
    session['last_user_data'] = user_data
    return redirect(url_for('ask'))

if __name__ == '__main__':
    app.run(debug=True) 