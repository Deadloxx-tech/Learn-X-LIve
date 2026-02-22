"""
LearnXLive — AI-Powered Assignment Insight & Feedback Assistant
Flask backend with Ollama LLM integration
"""

import os
import json
import sqlite3
import uuid
import re
import threading
from datetime import datetime
from functools import wraps

from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, g, send_from_directory
)
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import PyPDF2

# ── App Config ────────────────────────────────────────────────────────────────
app = Flask(
    __name__,
    static_folder='static',
    template_folder='templates'
)
app.secret_key = 'learnxlive-secret-key-2026'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['PROFILE_PHOTOS_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads', 'profile_photos')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB
CORS(app)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROFILE_PHOTOS_FOLDER'], exist_ok=True)

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'learnxlive.db')


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════════════════

def get_db():
    if 'db' not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            password TEXT NOT NULL,
            role TEXT CHECK(role IN ('student','teacher')) NOT NULL,
            qualification TEXT,
            subject_expertise TEXT,
            experience_years INTEGER,
            teacher_id_code TEXT,
            bio TEXT,
            department TEXT,
            institution TEXT,
            website TEXT,
            linkedin TEXT,
            github TEXT,
            profile_photo TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS courses (
            id TEXT PRIMARY KEY,
            teacher_id TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT,
            fees REAL DEFAULT 0,
            duration_weeks INTEGER DEFAULT 1,
            syllabus TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS course_enrollments (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            enrolled_at TEXT DEFAULT (datetime('now')),
            progress INTEGER DEFAULT 0,
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (student_id) REFERENCES users(id),
            UNIQUE(course_id, student_id)
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            master_answer TEXT NOT NULL,
            total_marks REAL DEFAULT 0,
            due_date TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            assignment_id TEXT NOT NULL,
            question_number INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            answer_key TEXT NOT NULL,
            marks REAL NOT NULL DEFAULT 1,
            FOREIGN KEY (assignment_id) REFERENCES assignments(id)
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY,
            assignment_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            content TEXT NOT NULL,
            file_path TEXT,
            submitted_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'submitted'
                CHECK(status IN ('submitted','analyzing','reviewed')),
            FOREIGN KEY (assignment_id) REFERENCES assignments(id),
            FOREIGN KEY (student_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS insights (
            id TEXT PRIMARY KEY,
            assignment_id TEXT NOT NULL,
            type TEXT CHECK(type IN ('class','student','question')) NOT NULL,
            student_id TEXT,
            data TEXT NOT NULL,
            confidence REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (assignment_id) REFERENCES assignments(id)
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL,
            ai_draft TEXT NOT NULL,
            teacher_edited TEXT,
            score REAL,
            status TEXT DEFAULT 'draft'
                CHECK(status IN ('draft','approved','rejected')),
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        );
    ''')
    db.commit()

    # ── Migrate existing databases: add new profile columns if missing ──
    new_columns = [
        ('bio', 'TEXT'),
        ('department', 'TEXT'),
        ('institution', 'TEXT'),
        ('website', 'TEXT'),
        ('linkedin', 'TEXT'),
        ('github', 'TEXT'),
        ('profile_photo', 'TEXT'),
    ]
    for col_name, col_type in new_columns:
        try:
            db.execute(f'ALTER TABLE users ADD COLUMN {col_name} {col_type}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    db.commit()


# ══════════════════════════════════════════════════════════════════════════════
#  AI ENGINE (Ollama LLM + keyword fallback)
# ══════════════════════════════════════════════════════════════════════════════

from ollama_engine import (
    check_ollama_health,
    analyze_student_submission,
    analyze_all_questions,
    generate_class_summary,
    generate_student_feedback,
    fallback_analyze_submission,
    parse_csv_submissions,
    parse_json_submissions,
    generate_assignment,
)


def extract_text_from_file(file_path):
    """Extract text content from uploaded PDF, TXT, CSV, or JSON files."""
    if not file_path or not os.path.isfile(file_path):
        return ''

    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.pdf':
        try:
            text_parts = []
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            return '\n'.join(text_parts).strip()
        except Exception:
            return ''

    elif ext == '.txt':
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read().strip()
        except Exception:
            return ''

    return ''


def _parse_student_answers(content, questions):
    """Parse student submission content into per-question answers."""
    answers = {}
    # Try JSON format first (from per-question submission UI)
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed  # {"1": "answer1", "2": "answer2", ...}
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: try to split by Q1, Q2, etc. patterns
    import re
    parts = re.split(r'(?:^|\n)\s*(?:Q|Question)\s*(\d+)\s*[:.)\-]\s*', content, flags=re.IGNORECASE)
    if len(parts) > 1:
        # parts = [preamble, num1, answer1, num2, answer2, ...]
        for i in range(1, len(parts) - 1, 2):
            qnum = parts[i].strip()
            ans = parts[i + 1].strip() if i + 1 < len(parts) else ''
            answers[qnum] = ans
        if answers:
            return answers

    # Final fallback: treat entire content as answer to all questions combined
    for q in questions:
        answers[str(q['question_number'])] = content
    return answers


def run_analysis(assignment_id, target_student_id=None):
    """Full AI pipeline for an assignment — per-question scoring with marks."""
    db = get_db()

    # Get assignment
    assignment = db.execute(
        'SELECT * FROM assignments WHERE id=?', (assignment_id,)
    ).fetchone()
    if not assignment:
        return None, 'Assignment not found'

    assignment_title = assignment['title']
    total_marks = float(assignment['total_marks'] or 0)

    # Get questions for this assignment
    questions = db.execute(
        'SELECT * FROM questions WHERE assignment_id=? ORDER BY question_number',
        (assignment_id,)
    ).fetchall()
    questions = [dict(q) for q in questions]

    # If no questions found (old assignment), fall back to legacy whole-answer mode
    if not questions:
        questions = [{
            'question_number': 1,
            'question_text': assignment_title,
            'answer_key': assignment['master_answer'],
            'marks': total_marks or 100
        }]
        if total_marks == 0:
            total_marks = 100

    master_answer = assignment['master_answer']

    # Get all submissions (or just for a specific student)
    if target_student_id:
        submissions = db.execute('''
            SELECT s.*, u.name as student_name
            FROM submissions s
            JOIN users u ON s.student_id = u.id
            WHERE s.assignment_id=? AND s.student_id=?
        ''', (assignment_id, target_student_id)).fetchall()
    else:
        submissions = db.execute('''
            SELECT s.*, u.name as student_name
            FROM submissions s
            JOIN users u ON s.student_id = u.id
            WHERE s.assignment_id=?
        ''', (assignment_id,)).fetchall()

    if not submissions:
        return None, 'No submissions found'

    # Mark as analyzing
    if target_student_id:
        db.execute(
            "UPDATE submissions SET status='analyzing' WHERE assignment_id=? AND student_id=?",
            (assignment_id, target_student_id)
        )
    else:
        db.execute(
            "UPDATE submissions SET status='analyzing' WHERE assignment_id=?",
            (assignment_id,)
        )

    # Check if Ollama is available
    health = check_ollama_health()
    use_ollama = health.get('status') == 'online' and health.get('has_required_model', False)

    submissions_data = []
    student_results = []

    for sub in submissions:
        sub_content = sub['content']
        if sub_content.startswith('[Uploaded file:') and sub['file_path']:
            extracted = extract_text_from_file(sub['file_path'])
            if extracted:
                sub_content = extracted

        # Parse per-question answers
        student_answers = _parse_student_answers(sub_content, questions)

        # Analyze all questions — batch call first, per-question fallback
        question_results = []
        total_marks_earned = 0
        question_confidences = []

        # Build a map of question results from batch or per-question analysis
        q_result_map = {}  # question_number -> result dict

        if use_ollama:
            # Try batch analysis (single Ollama call for all questions)
            batch_results = analyze_all_questions(
                questions, student_answers, assignment_title, sub['student_name']
            )
            if batch_results:
                for br in batch_results:
                    qn = br.get('question_number')
                    if qn is not None:
                        q_result_map[int(qn)] = br

        # Fill in any missing questions with per-question analysis
        for q in questions:
            qnum = str(q['question_number'])
            q_marks = float(q['marks'])
            result = q_result_map.get(q['question_number'])

            if not result:
                student_ans = student_answers.get(qnum, '')
                if use_ollama:
                    result = analyze_student_submission(
                        q['answer_key'], student_ans,
                        f"{assignment_title} - Q{qnum}", sub['student_name']
                    )
                if not result:
                    student_ans = student_answers.get(qnum, '')
                    result = fallback_analyze_submission(
                        q['answer_key'], student_ans, sub['student_name']
                    )

            question_confidences.append(result.get('confidence', 70))

            pct = result.get('score', 0)
            marks_earned = round((pct / 100) * q_marks, 1)
            total_marks_earned += marks_earned

            question_results.append({
                'question_number': q['question_number'],
                'question_text': q['question_text'],
                'max_marks': q_marks,
                'marks_earned': marks_earned,
                'percentage': round(pct, 1),
                'matched_concepts': result.get('matched_concepts', []),
                'missing_concepts': result.get('missing_concepts', []),
                'explanation': result.get('explanation', ''),
            })

        # Final score as percentage of total marks
        final_pct = round((total_marks_earned / total_marks) * 100, 1) if total_marks > 0 else 0
        depth = 'thorough' if final_pct >= 70 else 'moderate' if final_pct >= 40 else 'shallow'

        # Aggregate matched/missing concepts across questions
        all_matched = []
        all_missing = []
        for qr in question_results:
            all_matched.extend(qr['matched_concepts'])
            all_missing.extend(qr['missing_concepts'])

        sub_data = {
            'submission_id': sub['id'],
            'student_id': sub['student_id'],
            'student_name': sub['student_name'],
            'final_score': final_pct,
            'total_marks_earned': round(total_marks_earned, 1),
            'total_marks': total_marks,
            'question_results': question_results,
            'matched_concepts': list(set(all_matched)),
            'missing_concepts': list(set(all_missing)),
            'extra_concepts': [],
            'accuracy_issues': [],
            'depth_rating': depth,
            'confidence': round(sum(question_confidences) / len(question_confidences), 1) if question_confidences else 70,
            'explanation': f"Scored {total_marks_earned}/{total_marks} marks ({final_pct}%)",
            'is_fallback': not use_ollama,
        }
        submissions_data.append(sub_data)
        student_results.append(sub_data)

    # ── Class-level summary ──
    class_summary_data = None
    if use_ollama:
        if not target_student_id:
            class_summary_data = generate_class_summary(
                student_results, assignment_title, master_answer
            )
        else:
            # For a single student, don't regenerate the class summary, but fetch the old one if it exists
            old_class_insight = db.execute(
                "SELECT data FROM insights WHERE assignment_id=? AND type='class'", 
                (assignment_id,)
            ).fetchone()
            if old_class_insight:
                class_summary_data = json.loads(old_class_insight['data'])
                # Map old format to what's expected below
                class_summary_data = {
                    'class_summary': class_summary_data.get('ai_summary', ''),
                    'common_mistakes': class_summary_data.get('common_mistakes', []),
                    'concept_clusters': class_summary_data.get('concept_clusters', []),
                    'performance_patterns': class_summary_data.get('performance_patterns', {}),
                    'teaching_recommendations': class_summary_data.get('teaching_recommendations', [])
                }

    # Compute stats across ALL student insights (to update class stats accurately)
    # First, fetch all current insights to get everyone's score
    current_insights = db.execute(
        "SELECT data FROM insights WHERE assignment_id=? AND type='student'",
        (assignment_id,)
    ).fetchall()
    
    all_scores = []
    # Map of student_id -> score
    student_scores = {}
    for ci in current_insights:
        ci_data = json.loads(ci['data'])
        student_scores[ci_data.get('student_id', '')] = ci_data.get('final_score', 0)
        
    # Replace/Add the newly analyzed ones
    for s in submissions_data:
        student_scores[s['student_id']] = s['final_score']
        
    all_scores = list(student_scores.values())

    # Calculate actual unique submission count
    unique_subs_count = db.execute('''
        SELECT COUNT(DISTINCT student_id) as c 
        FROM submissions 
        WHERE assignment_id=?
    ''', (assignment_id,)).fetchone()['c']

    class_stats = {
        'average_score': round(sum(all_scores) / len(all_scores), 1) if all_scores else 0,
        'highest_score': max(all_scores) if all_scores else 0,
        'lowest_score': min(all_scores) if all_scores else 0,
        'total_submissions': unique_subs_count,
        'total_marks': total_marks,
        'above_70': sum(1 for s in all_scores if s >= 70),
        'below_50': sum(1 for s in all_scores if s < 50),
    }

    # Delete old insights and feedback for this assignment
    db.execute('DELETE FROM insights WHERE assignment_id=? AND type=?', (assignment_id, 'class'))
    if target_student_id:
        db.execute('DELETE FROM insights WHERE assignment_id=? AND type=? AND student_id=?', (assignment_id, 'student', target_student_id))
    else:
        db.execute('DELETE FROM insights WHERE assignment_id=? AND type=?', (assignment_id, 'student'))
        
    old_subs = [s['id'] for s in submissions]
    for sid in old_subs:
        db.execute('DELETE FROM feedback WHERE submission_id=?', (sid,))

    # Save class-level insight
    class_insight_data = {
        'stats': class_stats,
        'ai_summary': class_summary_data.get('class_summary', '') if class_summary_data else '',
        'common_mistakes': class_summary_data.get('common_mistakes', []) if class_summary_data else [],
        'concept_clusters': class_summary_data.get('concept_clusters', []) if class_summary_data else [],
        'performance_patterns': class_summary_data.get('performance_patterns', {}) if class_summary_data else {},
        'teaching_recommendations': class_summary_data.get('teaching_recommendations', []) if class_summary_data else [],
        'ollama_powered': use_ollama,
    }
    db.execute(
        'INSERT INTO insights (id, assignment_id, type, data, confidence) VALUES (?,?,?,?,?)',
        (str(uuid.uuid4()), assignment_id, 'class', json.dumps(class_insight_data),
         min(95, 60 + len(submissions) * 2))
    )

    # Save per-student insights and generate feedback
    for sub_data in submissions_data:
        student_insight = {
            'student_id': sub_data['student_id'],
            'student_name': sub_data['student_name'],
            'final_score': sub_data['final_score'],
            'total_marks_earned': sub_data['total_marks_earned'],
            'total_marks': sub_data['total_marks'],
            'question_results': sub_data['question_results'],
            'matched_concepts': sub_data['matched_concepts'],
            'missing_concepts': sub_data['missing_concepts'],
            'depth_rating': sub_data['depth_rating'],
            'explanation': sub_data['explanation'],
            'is_fallback': sub_data['is_fallback'],
        }
        db.execute(
            'INSERT INTO insights (id, assignment_id, type, student_id, data, confidence) VALUES (?,?,?,?,?,?)',
            (str(uuid.uuid4()), assignment_id, 'student', sub_data['student_id'],
             json.dumps(student_insight), sub_data['confidence'])
        )

        # Build per-question marks table for feedback draft
        marks_table = "Question-wise Marks Breakdown:\n"
        for qr in sub_data['question_results']:
            marks_table += f"  Q{qr['question_number']}: {qr['marks_earned']}/{qr['max_marks']} marks"
            marks_table += f" ({qr['percentage']}%)\n"
        marks_table += f"\nTotal: {sub_data['total_marks_earned']}/{sub_data['total_marks']} marks ({sub_data['final_score']}%)"

        # Generate feedback
        sub_row = next((s for s in submissions if s['id'] == sub_data['submission_id']), None)
        sub_content = sub_row['content'] if sub_row else ''

        fb = generate_student_feedback(
            sub_data['final_score'],
            sub_data['matched_concepts'],
            sub_data['missing_concepts'],
            sub_data.get('accuracy_issues', []),
            sub_data['student_name'],
            sub_content,
            assignment_title
        )

        draft_text = fb.get('draft', '') if fb else ''
        # Prepend the marks breakdown to the feedback
        draft_text = marks_table + "\n\n" + draft_text

        db.execute(
            'INSERT INTO feedback (id, submission_id, ai_draft, score, status) VALUES (?,?,?,?,?)',
            (str(uuid.uuid4()), sub_data['submission_id'], draft_text,
             sub_data['final_score'], 'draft')
        )

    # Mark submissions as reviewed
    if target_student_id:
        db.execute(
            "UPDATE submissions SET status='reviewed' WHERE assignment_id=? AND student_id=?",
            (assignment_id, target_student_id)
        )
    else:
        db.execute(
            "UPDATE submissions SET status='reviewed' WHERE assignment_id=?",
            (assignment_id,)
        )
    db.commit()

    return {
        'class_stats': class_stats,
        'ai_summary': class_insight_data.get('ai_summary', ''),
        'common_mistakes': class_insight_data.get('common_mistakes', []),
        'concept_clusters': class_insight_data.get('concept_clusters', []),
        'performance_patterns': class_insight_data.get('performance_patterns', {}),
        'teaching_recommendations': class_insight_data.get('teaching_recommendations', []),
        'submissions': submissions_data,
        'ollama_powered': use_ollama,
    }, None


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


def teacher_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'teacher':
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'teacher':
            return redirect(url_for('teacher_dashboard'))
        return redirect(url_for('student_dashboard'))
    return render_template('index.html')


@app.route('/login')
def login_page():
    return render_template('login.html')


@app.route('/teacher/onboarding')
@login_required
def teacher_onboarding():
    return render_template('teacher_onboarding.html')


@app.route('/teacher/dashboard')
@teacher_required
def teacher_dashboard():
    return render_template('teacher_dashboard.html',
                           user=session.get('user_name', 'Teacher'))


@app.route('/student/dashboard')
@login_required
def student_dashboard():
    # Enforce strict role validation for student dashboard to prevent session overlap
    if session.get('role') != 'student':
        return redirect(url_for('teacher_dashboard'))
    return render_template('student_dashboard.html',
                           user=session.get('user_name', 'Student'))


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    data = request.get_json()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'student').lower()

    if not name or not email or not password:
        return jsonify({'error': 'Name, email and password required'}), 400

    if role not in ('student', 'teacher'):
        return jsonify({'error': 'Invalid role'}), 400

    db = get_db()
    existing = db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
    if existing:
        return jsonify({'error': 'Email already exists'}), 409

    user_id = str(uuid.uuid4())
    hashed = generate_password_hash(password)
    teacher_code = f"TCH-{uuid.uuid4().hex[:5].upper()}" if role == 'teacher' else None

    db.execute(
        'INSERT INTO users (id, name, email, phone, password, role, teacher_id_code) VALUES (?,?,?,?,?,?,?)',
        (user_id, name, email, phone, hashed, role, teacher_code)
    )
    db.commit()

    session['user_id'] = user_id
    session['user_name'] = name
    session['role'] = role

    redirect_url = '/teacher/onboarding' if role == 'teacher' else '/student/dashboard'
    return jsonify({
        'id': user_id, 'name': name, 'email': email, 'role': role,
        'teacher_id_code': teacher_code, 'redirect': redirect_url
    }), 201


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'student').lower()

    db = get_db()
    user = db.execute(
        'SELECT * FROM users WHERE email=? AND role=?', (email, role)
    ).fetchone()

    if not user or not check_password_hash(user['password'], password):
        return jsonify({'error': 'Invalid credentials'}), 401

    session['user_id'] = user['id']
    session['user_name'] = user['name']
    session['role'] = user['role']

    redirect_url = '/teacher/dashboard' if role == 'teacher' else '/student/dashboard'
    return jsonify({
        'id': user['id'], 'name': user['name'], 'role': user['role'],
        'redirect': redirect_url
    })


@app.route('/api/auth/profile', methods=['GET'])
@login_required
def get_profile():
    db = get_db()
    user = db.execute(
        'SELECT id, name, email, phone, role, qualification, subject_expertise, '
        'experience_years, teacher_id_code, bio, department, institution, '
        'website, linkedin, github, profile_photo, created_at FROM users WHERE id=?',
        (session['user_id'],)
    ).fetchone()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    data = dict(user)
    if data.get('profile_photo'):
        data['profile_photo_url'] = f'/uploads/profile_photos/{data["profile_photo"]}'
    else:
        data['profile_photo_url'] = None
    return jsonify(data)


@app.route('/api/auth/profile', methods=['PUT'])
@login_required
def update_profile():
    data = request.get_json()
    db = get_db()
    db.execute('''
        UPDATE users SET
            qualification=?, subject_expertise=?, experience_years=?,
            name=COALESCE(?, name), phone=COALESCE(?, phone),
            bio=?, department=?, institution=?,
            website=?, linkedin=?, github=?
        WHERE id=?
    ''', (
        data.get('qualification'), data.get('subject_expertise'),
        data.get('experience_years'), data.get('name'),
        data.get('phone'),
        data.get('bio'), data.get('department'), data.get('institution'),
        data.get('website'), data.get('linkedin'), data.get('github'),
        session['user_id']
    ))
    db.commit()
    if data.get('name'):
        session['user_name'] = data['name']
    return jsonify({'status': 'updated'})


ALLOWED_PHOTO_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


@app.route('/api/auth/profile/photo', methods=['POST'])
@login_required
def upload_profile_photo():
    if 'photo' not in request.files:
        return jsonify({'error': 'No photo file provided'}), 400
    file = request.files['photo']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_PHOTO_EXTENSIONS:
        return jsonify({'error': 'Allowed formats: png, jpg, jpeg, gif, webp'}), 400

    filename = f"{session['user_id']}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(app.config['PROFILE_PHOTOS_FOLDER'], filename)
    file.save(filepath)

    # Remove old photo if exists
    db = get_db()
    old = db.execute('SELECT profile_photo FROM users WHERE id=?', (session['user_id'],)).fetchone()
    if old and old['profile_photo']:
        old_path = os.path.join(app.config['PROFILE_PHOTOS_FOLDER'], old['profile_photo'])
        if os.path.exists(old_path):
            os.remove(old_path)

    db.execute('UPDATE users SET profile_photo=? WHERE id=?', (filename, session['user_id']))
    db.commit()

    return jsonify({
        'status': 'uploaded',
        'profile_photo': filename,
        'profile_photo_url': f'/uploads/profile_photos/{filename}'
    })


@app.route('/uploads/profile_photos/<filename>')
def serve_profile_photo(filename):
    return send_from_directory(app.config['PROFILE_PHOTOS_FOLDER'], filename)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# ══════════════════════════════════════════════════════════════════════════════
#  COURSE API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/courses', methods=['GET'])
@login_required
def get_courses():
    db = get_db()
    if session['role'] == 'teacher':
        courses = db.execute(
            'SELECT * FROM courses WHERE teacher_id=? ORDER BY created_at DESC',
            (session['user_id'],)
        ).fetchall()
    else:
        courses = db.execute('''
            SELECT c.*, ce.progress, ce.enrolled_at
            FROM courses c
            LEFT JOIN course_enrollments ce
                ON c.id = ce.course_id AND ce.student_id=?
            ORDER BY c.created_at DESC
        ''', (session['user_id'],)).fetchall()
    return jsonify([dict(c) for c in courses])


@app.route('/api/courses', methods=['POST'])
@teacher_required
def create_course():
    data = request.get_json()
    course_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        'INSERT INTO courses (id, teacher_id, title, category, fees, duration_weeks, syllabus) VALUES (?,?,?,?,?,?,?)',
        (course_id, session['user_id'], data['title'], data.get('category', ''),
         data.get('fees', 0), data.get('duration_weeks', 1), data.get('syllabus', ''))
    )
    db.commit()
    return jsonify({'id': course_id, 'title': data['title']}), 201


@app.route('/api/courses/<course_id>/enroll', methods=['POST'])
@login_required
def enroll_course(course_id):
    db = get_db()
    try:
        db.execute(
            'INSERT INTO course_enrollments (id, course_id, student_id) VALUES (?,?,?)',
            (str(uuid.uuid4()), course_id, session['user_id'])
        )
        db.commit()
        return jsonify({'status': 'enrolled'})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Already enrolled'}), 409


# ══════════════════════════════════════════════════════════════════════════════
#  ASSIGNMENT API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/assignments', methods=['GET'])
@login_required
def get_assignments():
    db = get_db()
    course_id = request.args.get('course_id')
    if course_id:
        assignments = db.execute(
            'SELECT * FROM assignments WHERE course_id=? ORDER BY created_at DESC',
            (course_id,)
        ).fetchall()
    elif session['role'] == 'teacher':
        assignments = db.execute('''
            SELECT a.*, c.title as course_title
            FROM assignments a
            JOIN courses c ON a.course_id = c.id
            WHERE c.teacher_id=?
            ORDER BY a.created_at DESC
        ''', (session['user_id'],)).fetchall()
    else:
        assignments = db.execute('''
            SELECT a.*, c.title as course_title
            FROM assignments a
            JOIN courses c ON a.course_id = c.id
            ORDER BY a.created_at DESC
        ''').fetchall()
    return jsonify([dict(a) for a in assignments])


@app.route('/api/assignments', methods=['POST'])
@teacher_required
def create_assignment():
    data = request.get_json()
    assign_id = str(uuid.uuid4())
    db = get_db()

    questions = data.get('questions', [])
    if not questions:
        return jsonify({'error': 'At least one question is required'}), 400

    # Auto-generate master_answer from all question answer keys
    master_answer = '\n'.join(
        f"Q{i+1}: {q['answer_key']}" for i, q in enumerate(questions)
    )
    total_marks = sum(float(q.get('marks', 1)) for q in questions)

    db.execute(
        'INSERT INTO assignments (id, course_id, title, description, master_answer, total_marks, due_date) VALUES (?,?,?,?,?,?,?)',
        (assign_id, data['course_id'], data['title'],
         data.get('description', ''), master_answer, total_marks, data.get('due_date'))
    )

    # Insert each question
    for i, q in enumerate(questions):
        db.execute(
            'INSERT INTO questions (id, assignment_id, question_number, question_text, answer_key, marks) VALUES (?,?,?,?,?,?)',
            (str(uuid.uuid4()), assign_id, i + 1,
             q['question_text'], q['answer_key'], float(q.get('marks', 1)))
        )

    db.commit()
    return jsonify({'id': assign_id, 'total_marks': total_marks, 'question_count': len(questions)}), 201


@app.route('/api/assignments/<assignment_id>/questions', methods=['GET'])
@login_required
def get_questions(assignment_id):
    db = get_db()
    questions = db.execute(
        'SELECT * FROM questions WHERE assignment_id=? ORDER BY question_number',
        (assignment_id,)
    ).fetchall()
    return jsonify([dict(q) for q in questions])


@app.route('/api/assignments/generate', methods=['POST'])
@teacher_required
def auto_generate_assignment():
    data = request.get_json()
    title = data.get('title', '').strip()
    difficulty = data.get('difficulty', 'Mixed')
    try:
        num_questions = int(data.get('num_questions', 3))
    except (ValueError, TypeError):
        num_questions = 3

    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    # Cap number of questions to avoid overly long generation times
    num_questions = max(1, min(10, num_questions))

    result_data = generate_assignment(title, difficulty, num_questions)
    
    # result_data contains 'description' and 'questions'
    return jsonify(result_data), 200


# ══════════════════════════════════════════════════════════════════════════════
#  SUBMISSION API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/submissions', methods=['GET'])
@login_required
def get_submissions():
    db = get_db()
    assignment_id = request.args.get('assignment_id')
    if session['role'] == 'teacher':
        if assignment_id:
            subs = db.execute('''
                SELECT s.*, u.name as student_name
                FROM submissions s
                JOIN users u ON s.student_id = u.id
                WHERE s.assignment_id=?
                ORDER BY s.submitted_at DESC
            ''', (assignment_id,)).fetchall()
        else:
            subs = db.execute('''
                SELECT s.*, u.name as student_name
                FROM submissions s
                JOIN users u ON s.student_id = u.id
                ORDER BY s.submitted_at DESC
            ''').fetchall()
    else:
        subs = db.execute(
            'SELECT * FROM submissions WHERE student_id=? ORDER BY submitted_at DESC',
            (session['user_id'],)
        ).fetchall()
    return jsonify([dict(s) for s in subs])


@app.route('/api/submissions', methods=['POST'])
@login_required
def create_submission():
    try:
        ct = request.content_type or ''
        if 'multipart/form-data' in ct:
            assignment_id = request.form.get('assignment_id', '')
            content = request.form.get('content', '')
        else:
            data = request.get_json() or {}
            assignment_id = data.get('assignment_id', '')
            content = data.get('content', '')
            # Support per-question answers: {"answers": {"1": "...", "2": "..."}}
            answers = data.get('answers', {})
            if answers:
                content = json.dumps(answers)

        file_path = None
        if 'file' in request.files:
            f = request.files['file']
            if f.filename:
                filename = secure_filename(f.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                f.save(file_path)
                try:
                    extracted = extract_text_from_file(file_path)
                    if extracted:
                        content = extracted
                    elif not content or content == '{}':
                        content = f'[Uploaded file: {filename} does not contain readable text]'
                except Exception as ex:
                    print(f"Extraction error: {ex}")
                    if not content or content == '{}':
                        content = f'[Uploaded file: {filename} but extraction failed]'

        if not assignment_id or not content:
            return jsonify({'error': 'assignment_id and content required'}), 400

        sub_id = str(uuid.uuid4())
        student_id = session['user_id']
        db = get_db()
        db.execute(
            'INSERT INTO submissions (id, assignment_id, student_id, content, file_path) VALUES (?,?,?,?,?)',
            (sub_id, assignment_id, student_id, content, file_path)
        )
        db.commit()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Server Error: {str(e)}'}), 500

    # ── Auto-trigger AI analysis in background thread ──
    def _bg_analyze(app_obj, a_id, s_id):
        with app_obj.app_context():
            try:
                run_analysis(a_id, target_student_id=s_id)
            except Exception as e:
                print(f'[AutoAnalysis] Error analyzing submission: {e}')

    t = threading.Thread(
        target=_bg_analyze,
        args=(app, assignment_id, student_id),
        daemon=True
    )
    t.start()

    return jsonify({'id': sub_id, 'status': 'submitted', 'analysis_started': True}), 201


# ══════════════════════════════════════════════════════════════════════════════
#  AI / INSIGHT API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/ollama/status', methods=['GET'])
@login_required
def ollama_status():
    health = check_ollama_health()
    return jsonify(health)


@app.route('/api/analyze/<assignment_id>', methods=['POST'])
@login_required
def analyze(assignment_id):
    data = request.get_json() or {}
    # Students can only analyze their own submission
    if session.get('role') == 'student':
        student_id = session['user_id']
    else:
        student_id = data.get('student_id')
    result, error = run_analysis(assignment_id, target_student_id=student_id)
    if error:
        return jsonify({'error': error}), 404
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
#  STUDENT INSIGHTS API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/student/insights', methods=['GET'])
@login_required
def student_insights_all():
    """Return all AI insights for the logged-in student across all assignments."""
    db = get_db()
    sid = session['user_id']

    insights = db.execute('''
        SELECT i.*, a.title as assignment_title, a.total_marks,
               c.title as course_title, 
               (SELECT status FROM submissions s WHERE s.assignment_id = i.assignment_id AND s.student_id = i.student_id ORDER BY s.submitted_at DESC LIMIT 1) as submission_status,
               (SELECT submitted_at FROM submissions s WHERE s.assignment_id = i.assignment_id AND s.student_id = i.student_id ORDER BY s.submitted_at DESC LIMIT 1) as submitted_at
        FROM insights i
        JOIN assignments a ON i.assignment_id = a.id
        JOIN courses c ON a.course_id = c.id
        WHERE i.student_id=? AND i.type='student'
        ORDER BY i.created_at DESC
    ''', (sid,)).fetchall()

    result = []
    for ins in insights:
        d = dict(ins)
        d['data'] = json.loads(d['data'])
        result.append(d)
    return jsonify(result)


@app.route('/api/student/insights/<assignment_id>', methods=['GET'])
@login_required
def student_insights_assignment(assignment_id):
    """Return detailed AI insight + feedback for a student on a specific assignment."""
    db = get_db()
    sid = session['user_id']

    # Get the student's insight for this assignment
    insight = db.execute('''
        SELECT i.*, a.title as assignment_title, a.total_marks,
               a.description as assignment_description
        FROM insights i
        JOIN assignments a ON i.assignment_id = a.id
        WHERE i.assignment_id=? AND i.student_id=? AND i.type='student'
    ''', (assignment_id, sid)).fetchone()

    # Get the submission status
    submission = db.execute('''
        SELECT id, status, submitted_at FROM submissions
        WHERE assignment_id=? AND student_id=?
        ORDER BY submitted_at DESC LIMIT 1
    ''', (assignment_id, sid)).fetchone()

    # Get feedback
    feedback = None
    if submission:
        fb = db.execute('''
            SELECT * FROM feedback WHERE submission_id=?
        ''', (submission['id'],)).fetchone()
        if fb:
            feedback = dict(fb)

    result = {
        'submission': dict(submission) if submission else None,
        'insight': None,
        'feedback': feedback
    }

    if insight:
        d = dict(insight)
        d['data'] = json.loads(d['data'])
        result['insight'] = d

    return jsonify(result)


@app.route('/api/student/submission-status/<assignment_id>', methods=['GET'])
@login_required
def student_submission_status(assignment_id):
    """Quick poll endpoint to check if analysis is complete."""
    db = get_db()
    sid = session['user_id']
    sub = db.execute('''
        SELECT id, status, submitted_at FROM submissions
        WHERE assignment_id=? AND student_id=?
        ORDER BY submitted_at DESC LIMIT 1
    ''', (assignment_id, sid)).fetchone()

    if not sub:
        return jsonify({'status': 'not_submitted'})

    has_insight = db.execute('''
        SELECT COUNT(*) as c FROM insights
        WHERE assignment_id=? AND student_id=? AND type='student'
    ''', (assignment_id, sid)).fetchone()['c'] > 0

    return jsonify({
        'status': sub['status'],
        'has_insight': has_insight,
        'submitted_at': sub['submitted_at']
    })


@app.route('/api/insights/<assignment_id>', methods=['GET'])
@login_required
def get_insights(assignment_id):
    db = get_db()
    insights = db.execute('''
        SELECT i.*, u.name as student_name 
        FROM insights i
        LEFT JOIN users u ON i.student_id = u.id
        WHERE i.assignment_id=? 
        ORDER BY i.type, i.created_at DESC
    ''', (assignment_id,)).fetchall()
    result = []
    for ins in insights:
        d = dict(ins)
        data = json.loads(d['data'])
        # Inject the student name if missing
        if d['type'] == 'student' and not data.get('student_name'):
            data['student_name'] = d['student_name']
        d['data'] = data
        result.append(d)
    return jsonify(result)


@app.route('/api/submissions/bulk', methods=['POST'])
@teacher_required
def bulk_upload_submissions():
    """Accept CSV or JSON file with multiple student submissions."""
    assignment_id = request.form.get('assignment_id')
    if not assignment_id:
        return jsonify({'error': 'assignment_id required'}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400

    filename = secure_filename(f.filename)
    ext = os.path.splitext(filename)[1].lower()
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    f.save(file_path)

    if ext == '.csv':
        parsed = parse_csv_submissions(file_path)
    elif ext == '.json':
        parsed = parse_json_submissions(file_path)
    else:
        return jsonify({'error': 'Only CSV and JSON files are supported'}), 400

    if not parsed:
        return jsonify({'error': 'No valid submissions found in file'}), 400

    db = get_db()
    created = 0
    for sub in parsed:
        # Try to find the student by email, or create a placeholder
        student = None
        if sub.get('student_email'):
            student = db.execute(
                'SELECT id FROM users WHERE email=?', (sub['student_email'],)
            ).fetchone()

        student_id = student['id'] if student else session['user_id']

        sub_id = str(uuid.uuid4())
        db.execute(
            'INSERT INTO submissions (id, assignment_id, student_id, content, file_path) VALUES (?,?,?,?,?)',
            (sub_id, assignment_id, student_id, sub['content'], None)
        )
        created += 1

    db.commit()
    return jsonify({'created': created, 'message': f'{created} submissions uploaded'}), 201


# ══════════════════════════════════════════════════════════════════════════════
#  FEEDBACK API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/feedback', methods=['GET'])
@login_required
def get_feedback():
    db = get_db()
    assignment_id = request.args.get('assignment_id')

    if session['role'] == 'teacher' and assignment_id:
        feedbacks = db.execute('''
            SELECT f.*, s.student_id, u.name as student_name, s.content as submission_content
            FROM feedback f
            JOIN submissions s ON f.submission_id = s.id
            JOIN users u ON s.student_id = u.id
            WHERE s.assignment_id=?
            ORDER BY f.created_at DESC
        ''', (assignment_id,)).fetchall()
    elif session['role'] == 'student':
        # Only show APPROVED feedback — teacher controls what students see
        feedbacks = db.execute('''
            SELECT f.*, a.title as assignment_title, c.title as course_title
            FROM feedback f
            JOIN submissions s ON f.submission_id = s.id
            JOIN assignments a ON s.assignment_id = a.id
            JOIN courses c ON a.course_id = c.id
            WHERE s.student_id=? AND f.status='approved'
            ORDER BY f.created_at DESC
        ''', (session['user_id'],)).fetchall()
    else:
        feedbacks = db.execute('''
            SELECT f.*, a.title as assignment_title, c.title as course_title
            FROM feedback f
            JOIN submissions s ON f.submission_id = s.id
            JOIN assignments a ON s.assignment_id = a.id
            JOIN courses c ON a.course_id = c.id
            WHERE s.student_id=? AND f.status='approved'
            ORDER BY f.created_at DESC
        ''', (session['user_id'],)).fetchall()

    return jsonify([dict(f) for f in feedbacks])


@app.route('/api/feedback/<feedback_id>', methods=['PUT'])
@teacher_required
def update_feedback(feedback_id):
    data = request.get_json()
    db = get_db()
    db.execute('''
        UPDATE feedback SET
            teacher_edited=?, score=?, status=?
        WHERE id=?
    ''', (
        data.get('teacher_edited'), data.get('score'),
        data.get('status', 'approved'), feedback_id
    ))
    db.commit()
    return jsonify({'status': 'updated'})


@app.route('/api/feedback/<feedback_id>/export', methods=['GET'])
@login_required
def export_feedback(feedback_id):
    """Export a single feedback as plain text download."""
    db = get_db()
    fb = db.execute('''
        SELECT f.*, u.name as student_name, a.title as assignment_title
        FROM feedback f
        JOIN submissions s ON f.submission_id = s.id
        JOIN users u ON s.student_id = u.id
        JOIN assignments a ON s.assignment_id = a.id
        WHERE f.id=?
    ''', (feedback_id,)).fetchone()

    if not fb:
        return jsonify({'error': 'Feedback not found'}), 404

    text = fb['teacher_edited'] or fb['ai_draft'] or ''
    export = f"""═══════════════════════════════════════
LearnXLive — AI Feedback Report
═══════════════════════════════════════

Assignment: {fb['assignment_title']}
Student: {fb['student_name']}
Score: {fb['score']}%
Status: {fb['status'].upper()}
Generated: {fb['created_at']}

───────────────────────────────────────
FEEDBACK
───────────────────────────────────────

{text}

═══════════════════════════════════════
Powered by LearnXLive AI Engine
═══════════════════════════════════════
"""

    from flask import Response
    return Response(
        export,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename=feedback_{fb["student_name"].replace(" ", "_")}.txt'}
    )


# ══════════════════════════════════════════════════════════════════════════════
#  REPORTS API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/reports/<assignment_id>', methods=['GET'])
@teacher_required
def get_report(assignment_id):
    db = get_db()
    assignment = db.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone()
    if not assignment:
        return jsonify({'error': 'Not found'}), 404

    insights = db.execute(
        'SELECT * FROM insights WHERE assignment_id=?', (assignment_id,)
    ).fetchall()

    feedbacks = db.execute('''
        SELECT f.*, u.name as student_name, s.student_id
        FROM feedback f
        JOIN submissions s ON f.submission_id = s.id
        JOIN users u ON s.student_id = u.id
        WHERE s.assignment_id=?
    ''', (assignment_id,)).fetchall()

    # Dynamically compute class stats based on actual feedback data to prevent stale cache issues
    unique_subs = len(set(f['student_id'] for f in feedbacks))
    valid_scores = [f['score'] for f in feedbacks if f['score'] is not None]
    dyn_stats = {
        'average_score': round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else 0,
        'highest_score': max(valid_scores) if valid_scores else 0,
        'lowest_score': min(valid_scores) if valid_scores else 0,
        'total_submissions': unique_subs,
        'above_70': sum(1 for s in valid_scores if s >= 70),
        'below_50': sum(1 for s in valid_scores if s < 50),
    }

    insights_list = []
    for i in insights:
        d = dict(i)
        d['data'] = json.loads(d['data'])
        if d['type'] == 'class':
            d['data']['stats'] = dyn_stats
        insights_list.append(d)

    report = {
        'assignment': dict(assignment),
        'generated_at': datetime.now().isoformat(),
        'insights': insights_list,
        'feedback': [dict(f) for f in feedbacks],
    }
    return jsonify(report)


# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD DATA API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/teacher/students', methods=['GET'])
@teacher_required
def get_teacher_students():
    db = get_db()
    
    # Optional filtering by user's courses vs all registered students. 
    # Usually, a teacher wants to see all students or students enrolled in their courses.
    # The prompt explicitly said: "fetch studentss details from databse and show in student sec properly and setudeent details clearly"
    
    students = db.execute('''
        SELECT u.id, u.name, u.email, u.phone, u.bio, u.qualification, u.institution, u.created_at,
               (SELECT COUNT(*) FROM submissions s WHERE s.student_id = u.id) as total_submissions,
               (SELECT COUNT(*) FROM course_enrollments ce WHERE ce.student_id = u.id) as enrolled_courses
        FROM users u
        WHERE u.role = 'student'
        ORDER BY u.name
    ''').fetchall()
    
    return jsonify([dict(s) for s in students])


@app.route('/api/dashboard/teacher', methods=['GET'])
@teacher_required
def teacher_dashboard_data():
    db = get_db()
    tid = session['user_id']

    courses = db.execute('SELECT COUNT(*) as c FROM courses WHERE teacher_id=?', (tid,)).fetchone()
    students = db.execute('''
        SELECT COUNT(DISTINCT ce.student_id) as c
        FROM course_enrollments ce
        JOIN courses co ON ce.course_id = co.id
        WHERE co.teacher_id=?
    ''', (tid,)).fetchone()
    pending = db.execute('''
        SELECT COUNT(*) as c FROM submissions s
        JOIN assignments a ON s.assignment_id = a.id
        JOIN courses c ON a.course_id = c.id
        WHERE c.teacher_id=? AND s.status='submitted'
    ''', (tid,)).fetchone()
    reviewed = db.execute('''
        SELECT COUNT(*) as c FROM submissions s
        JOIN assignments a ON s.assignment_id = a.id
        JOIN courses c ON a.course_id = c.id
        WHERE c.teacher_id=? AND s.status='reviewed'
    ''', (tid,)).fetchone()

    return jsonify({
        'total_courses': courses['c'],
        'total_students': students['c'],
        'pending_reviews': pending['c'],
        'reviewed': reviewed['c']
    })


@app.route('/api/dashboard/student', methods=['GET'])
@login_required
def student_dashboard_data():
    db = get_db()
    sid = session['user_id']

    enrolled = db.execute(
        'SELECT COUNT(*) as c FROM course_enrollments WHERE student_id=?', (sid,)
    ).fetchone()
    submitted = db.execute(
        'SELECT COUNT(*) as c FROM submissions WHERE student_id=?', (sid,)
    ).fetchone()
    graded = db.execute('''
        SELECT COUNT(*) as c FROM feedback f
        JOIN submissions s ON f.submission_id = s.id
        WHERE s.student_id=? AND f.status='approved'
    ''', (sid,)).fetchone()
    avg_score = db.execute('''
        SELECT AVG(f.score) as avg FROM feedback f
        JOIN submissions s ON f.submission_id = s.id
        WHERE s.student_id=? AND f.score IS NOT NULL
    ''', (sid,)).fetchone()

    return jsonify({
        'enrolled_courses': enrolled['c'],
        'total_submissions': submitted['c'],
        'graded': graded['c'],
        'average_score': round(avg_score['avg'], 1) if avg_score['avg'] else 0
    })


# ══════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════════════

with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True,host='0.0.0.0', port=5000)
