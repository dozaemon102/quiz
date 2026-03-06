import os
import csv
import sqlite3
import random
from functools import lru_cache
from datetime import datetime
from flask import Flask, render_template, request, redirect, session

app = Flask(__name__)
app.secret_key = "quizapp"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "quiz.db")
PROBLEM_DIR = os.path.join(BASE_DIR, "problems")


def init_db():

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS results(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        problem_id TEXT,
        file TEXT,
        field TEXT,
        problem_no INTEGER,
        selected TEXT,
        correct INTEGER,
        answered_at TEXT
    )
    """)

    # Lightweight migration for existing DBs
    c.execute("PRAGMA table_info(results)")
    cols = {row[1] for row in c.fetchall()}
    if "problem_id" not in cols:
        c.execute("ALTER TABLE results ADD COLUMN problem_id TEXT")
    if "file" not in cols:
        c.execute("ALTER TABLE results ADD COLUMN file TEXT")
    if "field" not in cols:
        c.execute("ALTER TABLE results ADD COLUMN field TEXT")

    conn.commit()
    conn.close()


def _normalize_row(row: dict) -> dict:
    # Normalize column names across CSV variants
    if "No" not in row and "No." in row:
        row["No"] = row.get("No.")
    if "問題文" not in row and "問題" in row:
        row["問題文"] = row.get("問題")
    if "解説" not in row:
        row["解説"] = ""

    # Strip values that are used for comparison/IDs
    if "正解" in row and row["正解"] is not None:
        row["正解"] = str(row["正解"]).strip()
    if "No" in row and row["No"] is not None:
        row["No"] = str(row["No"]).strip()
    if "分野" in row and row["分野"] is not None:
        row["分野"] = str(row["分野"]).strip()

    return row


def _safe_int(value, default=None):
    try:
        return int(str(value).strip())
    except Exception:
        return default


def load_problems(file: str):

    file = os.path.basename(file or "")
    path = os.path.join(PROBLEM_DIR, file)

    problems = []

    with open(path, encoding="utf-8-sig") as f:

        reader = csv.DictReader(f)

        for row in reader:
            row = _normalize_row(row)

            no = row.get("No")
            if not no:
                continue

            row["file"] = file  # filename only
            row["problem_id"] = f"{file}_{no}"

            problems.append(row)

    return problems


@lru_cache(maxsize=64)
def _problem_map_by_no(file: str):
    problems = load_problems(file)
    by_no = {}
    for p in problems:
        no_int = _safe_int(p.get("No"))
        if no_int is not None:
            by_no[no_int] = p
    return by_no


def _scan_problem_totals():
    totals_by_file = {}
    totals_by_field = {}
    field_by_problem_id = {}

    for filename in get_csv_files():
        ps = load_problems(filename)
        totals_by_file[filename] = len(ps)
        for p in ps:
            field = (p.get("分野") or "").strip()
            if field:
                totals_by_field[field] = totals_by_field.get(field, 0) + 1
            field_by_problem_id[p["problem_id"]] = field

    return totals_by_file, totals_by_field, field_by_problem_id


def _scan_problem_totals_for_file(file: str):
    file = os.path.basename(file or "")
    ps = load_problems(file)
    total = len(ps)
    totals_by_field = {}
    for p in ps:
        field = (p.get("分野") or "").strip()
        if field:
            totals_by_field[field] = totals_by_field.get(field, 0) + 1
    return total, totals_by_field


def _backfill_result_fields_if_needed():
    # Fill NULL/empty field values using current CSVs (best-effort)
    _, _, field_by_problem_id = _scan_problem_totals()

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("SELECT problem_id FROM results WHERE field IS NULL OR field = ''")
    ids = [r[0] for r in c.fetchall()]

    updates = []
    for pid in ids:
        field = field_by_problem_id.get(pid)
        if field:
            updates.append((field, pid))

    if updates:
        c.executemany("UPDATE results SET field=? WHERE problem_id=?", updates)

    conn.commit()
    conn.close()


@app.route("/")
def start():

    files = get_csv_files()

    return render_template(
        "start.html",
        files=files
    )


@app.route("/setup", methods=["POST"])
def setup():

    file = request.form.get("file")
    if not file:
        return redirect("/")

    session["file"] = file

    # Clear quiz state when CSV changes
    session.pop("quiz_file", None)
    session.pop("quiz_order", None)
    session.pop("index", None)
    session.pop("score", None)

    problems = load_problems(file)

    fields = sorted({(p.get("分野") or "").strip() for p in problems if (p.get("分野") or "").strip()})

    return render_template(
        "start.html",
        files=get_csv_files(),
        fields=fields,
        selected_file=file
    )


@app.route("/start_quiz", methods=["POST"])
def start_quiz():

    file = session.get("file")
    if not file:
        return redirect("/")

    problems = load_problems(file)

    selected_fields = request.form.getlist("fields")
    count = request.form.get("count")
    wrong_only = request.form.get("wrong")

    if "all" not in selected_fields:
        problems = [p for p in problems if p["分野"] in selected_fields]

    if wrong_only:

        conn = sqlite3.connect(DB)
        c = conn.cursor()

        c.execute("""
        SELECT problem_id
        FROM results
        WHERE correct=0
        """)

        wrong_ids = [r[0] for r in c.fetchall()]

        problems = [p for p in problems if p["problem_id"] in wrong_ids]

        conn.close()

    random.shuffle(problems)

    if count != "all":
        problems = problems[:int(count)]

    # Store only lightweight quiz state in session (cookie-safe)
    session["quiz_file"] = file
    session["quiz_order"] = [_safe_int(p.get("No")) for p in problems if _safe_int(p.get("No")) is not None]
    session["index"] = 0
    session["score"] = 0

    return redirect("/quiz")


@app.route("/quiz", methods=["GET", "POST"])
def quiz():

    file = session.get("quiz_file")
    order = session.get("quiz_order") or []
    index = session.get("index", 0)

    if not file or not order:
        return redirect("/")

    # LRU cached per file
    by_no = _problem_map_by_no(file)

    if request.method == "POST":

        selected = request.form.get("answer")
        if not selected:
            return redirect("/quiz")

        no = order[index]
        problem = by_no.get(no)
        if not problem:
            return redirect("/")

        correct = problem["正解"]

        is_correct = selected == correct

        if is_correct:
            session["score"] += 1

        conn = sqlite3.connect(DB)
        c = conn.cursor()

        c.execute("""
        INSERT INTO results
        (problem_id,file,field,problem_no,selected,correct,answered_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            problem["problem_id"],
            problem["file"],
            problem.get("分野"),
            _safe_int(problem.get("No")),
            selected,
            int(is_correct),
            datetime.now().isoformat(timespec="seconds")
        ))

        conn.commit()
        conn.close()

        session["index"] += 1

        choice_text = {
            "A": problem.get("選択肢A", ""),
            "B": problem.get("選択肢B", ""),
            "C": problem.get("選択肢C", ""),
            "D": problem.get("選択肢D", ""),
        }
        selected_text = choice_text.get(selected, "")
        correct_text = choice_text.get(correct, "")

        return render_template(
            "result.html",
            problem=problem,
            selected=selected,
            selected_text=selected_text,
            correct=correct,
            correct_text=correct_text,
            is_correct=is_correct
        )

    if index >= len(order):
        return redirect("/stats")

    no = order[index]
    problem = by_no.get(no)
    if not problem:
        return redirect("/")

    choices = {
        "A": problem["選択肢A"],
        "B": problem["選択肢B"],
        "C": problem["選択肢C"],
        "D": problem["選択肢D"]
    }

    items = list(choices.items())
    random.shuffle(items)

    return render_template(
        "quiz.html",
        problem=problem,
        choices=items,
        index=index+1,
        total=len(order)
    )


@app.route("/next")
def next_question():
    return redirect("/quiz")


@app.route("/home")
def home():
    # Abort current quiz and return to start page
    session.pop("quiz_file", None)
    session.pop("quiz_order", None)
    session.pop("index", None)
    session.pop("score", None)
    return redirect("/")


@app.route("/clear")
def clear():

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("DELETE FROM results")

    conn.commit()
    conn.close()

    return redirect("/")


@app.route("/stats")
def stats():

    selected_file = session.get("file")
    if not selected_file:
        return redirect("/")

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    SELECT COUNT(*)
    FROM results
    WHERE file=?
    """, (selected_file,))
    total = c.fetchone()[0]

    c.execute("""
    SELECT COUNT(*)
    FROM results
    WHERE correct=1
    AND file=?
    """, (selected_file,))
    correct = c.fetchone()[0]

    c.execute("""
    SELECT field,
    SUM(correct),
    COUNT(*)
    FROM results
    WHERE field IS NOT NULL AND field != ''
    AND file=?
    GROUP BY field
    """, (selected_file,))
    by_field = c.fetchall()

    conn.close()

    return render_template(
        "stats.html",
        selected_file=selected_file,
        total=total,
        correct=correct,
        by_field=by_field,
    )


@app.route("/ranking")
def ranking():

    selected_file = session.get("file")
    if not selected_file:
        return redirect("/")

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # Mistakes per problem + last wrong selection (by answered_at, id)
    c.execute(
        """
        SELECT
          r.problem_id,
          COUNT(*) AS mistakes,
          (
            SELECT r2.selected
            FROM results r2
            WHERE r2.problem_id = r.problem_id
              AND r2.file = ?
              AND r2.correct = 0
            ORDER BY r2.answered_at DESC, r2.id DESC
            LIMIT 1
          ) AS last_selected
        FROM results r
        WHERE r.correct = 0 AND r.file = ?
        GROUP BY r.problem_id
        ORDER BY mistakes DESC
        LIMIT 50
        """,
        (selected_file, selected_file),
    )
    raw = c.fetchall()

    conn.close()

    # Map to problem details from current CSV
    by_no = _problem_map_by_no(selected_file)
    rows = []
    for pid, mistakes, last_selected in raw:
        try:
            no = _safe_int(str(pid).split("_")[-1])
        except Exception:
            no = None
        problem = by_no.get(no) if no is not None else None
        if not problem:
            continue

        selected_choice = (last_selected or "").strip()

        correct_choice = (problem.get("正解") or "").strip()
        choice_text = {
            "A": problem.get("選択肢A", ""),
            "B": problem.get("選択肢B", ""),
            "C": problem.get("選択肢C", ""),
            "D": problem.get("選択肢D", ""),
        }

        rows.append(
            {
                "problem_id": pid,
                "problem_no": _safe_int(problem.get("No")),
                "field": (problem.get("分野") or "").strip(),
                "question": problem.get("問題文") or "",
                "mistakes": mistakes,
                "selected_choice": selected_choice,
                "selected_text": choice_text.get(selected_choice, ""),
                "correct_choice": correct_choice,
                "correct_text": choice_text.get(correct_choice, ""),
                "explanation": problem.get("解説") or "",
            }
        )

    return render_template(
        "ranking.html",
        selected_file=selected_file,
        rows=rows,
    )


@app.route("/progress")
def progress():

    selected_file = session.get("file")
    if not selected_file:
        return redirect("/")

    file_total, totals_by_field = _scan_problem_totals_for_file(selected_file)

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute(
        "SELECT COUNT(DISTINCT problem_id) FROM results WHERE file=?",
        (selected_file,),
    )
    file_solved = c.fetchone()[0]

    c.execute("""
    SELECT field, COUNT(DISTINCT problem_id)
    FROM results
    WHERE field IS NOT NULL AND field != ''
    AND file=?
    GROUP BY field
    """, (selected_file,))
    field_solved = {r[0]: r[1] for r in c.fetchall()}

    conn.close()

    file_rate = round((file_solved / file_total * 100), 1) if file_total else 0.0

    field_rows = []
    for field in sorted(totals_by_field.keys()):
        total = totals_by_field[field]
        solved = field_solved.get(field, 0)
        rate = round((solved / total * 100), 1) if total else 0.0
        field_rows.append((field, solved, total, rate))

    return render_template(
        "progress.html",
        selected_file=selected_file,
        file_total=file_total,
        file_solved=file_solved,
        file_rate=file_rate,
        field_rows=field_rows,
    )


def get_csv_files():

    files = []

    if not os.path.isdir(PROBLEM_DIR):
        return files

    for f in os.listdir(PROBLEM_DIR):
        if f.lower().endswith(".csv"):
            files.append(f)

    return sorted(files)


if __name__ == "__main__":
    init_db()
    try:
        _backfill_result_fields_if_needed()
    except Exception:
        # Best-effort only; app should still run even if CSVs are missing/invalid.
        pass
    app.run(debug=True)