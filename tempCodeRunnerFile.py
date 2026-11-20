#!/usr/bin/env python3
"""
College Complaint Manager  |  GreenFuture Hack - Project #23
Zero-dependency (Python 3.10+ standard library only).

Features
  - Menu-driven terminal UI with ANSI colours
  - Defensive input validation (centralised wrappers)
  - Log complaints, assign categories, track resolution status
  - JSON persistence (auto-recovers from missing/corrupt files)
  - Keyword / category / status search
  - Executive summary (totals, averages, trends)
  - Overdue warnings (SLA based on priority)
  - Export to Markdown, TXT and CSV
  - Bulk CSV import with row-by-row error log
  - Sample dataset generator (40 realistic entries)

Run:  python complaint_manager.py
"""

import csv
import json
import os
import random
import re
import shutil
import sys
from collections import Counter
from datetime import date, datetime, timedelta

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DATA_FILE = "complaints.json"
EXPORT_DIR = "exports"
IMPORT_ERROR_LOG = "import_errors.log"
PAGE_SIZE = 10

CATEGORIES = [
    "Academic", "Hostel", "Mess/Food", "Infrastructure", "Library",
    "Transport", "Faculty", "Administration", "Ragging/Safety", "Other",
]
STATUSES = ["Open", "In Progress", "Resolved", "Rejected"]
PRIORITY_LABELS = {1: "Very Low", 2: "Low", 3: "Medium", 4: "High", 5: "Critical"}
# Days allowed to resolve a complaint, by priority (SLA)
SLA_DAYS = {1: 10, 2: 7, 3: 5, 4: 3, 5: 2}

# --------------------------------------------------------------------------
# ANSI helpers
# --------------------------------------------------------------------------
if os.name == "nt":
    os.system("")  # enable ANSI escape codes on Windows terminals


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREY = "\033[90m"


def paint(text, colour):
    return f"{colour}{text}{C.RESET}"


STATUS_COLOUR = {
    "Open": C.RED,
    "In Progress": C.YELLOW,
    "Resolved": C.GREEN,
    "Rejected": C.GREY,
}

# --------------------------------------------------------------------------
# Input wrappers (never crash on bad input)
# --------------------------------------------------------------------------


def ask_text(prompt, min_len=1, max_len=200, pattern=None, allow_blank=False):
    """Loop until a valid string is entered."""
    while True:
        value = input(f"{prompt}: ").strip()
        if not value and allow_blank:
            return ""
        if len(value) < min_len:
            print(paint(f"  ✖ Must be at least {min_len} characters.", C.RED))
            continue
        if len(value) > max_len:
            print(paint(f"  ✖ Must be at most {max_len} characters.", C.RED))
            continue
        if pattern and not re.fullmatch(pattern, value):
            print(paint("  ✖ Invalid format. Please try again.", C.RED))
            continue
        return value


def ask_int(prompt, lo, hi):
    """Loop until an integer in [lo, hi] is entered."""
    while True:
        raw = input(f"{prompt} [{lo}-{hi}]: ").strip()
        try:
            num = int(raw)
        except ValueError:
            print(paint("  ✖ Please enter a whole number.", C.RED))
            continue
        if not lo <= num <= hi:
            print(paint(f"  ✖ Value must be between {lo} and {hi}.", C.RED))
            continue
        return num


def ask_choice(prompt, options):
    """Show a numbered list and return the chosen option."""
    for i, opt in enumerate(options, 1):
        print(f"   {i}. {opt}")
    idx = ask_int(prompt, 1, len(options))
    return options[idx - 1]


def pause():
    input(paint("\nPress Enter to continue...", C.GREY))


# --------------------------------------------------------------------------
# Validation (shared by interactive entry and bulk import)
# --------------------------------------------------------------------------
NAME_RE = r"[A-Za-z][A-Za-z .'-]{1,59}"
ROLL_RE = r"[A-Za-z0-9]{6,15}"


def validate_record(raw):
    """Return (clean_dict, errors_list). Used by CSV import and seeding."""
    errors = []
    name = str(raw.get("student_name", "")).strip()
    roll = str(raw.get("roll_no", "")).strip().upper()
    category = str(raw.get("category", "")).strip()
    desc = str(raw.get("description", "")).strip()
    status = str(raw.get("status", "") or "Open").strip().title()
    created = str(raw.get("created", "") or date.today().isoformat()).strip()

    if not re.fullmatch(NAME_RE, name):
        errors.append("invalid student_name")
    if not re.fullmatch(ROLL_RE, roll):
        errors.append("invalid roll_no (6-15 letters/digits)")

    match = next((c for c in CATEGORIES if c.lower() == category.lower()), None)
    if match is None:
        errors.append(f"unknown category '{category}'")
    else:
        category = match

    if not 10 <= len(desc) <= 300:
        errors.append("description must be 10-300 characters")

    try:
        priority = int(str(raw.get("priority", "")).strip())
        if not 1 <= priority <= 5:
            raise ValueError
    except ValueError:
        priority = None
        errors.append("priority must be an integer 1-5")

    if status == "In progress":
        status = "In Progress"
    if status not in STATUSES:
        errors.append(f"unknown status '{status}'")

    try:
        created_dt = datetime.strptime(created, "%Y-%m-%d").date()
        if created_dt > date.today():
            errors.append("created date is in the future")
    except ValueError:
        errors.append("created must be YYYY-MM-DD")

    if errors:
        return None, errors
    return {
        "student_name": name.title(),
        "roll_no": roll,
        "category": category,
        "description": desc,
        "priority": priority,
        "status": status,
        "created": created,
    }, []


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def load_data():
    """Load complaints; recover safely from missing or corrupt files."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("root must be a list")
        return data
    except FileNotFoundError:
        save_data([])
        return []
    except (json.JSONDecodeError, ValueError):
        backup = DATA_FILE + ".corrupt.bak"
        shutil.copy(DATA_FILE, backup)
        print(paint(f"⚠ Data file was corrupted. Backup saved as {backup}. "
                    "Starting with a clean database.", C.YELLOW))
        save_data([])
        return []


def save_data(complaints):
    """Atomic write so a crash can never leave a half-written file."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(complaints, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


def next_id(complaints):
    return max((c["id"] for c in complaints), default=1000) + 1


# --------------------------------------------------------------------------
# Business logic
# --------------------------------------------------------------------------


def make_complaint(complaints, clean):
    return {
        "id": next_id(complaints),
        **clean,
        "updated": clean["created"],
        "resolved_on": clean["created"] if clean["status"] in ("Resolved", "Rejected") else None,
        "remarks": "",
    }


def age_days(c):
    return (date.today() - datetime.strptime(c["created"], "%Y-%m-%d").date()).days


def is_overdue(c):
    return c["status"] in ("Open", "In Progress") and age_days(c) > SLA_DAYS[c["priority"]]


def resolution_days(c):
    if c["status"] == "Resolved" and c.get("resolved_on"):
        d1 = datetime.strptime(c["created"], "%Y-%m-%d").date()
        d2 = datetime.strptime(c["resolved_on"], "%Y-%m-%d").date()
        return max((d2 - d1).days, 0)
    return None


def search(complaints, keyword="", category="", status=""):
    kw = keyword.lower()
    out = []
    for c in complaints:
        if category and c["category"] != category:
            continue
        if status and c["status"] != status:
            continue
        haystack = f"{c['description']} {c['student_name']} {c['roll_no']} {c['id']}".lower()
        if kw and kw not in haystack:
            continue
        out.append(c)
    return out


def compute_summary(complaints):
    total = len(complaints)
    s = {"total": total}
    s["by_status"] = Counter(c["status"] for c in complaints)
    s["by_category"] = Counter(c["category"] for c in complaints)
    s["by_priority"] = Counter(c["priority"] for c in complaints)
    s["avg_priority"] = (sum(c["priority"] for c in complaints) / total) if total else 0
    times = [resolution_days(c) for c in complaints if resolution_days(c) is not None]
    s["avg_resolution_days"] = (sum(times) / len(times)) if times else 0
    closed = s["by_status"]["Resolved"] + s["by_status"]["Rejected"]
    s["resolution_rate"] = (s["by_status"]["Resolved"] / total * 100) if total else 0
    s["pending"] = total - closed
    s["overdue"] = [c for c in complaints if is_overdue(c)]
    s["critical_open"] = [c for c in complaints
                          if c["priority"] == 5 and c["status"] in ("Open", "In Progress")]

    # trend: last 7 days vs previous 7 days
    last7 = prev7 = 0
    for c in complaints:
        a = age_days(c)
        if a < 7:
            last7 += 1
        elif a < 14:
            prev7 += 1
    s["last7"], s["prev7"] = last7, prev7
    s["trend_pct"] = ((last7 - prev7) / prev7 * 100) if prev7 else None
    s["top_category"] = s["by_category"].most_common(1)[0] if total else None
    return s


# --------------------------------------------------------------------------
# Display helpers
# --------------------------------------------------------------------------


def banner():
    line = "═" * 58
    print(paint(f"\n╔{line}╗", C.CYAN))
    print(paint("║", C.CYAN) + paint("COLLEGE COMPLAINT MANAGER".center(58), C.BOLD) + paint("║", C.CYAN))
    print(paint("║", C.CYAN) + "GreenFuture Hack • Campus & Administration".center(58) + paint("║", C.CYAN))
    print(paint(f"╚{line}╝", C.CYAN))


def clip(text, width):
    text = str(text)
    return text if len(text) <= width else text[: width - 1] + "…"


def print_table(rows):
    header = f"{'ID':<6}{'Date':<11}{'Student':<16}{'Category':<15}{'P':<3}{'Status':<13}Description"
    print(paint(header, C.BOLD))
    print("─" * 90)
    for c in rows:
        flag = paint("!", C.RED) if is_overdue(c) else " "
        status = paint(f"{c['status']:<12}", STATUS_COLOUR[c["status"]])
        print(f"{c['id']:<6}{c['created']:<11}{clip(c['student_name'], 15):<16}"
              f"{clip(c['category'], 14):<15}{c['priority']:<3}{status}{flag}"
              f"{clip(c['description'], 36)}")
    print("─" * 90)
    print(paint("  P = priority (1-5)   ! = overdue", C.GREY))


def paginate(rows):
    if not rows:
        print(paint("\nNo records found.", C.YELLOW))
        return
    pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
    page = 0
    while True:
        chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        print(paint(f"\nPage {page + 1}/{pages}  ({len(rows)} records)", C.BLUE))
        print_table(chunk)
        if pages == 1:
            return
        cmd = input("[n]ext  [p]revious  [q]uit : ").strip().lower()
        if cmd == "n" and page < pages - 1:
            page += 1
        elif cmd == "p" and page > 0:
            page -= 1
        elif cmd == "q":
            return


def print_detail(c):
    print(paint(f"\nComplaint #{c['id']}", C.BOLD))
    print(f"  Student     : {c['student_name']} ({c['roll_no']})")
    print(f"  Category    : {c['category']}")
    print(f"  Priority    : {c['priority']} - {PRIORITY_LABELS[c['priority']]}")
    print(f"  Status      : {paint(c['status'], STATUS_COLOUR[c['status']])}")
    print(f"  Filed on    : {c['created']}   (SLA: {SLA_DAYS[c['priority']]} days)")
    print(f"  Last update : {c['updated']}")
    print(f"  Description : {c['description']}")
    if c.get("remarks"):
        print(f"  Remarks     : {c['remarks']}")
    if is_overdue(c):
        print(paint(f"  ⚠ OVERDUE by {age_days(c) - SLA_DAYS[c['priority']]} day(s)", C.RED))


def bar(value, total, width=25):
    filled = int(round(value / total * width)) if total else 0
    return "█" * filled + "░" * (width - filled)


# --------------------------------------------------------------------------
# Menu actions
# --------------------------------------------------------------------------


def action_add(complaints):
    print(paint("\n── Log a New Complaint ──", C.BOLD))
    name = ask_text("Student name", 2, 60, NAME_RE)
    roll = ask_text("Roll number (6-15 letters/digits)", 6, 15, ROLL_RE).upper()
    print("Category:")
    category = ask_choice("Choose category", CATEGORIES)
    desc = ask_text("Description (10-300 chars)", 10, 300)
    print("Priority: 1=Very Low ... 5=Critical")
    priority = ask_int("Priority", 1, 5)

    clean = {
        "student_name": name.title(), "roll_no": roll, "category": category,
        "description": desc, "priority": priority, "status": "Open",
        "created": date.today().isoformat(),
    }
    record = make_complaint(complaints, clean)
    complaints.append(record)
    save_data(complaints)
    print(paint(f"\n✔ Complaint logged with ID #{record['id']}", C.GREEN))
    print(f"  Expected resolution within {SLA_DAYS[priority]} day(s).")


def find_by_id(complaints, cid):
    return next((c for c in complaints if c["id"] == cid), None)


def action_view(complaints):
    print(paint("\n── All Complaints ──", C.BOLD))
    rows = sorted(complaints, key=lambda c: (c["created"], c["id"]), reverse=True)
    paginate(rows)
    if rows:
        raw = input("Enter an ID to view details (or press Enter to skip): ").strip()
        if raw.isdigit():
            c = find_by_id(complaints, int(raw))
            if c:
                print_detail(c)
            else:
                print(paint("  ✖ No such ID.", C.RED))


def action_search(complaints):
    print(paint("\n── Search / Filter ──", C.BOLD))
    keyword = ask_text("Keyword (blank to skip)", 0, 60, allow_blank=True)
    category = status = ""
    if input("Filter by category? (y/N): ").strip().lower() == "y":
        category = ask_choice("Choose category", CATEGORIES)
    if input("Filter by status? (y/N): ").strip().lower() == "y":
        status = ask_choice("Choose status", STATUSES)
    paginate(search(complaints, keyword, category, status))


def action_update(complaints):
    print(paint("\n── Update Resolution Status ──", C.BOLD))
    cid = ask_int("Complaint ID", 1001, 10 ** 7)
    c = find_by_id(complaints, cid)
    if not c:
        print(paint("  ✖ No complaint with that ID.", C.RED))
        return
    print_detail(c)
    print("\nNew status:")
    new_status = ask_choice("Choose status", STATUSES)
    remarks = ask_text("Remarks (optional)", 0, 200, allow_blank=True)
    c["status"] = new_status
    c["updated"] = date.today().isoformat()
    c["resolved_on"] = c["updated"] if new_status in ("Resolved", "Rejected") else None
    if remarks:
        c["remarks"] = remarks
    save_data(complaints)
    print(paint(f"✔ Complaint #{cid} is now '{new_status}'.", C.GREEN))


def action_delete(complaints):
    print(paint("\n── Delete Complaint ──", C.BOLD))
    cid = ask_int("Complaint ID", 1001, 10 ** 7)
    c = find_by_id(complaints, cid)
    if not c:
        print(paint("  ✖ No complaint with that ID.", C.RED))
        return
    print_detail(c)
    if input("Type 'yes' to confirm delete: ").strip().lower() == "yes":
        complaints.remove(c)
        save_data(complaints)
        print(paint("✔ Deleted.", C.GREEN))
    else:
        print("Cancelled.")


def action_summary(complaints):
    s = compute_summary(complaints)
    print(paint("\n══════════ EXECUTIVE SUMMARY ══════════", C.BOLD))
    if not s["total"]:
        print(paint("No data yet. Log complaints or load the sample dataset.", C.YELLOW))
        return
    ov, cr = len(s["overdue"]), len(s["critical_open"])
    print(f"Total complaints      : {s['total']}")
    print(f"Pending               : {s['pending']}")
    print(f"Resolution rate       : {s['resolution_rate']:.1f}%")
    print(f"Average priority      : {s['avg_priority']:.2f} / 5")
    print(f"Avg resolution time   : {s['avg_resolution_days']:.1f} day(s)")
    print(f"Overdue complaints    : {paint(str(ov), C.RED if ov else C.GREEN)}")
    print(f"Critical & unresolved : {paint(str(cr), C.RED if cr else C.GREEN)}")

    print(paint("\nBy Status", C.BLUE))
    for st in STATUSES:
        n = s["by_status"][st]
        print(f"  {st:<12} {n:>3}  {paint(bar(n, s['total']), STATUS_COLOUR[st])}")

    print(paint("\nBy Category", C.BLUE))
    for cat, n in s["by_category"].most_common():
        print(f"  {cat:<15} {n:>3}  {bar(n, s['total'])}")

    print(paint("\nBy Priority", C.BLUE))
    for p in range(5, 0, -1):
        n = s["by_priority"][p]
        print(f"  {p} {PRIORITY_LABELS[p]:<9} {n:>3}  {bar(n, s['total'])}")

    print(paint("\nTrends", C.BLUE))
    print(f"  Last 7 days: {s['last7']}   |   Previous 7 days: {s['prev7']}")
    if s["trend_pct"] is None:
        print("  Trend: not enough history to compare.")
    else:
        arrow = "▲ rising" if s["trend_pct"] > 0 else ("▼ falling" if s["trend_pct"] < 0 else "■ flat")
        print(f"  Trend: {arrow} ({s['trend_pct']:+.0f}%)")
    cat, n = s["top_category"]
    print(f"  Most complained about category: {cat} ({n} complaints)")


def action_warnings(complaints):
    print(paint("\n── ⚠ Threshold Warnings ──", C.BOLD))
    s = compute_summary(complaints)
    overdue = sorted(s["overdue"], key=lambda c: -c["priority"])
    if overdue:
        print(paint(f"\n{len(overdue)} OVERDUE complaint(s):", C.RED))
        for c in overdue[:25]:
            late = age_days(c) - SLA_DAYS[c["priority"]]
            print(f"  #{c['id']}  [{PRIORITY_LABELS[c['priority']]}] {c['category']} "
                  f"- {late} day(s) late - {clip(c['description'], 45)}")
    else:
        print(paint("✔ No overdue complaints.", C.GREEN))

    if s["critical_open"]:
        print(paint(f"\n{len(s['critical_open'])} CRITICAL complaint(s) still unresolved!", C.RED))
    for cat, n in s["by_category"].items():
        if s["total"] >= 10 and n / s["total"] > 0.30:
            print(paint(f"\n⚠ Category '{cat}' makes up {n / s['total'] * 100:.0f}% of all "
                        "complaints - investigate root cause.", C.YELLOW))
    stale = [c for c in complaints if c["status"] == "Open" and age_days(c) > 14]
    if stale:
        print(paint(f"\n⚠ {len(stale)} complaint(s) have been 'Open' for over 14 days "
                    "with no action.", C.YELLOW))


def action_export(complaints):
    print(paint("\n── Export Reports ──", C.BOLD))
    if not complaints:
        print(paint("Nothing to export.", C.YELLOW))
        return
    os.makedirs(EXPORT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    s = compute_summary(complaints)

    # --- Markdown report
    md_path = os.path.join(EXPORT_DIR, f"report_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# College Complaint Manager - Summary Report\n\n")
        f.write(f"_Generated: {datetime.now():%Y-%m-%d %H:%M}_\n\n")
        f.write("## Key Metrics\n\n| Metric | Value |\n|---|---|\n")
        f.write(f"| Total complaints | {s['total']} |\n")
        f.write(f"| Pending | {s['pending']} |\n")
        f.write(f"| Resolution rate | {s['resolution_rate']:.1f}% |\n")
        f.write(f"| Average priority | {s['avg_priority']:.2f} |\n")
        f.write(f"| Avg resolution time (days) | {s['avg_resolution_days']:.1f} |\n")
        f.write(f"| Overdue | {len(s['overdue'])} |\n\n")
        f.write("## By Status\n\n| Status | Count |\n|---|---|\n")
        for st in STATUSES:
            f.write(f"| {st} | {s['by_status'][st]} |\n")
        f.write("\n## By Category\n\n| Category | Count |\n|---|---|\n")
        for cat, n in s["by_category"].most_common():
            f.write(f"| {cat} | {n} |\n")
        f.write("\n## Overdue Complaints\n\n")
        if s["overdue"]:
            f.write("| ID | Category | Priority | Description |\n|---|---|---|---|\n")
            for c in s["overdue"]:
                f.write(f"| {c['id']} | {c['category']} | {c['priority']} | "
                        f"{c['description'].replace('|', '/')} |\n")
        else:
            f.write("None\n")

    # --- Plain-text audit log
    txt_path = os.path.join(EXPORT_DIR, f"audit_{stamp}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"COMPLAINT AUDIT LOG - {datetime.now():%Y-%m-%d %H:%M}\n")
        f.write("=" * 70 + "\n")
        for c in sorted(complaints, key=lambda x: x["id"]):
            f.write(f"#{c['id']} | {c['created']} | {c['student_name']} ({c['roll_no']}) | "
                    f"{c['category']} | P{c['priority']} | {c['status']}"
                    f"{' | OVERDUE' if is_overdue(c) else ''}\n")
            f.write(f"    {c['description']}\n")
            if c.get("remarks"):
                f.write(f"    Remarks: {c['remarks']}\n")

    # --- CSV
    csv_path = os.path.join(EXPORT_DIR, f"complaints_{stamp}.csv")
    fields = ["id", "student_name", "roll_no", "category", "description",
              "priority", "status", "created", "updated", "resolved_on", "remarks"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(complaints)

    print(paint("✔ Reports generated:", C.GREEN))
    for p in (md_path, txt_path, csv_path):
        print(f"   {p}")


def action_import(complaints):
    print(paint("\n── Bulk CSV Import ──", C.BOLD))
    print("Required columns: student_name, roll_no, category, description, priority")
    print("Optional columns: status, created (YYYY-MM-DD)")
    path = ask_text("Path to CSV file", 1, 260)
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            required = {"student_name", "roll_no", "category", "description", "priority"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                print(paint(f"  ✖ Missing required columns. Found: {reader.fieldnames}", C.RED))
                return
            good = bad = 0
            with open(IMPORT_ERROR_LOG, "w", encoding="utf-8") as log:
                log.write(f"Import errors - {datetime.now():%Y-%m-%d %H:%M}\n")
                for line_no, row in enumerate(reader, start=2):
                    clean, errs = validate_record(row)
                    if errs:
                        bad += 1
                        log.write(f"Row {line_no}: {'; '.join(errs)}\n")
                    else:
                        complaints.append(make_complaint(complaints, clean))
                        good += 1
        save_data(complaints)
        print(paint(f"✔ Imported {good} row(s).", C.GREEN))
        if bad:
            print(paint(f"⚠ {bad} row(s) rejected - see {IMPORT_ERROR_LOG}", C.YELLOW))
    except FileNotFoundError:
        print(paint("  ✖ File not found.", C.RED))
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        print(paint(f"  ✖ Could not read file: {e}", C.RED))


# --------------------------------------------------------------------------
# Sample data
# --------------------------------------------------------------------------
SAMPLES = {
    "Academic": ["Internal marks not updated on the portal after re-evaluation",
                 "Timetable clashes between lab and elective classes",
                 "Syllabus not completed before the mid-term examination"],
    "Hostel": ["Water heater in block B not working for two weeks",
               "Wi-Fi extremely slow in hostel rooms at night",
               "Room cleaning not done regularly on the third floor"],
    "Mess/Food": ["Food quality has dropped, found insects in the rice",
                  "Mess timings are too short for students with late labs",
                  "Drinking water cooler near mess is dirty"],
    "Infrastructure": ["Projector in classroom 204 is not working",
                       "Broken benches in the main seminar hall",
                       "Street lights near the parking area are off"],
    "Library": ["Required reference books are unavailable in the library",
                "Library AC not functioning during exam week"],
    "Transport": ["College bus on route 7 arrives very late every day",
                  "Overcrowding in morning bus, students standing"],
    "Faculty": ["Faculty regularly arrives late to the first period",
                "Doubt clearing sessions are not being conducted"],
    "Administration": ["Bonafide certificate pending for over two weeks",
                       "Fee receipt shows a wrong amount, correction needed"],
    "Ragging/Safety": ["Seniors harassing juniors near the canteen at night",
                       "No security guard at the girls hostel entrance"],
    "Other": ["Sports ground is waterlogged after rain",
              "Canteen prices are higher than the displayed menu"],
}
FIRST = ["Aarav", "Ananya", "Rohit", "Sneha", "Karthik", "Divya", "Vikram", "Priya",
         "Rahul", "Meghana", "Arjun", "Lakshmi", "Sai", "Pooja", "Nikhil"]
LAST = ["Reddy", "Sharma", "Kumar", "Rao", "Patel", "Naidu", "Gupta", "Verma"]


def action_seed(complaints):
    print(paint("\n── Load Sample Dataset ──", C.BOLD))
    if complaints and input("This ADDS 40 sample records. Continue? (y/N): ").strip().lower() != "y":
        return
    rnd = random.Random(42)  # fixed seed -> same demo data every time
    cats = list(SAMPLES)
    for _ in range(40):
        cat = rnd.choice(cats)
        created = date.today() - timedelta(days=rnd.randint(0, 30))
        status = rnd.choices(STATUSES, weights=[35, 25, 35, 5])[0]
        raw = {
            "student_name": f"{rnd.choice(FIRST)} {rnd.choice(LAST)}",
            "roll_no": f"{rnd.randint(22, 25)}J{rnd.randint(1, 9)}A{rnd.randint(1000, 9999)}",
            "category": cat,
            "description": rnd.choice(SAMPLES[cat]),
            "priority": rnd.choices([1, 2, 3, 4, 5], weights=[10, 20, 30, 25, 15])[0],
            "status": status,
            "created": created.isoformat(),
        }
        clean, errs = validate_record(raw)
        if errs:
            continue
        rec = make_complaint(complaints, clean)
        if status in ("Resolved", "Rejected"):
            rec["resolved_on"] = min(date.today(),
                                     created + timedelta(days=rnd.randint(1, 8))).isoformat()
            rec["updated"] = rec["resolved_on"]
        elif status == "In Progress":
            rec["updated"] = min(date.today(), created + timedelta(days=1)).isoformat()
        complaints.append(rec)
    save_data(complaints)
    print(paint("✔ 40 sample complaints added.", C.GREEN))


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
MENU = [
    ("Log a new complaint", action_add),
    ("View all complaints", action_view),
    ("Search / filter complaints", action_search),
    ("Update complaint status", action_update),
    ("Executive summary", action_summary),
    ("Overdue & threshold warnings", action_warnings),
    ("Export reports (MD / TXT / CSV)", action_export),
    ("Bulk import from CSV", action_import),
    ("Load sample dataset", action_seed),
    ("Delete a complaint", action_delete),
]


def main():
    complaints = load_data()
    banner()
    overdue_now = sum(1 for c in complaints if is_overdue(c))
    print(f"\nLoaded {len(complaints)} complaint(s) from {DATA_FILE}.")
    if overdue_now:
        print(paint(f"⚠ {overdue_now} complaint(s) are overdue!", C.RED))

    while True:
        print(paint("\n──────── MAIN MENU ────────", C.CYAN))
        for i, (label, _) in enumerate(MENU, 1):
            print(f"  {i:>2}. {label}")
        print("   0. Exit")
        try:
            choice = ask_int("Select option", 0, len(MENU))
            if choice == 0:
                print(paint("\nAll data saved. Goodbye!\n", C.GREEN))
                break
            MENU[choice - 1][1](complaints)
            pause()
        except (KeyboardInterrupt, EOFError):
            print(paint("\n\nInterrupted - data is already saved. Bye!", C.YELLOW))
            sys.exit(0)
        except Exception as e:  # last-resort guard so the demo never crashes
            print(paint(f"\n✖ Unexpected error: {e}", C.RED))
            pause()


if __name__ == "__main__":
    main()