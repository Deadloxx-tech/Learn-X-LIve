"""
ollama_engine.py — Ollama LLM integration for LearnXLive
Provides AI-powered analysis, scoring, and feedback generation
via the local Ollama REST API.
"""

import json
import csv
import io
import re
import requests
from typing import Optional

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2"
TIMEOUT = 60  # seconds per LLM call


# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════

def check_ollama_health() -> dict:
    """Check if Ollama is running and which models are available."""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        if r.ok:
            models = [m['name'] for m in r.json().get('models', [])]
            has_model = any(OLLAMA_MODEL in m for m in models)
            return {
                'status': 'online',
                'models': models,
                'has_required_model': has_model,
                'model': OLLAMA_MODEL
            }
        return {'status': 'error', 'message': 'Ollama responded with error'}
    except requests.ConnectionError:
        return {'status': 'offline', 'message': 'Ollama is not running'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


# ══════════════════════════════════════════════════════════════════════════════
#  CORE LLM CALL
# ══════════════════════════════════════════════════════════════════════════════

def _call_ollama(prompt: str, temperature: float = 0.3) -> Optional[str]:
    """Send a prompt to Ollama and return the response text."""
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": 2048,
                }
            },
            timeout=TIMEOUT
        )
        if r.ok:
            return r.json().get('response', '')
        return None
    except Exception:
        return None


def _parse_json_response(text: str) -> Optional[dict]:
    """Extract and parse JSON from LLM response text."""
    if not text:
        return None
    # Try to find JSON block in response
    # First try: look for ```json ... ``` blocks
    json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    # Second try: look for { ... } blocks
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  STUDENT SUBMISSION ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_student_submission(
    master_answer: str,
    student_answer: str,
    assignment_title: str,
    student_name: str = "Student"
) -> Optional[dict]:
    """
    Use Ollama to analyze a student's submission against the master answer.
    Returns structured analysis with score, matched/missing concepts, and explanation.
    """
    prompt = f"""You are an expert educational AI assistant analyzing a student's assignment submission.

ASSIGNMENT: "{assignment_title}"

MASTER ANSWER (ideal answer):
{master_answer}

STUDENT'S ANSWER:
{student_answer}

Analyze the student's answer compared to the master answer. Respond ONLY with a valid JSON object (no other text):

{{
    "score": <number 0-100 based on concept coverage, accuracy, and depth>,
    "matched_concepts": [<list of key concepts the student correctly covered>],
    "missing_concepts": [<list of important concepts the student missed>],
    "extra_concepts": [<any relevant concepts the student added beyond the master answer>],
    "accuracy_issues": [<any factual errors or misconceptions in the student's answer>],
    "depth_rating": "<shallow|moderate|thorough>",
    "confidence": <number 60-95 indicating your confidence in this assessment>,
    "explanation": "<2-3 sentence explanation of why this score was assigned, referencing specific concepts>"
}}

Be fair and objective. Score based on:
- Concept coverage (40%): How many key concepts from master answer are present
- Accuracy (30%): Are the covered concepts explained correctly  
- Depth (20%): Level of detail and understanding shown
- Clarity (10%): How well organized and clear the answer is"""

    response = _call_ollama(prompt)
    result = _parse_json_response(response)

    if result and 'score' in result:
        # Normalize fields
        result['score'] = max(0, min(100, float(result.get('score', 0))))
        result['confidence'] = max(60, min(95, float(result.get('confidence', 75))))
        result['matched_concepts'] = result.get('matched_concepts', [])
        result['missing_concepts'] = result.get('missing_concepts', [])
        result['extra_concepts'] = result.get('extra_concepts', [])
        result['accuracy_issues'] = result.get('accuracy_issues', [])
        result['depth_rating'] = result.get('depth_rating', 'moderate')
        result['explanation'] = result.get('explanation', 'Analysis completed.')
        result['student_name'] = student_name
        return result

    return None


def analyze_all_questions(
    questions: list,
    student_answers: dict,
    assignment_title: str,
    student_name: str = "Student"
) -> list:
    """
    Batch-analyze ALL questions in a SINGLE Ollama call.
    Returns a list of result dicts, one per question.
    """
    qa_block = ""
    for q in questions:
        qnum = str(q['question_number'])
        qa_block += f"""\n--- Question {qnum} (Marks: {q['marks']}) ---
Question: {q['question_text']}
Master Answer: {q['answer_key']}
Student Answer: {student_answers.get(qnum, '(no answer)')}
"""

    prompt = f"""You are an expert educational AI assistant grading a student's assignment.

ASSIGNMENT: "{assignment_title}"
STUDENT: {student_name}

Below are ALL the questions, master answers, and student answers.
{qa_block}

Analyze EACH question. Respond ONLY with a valid JSON array (no other text), one object per question:

[
  {{
    "question_number": <number>,
    "score": <0-100 percentage>,
    "matched_concepts": [<concepts student covered>],
    "missing_concepts": [<concepts student missed>],
    "depth_rating": "<shallow|moderate|thorough>",
    "confidence": <60-95>,
    "explanation": "<1-2 sentence explanation>"
  }}
]

Score based on: concept coverage (40%), accuracy (30%), depth (20%), clarity (10%).
Be fair and objective."""

    response = _call_ollama(prompt)
    if not response:
        return None

    # Parse the JSON array from response
    try:
        arr_match = re.search(r'\[.*\]', response, re.DOTALL)
        if arr_match:
            results = json.loads(arr_match.group(0))
            if isinstance(results, list) and len(results) > 0:
                for r in results:
                    r['score'] = max(0, min(100, float(r.get('score', 0))))
                    r['confidence'] = max(60, min(95, float(r.get('confidence', 75))))
                    r['matched_concepts'] = r.get('matched_concepts', [])
                    r['missing_concepts'] = r.get('missing_concepts', [])
                    r['depth_rating'] = r.get('depth_rating', 'moderate')
                    r['explanation'] = r.get('explanation', '')
                    r['student_name'] = student_name
                return results
    except (json.JSONDecodeError, TypeError):
        pass

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  CLASS-LEVEL SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def generate_class_summary(
    student_results: list,
    assignment_title: str,
    master_answer: str
) -> Optional[dict]:
    """
    Generate class-level insights from all student analyses.
    Identifies common mistakes, conceptual gaps, and performance patterns.
    """
    # Build a compact summary of all results for the prompt
    summary_lines = []
    all_missing = []
    all_accuracy_issues = []
    scores = []

    for r in student_results:
        name = r.get('student_name', 'Student')
        score = r.get('score', 0)
        scores.append(score)
        missing = r.get('missing_concepts', [])
        issues = r.get('accuracy_issues', [])
        all_missing.extend(missing)
        all_accuracy_issues.extend(issues)
        summary_lines.append(
            f"- {name}: score={score}, missing={missing}, issues={issues}"
        )

    students_summary = "\n".join(summary_lines)

    prompt = f"""You are an expert educational AI assistant analyzing class-level performance.

ASSIGNMENT: "{assignment_title}"
TOTAL STUDENTS: {len(student_results)}

STUDENT RESULTS:
{students_summary}

Based on these results, generate a comprehensive class analysis. Respond ONLY with a valid JSON object:

{{
    "class_summary": "<3-4 sentence narrative summary of overall class performance>",
    "common_mistakes": [
        {{
            "concept": "<the concept or topic>",
            "frequency": "<how many students missed this>",
            "why_flagged": "<explain why this is a common mistake>",
            "suggestion": "<teaching suggestion to address this gap>"
        }}
    ],
    "concept_clusters": [
        {{
            "cluster_name": "<name for this group of related concepts>",
            "concepts": [<list of related missed concepts>],
            "students_affected_pct": <percentage of students affected>,
            "severity": "<low|medium|high>"
        }}
    ],
    "performance_patterns": {{
        "high_performers": "<description of what top students did well>",
        "struggling_students": "<description of common issues among low scorers>",
        "improvement_areas": [<top 3 topics the class should review>]
    }},
    "teaching_recommendations": [<3-4 specific actionable suggestions for the teacher>]
}}"""

    response = _call_ollama(prompt, temperature=0.4)
    result = _parse_json_response(response)

    if result:
        # Add computed stats
        result['stats'] = {
            'average_score': round(sum(scores) / len(scores), 1) if scores else 0,
            'highest_score': max(scores) if scores else 0,
            'lowest_score': min(scores) if scores else 0,
            'total_submissions': len(student_results),
            'above_70': sum(1 for s in scores if s >= 70),
            'below_50': sum(1 for s in scores if s < 50),
        }
        return result

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  STUDENT FEEDBACK GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def generate_student_feedback(
    score: float,
    matched_concepts: list,
    missing_concepts: list,
    accuracy_issues: list,
    student_name: str,
    student_answer: str,
    assignment_title: str
) -> Optional[dict]:
    """
    Generate detailed, personalized feedback for a student using Ollama.
    """
    prompt = f"""You are a supportive and constructive teacher writing feedback for a student.

ASSIGNMENT: "{assignment_title}"
STUDENT: {student_name}
SCORE: {score}/100

CONCEPTS COVERED CORRECTLY: {', '.join(matched_concepts) if matched_concepts else 'None identified'}
CONCEPTS MISSED: {', '.join(missing_concepts) if missing_concepts else 'None — all covered!'}
ACCURACY ISSUES: {', '.join(accuracy_issues) if accuracy_issues else 'None found'}

STUDENT'S ANSWER:
{student_answer[:1500]}

Write personalized feedback. Respond ONLY with a valid JSON object:

{{
    "grade": "<Excellent|Good|Needs Improvement|Unsatisfactory>",
    "summary": "<2-3 sentence overall assessment, addressing the student by name>",
    "strengths": [<2-3 specific things the student did well>],
    "improvements": [<2-4 specific areas to improve with actionable guidance>],
    "resources": [<1-2 suggested topics or areas to study further>],
    "encouragement": "<a brief encouraging closing remark>",
    "draft": "<Complete feedback paragraph combining all the above into a natural, teacher-like message>"
}}

Be constructive, specific, and encouraging. Never be harsh."""

    response = _call_ollama(prompt, temperature=0.5)
    result = _parse_json_response(response)

    if result and 'draft' in result:
        return result

    # Fallback: generate basic feedback without LLM
    return _fallback_feedback(score, matched_concepts, missing_concepts, student_name)


def _fallback_feedback(score, matched, missing, student_name):
    """Template-based fallback when Ollama is unavailable."""
    if score >= 85:
        grade = 'Excellent'
        summary = f'{student_name} demonstrated a strong understanding of the key concepts.'
        suggestion = 'Keep up the excellent work. Consider exploring advanced topics.'
    elif score >= 70:
        grade = 'Good'
        summary = f'{student_name} covered most of the important points.'
        suggestion = f'Review these areas: {", ".join(missing[:5])}.'
    elif score >= 50:
        grade = 'Needs Improvement'
        summary = f'{student_name} shows partial understanding but missed several key concepts.'
        suggestion = f'Focus on: {", ".join(missing[:7])}.'
    else:
        grade = 'Unsatisfactory'
        summary = f'{student_name} needs significant revision.'
        suggestion = f'Major concepts missing: {", ".join(missing[:10])}.'

    draft = f"Grade: {grade}\n\n{summary}\n\nKey strengths: Covered {len(matched)} key concepts.\n\nAreas for improvement: {suggestion}"

    return {
        'grade': grade,
        'summary': summary,
        'strengths': [f'Covered {len(matched)} concepts correctly'],
        'improvements': [suggestion],
        'resources': [],
        'encouragement': 'Keep studying and you will improve!',
        'draft': draft
    }


# ══════════════════════════════════════════════════════════════════════════════
#  ASSIGNMENT GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def generate_assignment(title: str, difficulty: str, num_questions: int) -> dict:
    """
    Use Ollama to generate an assignment description, questions, and master answers based on a title.
    """
    prompt = f"""You are an expert educator creating an assignment for students.

ASSIGNMENT TITLE: "{title}"
DIFFICULTY: {difficulty}
NUMBER OF QUESTIONS: {num_questions}

Generate a brief description for this assignment and {num_questions} meaningful questions at the specified difficulty level. For each question, provide a detailed ideal answer key.

Respond ONLY with a valid JSON object (no comments, no markdown outside the JSON block), exactly matching this structure:
{{
    "description": "<A 1-2 sentence description explaining what concepts this assignment covers>",
    "questions": [
        {{
            "question_text": "<The question text>",
            "answer_key": "<The detailed ideal answer for this question>",
            "marks": <Suggested marks for this question as a number, e.g., 5 or 10>
        }}
    ]
}}"""

    response = _call_ollama(prompt, temperature=0.7)
    result = _parse_json_response(response)

    if isinstance(result, dict) and 'questions' in result and isinstance(result['questions'], list):
        valid_q = []
        for q in result['questions']:
            if isinstance(q, dict) and 'question_text' in q and 'answer_key' in q:
                valid_q.append({
                    'question_text': q['question_text'],
                    'answer_key': q['answer_key'],
                    'marks': float(q.get('marks', 5))
                })
        if valid_q:
            return {
                'description': result.get('description', f'An assignment covering {title}.'),
                'questions': valid_q
            }

    # Fallback if Ollama fails or is offline
    fallback_qs = []
    for i in range(num_questions):
        fallback_qs.append({
            "question_text": f"Explain the core concepts of {title} (Question {i+1}).",
            "answer_key": f"The student should provide a comprehensive explanation of {title}.",
            "marks": 5.0
        })
    return {
        'description': f"A set of questions testing knowledge of {title}.",
        'questions': fallback_qs
    }


# ══════════════════════════════════════════════════════════════════════════════
#  BULK UPLOAD PARSERS
# ══════════════════════════════════════════════════════════════════════════════

def parse_csv_submissions(file_path: str) -> list:
    """
    Parse a CSV file of student submissions.
    Expected columns: student_name, student_email, answer
    """
    submissions = []
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get('student_name') or row.get('name') or row.get('Name', 'Unknown')
                email = row.get('student_email') or row.get('email') or row.get('Email', '')
                answer = row.get('answer') or row.get('Answer') or row.get('submission') or row.get('content', '')
                if answer.strip():
                    submissions.append({
                        'student_name': name.strip(),
                        'student_email': email.strip(),
                        'content': answer.strip()
                    })
    except Exception:
        pass
    return submissions


def parse_json_submissions(file_path: str) -> list:
    """
    Parse a JSON file of student submissions.
    Expected format: [{"student_name": "...", "student_email": "...", "answer": "..."}]
    """
    submissions = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    name = item.get('student_name') or item.get('name', 'Unknown')
                    email = item.get('student_email') or item.get('email', '')
                    answer = item.get('answer') or item.get('submission') or item.get('content', '')
                    if answer.strip():
                        submissions.append({
                            'student_name': name.strip(),
                            'student_email': email.strip(),
                            'content': answer.strip()
                        })
    except Exception:
        pass
    return submissions


# ══════════════════════════════════════════════════════════════════════════════
#  FALLBACK ANALYSIS (Enhanced NLP — no external deps)
# ══════════════════════════════════════════════════════════════════════════════

import math
from collections import Counter
from difflib import SequenceMatcher

_STOP_WORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'shall',
    'should', 'may', 'might', 'can', 'could', 'and', 'but', 'or', 'nor',
    'not', 'so', 'yet', 'both', 'either', 'neither', 'each', 'every',
    'all', 'any', 'few', 'more', 'most', 'other', 'some', 'such', 'no',
    'only', 'own', 'same', 'than', 'too', 'very', 'just', 'because',
    'as', 'until', 'while', 'of', 'at', 'by', 'for', 'with', 'about',
    'against', 'between', 'through', 'during', 'before', 'after', 'above',
    'below', 'to', 'from', 'up', 'down', 'in', 'out', 'on', 'off', 'over',
    'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when',
    'where', 'why', 'how', 'what', 'which', 'who', 'whom', 'this', 'that',
    'these', 'those', 'it', 'its', 'i', 'me', 'my', 'we', 'our', 'you',
    'your', 'he', 'him', 'his', 'she', 'her', 'they', 'them', 'their',
    'also', 'like', 'even', 'well', 'back', 'much', 'way', 'get', 'got',
    'use', 'used', 'using', 'make', 'made', 'say', 'said', 'know', 'take',
    'come', 'want', 'give', 'tell', 'work', 'call', 'try', 'ask', 'need',
    'feel', 'let', 'keep', 'set', 'put', 'seem', 'help', 'show', 'turn',
}


def _tokenize(text: str) -> list:
    """Tokenize text — accepts any word with 2+ alphanumeric chars."""
    words = re.findall(r'\b[a-zA-Z0-9]{2,}\b', text.lower())
    return [w for w in words if w not in _STOP_WORDS]


def _stem(word: str) -> str:
    """Simple suffix-stripping stemmer for English."""
    if len(word) <= 4:
        return word
    for suffix in ('ation', 'ment', 'ness', 'ting', 'sion', 'ious',
                    'able', 'ible', 'ally', 'ful', 'ing', 'ity',
                    'ive', 'ous', 'ure', 'ise', 'ize', 'ely',
                    'ly', 'ed', 'er', 'es', 'en', 'al', 'ic'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    if word.endswith('s') and len(word) > 4 and not word.endswith('ss'):
        return word[:-1]
    return word


def _get_ngrams(tokens: list, n: int) -> set:
    """Generate n-grams from a token list."""
    return set(tuple(tokens[i:i+n]) for i in range(len(tokens) - n + 1))


def _fuzzy_word_match(word_a: str, word_b: str) -> float:
    """Return similarity ratio (0-1) between two words using SequenceMatcher."""
    if word_a == word_b:
        return 1.0
    if _stem(word_a) == _stem(word_b):
        return 0.9
    ratio = SequenceMatcher(None, word_a, word_b).ratio()
    return ratio if ratio >= 0.75 else 0.0


def _compute_tf(tokens: list) -> dict:
    """Compute term-frequency for a list of tokens."""
    counts = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {t: c / total for t, c in counts.items()}


def _compute_idf(doc_list: list) -> dict:
    """Compute inverse document frequency across documents (each is a token list)."""
    n = len(doc_list)
    df = Counter()
    for doc in doc_list:
        unique = set(doc)
        for t in unique:
            df[t] += 1
    return {t: math.log((n + 1) / (d + 1)) + 1 for t, d in df.items()}


def _cosine_similarity(vec_a: dict, vec_b: dict) -> float:
    """Cosine similarity between two sparse vectors (dicts)."""
    common = set(vec_a.keys()) & set(vec_b.keys())
    dot = sum(vec_a[k] * vec_b[k] for k in common)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values())) if vec_a else 0
    mag_b = math.sqrt(sum(v * v for v in vec_b.values())) if vec_b else 0
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _sentence_overlap(master_text: str, student_text: str) -> float:
    """Compute sentence-level overlap between master and student answers."""
    master_sents = [s.strip().lower() for s in re.split(r'[.!?\n]+', master_text) if s.strip()]
    student_sents = [s.strip().lower() for s in re.split(r'[.!?\n]+', student_text) if s.strip()]
    if not master_sents or not student_sents:
        return 0.0

    total_match = 0.0
    for ms in master_sents:
        ms_words = set(ms.split())
        if len(ms_words) < 2:
            continue
        best = 0.0
        for ss in student_sents:
            ss_words = set(ss.split())
            if not ss_words:
                continue
            overlap = len(ms_words & ss_words) / max(len(ms_words), 1)
            best = max(best, overlap)
        total_match += best

    return (total_match / len(master_sents)) * 100 if master_sents else 0.0


def fallback_analyze_submission(master_answer: str, student_answer: str, student_name: str = "Student") -> dict:
    """Enhanced NLP fallback analysis — accurate scoring without requiring Ollama."""
    master_tokens_list = _tokenize(master_answer)
    student_tokens_list = _tokenize(student_answer)
    master_tokens = set(master_tokens_list)
    student_tokens = set(student_tokens_list)

    if not master_tokens:
        return {'score': 0, 'matched_concepts': [], 'missing_concepts': [],
                'extra_concepts': [], 'accuracy_issues': [], 'depth_rating': 'shallow',
                'confidence': 75, 'explanation': 'No master answer content to compare.',
                'student_name': student_name}

    # ── 1. TF-IDF Cosine Similarity (semantic direction) ──
    idf = _compute_idf([master_tokens_list, student_tokens_list])
    tf_master = _compute_tf(master_tokens_list)
    tf_student = _compute_tf(student_tokens_list)

    tfidf_master = {t: tf_master[t] * idf.get(t, 1) for t in tf_master}
    tfidf_student = {t: tf_student[t] * idf.get(t, 1) for t in tf_student}

    cosine_sim = _cosine_similarity(tfidf_master, tfidf_student) * 100

    # ── 2. Exact + Fuzzy Keyword Coverage ──
    exact_matched = master_tokens & student_tokens
    fuzzy_matched = set()
    for mw in (master_tokens - exact_matched):
        for sw in (student_tokens - exact_matched):
            if _fuzzy_word_match(mw, sw) >= 0.75:
                fuzzy_matched.add(mw)
                break

    all_matched = sorted(exact_matched | fuzzy_matched)
    missing = sorted(master_tokens - exact_matched - fuzzy_matched)
    extra = sorted(student_tokens - master_tokens)

    exact_coverage = (len(exact_matched) / len(master_tokens)) * 100
    fuzzy_coverage = (len(all_matched) / len(master_tokens)) * 100

    # ── 3. N-gram Matching (phrase-level accuracy) ──
    master_bigrams = _get_ngrams(master_tokens_list, 2)
    student_bigrams = _get_ngrams(student_tokens_list, 2)
    master_trigrams = _get_ngrams(master_tokens_list, 3)
    student_trigrams = _get_ngrams(student_tokens_list, 3)

    bigram_overlap = len(master_bigrams & student_bigrams) / max(len(master_bigrams), 1) * 100 if master_bigrams else 0
    trigram_overlap = len(master_trigrams & student_trigrams) / max(len(master_trigrams), 1) * 100 if master_trigrams else 0
    ngram_score = bigram_overlap * 0.6 + trigram_overlap * 0.4

    # ── 4. Sentence-level Overlap ──
    sent_overlap = _sentence_overlap(master_answer, student_answer)

    # ── 5. Length/Effort Ratio ──
    master_len = max(len(master_tokens_list), 1)
    student_len = len(student_tokens_list)
    length_ratio = min(student_len / master_len, 1.5)
    length_score = min(100, length_ratio * 70)  # up to 100, capped

    # ── Final Blended Score ──
    # 25% cosine + 25% fuzzy coverage + 20% ngram + 20% sentence overlap + 10% length
    score = round(
        cosine_sim * 0.25 +
        fuzzy_coverage * 0.25 +
        ngram_score * 0.20 +
        sent_overlap * 0.20 +
        length_score * 0.10,
        1
    )
    score = max(0, min(100, score))

    depth = 'thorough' if score >= 70 else 'moderate' if score >= 40 else 'shallow'

    # ── Confidence: higher when multiple signals agree ──
    signals = [cosine_sim, fuzzy_coverage, ngram_score, sent_overlap]
    avg_signal = sum(signals) / len(signals)
    signal_stddev = (sum((s - avg_signal) ** 2 for s in signals) / len(signals)) ** 0.5
    # Low stddev = signals agree = higher confidence
    confidence = round(max(60, min(90, 85 - signal_stddev * 0.5)), 1)

    # ── Rank missing by importance ──
    missing_ranked = sorted(
        missing,
        key=lambda t: idf.get(t, 0) * tf_master.get(t, 0),
        reverse=True
    )

    explanation = (
        f'Enhanced NLP Analysis: '
        f'Cosine={cosine_sim:.0f}%, Coverage={fuzzy_coverage:.0f}% '
        f'({len(exact_matched)} exact + {len(fuzzy_matched)} fuzzy of {len(master_tokens)} concepts), '
        f'N-gram={ngram_score:.0f}%, Sentence overlap={sent_overlap:.0f}%, '
        f'Length ratio={length_ratio:.1f}x. '
        f'Blended score: 25% cosine + 25% coverage + 20% ngram + 20% sentence + 10% length.'
    )

    return {
        'score': score,
        'matched_concepts': all_matched[:20],
        'missing_concepts': missing_ranked[:20],
        'extra_concepts': extra[:10],
        'accuracy_issues': [],
        'depth_rating': depth,
        'confidence': confidence,
        'explanation': explanation,
        'student_name': student_name,
        'is_fallback': True,
        'analysis_method': 'enhanced_nlp',
        'cosine_similarity': round(cosine_sim, 1),
        'keyword_coverage': round(fuzzy_coverage, 1),
        'ngram_score': round(ngram_score, 1),
        'sentence_overlap': round(sent_overlap, 1),
    }
