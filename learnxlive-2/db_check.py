import sqlite3, json, os

db = sqlite3.connect(os.path.join('data', 'learnxlive.db'))
db.row_factory = sqlite3.Row

# Check recent student insights
print("=== RECENT STUDENT INSIGHTS ===")
rows = db.execute("SELECT type, student_id, data, confidence FROM insights WHERE type='student' ORDER BY created_at DESC LIMIT 5").fetchall()
for r in rows:
    d = json.loads(r['data'])
    print(f"  Student: {d.get('student_name')}, Score: {d.get('final_score')}, "
          f"Marks: {d.get('total_marks_earned')}/{d.get('total_marks')}, "
          f"Confidence: {r['confidence']}, Fallback: {d.get('is_fallback')}")
    qr = d.get('question_results', [])
    for q in qr:
        print(f"    Q{q['question_number']}: {q['marks_earned']}/{q['max_marks']} ({q['percentage']}%)")

# Check submissions content
print("\n=== RECENT SUBMISSIONS ===")
subs = db.execute("SELECT s.id, s.content, s.student_id, u.name, s.file_path FROM submissions s JOIN users u ON s.student_id=u.id ORDER BY s.submitted_at DESC LIMIT 3").fetchall()
for s in subs:
    content_preview = s['content'][:200] if s['content'] else 'EMPTY'
    print(f"  Student: {s['name']}, Content: {content_preview}")
    print(f"  File path: {s['file_path']}")

# Check questions
print("\n=== ASSIGNMENT QUESTIONS ===")
assignments = db.execute("SELECT id, title, total_marks FROM assignments ORDER BY created_at DESC LIMIT 2").fetchall()
for a in assignments:
    print(f"  Assignment: {a['title']}, Total marks: {a['total_marks']}")
    qs = db.execute("SELECT * FROM questions WHERE assignment_id=? ORDER BY question_number", (a['id'],)).fetchall()
    for q in qs:
        print(f"    Q{q['question_number']}: marks={q['marks']}, text={q['question_text'][:80]}")
        print(f"      Answer key: {q['answer_key'][:100]}")

db.close()
