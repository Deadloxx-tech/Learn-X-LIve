"""
seed.py — Populate the database with demo data for testing.
Run: python seed.py
"""

import os
import sys
import sqlite3
import uuid
import json
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'learnxlive.db')


def seed():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA foreign_keys=ON')
    cur = conn.cursor()

    # ── Create tables (same schema as app.py) ─────────────────────────────
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            phone TEXT, password TEXT NOT NULL,
            role TEXT CHECK(role IN ('student','teacher')) NOT NULL,
            qualification TEXT, subject_expertise TEXT, experience_years INTEGER,
            teacher_id_code TEXT, created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS courses (
            id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL, title TEXT NOT NULL,
            category TEXT, fees REAL DEFAULT 0, duration_weeks INTEGER DEFAULT 1,
            syllabus TEXT, created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS course_enrollments (
            id TEXT PRIMARY KEY, course_id TEXT NOT NULL, student_id TEXT NOT NULL,
            enrolled_at TEXT DEFAULT (datetime('now')), progress INTEGER DEFAULT 0,
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (student_id) REFERENCES users(id), UNIQUE(course_id, student_id)
        );
        CREATE TABLE IF NOT EXISTS assignments (
            id TEXT PRIMARY KEY, course_id TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT, master_answer TEXT NOT NULL, due_date TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL, student_id TEXT NOT NULL,
            content TEXT NOT NULL, file_path TEXT,
            submitted_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'submitted' CHECK(status IN ('submitted','analyzing','reviewed')),
            FOREIGN KEY (assignment_id) REFERENCES assignments(id),
            FOREIGN KEY (student_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS insights (
            id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL,
            type TEXT CHECK(type IN ('class','student','question')) NOT NULL,
            student_id TEXT, data TEXT NOT NULL, confidence REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (assignment_id) REFERENCES assignments(id)
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY, submission_id TEXT NOT NULL,
            ai_draft TEXT NOT NULL, teacher_edited TEXT, score REAL,
            status TEXT DEFAULT 'draft' CHECK(status IN ('draft','approved','rejected')),
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        );
    ''')

    # ── Clear old data ────────────────────────────────────────────────────
    for table in ['feedback', 'insights', 'submissions', 'assignments',
                   'course_enrollments', 'courses', 'users']:
        cur.execute(f'DELETE FROM {table}')

    # ── Teachers ──────────────────────────────────────────────────────────
    t1 = str(uuid.uuid4())
    t2 = str(uuid.uuid4())
    pwd = generate_password_hash('teacher123')

    cur.execute(
        'INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,datetime("now"))',
        (t1, 'Dr. Priya Sharma', 'priya@edu.com', '+91-9876543210', pwd,
         'teacher', 'PhD Computer Science', 'Python, Data Structures', 12, 'TCH-A1B2C')
    )
    cur.execute(
        'INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,datetime("now"))',
        (t2, 'Prof. Arjun Mehta', 'arjun@edu.com', '+91-9988776655', pwd,
         'teacher', 'M.Tech AI/ML', 'Java, OOP', 8, 'TCH-D3E4F')
    )

    # ── Students ──────────────────────────────────────────────────────────
    students = []
    student_data = [
        ('Rahul Kumar', 'rahul@student.com'),
        ('Anjali Gupta', 'anjali@student.com'),
        ('Amit Singh', 'amit@student.com'),
        ('Sneha Patel', 'sneha@student.com'),
        ('Vikram Joshi', 'vikram@student.com'),
    ]
    spwd = generate_password_hash('student123')
    for name, email in student_data:
        sid = str(uuid.uuid4())
        students.append(sid)
        cur.execute(
            'INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,datetime("now"))',
            (sid, name, email, '+91-0000000000', spwd,
             'student', None, None, None, None)
        )

    # ── Courses ───────────────────────────────────────────────────────────
    c1 = str(uuid.uuid4())
    c2 = str(uuid.uuid4())
    cur.execute(
        'INSERT INTO courses VALUES (?,?,?,?,?,?,?,datetime("now"))',
        (c1, t1, 'Python for Beginners', 'coding', 499, 8,
         'Variables, Loops, Functions, OOP, File Handling, Error Handling, Data Structures')
    )
    cur.execute(
        'INSERT INTO courses VALUES (?,?,?,?,?,?,?,datetime("now"))',
        (c2, t2, 'Advanced Java & OOP', 'coding', 799, 12,
         'OOP Concepts, Inheritance, Polymorphism, Interfaces, Collections, Threads')
    )

    # ── Enrollments ───────────────────────────────────────────────────────
    for sid in students:
        cur.execute(
            'INSERT INTO course_enrollments VALUES (?,?,?,datetime("now"),?)',
            (str(uuid.uuid4()), c1, sid, 50)
        )
    for sid in students[:3]:
        cur.execute(
            'INSERT INTO course_enrollments VALUES (?,?,?,datetime("now"),?)',
            (str(uuid.uuid4()), c2, sid, 30)
        )

    # ── Assignments ───────────────────────────────────────────────────────
    a1 = str(uuid.uuid4())
    a2 = str(uuid.uuid4())

    # Assignment 1: Python Functions (3 questions, 25 total marks)
    a1_master = '''Q1: Python functions are reusable blocks of code defined using the def keyword. Functions can accept parameters including positional arguments, keyword arguments, default values, and variable-length arguments using *args and **kwargs.
Q2: Functions return values using the return statement. If no return is specified, the function returns None. Python supports variable scope through LEGB rule: Local, Enclosing, Global, and Built-in scopes.
Q3: Lambda functions are anonymous single-expression functions. Decorators are functions that modify the behavior of other functions using the @decorator syntax. Closures occur when nested functions reference variables from the enclosing scope.'''
    cur.execute(
        'INSERT INTO assignments VALUES (?,?,?,?,?,?,?,datetime("now"))',
        (a1, c1, 'Python Functions & Scope',
         'Explain Python functions, parameters, return values, scope, lambda, decorators.',
         a1_master, 25, '2026-03-01')
    )
    # Questions for a1
    a1_questions = [
        (str(uuid.uuid4()), a1, 1, 'Explain how functions are defined in Python and the types of arguments they accept.',
         'Python functions are reusable blocks of code defined using the def keyword. Functions can accept parameters including positional arguments, keyword arguments, default values, and variable-length arguments using *args and **kwargs.', 10),
        (str(uuid.uuid4()), a1, 2, 'What are return values and explain variable scope in Python?',
         'Functions return values using the return statement. If no return is specified, the function returns None. Python supports variable scope through LEGB rule: Local, Enclosing, Global, and Built-in scopes.', 10),
        (str(uuid.uuid4()), a1, 3, 'Explain lambda functions and decorators in Python.',
         'Lambda functions are anonymous single-expression functions. Decorators are functions that modify the behavior of other functions using the @decorator syntax. Closures occur when nested functions reference variables from the enclosing scope.', 5),
    ]
    for q in a1_questions:
        cur.execute('INSERT INTO questions VALUES (?,?,?,?,?,?)', q)

    # Assignment 2: OOP in Java (4 questions, 40 total marks)
    a2_master = '''Q1: Encapsulation bundles data and methods together in classes, restricting direct access through access modifiers like private, protected, and public.
Q2: Inheritance allows a subclass to inherit fields and methods from a superclass using the extends keyword, enabling code reuse. Java supports single inheritance for classes but multiple inheritance through interfaces.
Q3: Polymorphism enables objects to take multiple forms through method overloading (compile-time) and method overriding (runtime).
Q4: Abstraction hides implementation details and exposes only essential features through abstract classes and interfaces. The SOLID principles guide good OOP design.'''
    cur.execute(
        'INSERT INTO assignments VALUES (?,?,?,?,?,?,?,datetime("now"))',
        (a2, c2, 'OOP Concepts in Java',
         'Explain core Object-Oriented Programming concepts in Java.',
         a2_master, 40, '2026-03-15')
    )
    # Questions for a2
    a2_questions = [
        (str(uuid.uuid4()), a2, 1, 'What is Encapsulation in Java?',
         'Encapsulation bundles data and methods together in classes, restricting direct access through access modifiers like private, protected, and public.', 10),
        (str(uuid.uuid4()), a2, 2, 'Explain Inheritance in Java.',
         'Inheritance allows a subclass to inherit fields and methods from a superclass using the extends keyword, enabling code reuse. Java supports single inheritance for classes but multiple inheritance through interfaces.', 10),
        (str(uuid.uuid4()), a2, 3, 'What is Polymorphism?',
         'Polymorphism enables objects to take multiple forms through method overloading (compile-time) and method overriding (runtime).', 10),
        (str(uuid.uuid4()), a2, 4, 'Explain Abstraction and SOLID principles.',
         'Abstraction hides implementation details and exposes only essential features through abstract classes and interfaces. The SOLID principles guide good OOP design.', 10),
    ]
    for q in a2_questions:
        cur.execute('INSERT INTO questions VALUES (?,?,?,?,?,?)', q)

    # ── Submissions (varied quality) ──────────────────────────────────────
    submission_texts_a1 = [
        # Rahul — Excellent (covers most concepts)
        """Python functions are defined using def keyword and are reusable blocks of code. 
They accept parameters like positional arguments, keyword arguments, and default values. 
The *args and **kwargs syntax handles variable-length arguments. Functions return values 
using return statement, or None if not specified. Variable scope follows the LEGB rule 
covering Local, Enclosing, Global, and Built-in scopes. Lambda functions are anonymous 
single-expression functions defined inline. Decorators modify function behavior using 
the @decorator syntax. Closures reference variables from enclosing scope. Recursion 
allows functions to call themselves for solving problems.""",

        # Anjali — Good (misses some terms)
        """Functions in Python are created with def keyword. They take parameters and 
return values. You can pass positional and keyword arguments. Default values 
provide fallback options. The scope determines where variables are accessible, 
following the LEGB rule with Local, Global and Built-in levels. Lambda creates 
small anonymous functions. Decorators add functionality to existing functions.""",

        # Amit — Moderate (partial coverage)
        """Python functions are blocks of code that perform specific tasks. You define 
them with def and call them by name. Functions can receive parameters and return 
results. There are different types of arguments. Scope means where variables 
can be used in the program. Lambda is a short function.""",

        # Sneha — Good (different phrasing but covers concepts)
        """In Python, functions are fundamental building blocks defined with the def keyword. 
Parameters can be positional arguments, keyword arguments with default values, 
or variable-length using *args and **kwargs. Return statement sends back values 
from functions. Without return, Python returns None by default. 
The LEGB scope rule governs variable visibility: Local, Enclosing, Global, Built-in. 
Decorators are powerful patterns using @decorator syntax to wrap functions. 
Closures capture enclosing scope variables. Recursion breaks down problems into 
smaller subproblems by having functions call themselves.""",

        # Vikram — Poor (very little coverage)
        """Functions are used in Python to organize code. You can create a function 
and call it later. Functions can take inputs and give outputs. Python is a 
popular programming language used for many applications including web development 
and data science."""
    ]

    submission_texts_a2 = [
        # Rahul — Excellent
        """OOP in Java has four pillars. Encapsulation bundles data and methods in classes 
using access modifiers like private, protected, and public. Inheritance uses extends 
keyword for subclass to inherit from superclass enabling code reuse. Java supports 
single inheritance for classes and multiple inheritance through interfaces. 
Polymorphism has method overloading for compile-time and method overriding for runtime. 
Abstraction uses abstract classes and interfaces to hide implementation details. 
SOLID principles include Single Responsibility, Open-Closed, and Liskov Substitution.""",

        # Anjali — Moderate
        """Java OOP includes encapsulation, inheritance, polymorphism, and abstraction. 
Classes contain data and methods. Inheritance lets you reuse code with extends. 
Polymorphism means objects can behave differently. Interfaces define contracts. 
Access modifiers control visibility of class members.""",

        # Amit — Poor
        """Java is an object-oriented language. It has classes and objects. You can 
create classes with fields and methods. Java is used for Android apps and 
enterprise software. It runs on JVM."""
    ]

    for i, sid in enumerate(students):
        sub_id = str(uuid.uuid4())
        cur.execute(
            'INSERT INTO submissions VALUES (?,?,?,?,?,datetime("now"),?)',
            (sub_id, a1, sid, submission_texts_a1[i], None, 'submitted')
        )

    for i, sid in enumerate(students[:3]):
        sub_id = str(uuid.uuid4())
        cur.execute(
            'INSERT INTO submissions VALUES (?,?,?,?,?,datetime("now"),?)',
            (sub_id, a2, sid, submission_texts_a2[i], None, 'submitted')
        )

    conn.commit()
    conn.close()
    print('[OK] Database seeded successfully!')
    print(f'   DB location: {DB_PATH}')
    print(f'   Teachers: priya@edu.com / arjun@edu.com  (password: teacher123)')
    print(f'   Students: rahul@student.com / anjali@student.com / etc.  (password: student123)')


if __name__ == '__main__':
    seed()
