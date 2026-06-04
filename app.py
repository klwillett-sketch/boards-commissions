"""
Boards & Commissions Management System
Flask backend — reads/writes boards_commissions.db via SQLite
"""

from flask import Flask, jsonify, request, send_from_directory
import sqlite3, json, os
from datetime import date, datetime, timedelta

app = Flask(__name__, static_folder='public', static_url_path='')

# ── database path ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'boards_commissions.db'))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def row_to_dict(row):
    d = dict(row)
    # parse terms JSON
    if 'terms' in d and d['terms']:
        try:
            d['terms'] = json.loads(d['terms'])
        except Exception:
            d['terms'] = []
    return d

# ── consecutive years / eligibility logic ───────────────────────
LIMIT_YEARS = 12
GAP_YEARS   = 3

def calc_consec(terms, ref_date=None):
    """
    Given a list of term dicts [{start, end, type, assumed}],
    returns (run_start_str, yrs_today, yrs_at_end, limit_date_str, eligible_to_return_str)
    """
    if not terms:
        return None, None, None, None, None

    today = date.today()
    ref   = ref_date or today

    # parse and sort
    parsed = []
    for t in terms:
        try:
            s = date.fromisoformat(t['start']) if t.get('start') else None
            e = date.fromisoformat(t['end'])   if t.get('end')   else None
            if s:
                parsed.append((s, e))
        except Exception:
            pass

    if not parsed:
        return None, None, None, None, None

    parsed.sort(key=lambda x: x[0])

    # find run start — reset on gap >= GAP_YEARS
    run_start = parsed[0][0]
    for i in range(1, len(parsed)):
        prev_end   = parsed[i-1][1] or today
        this_start = parsed[i][0]
        gap_days   = (this_start - prev_end).days
        if gap_days / 365.25 >= GAP_YEARS:
            run_start = this_start

    # consecutive years
    yrs_today  = (today     - run_start).days / 365.25
    # term end = last term's end date if in the future, else today
    last_end   = parsed[-1][1]
    if last_end and last_end > today:
        yrs_at_end = (last_end - run_start).days / 365.25
    else:
        yrs_at_end = yrs_today

    limit_date = date(run_start.year + LIMIT_YEARS, run_start.month, run_start.day)

    # eligible to return: set if yrs_at_end >= limit
    etr = None
    if yrs_at_end >= LIMIT_YEARS and last_end:
        etr_date = date(last_end.year + GAP_YEARS, last_end.month, last_end.day)
        etr = etr_date.isoformat()

    return (
        run_start.isoformat(),
        round(yrs_today, 1),
        round(yrs_at_end, 1),
        limit_date.isoformat(),
        etr
    )

def enrich_membership(m):
    """Add computed eligibility fields to a membership dict."""
    terms = m.get('terms', [])
    run_start, yrs_today, yrs_at_end, limit_date, etr = calc_consec(terms)
    m['run_start']           = run_start
    m['yrs_today']           = yrs_today
    m['yrs_at_end']          = yrs_at_end
    m['limit_date']          = limit_date
    m['computed_etr']        = etr

    # eligibility status
    if yrs_at_end is None:
        m['eligibility'] = 'unknown'
    elif yrs_at_end >= LIMIT_YEARS:
        # check if in active term
        last_end = None
        for t in sorted(terms, key=lambda x: x.get('start',''), reverse=True):
            if t.get('end'):
                try:
                    last_end = date.fromisoformat(t['end'])
                    break
                except Exception:
                    pass
        if last_end and last_end > date.today():
            m['eligibility'] = 'finishing'
        else:
            m['eligibility'] = 'limited'
    elif yrs_at_end >= 10:
        m['eligibility'] = 'approaching'
    else:
        m['eligibility'] = 'eligible'

    # current term end
    ends = [t['end'] for t in terms if t.get('end')]
    m['current_term_end'] = max(ends) if ends else None

    # term status
    cte = m.get('current_term_end')
    if not cte:
        m['term_status'] = 'none'
    else:
        days = (date.fromisoformat(cte) - date.today()).days
        if days < 0:
            m['term_status'] = 'expired'
        elif days <= 90:
            m['term_status'] = 'expiring'
        else:
            m['term_status'] = 'active'

    return m

# ── serve frontend ───────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

# ── PERSONS ─────────────────────────────────────────────────────
@app.route('/api/persons', methods=['GET'])
def list_persons():
    db = get_db()
    q  = request.args.get('q','').strip()
    if q:
        rows = db.execute(
            "SELECT p.*, COUNT(bm.id) as membership_count "
            "FROM persons p LEFT JOIN board_memberships bm ON bm.person_id=p.id "
            "WHERE p.name LIKE ? GROUP BY p.id ORDER BY p.name",
            (f'%{q}%',)).fetchall()
    else:
        rows = db.execute(
            "SELECT p.*, COUNT(bm.id) as membership_count "
            "FROM persons p LEFT JOIN board_memberships bm ON bm.person_id=p.id "
            "GROUP BY p.id ORDER BY p.name").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/persons/<int:pid>', methods=['GET'])
def get_person(pid):
    db  = get_db()
    p   = db.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    if not p:
        db.close(); return jsonify({'error':'Not found'}), 404
    mbs = db.execute(
        "SELECT * FROM board_memberships WHERE person_id=? ORDER BY board",
        (pid,)).fetchall()
    db.close()
    person = dict(p)
    person['memberships'] = [enrich_membership(row_to_dict(m)) for m in mbs]
    return jsonify(person)

@app.route('/api/persons', methods=['POST'])

def create_person():
    data = request.json
    name  = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error':'Name is required'}), 400
    party = data.get('party','Unknown')
    notes = data.get('notes','')
    db = get_db()
    cur = db.execute(
        "INSERT INTO persons (name,party,notes) VALUES (?,?,?)",
        (name, party, notes))
    db.commit()
    pid = cur.lastrowid
    row = db.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    db.close()
    return jsonify(dict(row)), 201

@app.route('/api/persons/<int:pid>', methods=['PUT'])

def update_person(pid):
    data  = request.json
    db    = get_db()
    existing = db.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    if not existing:
        db.close(); return jsonify({'error':'Not found'}), 404
    name  = (data.get('name') or existing['name']).strip()
    party = data.get('party', existing['party'])
    notes = data.get('notes', existing['notes'])
    db.execute(
        "UPDATE persons SET name=?,party=?,notes=?,updated_at=datetime('now') WHERE id=?",
        (name, party, notes, pid))
    db.commit()
    row = db.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    db.close()
    return jsonify(dict(row))

@app.route('/api/persons/<int:pid>', methods=['DELETE'])

def delete_person(pid):
    db = get_db()
    count = db.execute(
        "SELECT COUNT(*) FROM board_memberships WHERE person_id=?", (pid,)).fetchone()[0]
    if count > 0:
        db.close()
        return jsonify({'error': f'Cannot delete: person has {count} board membership(s). Remove all memberships first.'}), 409
    db.execute("DELETE FROM persons WHERE id=?", (pid,))
    db.commit()
    db.close()
    return jsonify({'deleted': pid})

# ── MEMBERSHIPS ──────────────────────────────────────────────────
@app.route('/api/memberships', methods=['GET'])
def list_memberships():
    db   = get_db()
    board  = request.args.get('board','')
    role   = request.args.get('role','')
    sql    = ("SELECT bm.*, p.name as person_name, p.party "
              "FROM board_memberships bm JOIN persons p ON p.id=bm.person_id WHERE 1=1")
    params = []
    if board:
        sql += " AND bm.board=?"; params.append(board)
    if role:
        sql += " AND bm.role=?";  params.append(role)
    sql += " ORDER BY p.name"
    rows = db.execute(sql, params).fetchall()
    db.close()
    result = [enrich_membership(row_to_dict(r)) for r in rows]
    # filter by eligibility if requested
    elig = request.args.get('eligibility','')
    if elig:
        result = [m for m in result if m.get('eligibility')==elig]
    return jsonify(result)

@app.route('/api/memberships/<int:mid>', methods=['GET'])
def get_membership(mid):
    db  = get_db()
    row = db.execute(
        "SELECT bm.*, p.name as person_name, p.party "
        "FROM board_memberships bm JOIN persons p ON p.id=bm.person_id WHERE bm.id=?",
        (mid,)).fetchone()
    db.close()
    if not row:
        return jsonify({'error':'Not found'}), 404
    return jsonify(enrich_membership(row_to_dict(row)))

@app.route('/api/memberships', methods=['POST'])

def create_membership():
    data      = request.json
    person_id = data.get('person_id')
    board     = (data.get('board') or '').strip()
    if not person_id or not board:
        return jsonify({'error':'person_id and board are required'}), 400

    db = get_db()
    # cooling-off check
    existing = db.execute(
        "SELECT eligible_to_return FROM board_memberships "
        "WHERE person_id=? AND board=?", (person_id, board)).fetchone()
    if existing and existing['eligible_to_return']:
        etr = date.fromisoformat(existing['eligible_to_return'])
        if etr > date.today():
            if not data.get('override_cooling_off'):
                db.close()
                return jsonify({
                    'error': 'cooling_off',
                    'eligible_to_return': existing['eligible_to_return'],
                    'message': (f"This person has a cooling-off period on {board} "
                                f"until {existing['eligible_to_return']}.")
                }), 409

    role              = data.get('role','regular')
    terms             = data.get('terms', [])
    is_leaving_at_end = 1 if data.get('is_leaving_at_end') else 0
    notes             = data.get('notes','')

    # compute etr
    _, _, yrs_at_end, _, etr = calc_consec(terms)
    terms_json = json.dumps(terms)

    cur = db.execute(
        "INSERT INTO board_memberships "
        "(person_id,board,role,terms,is_leaving_at_end,eligible_to_return,notes) "
        "VALUES (?,?,?,?,?,?,?)",
        (person_id, board, role, terms_json, is_leaving_at_end, etr, notes))
    db.commit()
    mid = cur.lastrowid
    row = db.execute(
        "SELECT bm.*, p.name as person_name, p.party "
        "FROM board_memberships bm JOIN persons p ON p.id=bm.person_id WHERE bm.id=?",
        (mid,)).fetchone()
    db.close()
    return jsonify(enrich_membership(row_to_dict(row))), 201

@app.route('/api/memberships/<int:mid>', methods=['PUT'])

def update_membership(mid):
    data = request.json
    db   = get_db()
    existing = db.execute("SELECT * FROM board_memberships WHERE id=?", (mid,)).fetchone()
    if not existing:
        db.close(); return jsonify({'error':'Not found'}), 404

    terms             = data.get('terms', json.loads(existing['terms']))
    role              = data.get('role',              existing['role'])
    is_leaving_at_end = 1 if data.get('is_leaving_at_end') else 0
    notes             = data.get('notes',             existing['notes'])

    _, _, yrs_at_end, _, etr = calc_consec(terms)
    # Always use the freshly computed etr so that a gap resetting the clock
    # properly clears a previously stored cooling-off date.

    db.execute(
        "UPDATE board_memberships SET role=?,terms=?,is_leaving_at_end=?,"
        "eligible_to_return=?,notes=?,updated_at=datetime('now') WHERE id=?",
        (role, json.dumps(terms), is_leaving_at_end, etr, notes, mid))
    db.commit()
    row = db.execute(
        "SELECT bm.*, p.name as person_name, p.party "
        "FROM board_memberships bm JOIN persons p ON p.id=bm.person_id WHERE bm.id=?",
        (mid,)).fetchone()
    db.close()
    return jsonify(enrich_membership(row_to_dict(row)))

@app.route('/api/memberships/<int:mid>', methods=['DELETE'])

def delete_membership(mid):
    db = get_db()
    db.execute("DELETE FROM board_memberships WHERE id=?", (mid,))
    db.commit()
    db.close()
    return jsonify({'deleted': mid})

# ── BULK REAPPOINT ───────────────────────────────────────────────
@app.route('/api/memberships/bulk-reappoint', methods=['POST'])

def bulk_reappoint():
    data       = request.json
    ids        = data.get('membership_ids', [])
    new_start  = data.get('start')
    new_end    = data.get('end')
    term_type  = data.get('type', 'full_term')
    if not ids or not new_start or not new_end:
        return jsonify({'error':'membership_ids, start, and end are required'}), 400

    db = get_db()
    warnings = []
    for mid in ids:
        row = db.execute("SELECT * FROM board_memberships WHERE id=?", (mid,)).fetchone()
        if not row: continue
        if row['eligible_to_return']:
            try:
                etr = date.fromisoformat(row['eligible_to_return'])
                if etr > date.fromisoformat(new_start):
                    p = db.execute("SELECT name FROM persons WHERE id=?",
                                   (row['person_id'],)).fetchone()
                    warnings.append({
                        'membership_id': mid,
                        'person': p['name'] if p else '?',
                        'board': row['board'],
                        'eligible_to_return': row['eligible_to_return']
                    })
            except Exception:
                pass

    if warnings and not data.get('override_cooling_off'):
        db.close()
        return jsonify({'error':'cooling_off_violations','warnings':warnings}), 409

    updated = []
    for mid in ids:
        row = db.execute("SELECT * FROM board_memberships WHERE id=?", (mid,)).fetchone()
        if not row: continue
        terms = json.loads(row['terms'])
        new_term = {'start': new_start, 'end': new_end,
                    'type': term_type, 'assumed': False}
        terms.append(new_term)
        _, _, yrs_at_end, _, etr = calc_consec(terms)
        db.execute(
            "UPDATE board_memberships SET terms=?,is_leaving_at_end=0,"
            "eligible_to_return=?,updated_at=datetime('now') WHERE id=?",
            (json.dumps(terms), etr, mid))
        updated.append(mid)

    db.commit()
    db.close()
    return jsonify({'updated': updated})

# ── DASHBOARD ────────────────────────────────────────────────────
@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    db    = get_db()
    rows  = db.execute(
        "SELECT bm.*, p.name as person_name, p.party "
        "FROM board_memberships bm JOIN persons p ON p.id=bm.person_id").fetchall()
    db.close()

    today   = date.today()
    horizon = today + timedelta(days=183)  # ~6 months

    total = len(rows)
    expiring90 = limited = approaching = vacancies = 0
    upcoming   = []
    vac_list   = []
    party_counts = {}

    for row in rows:
        m = enrich_membership(row_to_dict(row))
        party = m.get('party','Unknown')
        if party not in ('Unknown','Other'):
            party_counts[party] = party_counts.get(party,0) + 1

        if m['eligibility'] in ('limited','finishing'):
            limited += 1
        if m['eligibility'] == 'approaching':
            approaching += 1

        cte = m.get('current_term_end')
        if cte:
            cte_date = date.fromisoformat(cte)
            days = (cte_date - today).days
            if 0 <= days <= 90:
                expiring90 += 1
            if today <= cte_date <= horizon:
                upcoming.append({**m, 'days_until_expiry': days})

            # vacancy detection
            is_leaving = m.get('is_leaving_at_end')
            if is_leaving and cte_date >= today:
                vac_list.append({**m, 'vacancy_type':'Opening at term end'})
                vacancies += 1
            elif m['eligibility'] in ('limited','finishing') and cte_date < today:
                vac_list.append({**m, 'vacancy_type':'Vacancy — term limited'})
                vacancies += 1
            elif cte_date < today and m['eligibility'] not in ('limited','finishing'):
                vac_list.append({**m, 'vacancy_type':'Vacancy — unfilled'})
                vacancies += 1

    upcoming.sort(key=lambda x: x.get('current_term_end',''))

    return jsonify({
        'stats': {
            'total': total,
            'expiring_90': expiring90,
            'limited': limited,
            'approaching': approaching,
            'vacancies': vacancies,
        },
        'party_counts': party_counts,
        'upcoming_expirations': upcoming,
        'vacancies': vac_list,
    })

# ── PUBLIC ROSTER ────────────────────────────────────────────────
@app.route('/api/public/rosters', methods=['GET'])
def public_rosters():
    db   = get_db()
    rows = db.execute(
        "SELECT p.name, bm.board, bm.role "
        "FROM board_memberships bm JOIN persons p ON p.id=bm.person_id "
        "ORDER BY bm.board, p.name").fetchall()
    db.close()
    by_board = {}
    for r in rows:
        b = r['board']
        if b not in by_board:
            by_board[b] = []
        by_board[b].append({'name': r['name'], 'role': r['role']})
    return jsonify(by_board)

if __name__ == '__main__':
    app.run(debug=False)
