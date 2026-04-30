from flask import Flask, render_template, jsonify, request, redirect, make_response, Response
import psycopg2
import psycopg2.extras
import os
import io
import time as _time
import threading
from datetime import datetime, timedelta
from fpdf import FPDF

app = Flask(__name__)

# ───────────── Cache: TTL + stale-while-revalidate ─────────────
_CACHE = {}          # key -> (timestamp, value)
_CACHE_LOCK = threading.Lock()
_REFRESHING = set()  # arka planda yenilenmekte olan anahtarlar

def cache_get(key, ttl):
    """Sadece TAZE değeri döner. Stale ise None."""
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and (_time.monotonic() - hit[0]) < ttl:
            return hit[1]
    return None

def cache_set(key, value):
    with _CACHE_LOCK:
        _CACHE[key] = (_time.monotonic(), value)

def cache_invalidate(prefix=None):
    with _CACHE_LOCK:
        if prefix is None:
            _CACHE.clear()
        else:
            for k in list(_CACHE.keys()):
                if isinstance(k, tuple) and k and k[0] == prefix:
                    _CACHE.pop(k, None)

def _trigger_refresh(key, compute_fn):
    """Arka planda yenile; aynı anahtar için zaten çalışan thread varsa yeni başlatma."""
    with _CACHE_LOCK:
        if key in _REFRESHING:
            return
        _REFRESHING.add(key)
    def _work():
        try:
            result = compute_fn()
            cache_set(key, result)
        except Exception:
            pass
        finally:
            with _CACHE_LOCK:
                _REFRESHING.discard(key)
    threading.Thread(target=_work, daemon=True).start()

def cached_swr(key, ttl, compute_fn):
    """Stale-while-revalidate: taze varsa taze, stale varsa stale (arka planda yenile), hiç yoksa senkron hesapla."""
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit:
        age = _time.monotonic() - hit[0]
        if age < ttl:
            return hit[1]
        _trigger_refresh(key, compute_fn)
        return hit[1]
    result = compute_fn()
    cache_set(key, result)
    return result
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'postgres'),
    'port': os.environ.get('DB_PORT', '5432'),
    'user': os.environ.get('DB_USER', 'wakapi'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'wakapi'),
}

# AI Editor tanımlama — editor adı veya user_agent'ta geçen keywordler
AI_EDITORS = ['cursor', 'windsurf', 'trae']
AI_USERAGENT_KEYWORDS = ['claudecode', 'copilot', 'codeium', 'tabnine', 'supermaven', 'cody']

def get_db():
    return psycopg2.connect(**DB_CONFIG)

def init_db():
    """domain_tags tablosunu oluştur ve varsayılan verileri ekle."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS domain_tags (
                id SERIAL PRIMARY KEY,
                tag_name VARCHAR(50) NOT NULL,
                tag_color VARCHAR(7) NOT NULL,
                tag_icon VARCHAR(10) DEFAULT '🏷️',
                domain_pattern VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_time_adjustments (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                seconds INTEGER NOT NULL,
                adjustment_date DATE NOT NULL DEFAULT CURRENT_DATE,
                reason TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Migration: mevcut kurulumlara adjustment_date ekle
        cur.execute("ALTER TABLE user_time_adjustments ADD COLUMN IF NOT EXISTS adjustment_date DATE")
        cur.execute("UPDATE user_time_adjustments SET adjustment_date = DATE(created_at + INTERVAL '3 hours') WHERE adjustment_date IS NULL")
        cur.execute("ALTER TABLE user_time_adjustments ALTER COLUMN adjustment_date SET NOT NULL")
        cur.execute("ALTER TABLE user_time_adjustments ALTER COLUMN adjustment_date SET DEFAULT CURRENT_DATE")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_time_adj_user ON user_time_adjustments(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_time_adj_date ON user_time_adjustments(user_id, adjustment_date)")
        # İzin tablosu (yıllık, hastalık, resmî tatil)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_leaves (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                leave_type VARCHAR(50) NOT NULL DEFAULT 'annual',
                reason TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_leaves_user ON user_leaves(user_id, start_date, end_date)")
        conn.commit()
        # Heartbeats performans index'leri (admin panel sorguları için)
        # Her biri ayrı transaction — biri patlarsa diğerleri etkilenmesin
        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_hb_user_time ON heartbeats(user_id, time)",
            "CREATE INDEX IF NOT EXISTS idx_hb_time ON heartbeats(time)",
        ):
            try:
                cur2 = conn.cursor()
                cur2.execute(ddl)
                conn.commit()
            except psycopg2.Error:
                conn.rollback()
        # Varsayılan tag'leri ekle (tablo boşsa)
        cur.execute("SELECT COUNT(*) FROM domain_tags")
        if cur.fetchone()[0] == 0:
            defaults = [
                ('İş', '#4ade80', '💼', 'aysbulut.com'),
                ('İş', '#4ade80', '💼', 'github.com'),
                ('İş', '#4ade80', '💼', 'gitlab.com'),
                ('İş', '#4ade80', '💼', 'stackoverflow.com'),
                ('İş', '#4ade80', '💼', 'localhost'),
                ('İş', '#4ade80', '💼', '127.0.0.1'),
                ('İş', '#4ade80', '💼', '172.'),
                ('İş', '#4ade80', '💼', '192.168.'),
                ('İş', '#4ade80', '💼', '10.'),
                ('Eğlence', '#f87171', '🎮', 'youtube.com'),
                ('Eğlence', '#f87171', '🎮', 'netflix.com'),
                ('Eğlence', '#f87171', '🎮', 'twitch.tv'),
                ('Eğlence', '#f87171', '🎮', 'reddit.com'),
                ('Eğlence', '#f87171', '🎮', 'haber7.com'),
                ('Eğlence', '#f87171', '🎮', 'flo.com.tr'),
                ('Sosyal Medya', '#a78bfa', '📱', 'instagram.com'),
                ('Sosyal Medya', '#a78bfa', '📱', 'twitter.com'),
                ('Sosyal Medya', '#a78bfa', '📱', 'x.com'),
                ('Sosyal Medya', '#a78bfa', '📱', 'linkedin.com'),
                ('Sosyal Medya', '#a78bfa', '📱', 'facebook.com'),
                ('AI Araçları', '#f472b6', '🤖', 'chatgpt.com'),
                ('AI Araçları', '#f472b6', '🤖', 'claude.ai'),
                ('AI Araçları', '#f472b6', '🤖', 'gemini.google.com'),
                ('AI Araçları', '#f472b6', '🤖', 'openrouter.ai'),
                ('AI Araçları', '#f472b6', '🤖', 'notebooklm.google.com'),
                ('AI Araçları', '#f472b6', '🤖', 'copilot.microsoft.com'),
                ('Eğitim', '#38bdf8', '📚', 'docs.google.com'),
                ('Eğitim', '#38bdf8', '📚', 'translate.google.com'),
                ('Eğitim', '#38bdf8', '📚', 'drive.google.com'),
                ('Eğitim', '#38bdf8', '📚', 'figma.com'),
                ('Eğitim', '#38bdf8', '📚', 'medium.com'),
            ]
            cur.executemany(
                "INSERT INTO domain_tags (tag_name, tag_color, tag_icon, domain_pattern) VALUES (%s, %s, %s, %s)",
                defaults
            )
        conn.commit()
    finally:
        conn.close()

init_db()

def query(sql, params=None):
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def execute(sql, params=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()

def now_tr():
    return datetime.utcnow() + timedelta(hours=3)

def today_start_utc():
    n = now_tr()
    return n.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)

def week_start_utc():
    n = now_tr()
    return (n - timedelta(days=n.weekday())).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)

def prev_week_start_utc():
    return week_start_utc() - timedelta(days=7)

def _fetch_adjustments(user_id=None, start_date=None, end_date=None):
    """user_time_adjustments'tan filtreli kayıt getirir."""
    parts = []
    params = []
    if user_id is not None:
        parts.append("user_id = %s")
        params.append(user_id)
    if start_date is not None:
        parts.append("adjustment_date >= %s")
        params.append(start_date)
    if end_date is not None:
        parts.append("adjustment_date <= %s")
        params.append(end_date)
    where = (" WHERE " + " AND ".join(parts)) if parts else ""
    return query(f"SELECT user_id, adjustment_date, seconds FROM user_time_adjustments{where}", tuple(params))

def adjustments_by_user_day(start_date=None, end_date=None, user_id=None):
    """{(user_id, 'YYYY-MM-DD'): seconds} döndürür."""
    out = {}
    for r in _fetch_adjustments(user_id, start_date, end_date):
        key = (r['user_id'], r['adjustment_date'].strftime('%Y-%m-%d'))
        out[key] = out.get(key, 0) + (r['seconds'] or 0)
    return out

def adjustments_by_user(start_date=None, end_date=None, user_id=None):
    """{user_id: total_seconds} - verilen tarih aralığı için."""
    out = {}
    for r in _fetch_adjustments(user_id, start_date, end_date):
        out[r['user_id']] = out.get(r['user_id'], 0) + (r['seconds'] or 0)
    return out

def leaves_by_user(start_date, end_date, user_id=None):
    """{user_id: set(date)} — belirli aralıkta izinli olduğu günler."""
    if user_id is not None:
        rows = query("""
            SELECT user_id, start_date, end_date FROM user_leaves
            WHERE user_id=%s AND start_date <= %s AND end_date >= %s
        """, (user_id, end_date, start_date))
    else:
        rows = query("""
            SELECT user_id, start_date, end_date FROM user_leaves
            WHERE start_date <= %s AND end_date >= %s
        """, (end_date, start_date))
    out = {}
    for r in rows:
        uid = r['user_id']
        if uid not in out:
            out[uid] = set()
        d = max(r['start_date'], start_date)
        end = min(r['end_date'], end_date)
        while d <= end:
            out[uid].add(d)
            d += timedelta(days=1)
    return out

def user_on_leave_today(user_id):
    today = now_tr().date()
    rows = query("""
        SELECT 1 FROM user_leaves WHERE user_id=%s AND start_date <= %s AND end_date >= %s LIMIT 1
    """, (user_id, today, today))
    return bool(rows)

def _ai_editor_condition():
    """AI editor SQL koşulu — editor, user_agent veya category bazlı"""
    parts = []
    for e in AI_EDITORS:
        parts.append(f"LOWER(editor) LIKE '%%{e}%%'")
    for k in AI_USERAGENT_KEYWORDS:
        parts.append(f"LOWER(COALESCE(user_agent,'')) LIKE '%%{k}%%'")
    parts.append("LOWER(COALESCE(category,'')) = 'ai coding'")
    return '(' + ' OR '.join(parts) + ')'

# ───────────── SAYFA ROUTE'LARI ─────────────
@app.route('/')
def dashboard():
    return render_template('dashboard.html', page='dashboard')

@app.route('/projects')
def projects_page():
    return render_template('dashboard.html', page='projects')

@app.route('/languages')
def languages_page():
    return render_template('dashboard.html', page='languages')

@app.route('/users')
def users_page():
    return render_template('dashboard.html', page='users')

@app.route('/set-view-user')
def set_view_user():
    return redirect('https://wakapi.aysbulut.com')

# ───────────── REALTIME — Şu an aktif olanlar ─────────────
def _compute_realtime():
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    ts = today_start_utc()
    ai_cond = _ai_editor_condition()
    active = query(f"""
        WITH latest AS (
            SELECT user_id,
                MAX(editor) as editor,
                MAX(project) FILTER (WHERE type='file') as project,
                MAX(language) as language,
                MAX(type) as last_type,
                MAX(entity) as last_entity,
                COUNT(*) as recent_hb,
                MAX(time) as max_time,
                to_char(MAX(time + INTERVAL '3 hours'), 'HH24:MI:SS') as last_seen,
                BOOL_OR({ai_cond}) as is_ai,
                EXTRACT(EPOCH FROM (NOW() - MAX(time)))::int as seconds_ago
            FROM heartbeats
            WHERE time >= %s
            GROUP BY user_id
        ),
        today_stats AS (
            SELECT user_id, time,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s
        ),
        today_agg AS (
            SELECT user_id,
                COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
            FROM today_stats GROUP BY user_id
        )
        SELECT l.*, COALESCE(t.coding_seconds, 0) as today_coding_seconds
        FROM latest l
        LEFT JOIN today_agg t ON l.user_id = t.user_id
        ORDER BY l.max_time DESC
    """, (cutoff, ts))
    return {'active': active, 'count': len(active), 'checked_at': now_tr().strftime('%H:%M:%S')}

@app.route('/api/realtime')
def api_realtime():
    data = cached_swr(('realtime',), 10, _compute_realtime)
    return jsonify(data)

# ───────────── DASHBOARD SUMMARY ─────────────
SUMMARY_TTL = 30  # saniye

@app.route('/api/summary')
def api_summary():
    data = cached_swr(('summary',), SUMMARY_TTL, _compute_summary)
    return jsonify(data)

def _compute_summary():
    ts = today_start_utc()
    ws = week_start_utc()
    pws = prev_week_start_utc()
    ai_cond = _ai_editor_condition()

    users = query("SELECT id, email, created_at FROM users ORDER BY id")

    today_stats = query(f"""
        WITH ordered AS (
            SELECT user_id, time, project, language, editor, user_agent, category,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s
        )
        SELECT user_id, COUNT(*) as heartbeats,
            COUNT(DISTINCT project) as project_count,
            string_agg(DISTINCT NULLIF(project,''),', ') as projects,
            string_agg(DISTINCT NULLIF(language,''),', ') as languages,
            string_agg(DISTINCT NULLIF(editor,''),', ') as editors,
            to_char(MIN(time+INTERVAL '3 hours'),'HH24:MI') as first_active,
            to_char(MAX(time+INTERVAL '3 hours'),'HH24:MI') as last_active,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(category,'')) != 'browsing' AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ide_coding_seconds,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN {ai_cond} AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_coding_seconds
        FROM ordered
        GROUP BY user_id ORDER BY coding_seconds DESC
    """, (ts,))

    week_stats = query(f"""
        WITH ordered AS (
            SELECT user_id, time, project, language, editor, user_agent, category,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s
        )
        SELECT user_id, COUNT(*) as heartbeats,
            COUNT(DISTINCT project) as project_count,
            string_agg(DISTINCT NULLIF(project,''),', ') as projects,
            string_agg(DISTINCT NULLIF(language,''),', ') as languages,
            COUNT(DISTINCT DATE(time+INTERVAL '3 hours')) as active_days,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(category,'')) != 'browsing' AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ide_coding_seconds,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN {ai_cond} AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_coding_seconds
        FROM ordered
        GROUP BY user_id ORDER BY coding_seconds DESC
    """, (ws,))

    # Geçen hafta karşılaştırma
    prev_week_totals = query("""
        WITH ordered AS (
            SELECT user_id, time,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND time < %s
        )
        SELECT COUNT(DISTINCT user_id) as active_users, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered
    """, (pws, ws))

    daily_breakdown = query(f"""
        WITH ordered AS (
            SELECT user_id, time, project, language, editor, user_agent, category,
                LAG(time) OVER (PARTITION BY user_id, DATE(time+INTERVAL '3 hours') ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s
        )
        SELECT user_id, DATE(time+INTERVAL '3 hours') as day, COUNT(*) as heartbeats,
            string_agg(DISTINCT NULLIF(project,''),', ') as projects,
            string_agg(DISTINCT NULLIF(language,''),', ') as languages,
            to_char(MIN(time+INTERVAL '3 hours'),'HH24:MI') as first_active,
            to_char(MAX(time+INTERVAL '3 hours'),'HH24:MI') as last_active,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN {ai_cond} AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_coding_seconds
        FROM ordered
        GROUP BY user_id, DATE(time+INTERVAL '3 hours') ORDER BY day DESC, coding_seconds DESC
    """, (ts - timedelta(days=7),))

    totals = query("SELECT COUNT(DISTINCT user_id) as a, COUNT(*) as h FROM heartbeats WHERE time >= %s", (ts,))
    total_users = len(users)
    active_today = totals[0]['a'] if totals else 0
    hb_today = totals[0]['h'] if totals else 0

    # Adjustments: today, week, daily breakdown — hesaplamalardan ÖNCE yüklenmeli
    today_date = now_tr().date()
    week_start_date = (now_tr() - timedelta(days=now_tr().weekday())).date()
    seven_days_ago = today_date - timedelta(days=7)
    adj_today_by_user = adjustments_by_user(start_date=today_date, end_date=today_date)
    adj_week_by_user = adjustments_by_user(start_date=week_start_date, end_date=today_date)
    adj_daily_map = adjustments_by_user_day(start_date=seven_days_ago, end_date=today_date)

    # Self-comparison — son 4 hafta (bu hafta hariç) ortalama günlük/haftalık
    four_weeks_ago = ws - timedelta(days=28)
    history = query("""
        WITH ordered AS (
            SELECT user_id, time,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND time < %s
        )
        SELECT user_id,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as total_sec,
            COUNT(DISTINCT DATE(time+INTERVAL '3 hours')) as active_days_4w
        FROM ordered
        GROUP BY user_id
    """, (four_weeks_ago, ws))
    # Aktif gün başına ortalama süre (leave/hafta sonu etkisini izole eder)
    avg_daily_by_user = {h['user_id']: (h['total_sec'] / max(h['active_days_4w'], 1)) for h in history}
    # 4 haftalık toplam / 4 (hafta başına ortalama) — bu hafta ile karşılaştırmak için
    avg_weekly_by_user = {h['user_id']: ((h['total_sec'] or 0) / 4.0) for h in history}

    total_coding_today = max(sum(s.get('coding_seconds', 0) or 0 for s in today_stats) + sum(adj_today_by_user.values()), 0)
    total_coding_week = max(sum(s.get('coding_seconds', 0) or 0 for s in week_stats) + sum(adj_week_by_user.values()), 0)
    total_ide_today = sum(s.get('ide_coding_seconds', 0) or 0 for s in today_stats)
    total_ide_week = sum(s.get('ide_coding_seconds', 0) or 0 for s in week_stats)
    total_ai_today = sum(s.get('ai_coding_seconds', 0) or 0 for s in today_stats)
    total_ai_week = sum(s.get('ai_coding_seconds', 0) or 0 for s in week_stats)

    # Delta hesapla
    pw = prev_week_totals[0] if prev_week_totals else {}
    pw_coding = pw.get('coding_seconds', 0) or 0

    today_map = {s['user_id']: s for s in today_stats}
    week_map = {s['user_id']: s for s in week_stats}

    user_list = []
    for u in users:
        uid = u['id']
        t = today_map.get(uid, {})
        w = week_map.get(uid, {})
        today_adj = adj_today_by_user.get(uid, 0)
        week_adj = adj_week_by_user.get(uid, 0)
        today_coding = max((t.get('coding_seconds', 0) or 0) + today_adj, 0)
        week_coding = max((w.get('coding_seconds', 0) or 0) + week_adj, 0)
        ai_pct_today = round((t.get('ai_coding_seconds', 0) or 0) / max(t.get('ide_coding_seconds', 0) or 1, 1) * 100)
        ai_pct_week = round((w.get('ai_coding_seconds', 0) or 0) / max(w.get('ide_coding_seconds', 0) or 1, 1) * 100)
        # Kendi geçmişine göre trend: %sapma (son 4 hafta baz)
        avg_daily = avg_daily_by_user.get(uid, 0)
        avg_weekly = avg_weekly_by_user.get(uid, 0)
        today_trend = None
        week_trend = None
        if avg_daily >= 600:  # en az 10 dk'lık baseline olmadan trend anlamsız
            today_trend = round((today_coding - avg_daily) / avg_daily * 100)
        if avg_weekly >= 3600:  # haftalık baseline en az 1 saat
            week_trend = round((week_coding - avg_weekly) / avg_weekly * 100)
        user_list.append({
            'id': uid,
            'email': u.get('email', ''),
            'today': {
                'heartbeats': t.get('heartbeats', 0),
                'projects': t.get('projects', '-'),
                'languages': t.get('languages', '-'),
                'editors': t.get('editors', '-'),
                'first_active': t.get('first_active', '-'),
                'last_active': t.get('last_active', '-'),
                'project_count': t.get('project_count', 0),
                'coding_seconds': today_coding,
                'adjustment_seconds': today_adj,
                'ai_heartbeats': t.get('ai_heartbeats', 0),
                'ai_coding_seconds': t.get('ai_coding_seconds', 0),
                'ai_pct': ai_pct_today,
                'trend_pct': today_trend,
                'avg_daily_4w': round(avg_daily)
            },
            'week': {
                'heartbeats': w.get('heartbeats', 0),
                'projects': w.get('projects', '-'),
                'languages': w.get('languages', '-'),
                'active_days': w.get('active_days', 0),
                'project_count': w.get('project_count', 0),
                'coding_seconds': week_coding,
                'adjustment_seconds': week_adj,
                'ai_heartbeats': w.get('ai_heartbeats', 0),
                'ai_coding_seconds': w.get('ai_coding_seconds', 0),
                'ai_pct': ai_pct_week,
                'trend_pct': week_trend,
                'avg_weekly_4w': round(avg_weekly)
            }
        })
    user_list.sort(key=lambda x: x['today']['coding_seconds'], reverse=True)

    daily_map = {}
    daily_user_seen = set()
    for d in daily_breakdown:
        day_str = d['day'].strftime('%Y-%m-%d') if d['day'] else ''
        if day_str not in daily_map:
            daily_map[day_str] = []
        adj = adj_daily_map.get((d['user_id'], day_str), 0)
        coding = max((d.get('coding_seconds', 0) or 0) + adj, 0)
        daily_map[day_str].append({
            'user_id': d['user_id'],
            'heartbeats': d['heartbeats'],
            'projects': d.get('projects', '-'),
            'languages': d.get('languages', '-'),
            'first_active': d.get('first_active', '-'),
            'last_active': d.get('last_active', '-'),
            'coding_seconds': coding,
            'adjustment_seconds': adj,
            'ai_heartbeats': d.get('ai_heartbeats', 0),
            'ai_coding_seconds': d.get('ai_coding_seconds', 0)
        })
        daily_user_seen.add((d['user_id'], day_str))

    # Sadece adjustment'ı olan (heartbeat'i olmayan) günler için de satır oluştur
    for (uid_adj, day_str_adj), adj_sec in adj_daily_map.items():
        if (uid_adj, day_str_adj) in daily_user_seen:
            continue
        if day_str_adj not in daily_map:
            daily_map[day_str_adj] = []
        daily_map[day_str_adj].append({
            'user_id': uid_adj,
            'heartbeats': 0,
            'projects': '-', 'languages': '-',
            'first_active': '-', 'last_active': '-',
            'coding_seconds': max(adj_sec, 0),
            'adjustment_seconds': adj_sec,
            'ai_heartbeats': 0, 'ai_coding_seconds': 0
        })

    for day_str in daily_map:
        daily_map[day_str].sort(key=lambda x: x.get('coding_seconds', 0) or 0, reverse=True)

    return {
        'total_users': total_users,
        'active_today': active_today,
        'inactive_today': total_users - active_today,
        'heartbeats_today': hb_today,
        'total_coding_today': total_coding_today,
        'total_coding_week': total_coding_week,
        'total_ai_today': total_ai_today,
        'total_ai_week': total_ai_week,
        'ai_pct_today': round(total_ai_today / max(total_ide_today, 1) * 100),
        'prev_week_coding': pw_coding,
        'week_delta_pct': round((total_coding_week - pw_coding) / max(pw_coding, 1) * 100) if pw_coding > 0 else 0,
        'users': user_list,
        'daily_breakdown': daily_map,
        'generated_at': now_tr().strftime('%d/%m/%Y %H:%M')
    }

# ───────────── USER DETAIL ─────────────
@app.route('/api/user/<user_id>')
def api_user_detail(user_id):
    ts = today_start_utc()
    ai_cond = _ai_editor_condition()

    daily = query(f"""
        WITH ordered AS (
            SELECT user_id, time, project, language, editor, user_agent, category,
                LAG(time) OVER (PARTITION BY DATE(time+INTERVAL '3 hours') ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s
        )
        SELECT DATE(time+INTERVAL '3 hours') as day, COUNT(*) as heartbeats,
            string_agg(DISTINCT NULLIF(project,''),', ') as projects,
            string_agg(DISTINCT NULLIF(language,''),', ') as languages,
            string_agg(DISTINCT NULLIF(editor,''),', ') as editors,
            to_char(MIN(time+INTERVAL '3 hours'),'HH24:MI') as first_active,
            to_char(MAX(time+INTERVAL '3 hours'),'HH24:MI') as last_active,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN {ai_cond} AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_coding_seconds
        FROM ordered
        GROUP BY DATE(time+INTERVAL '3 hours') ORDER BY day DESC
    """, (user_id, ts - timedelta(days=14)))

    projects = query("""
        WITH ordered AS (
            SELECT time, project,
                LAG(time) OVER (PARTITION BY project ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND project != ''
        )
        SELECT NULLIF(project,'') as name, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY project ORDER BY coding_seconds DESC LIMIT 10
    """, (user_id, ts - timedelta(days=30)))

    languages = query("""
        WITH ordered AS (
            SELECT time, language,
                LAG(time) OVER (PARTITION BY language ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND language != ''
        )
        SELECT NULLIF(language,'') as name, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY language ORDER BY coding_seconds DESC LIMIT 10
    """, (user_id, ts - timedelta(days=30)))

    editors = query(f"""
        WITH ordered AS (
            SELECT time, editor, user_agent, category,
                LAG(time) OVER (PARTITION BY editor ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND editor != ''
        )
        SELECT NULLIF(editor,'') as name, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN {ai_cond} AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_coding_seconds
        FROM ordered GROUP BY editor ORDER BY coding_seconds DESC LIMIT 10
    """, (user_id, ts - timedelta(days=30)))

    weekly_trend = query(f"""
        WITH ordered AS (
            SELECT time, DATE(time+INTERVAL '3 hours') as day, editor, user_agent, category,
                LAG(time) OVER (PARTITION BY DATE(time+INTERVAL '3 hours') ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s
        )
        SELECT day, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats
        FROM ordered GROUP BY day ORDER BY day ASC
    """, (user_id, ts - timedelta(days=7)))

    file_activity = query("""
        WITH ordered AS (
            SELECT time, entity, project, language,
                LAG(time) OVER (PARTITION BY entity ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND type='file' AND entity != ''
        )
        SELECT entity, project, language, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY entity, project, language ORDER BY coding_seconds DESC LIMIT 30
    """, (user_id, ts - timedelta(days=7)))

    domain_activity = query("""
        WITH ordered AS (
            SELECT time, entity,
                LAG(time) OVER (PARTITION BY entity ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND type IN ('domain','url') AND entity != ''
        )
        SELECT entity, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY entity ORDER BY coding_seconds DESC LIMIT 30
    """, (user_id, ts - timedelta(days=7)))

    hourly = query("SELECT EXTRACT(HOUR FROM time+INTERVAL '3 hours')::int as hour, COUNT(*) as heartbeats FROM heartbeats WHERE user_id=%s AND time >= %s GROUP BY EXTRACT(HOUR FROM time+INTERVAL '3 hours') ORDER BY hour", (user_id, ts))

    totals = query("SELECT COUNT(*) as total_heartbeats, COUNT(DISTINCT project) as total_projects, COUNT(DISTINCT language) as total_languages, COUNT(DISTINCT DATE(time+INTERVAL '3 hours')) as total_active_days, to_char(MIN(time+INTERVAL '3 hours'),'DD/MM/YYYY') as first_seen, to_char(MAX(time+INTERVAL '3 hours'),'DD/MM/YYYY HH24:MI') as last_seen FROM heartbeats WHERE user_id=%s", (user_id,))

    user_info = query("SELECT email, api_key FROM users WHERE id=%s", (user_id,))

    # Makine istatistikleri
    machines = query("""
        WITH ordered AS (
            SELECT time, machine, operating_system,
                LAG(time) OVER (PARTITION BY machine ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND machine != ''
        )
        SELECT machine, MAX(operating_system) as os, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY machine ORDER BY coding_seconds DESC LIMIT 10
    """, (user_id, ts - timedelta(days=30)))

    # 90 günlük heatmap
    heatmap = query("""
        SELECT DATE(time+INTERVAL '3 hours') as day, COUNT(*) as heartbeats
        FROM heartbeats WHERE user_id=%s AND time >= %s
        GROUP BY DATE(time+INTERVAL '3 hours') ORDER BY day
    """, (user_id, ts - timedelta(days=90)))

    # AI coding toplam
    ai_totals = query(f"""
        WITH ordered AS (
            SELECT time,
                LAG(time) OVER (ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND {ai_cond}
        )
        SELECT COUNT(*) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_coding_seconds
        FROM ordered
    """, (user_id, ts - timedelta(days=30)))

    hours_full = [0] * 24
    for h in hourly:
        if h['hour'] is not None:
            hours_full[int(h['hour'])] = h['heartbeats']

    # Adjustments for this user by date
    user_adj_by_day = adjustments_by_user_day(user_id=user_id)

    daily_list = []
    daily_seen_days = set()
    for d in daily:
        day_str = d['day'].strftime('%Y-%m-%d') if d['day'] else ''
        adj = user_adj_by_day.get((user_id, day_str), 0)
        daily_list.append({
            'day': day_str,
            'heartbeats': d['heartbeats'],
            'projects': d.get('projects', '-'),
            'languages': d.get('languages', '-'),
            'editors': d.get('editors', '-'),
            'first_active': d.get('first_active', '-'),
            'last_active': d.get('last_active', '-'),
            'coding_seconds': max((d.get('coding_seconds', 0) or 0) + adj, 0),
            'adjustment_seconds': adj,
            'ai_heartbeats': d.get('ai_heartbeats', 0),
            'ai_coding_seconds': d.get('ai_coding_seconds', 0)
        })
        daily_seen_days.add(day_str)
    # Sadece ayar kaydı olan günler için de satır ekle
    for (u_adj, day_str), adj_sec in user_adj_by_day.items():
        if u_adj != user_id or day_str in daily_seen_days:
            continue
        daily_list.append({
            'day': day_str, 'heartbeats': 0,
            'projects': '-', 'languages': '-', 'editors': '-',
            'first_active': '-', 'last_active': '-',
            'coding_seconds': max(adj_sec, 0),
            'adjustment_seconds': adj_sec,
            'ai_heartbeats': 0, 'ai_coding_seconds': 0
        })
    daily_list.sort(key=lambda x: x['day'], reverse=True)

    weekly_trend_out = []
    for w in weekly_trend:
        day_str = w['day'].strftime('%Y-%m-%d')
        adj = user_adj_by_day.get((user_id, day_str), 0)
        weekly_trend_out.append({
            'day': day_str,
            'heartbeats': w['heartbeats'],
            'coding_seconds': max((w['coding_seconds'] or 0) + adj, 0),
            'adjustment_seconds': adj,
            'ai_heartbeats': w.get('ai_heartbeats', 0)
        })

    adj_totals = query("SELECT COALESCE(SUM(seconds),0)::bigint as total FROM user_time_adjustments WHERE user_id=%s", (user_id,))
    adjustment_seconds = int(adj_totals[0]['total']) if adj_totals else 0

    return jsonify({
        'user_id': user_id,
        'email': user_info[0]['email'] if user_info else '',
        'api_key': user_info[0]['api_key'] if user_info else '',
        'adjustment_seconds': adjustment_seconds,
        'totals': totals[0] if totals else {},
        'daily': daily_list,
        'editors': [{'name': e['name'], 'heartbeats': e['heartbeats'], 'coding_seconds': e['coding_seconds'], 'ai_heartbeats': e.get('ai_heartbeats', 0), 'ai_coding_seconds': e.get('ai_coding_seconds', 0)} for e in editors if e['name']],
        'weekly_trend': weekly_trend_out,
        'file_activity': [{'entity': f['entity'], 'project': f.get('project', ''), 'language': f.get('language', ''), 'heartbeats': f['heartbeats'], 'coding_seconds': f['coding_seconds']} for f in file_activity],
        'domain_activity': [{'entity': d['entity'], 'heartbeats': d['heartbeats'], 'coding_seconds': d['coding_seconds']} for d in domain_activity],
        'projects': [{'name': p['name'], 'heartbeats': p['heartbeats'], 'coding_seconds': p.get('coding_seconds', 0)} for p in projects if p['name']],
        'languages': [{'name': l['name'], 'heartbeats': l['heartbeats'], 'coding_seconds': l.get('coding_seconds', 0)} for l in languages if l['name']],
        'hourly': hours_full,
        'machines': [{'name': m['machine'], 'os': m.get('os', ''), 'heartbeats': m['heartbeats'], 'coding_seconds': m['coding_seconds']} for m in machines],
        'heatmap': [{'day': h['day'].strftime('%Y-%m-%d'), 'count': h['heartbeats']} for h in heatmap],
        'ai_totals': ai_totals[0] if ai_totals else {'ai_heartbeats': 0, 'ai_coding_seconds': 0}
    })

# ───────────── TIME ADJUSTMENTS (Manuel zaman ekle/çıkar) ─────────────
@app.route('/api/user/<user_id>/adjustments', methods=['GET'])
def api_adjustments_list(user_id):
    rows = query("""
        SELECT id, user_id, seconds, reason,
               to_char(adjustment_date, 'YYYY-MM-DD') as adjustment_date,
               to_char(created_at + INTERVAL '3 hours', 'DD/MM/YYYY HH24:MI') as created_at
        FROM user_time_adjustments
        WHERE user_id=%s
        ORDER BY adjustment_date DESC, created_at DESC
    """, (user_id,))
    totals = query("SELECT COALESCE(SUM(seconds),0)::bigint as total FROM user_time_adjustments WHERE user_id=%s", (user_id,))
    total_sec = int(totals[0]['total']) if totals else 0
    return jsonify({'adjustments': rows, 'total_seconds': total_sec})

@app.route('/api/user/<user_id>/adjustments', methods=['POST'])
def api_adjustments_create(user_id):
    data = request.get_json() or {}
    try:
        hours = float(data.get('hours', 0))
        minutes = float(data.get('minutes', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Geçersiz sayı'}), 400
    op = (data.get('op') or 'add').lower()
    if op not in ('add', 'sub'):
        return jsonify({'error': 'op add veya sub olmalı'}), 400
    magnitude_sec = int(round(hours * 3600 + minutes * 60))
    if magnitude_sec <= 0:
        return jsonify({'error': 'Saat veya dakika 0\'dan büyük olmalı'}), 400
    seconds = magnitude_sec if op == 'add' else -magnitude_sec
    reason = (data.get('reason') or '').strip() or None
    # Tarih: YYYY-MM-DD ya da boş (bugün TR)
    date_str = (data.get('adjustment_date') or '').strip()
    if date_str:
        try:
            adj_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Geçersiz tarih formatı (YYYY-MM-DD)'}), 400
    else:
        adj_date = now_tr().date()
    # Kullanıcı gerçekten var mı?
    exists = query("SELECT 1 FROM users WHERE id=%s", (user_id,))
    if not exists:
        return jsonify({'error': 'Kullanıcı bulunamadı'}), 404
    execute("INSERT INTO user_time_adjustments (user_id, seconds, adjustment_date, reason) VALUES (%s, %s, %s, %s)",
            (user_id, seconds, adj_date, reason))
    cache_invalidate()
    return jsonify({'ok': True, 'seconds': seconds, 'adjustment_date': adj_date.strftime('%Y-%m-%d')})

@app.route('/api/adjustments/<int:adj_id>', methods=['DELETE'])
def api_adjustments_delete(adj_id):
    execute("DELETE FROM user_time_adjustments WHERE id=%s", (adj_id,))
    cache_invalidate()
    return jsonify({'ok': True})

# ───────────── USER LEAVES (İzin / Tatil) ─────────────
LEAVE_TYPES = {'annual': 'Yıllık İzin', 'sick': 'Hastalık', 'holiday': 'Resmî Tatil', 'other': 'Diğer'}

@app.route('/api/leaves', methods=['GET'])
def api_leaves_list():
    user_id = request.args.get('user_id')
    if user_id:
        rows = query("""
            SELECT id, user_id, leave_type, reason,
                to_char(start_date, 'YYYY-MM-DD') as start_date,
                to_char(end_date, 'YYYY-MM-DD') as end_date,
                (end_date - start_date + 1) as days,
                to_char(created_at + INTERVAL '3 hours', 'DD/MM/YYYY HH24:MI') as created_at
            FROM user_leaves WHERE user_id=%s
            ORDER BY start_date DESC
        """, (user_id,))
    else:
        rows = query("""
            SELECT id, user_id, leave_type, reason,
                to_char(start_date, 'YYYY-MM-DD') as start_date,
                to_char(end_date, 'YYYY-MM-DD') as end_date,
                (end_date - start_date + 1) as days,
                to_char(created_at + INTERVAL '3 hours', 'DD/MM/YYYY HH24:MI') as created_at
            FROM user_leaves
            ORDER BY start_date DESC
        """)
    return jsonify({'leaves': rows, 'types': LEAVE_TYPES})

@app.route('/api/user/<user_id>/leaves', methods=['GET'])
def api_user_leaves_list(user_id):
    rows = query("""
        SELECT id, user_id, leave_type, reason,
            to_char(start_date, 'YYYY-MM-DD') as start_date,
            to_char(end_date, 'YYYY-MM-DD') as end_date,
            (end_date - start_date + 1) as days,
            to_char(created_at + INTERVAL '3 hours', 'DD/MM/YYYY HH24:MI') as created_at
        FROM user_leaves WHERE user_id=%s
        ORDER BY start_date DESC
    """, (user_id,))
    total_days = sum(r['days'] for r in rows)
    return jsonify({'leaves': rows, 'total_days': total_days, 'types': LEAVE_TYPES})

@app.route('/api/user/<user_id>/leaves', methods=['POST'])
def api_user_leaves_create(user_id):
    data = request.get_json() or {}
    start = (data.get('start_date') or '').strip()
    end = (data.get('end_date') or start).strip()
    leave_type = (data.get('leave_type') or 'annual').strip()
    reason = (data.get('reason') or '').strip() or None
    if not start:
        return jsonify({'error': 'start_date zorunlu'}), 400
    if leave_type not in LEAVE_TYPES:
        return jsonify({'error': 'Geçersiz izin türü'}), 400
    try:
        sd = datetime.strptime(start, '%Y-%m-%d').date()
        ed = datetime.strptime(end, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Tarih formatı YYYY-MM-DD olmalı'}), 400
    if ed < sd:
        return jsonify({'error': 'Bitiş tarihi başlangıçtan küçük olamaz'}), 400
    exists = query("SELECT 1 FROM users WHERE id=%s", (user_id,))
    if not exists:
        return jsonify({'error': 'Kullanıcı bulunamadı'}), 404
    execute("""
        INSERT INTO user_leaves (user_id, start_date, end_date, leave_type, reason)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, sd, ed, leave_type, reason))
    cache_invalidate()
    return jsonify({'ok': True, 'days': (ed - sd).days + 1})

@app.route('/api/leaves/<int:leave_id>', methods=['DELETE'])
def api_leaves_delete(leave_id):
    execute("DELETE FROM user_leaves WHERE id=%s", (leave_id,))
    cache_invalidate()
    return jsonify({'ok': True})

# ───────────── PROJECTS ─────────────
def _compute_projects(days):
    ts = today_start_utc() - timedelta(days=days)
    ai_cond = _ai_editor_condition()
    projects = query(f"""
        WITH ordered AS (
            SELECT time, project, user_id, language, editor, user_agent, category,
                LAG(time) OVER (PARTITION BY project ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND project != ''
        )
        SELECT NULLIF(project,'') as name, COUNT(*) as heartbeats, COUNT(DISTINCT user_id) as user_count,
            string_agg(DISTINCT user_id, ', ') as users, string_agg(DISTINCT NULLIF(language,''), ', ') as languages,
            to_char(MIN(time+INTERVAL '3 hours'),'DD/MM/YYYY') as first_activity,
            to_char(MAX(time+INTERVAL '3 hours'),'DD/MM/YYYY HH24:MI') as last_activity,
            COUNT(DISTINCT DATE(time+INTERVAL '3 hours')) as active_days,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY project ORDER BY coding_seconds DESC
    """, (ts,))
    return {'projects': [p for p in projects if p['name']], 'generated_at': now_tr().strftime('%d/%m/%Y %H:%M')}

@app.route('/api/projects')
def api_projects():
    days = int(request.args.get('days', 30))
    data = cached_swr(('projects', days), 90, lambda: _compute_projects(days))
    return jsonify(data)

# ───────────── LANGUAGES ─────────────
def _compute_languages(days):
    ts = today_start_utc() - timedelta(days=days)
    languages = query("""
        WITH ordered AS (
            SELECT time, language, user_id, project,
                LAG(time) OVER (PARTITION BY language ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND language != ''
        )
        SELECT NULLIF(language,'') as name, COUNT(*) as heartbeats, COUNT(DISTINCT user_id) as user_count,
            string_agg(DISTINCT user_id, ', ') as users, string_agg(DISTINCT NULLIF(project,''), ', ') as projects,
            COUNT(DISTINCT DATE(time+INTERVAL '3 hours')) as active_days,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY language ORDER BY coding_seconds DESC
    """, (ts,))
    user_langs = query("""
        WITH ordered AS (
            SELECT time, user_id, language,
                LAG(time) OVER (PARTITION BY user_id, language ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND language != ''
        )
        SELECT user_id, NULLIF(language,'') as language, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY user_id, language ORDER BY user_id, coding_seconds DESC
    """, (ts,))
    user_lang_map = {}
    for ul in user_langs:
        uid = ul['user_id']
        if uid not in user_lang_map:
            user_lang_map[uid] = []
        user_lang_map[uid].append({'name': ul['language'], 'heartbeats': ul['heartbeats'], 'coding_seconds': ul.get('coding_seconds', 0)})
    return {'languages': [l for l in languages if l['name']], 'user_languages': user_lang_map, 'generated_at': now_tr().strftime('%d/%m/%Y %H:%M')}

@app.route('/api/languages')
def api_languages():
    days = int(request.args.get('days', 30))
    data = cached_swr(('languages', days), 90, lambda: _compute_languages(days))
    return jsonify(data)

# ───────────── USERS ─────────────
def _compute_users():
    ai_cond = _ai_editor_condition()
    users = query(f"""
        WITH ordered AS (
            SELECT user_id, time, project, language,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats
        ),
        total_agg AS (
            SELECT user_id, COUNT(*) as total_hb,
                COUNT(DISTINCT project) as total_projects,
                COUNT(DISTINCT language) as total_languages,
                COUNT(DISTINCT DATE(time)) as active_days,
                to_char(MIN(time+INTERVAL '3 hours'),'DD/MM/YYYY') as first_seen,
                to_char(MAX(time+INTERVAL '3 hours'),'DD/MM/YYYY HH24:MI') as last_seen,
                COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::bigint as total_coding_seconds
            FROM ordered GROUP BY user_id
        )
        SELECT u.id, u.email, u.created_at,
            COALESCE(h.total_hb, 0) as total_heartbeats, COALESCE(h.total_projects, 0) as total_projects,
            COALESCE(h.total_languages, 0) as total_languages, COALESCE(h.active_days, 0) as active_days,
            COALESCE(h.total_coding_seconds, 0) as total_coding_seconds,
            h.first_seen, h.last_seen,
            COALESCE(t.today_hb, 0) as today_heartbeats, COALESCE(w.week_hb, 0) as week_heartbeats,
            COALESCE(ai.ai_hb, 0) as ai_heartbeats_30d
        FROM users u
        LEFT JOIN total_agg h ON u.id = h.user_id
        LEFT JOIN (SELECT user_id, COUNT(*) as today_hb FROM heartbeats WHERE time >= %s GROUP BY user_id) t ON u.id = t.user_id
        LEFT JOIN (SELECT user_id, COUNT(*) as week_hb FROM heartbeats WHERE time >= %s GROUP BY user_id) w ON u.id = w.user_id
        LEFT JOIN (SELECT user_id, COUNT(*) as ai_hb FROM heartbeats WHERE time >= %s AND {ai_cond} GROUP BY user_id) ai ON u.id = ai.user_id
        ORDER BY COALESCE(h.total_coding_seconds, 0) DESC
    """, (today_start_utc(), week_start_utc(), today_start_utc() - timedelta(days=30)))
    return {'users': users, 'generated_at': now_tr().strftime('%d/%m/%Y %H:%M')}

@app.route('/api/users')
def api_users():
    data = cached_swr(('users',), 90, _compute_users)
    return jsonify(data)

# ───────────── TAGS CRUD ─────────────
@app.route('/api/tags')
def api_tags():
    tags = query("SELECT * FROM domain_tags ORDER BY tag_name, domain_pattern")
    groups = {}
    for t in tags:
        name = t['tag_name']
        if name not in groups:
            groups[name] = {'tag_name': name, 'tag_color': t['tag_color'], 'tag_icon': t['tag_icon'], 'patterns': []}
        groups[name]['patterns'].append({'id': t['id'], 'domain_pattern': t['domain_pattern']})
    return jsonify({'tags': list(groups.values())})

@app.route('/api/tags', methods=['POST'])
def api_tags_create():
    data = request.get_json()
    tag_name = (data.get('tag_name') or '').strip()
    tag_color = (data.get('tag_color') or '#94a3b8').strip()
    tag_icon = (data.get('tag_icon') or '🏷️').strip()
    domain_pattern = (data.get('domain_pattern') or '').strip()
    if not tag_name or not domain_pattern:
        return jsonify({'error': 'tag_name ve domain_pattern gerekli'}), 400
    execute("INSERT INTO domain_tags (tag_name, tag_color, tag_icon, domain_pattern) VALUES (%s, %s, %s, %s)",
            (tag_name, tag_color, tag_icon, domain_pattern))
    return jsonify({'ok': True})

@app.route('/api/tags/<int:tag_id>', methods=['PUT'])
def api_tags_update(tag_id):
    data = request.get_json()
    tag_name = (data.get('tag_name') or '').strip()
    tag_color = (data.get('tag_color') or '').strip()
    tag_icon = (data.get('tag_icon') or '').strip()
    domain_pattern = (data.get('domain_pattern') or '').strip()
    if not tag_name or not domain_pattern:
        return jsonify({'error': 'tag_name ve domain_pattern gerekli'}), 400
    execute("UPDATE domain_tags SET tag_name=%s, tag_color=%s, tag_icon=%s, domain_pattern=%s WHERE id=%s",
            (tag_name, tag_color, tag_icon, domain_pattern, tag_id))
    return jsonify({'ok': True})

@app.route('/api/tags/<int:tag_id>', methods=['DELETE'])
def api_tags_delete(tag_id):
    execute("DELETE FROM domain_tags WHERE id=%s", (tag_id,))
    return jsonify({'ok': True})

@app.route('/api/untagged-domains')
def api_untagged_domains():
    """Son 30 günde görülüp hiçbir tag'e uymayan unique domain'ler."""
    ts = today_start_utc() - timedelta(days=30)
    domains = query("SELECT DISTINCT entity FROM heartbeats WHERE type IN ('domain','url') AND entity != '' AND time >= %s ORDER BY entity", (ts,))
    tags = query("SELECT domain_pattern FROM domain_tags")
    patterns = [t['domain_pattern'].lower() for t in tags]
    untagged = []
    for d in domains:
        entity = (d['entity'] or '').lower()
        if not any(p in entity for p in patterns):
            untagged.append(d['entity'])
    return jsonify({'domains': untagged})

# ───────────── USER SITES ─────────────
@app.route('/api/user/<user_id>/sites')
def api_user_sites(user_id):
    days = int(request.args.get('days', 7))
    ts = today_start_utc() - timedelta(days=days)

    domains = query("""
        WITH ordered AS (
            SELECT time, entity,
                LAG(time) OVER (PARTITION BY entity ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND type IN ('domain','url') AND entity != ''
        )
        SELECT entity, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as seconds,
            to_char(MIN(time+INTERVAL '3 hours'),'DD/MM HH24:MI') as first_visit,
            to_char(MAX(time+INTERVAL '3 hours'),'DD/MM HH24:MI') as last_visit,
            COUNT(DISTINCT DATE(time+INTERVAL '3 hours')) as visit_days
        FROM ordered GROUP BY entity ORDER BY seconds DESC, heartbeats DESC
    """, (user_id, ts))

    daily = query("""
        WITH ordered AS (
            SELECT time, entity, DATE(time+INTERVAL '3 hours') as day,
                LAG(time) OVER (PARTITION BY entity, DATE(time+INTERVAL '3 hours') ORDER BY time) as prev_time
            FROM heartbeats WHERE user_id=%s AND time >= %s AND type IN ('domain','url') AND entity != ''
        )
        SELECT day, entity, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as seconds
        FROM ordered GROUP BY day, entity ORDER BY day DESC, seconds DESC
    """, (user_id, ts))

    # Tag matching
    tags = query("SELECT * FROM domain_tags ORDER BY tag_name")
    tag_map = {}  # pattern -> tag info
    for t in tags:
        tag_map[t['domain_pattern'].lower()] = {
            'tag_name': t['tag_name'], 'tag_color': t['tag_color'], 'tag_icon': t['tag_icon']
        }

    def match_tag(entity):
        entity_lower = (entity or '').lower()
        for pattern, info in tag_map.items():
            if pattern in entity_lower:
                return info
        return None

    # Tag istatistikleri
    tag_stats = {}
    domain_list = []
    for d in domains:
        tag = match_tag(d['entity'])
        tag_name = tag['tag_name'] if tag else 'Etiketlenmemiş'
        if tag_name not in tag_stats:
            tag_stats[tag_name] = {
                'tag_name': tag_name,
                'tag_color': tag['tag_color'] if tag else '#475569',
                'tag_icon': tag['tag_icon'] if tag else '🏷️',
                'seconds': 0, 'heartbeats': 0
            }
        tag_stats[tag_name]['seconds'] += d['seconds'] or 0
        tag_stats[tag_name]['heartbeats'] += d['heartbeats'] or 0
        domain_list.append({
            'entity': d['entity'],
            'heartbeats': d['heartbeats'],
            'seconds': d['seconds'] or 0,
            'first_visit': d.get('first_visit', '-'),
            'last_visit': d.get('last_visit', '-'),
            'visit_days': d.get('visit_days', 0),
            'tag': tag
        })

    # Günlük breakdown
    daily_map = {}
    for d in daily:
        day_str = d['day'].strftime('%Y-%m-%d') if d['day'] else ''
        if day_str not in daily_map:
            daily_map[day_str] = []
        daily_map[day_str].append({
            'entity': d['entity'],
            'heartbeats': d['heartbeats'],
            'seconds': d['seconds'] or 0,
            'tag': match_tag(d['entity'])
        })

    return jsonify({
        'domains': domain_list,
        'tag_stats': list(tag_stats.values()),
        'daily': daily_map
    })

# ───────────── ETIKETLER SAYFASI ─────────────
@app.route('/tags')
def tags_page():
    return render_template('dashboard.html', page='tags')

@app.route('/reports')
def reports_page():
    return render_template('dashboard.html', page='reports')

# ───────────── RAPORLAR ─────────────
@app.route('/api/reports/daily')
def api_report_daily():
    """Günlük detaylı rapor."""
    date_str = request.args.get('date', now_tr().strftime('%Y-%m-%d'))
    try:
        report_date = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Geçersiz tarih formatı'}), 400

    day_start = report_date.replace(hour=0, minute=0, second=0) - timedelta(hours=3)
    day_end = day_start + timedelta(days=1)
    ai_cond = _ai_editor_condition()

    # Kullanıcı bazlı istatistikler
    user_stats = query(f"""
        WITH ordered AS (
            SELECT user_id, time, project, language, editor, user_agent, category, type, entity,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND time < %s
        )
        SELECT user_id, COUNT(*) as heartbeats,
            COUNT(DISTINCT NULLIF(project,'')) as project_count,
            string_agg(DISTINCT NULLIF(project,''), ', ') as projects,
            string_agg(DISTINCT NULLIF(language,''), ', ') as languages,
            string_agg(DISTINCT NULLIF(editor,''), ', ') as editors,
            to_char(MIN(time+INTERVAL '3 hours'), 'HH24:MI') as first_active,
            to_char(MAX(time+INTERVAL '3 hours'), 'HH24:MI') as last_active,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(category,'')) != 'browsing' AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ide_coding_seconds,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(category,'')) = 'browsing' AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as browsing_seconds,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN {ai_cond} AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_coding_seconds,
            COUNT(*) FILTER (WHERE type = 'file') as file_heartbeats,
            COUNT(*) FILTER (WHERE type IN ('domain','url')) as domain_heartbeats
        FROM ordered
        GROUP BY user_id ORDER BY coding_seconds DESC
    """, (day_start, day_end))

    # Tag bazlı site kullanımı
    tags_data = query("SELECT * FROM domain_tags")
    tag_patterns = [(t['domain_pattern'].lower(), t) for t in tags_data]

    site_stats_per_user = query("""
        WITH ordered AS (
            SELECT user_id, entity,
                time, LAG(time) OVER (PARTITION BY user_id, entity ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND time < %s AND type IN ('domain','url') AND entity != ''
        )
        SELECT user_id, entity, COUNT(*) as heartbeats,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as seconds
        FROM ordered GROUP BY user_id, entity ORDER BY user_id, seconds DESC
    """, (day_start, day_end))

    # Site verilerini tag bazlı grupla
    user_site_tags = {}
    for s in site_stats_per_user:
        uid = s['user_id']
        if uid not in user_site_tags:
            user_site_tags[uid] = {}
        entity_lower = (s['entity'] or '').lower()
        tag_name = 'Etiketlenmemiş'
        for pattern, tag in tag_patterns:
            if pattern in entity_lower:
                tag_name = tag['tag_name']
                break
        if tag_name not in user_site_tags[uid]:
            user_site_tags[uid][tag_name] = {'seconds': 0, 'heartbeats': 0, 'sites': []}
        user_site_tags[uid][tag_name]['seconds'] += s['seconds'] or 0
        user_site_tags[uid][tag_name]['heartbeats'] += s['heartbeats']
        user_site_tags[uid][tag_name]['sites'].append({
            'entity': s['entity'], 'heartbeats': s['heartbeats'], 'seconds': s['seconds'] or 0
        })

    # Bu güne ait ayarları getir
    report_day = report_date.date()
    daily_adj = adjustments_by_user(start_date=report_day, end_date=report_day)

    # Verimlilik skoru hesapla
    users_report = []
    seen_uids = set()
    for u in user_stats:
        uid = u['user_id']
        seen_uids.add(uid)
        ide_sec = u.get('ide_coding_seconds', 0) or 0
        browsing_sec = u.get('browsing_seconds', 0) or 0
        adj = daily_adj.get(uid, 0)
        ide_sec_adj = max(ide_sec + adj, 0)
        total_active = ide_sec_adj + browsing_sec
        productivity = round(ide_sec_adj / max(total_active, 1) * 100) if total_active > 0 else 0
        ai_pct = round((u.get('ai_coding_seconds', 0) or 0) / max(ide_sec_adj, 1) * 100) if ide_sec_adj > 0 else 0

        users_report.append({
            'user_id': uid,
            'heartbeats': u['heartbeats'],
            'projects': u.get('projects', '-'),
            'languages': u.get('languages', '-'),
            'editors': u.get('editors', '-'),
            'first_active': u.get('first_active', '-'),
            'last_active': u.get('last_active', '-'),
            'coding_seconds': max((u.get('coding_seconds', 0) or 0) + adj, 0),
            'ide_coding_seconds': ide_sec_adj,
            'browsing_seconds': browsing_sec,
            'adjustment_seconds': adj,
            'ai_coding_seconds': u.get('ai_coding_seconds', 0) or 0,
            'ai_pct': ai_pct,
            'productivity': productivity,
            'file_heartbeats': u.get('file_heartbeats', 0),
            'domain_heartbeats': u.get('domain_heartbeats', 0),
            'site_tags': user_site_tags.get(uid, {})
        })
    # Sadece adjustment olan kullanıcılar için de satır
    for uid_a, adj_s in daily_adj.items():
        if uid_a in seen_uids:
            continue
        users_report.append({
            'user_id': uid_a, 'heartbeats': 0,
            'projects': '-', 'languages': '-', 'editors': '-',
            'first_active': '-', 'last_active': '-',
            'coding_seconds': max(adj_s, 0),
            'ide_coding_seconds': max(adj_s, 0),
            'browsing_seconds': 0,
            'adjustment_seconds': adj_s,
            'ai_coding_seconds': 0, 'ai_pct': 0,
            'productivity': 100 if adj_s > 0 else 0,
            'file_heartbeats': 0, 'domain_heartbeats': 0,
            'site_tags': {}
        })
    users_report.sort(key=lambda x: x['ide_coding_seconds'], reverse=True)

    total_coding = sum(u['ide_coding_seconds'] for u in users_report)
    total_browsing = sum(u['browsing_seconds'] for u in users_report)
    total_ai = sum(u['ai_coding_seconds'] for u in users_report)
    avg_productivity = round(sum(u['productivity'] for u in users_report) / max(len(users_report), 1))

    return jsonify({
        'date': date_str,
        'total_users': len(users_report),
        'total_coding_seconds': total_coding,
        'total_browsing_seconds': total_browsing,
        'total_ai_seconds': total_ai,
        'avg_productivity': avg_productivity,
        'team_productivity': round(total_coding / max(total_coding + total_browsing, 1) * 100),
        'users': users_report,
        'generated_at': now_tr().strftime('%d/%m/%Y %H:%M')
    })

def _compute_range_report(ws, we):
    """Belirli bir tarih aralığı için özet rapor üretir (haftalık ve custom için)."""
    ai_cond = _ai_editor_condition()

    user_stats = query(f"""
        WITH ordered AS (
            SELECT user_id, time, project, language, editor, user_agent, category,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND time < %s
        )
        SELECT user_id, COUNT(*) as heartbeats,
            COUNT(DISTINCT NULLIF(project,'')) as project_count,
            string_agg(DISTINCT NULLIF(project,''), ', ') as projects,
            string_agg(DISTINCT NULLIF(language,''), ', ') as languages,
            string_agg(DISTINCT NULLIF(editor,''), ', ') as editors,
            COUNT(DISTINCT DATE(time+INTERVAL '3 hours')) as active_days,
            to_char(MIN(time+INTERVAL '3 hours'), 'HH24:MI') as first_active,
            to_char(MAX(time+INTERVAL '3 hours'), 'HH24:MI') as last_active,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(category,'')) != 'browsing' AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ide_coding_seconds,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(category,'')) = 'browsing' AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as browsing_seconds,
            COUNT(*) FILTER (WHERE {ai_cond}) as ai_heartbeats,
            COALESCE(SUM(CASE WHEN {ai_cond} AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_coding_seconds
        FROM ordered
        GROUP BY user_id ORDER BY coding_seconds DESC
    """, (ws, we))

    daily_totals = query("""
        WITH ordered AS (
            SELECT DATE(time+INTERVAL '3 hours') as day, time, user_id,
                LAG(time) OVER (PARTITION BY DATE(time+INTERVAL '3 hours') ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s AND time < %s
        )
        SELECT day, COUNT(*) as heartbeats, COUNT(DISTINCT user_id) as active_users,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as coding_seconds
        FROM ordered GROUP BY day ORDER BY day
    """, (ws, we))

    # Aralığa ait ayarları getir (ws/we UTC datetime; TR gününe çevir)
    range_start_date = (ws + timedelta(hours=3)).date()
    range_end_date = (we + timedelta(hours=3) - timedelta(seconds=1)).date()
    range_adj = adjustments_by_user(start_date=range_start_date, end_date=range_end_date)
    range_adj_daily = adjustments_by_user_day(start_date=range_start_date, end_date=range_end_date)

    users_report = []
    seen_uids = set()
    for u in user_stats:
        uid = u['user_id']
        seen_uids.add(uid)
        ide_sec = u.get('ide_coding_seconds', 0) or 0
        browsing_sec = u.get('browsing_seconds', 0) or 0
        adj = range_adj.get(uid, 0)
        ide_sec_adj = max(ide_sec + adj, 0)
        total_active = ide_sec_adj + browsing_sec
        productivity = round(ide_sec_adj / max(total_active, 1) * 100) if total_active > 0 else 0
        users_report.append({
            'user_id': uid,
            'heartbeats': u['heartbeats'],
            'projects': u.get('projects', '-'),
            'languages': u.get('languages', '-'),
            'editors': u.get('editors', '-'),
            'active_days': u.get('active_days', 0),
            'first_active': u.get('first_active', '-'),
            'last_active': u.get('last_active', '-'),
            'coding_seconds': max((u.get('coding_seconds', 0) or 0) + adj, 0),
            'ide_coding_seconds': ide_sec_adj,
            'browsing_seconds': browsing_sec,
            'adjustment_seconds': adj,
            'ai_coding_seconds': u.get('ai_coding_seconds', 0) or 0,
            'productivity': productivity,
            'project_count': u.get('project_count', 0)
        })
    # Sadece adjustment olan kullanıcılar
    for uid_a, adj_s in range_adj.items():
        if uid_a in seen_uids:
            continue
        users_report.append({
            'user_id': uid_a, 'heartbeats': 0,
            'projects': '-', 'languages': '-', 'editors': '-',
            'active_days': 0, 'first_active': '-', 'last_active': '-',
            'coding_seconds': max(adj_s, 0),
            'ide_coding_seconds': max(adj_s, 0),
            'browsing_seconds': 0,
            'adjustment_seconds': adj_s,
            'ai_coding_seconds': 0,
            'productivity': 100 if adj_s > 0 else 0,
            'project_count': 0
        })
    users_report.sort(key=lambda x: x['ide_coding_seconds'], reverse=True)

    total_coding = sum(u['ide_coding_seconds'] for u in users_report)
    total_browsing = sum(u['browsing_seconds'] for u in users_report)
    total_ai = sum(u['ai_coding_seconds'] for u in users_report)

    # Günlük totallere adjustment ekle (toplam gün bazında)
    day_adj_totals = {}
    for (uid_a, day_str), sec in range_adj_daily.items():
        day_adj_totals[day_str] = day_adj_totals.get(day_str, 0) + sec

    daily_totals_out = []
    seen_days = set()
    for d in daily_totals:
        day_str = d['day'].strftime('%Y-%m-%d')
        adj = day_adj_totals.get(day_str, 0)
        daily_totals_out.append({
            'day': day_str,
            'heartbeats': d['heartbeats'],
            'active_users': d['active_users'],
            'coding_seconds': max((d['coding_seconds'] or 0) + adj, 0)
        })
        seen_days.add(day_str)
    for day_str_a, adj in day_adj_totals.items():
        if day_str_a in seen_days:
            continue
        daily_totals_out.append({
            'day': day_str_a, 'heartbeats': 0, 'active_users': 0,
            'coding_seconds': max(adj, 0)
        })
    daily_totals_out.sort(key=lambda x: x['day'])

    return {
        'total_users': len(users_report),
        'total_coding_seconds': total_coding,
        'total_browsing_seconds': total_browsing,
        'total_ai_seconds': total_ai,
        'team_productivity': round(total_coding / max(total_coding + total_browsing, 1) * 100),
        'users': users_report,
        'daily_totals': daily_totals_out,
    }


@app.route('/api/reports/weekly')
def api_report_weekly():
    """Haftalık özet rapor."""
    try:
        week_str = request.args.get('week_start')
        if week_str:
            try:
                base = datetime.strptime(week_str, '%Y-%m-%d')
            except ValueError:
                return jsonify({'error': 'Geçersiz tarih'}), 400
            # Haftanın başına (Pazartesi) hizala
            monday = base - timedelta(days=base.weekday())
            ws = monday.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)
        else:
            ws = week_start_utc()
        we = ws + timedelta(days=7)

        result = _compute_range_report(ws, we)
        result['week_start'] = (ws + timedelta(hours=3)).strftime('%Y-%m-%d')
        result['week_end'] = (we + timedelta(hours=3) - timedelta(days=1)).strftime('%Y-%m-%d')
        result['generated_at'] = now_tr().strftime('%d/%m/%Y %H:%M')
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Haftalık rapor hatası: {str(e)}'}), 500


@app.route('/api/reports/range')
def api_report_range():
    """Özel tarih aralığı raporu."""
    try:
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        if not start_str or not end_str:
            return jsonify({'error': 'start ve end parametreleri gerekli'}), 400
        try:
            start_dt = datetime.strptime(start_str, '%Y-%m-%d')
            end_dt = datetime.strptime(end_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Geçersiz tarih formatı (YYYY-MM-DD)'}), 400

        if end_dt < start_dt:
            return jsonify({'error': 'Bitiş tarihi başlangıçtan küçük olamaz'}), 400

        # TR saat dilimi - UTC çevirisi
        ws = start_dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)
        we = (end_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)

        result = _compute_range_report(ws, we)
        result['range_start'] = start_str
        result['range_end'] = end_str
        result['days'] = (end_dt - start_dt).days + 1
        result['generated_at'] = now_tr().strftime('%d/%m/%Y %H:%M')
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Rapor hatası: {str(e)}'}), 500

@app.route('/api/reports/pdf')
def api_report_pdf():
    """Rapor PDF export."""
    report_type = request.args.get('type', 'daily')
    date_str = request.args.get('date', now_tr().strftime('%Y-%m-%d'))
    start_str = request.args.get('start')
    end_str = request.args.get('end')

    try:
        if report_type == 'daily':
            resp = api_report_daily()
            # api_report_daily bir response veya tuple dönebilir
            if isinstance(resp, tuple):
                return resp
            data = resp.get_json()
        elif report_type == 'range':
            if not start_str or not end_str:
                return jsonify({'error': 'start ve end parametreleri gerekli'}), 400
            try:
                start_dt = datetime.strptime(start_str, '%Y-%m-%d')
                end_dt = datetime.strptime(end_str, '%Y-%m-%d')
            except ValueError:
                return jsonify({'error': 'Geçersiz tarih formatı'}), 400
            if end_dt < start_dt:
                return jsonify({'error': 'Bitiş tarihi başlangıçtan küçük olamaz'}), 400
            ws = start_dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)
            we = (end_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)
            data = _compute_range_report(ws, we)
            data['range_start'] = start_str
            data['range_end'] = end_str
            data['days'] = (end_dt - start_dt).days + 1
        else:
            # weekly
            try:
                base = datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                return jsonify({'error': 'Geçersiz tarih formatı'}), 400
            monday = base - timedelta(days=base.weekday())
            ws = monday.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)
            we = ws + timedelta(days=7)
            data = _compute_range_report(ws, we)
            data['week_start'] = (ws + timedelta(hours=3)).strftime('%Y-%m-%d')
            data['week_end'] = (we + timedelta(hours=3) - timedelta(days=1)).strftime('%Y-%m-%d')
    except Exception as e:
        return jsonify({'error': f'PDF hatası: {str(e)}'}), 500

    def fmt(s):
        if not s: return '-'
        h, m = int(s) // 3600, (int(s) % 3600) // 60
        return f"{h}sa {m}dk" if h > 0 else f"{m}dk"

    pdf = FPDF(orientation='L', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)

    # DejaVu font - Turkce karakter destegi
    dv_regular = '/usr/share/fonts/dejavu/DejaVuSans.ttf'
    dv_bold = '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf'
    if os.path.exists(dv_regular):
        pdf.add_font('DV', '', dv_regular, uni=True)
        fn = 'DV'
    else:
        fn = 'Helvetica'
    if os.path.exists(dv_bold):
        pdf.add_font('DV', 'B', dv_bold, uni=True)

    PAGE_W = 297
    MARGIN = 14
    CONTENT_W = PAGE_W - 2 * MARGIN

    pdf.add_page()

    # ── HEADER ──
    pdf.set_fill_color(15, 23, 42)
    pdf.rect(0, 0, PAGE_W, 30, 'F')
    # Accent line
    pdf.set_fill_color(56, 189, 248)
    pdf.rect(0, 30, PAGE_W, 0.8, 'F')
    pdf.set_font(fn, 'B', 22)
    pdf.set_text_color(56, 189, 248)
    pdf.set_xy(MARGIN, 5)
    pdf.cell(0, 10, 'Wakapi', ln=True)
    pdf.set_font(fn, '', 10)
    pdf.set_text_color(148, 163, 184)
    pdf.set_x(MARGIN)
    if report_type == 'daily':
        dn_map = {'Monday':'Pazartesi','Tuesday':'Salı','Wednesday':'Çarşamba','Thursday':'Perşembe','Friday':'Cuma','Saturday':'Cumartesi','Sunday':'Pazar'}
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            day_name = dn_map.get(dt.strftime('%A'), dt.strftime('%A'))
            pdf.cell(0, 6, f"Gunluk Aktivite Raporu  —  {day_name}, {dt.strftime('%d/%m/%Y')}", ln=True)
        except Exception:
            pdf.cell(0, 6, f"Gunluk Aktivite Raporu  —  {date_str}", ln=True)
    elif report_type == 'range':
        try:
            s = datetime.strptime(data.get('range_start', start_str), '%Y-%m-%d').strftime('%d/%m/%Y')
            e = datetime.strptime(data.get('range_end', end_str), '%Y-%m-%d').strftime('%d/%m/%Y')
            pdf.cell(0, 6, f"Ozel Aralik Raporu  —  {s} - {e}  ({data.get('days', 0)} gun)", ln=True)
        except Exception:
            pdf.cell(0, 6, f"Ozel Aralik Raporu  —  {start_str} / {end_str}", ln=True)
    else:
        try:
            ws_d = datetime.strptime(data.get('week_start', ''), '%Y-%m-%d').strftime('%d/%m/%Y')
            we_d = datetime.strptime(data.get('week_end', ''), '%Y-%m-%d').strftime('%d/%m/%Y')
            pdf.cell(0, 6, f"Haftalik Rapor  —  {ws_d} - {we_d}", ln=True)
        except Exception:
            pdf.cell(0, 6, f"Haftalik Rapor  —  {data.get('week_start', '')} / {data.get('week_end', '')}", ln=True)

    pdf.ln(8)

    # ── ÖZET KUTULARI ──
    boxes = [
        ('Aktif Kullanici', str(data.get('total_users', 0)), (74, 222, 128), (20, 83, 45)),
        ('IDE Sure', fmt(data.get('total_coding_seconds', 0)), (45, 212, 191), (13, 148, 136)),
        ('Tarayici Sure', fmt(data.get('total_browsing_seconds', 0)), (248, 113, 113), (127, 29, 29)),
        ('AI Sure', fmt(data.get('total_ai_seconds', 0)), (244, 114, 182), (131, 24, 67)),
        ('Takim Verimlilik', f"%{data.get('team_productivity', 0)}", (56, 189, 248), (30, 64, 175)),
    ]

    box_count = len(boxes)
    gap = 4
    bw = (CONTENT_W - gap * (box_count - 1)) / box_count
    box_h = 22
    box_y = pdf.get_y()
    for i, (label, val, text_color, border_color) in enumerate(boxes):
        x = MARGIN + i * (bw + gap)
        # Card background
        pdf.set_fill_color(30, 41, 59)
        pdf.set_draw_color(51, 65, 85)
        pdf.rect(x, box_y, bw, box_h, 'FD')
        # Top accent line
        pdf.set_fill_color(*text_color)
        pdf.rect(x, box_y, bw, 2.5, 'F')
        # Label
        pdf.set_font(fn, '', 7.5)
        pdf.set_text_color(148, 163, 184)
        pdf.set_xy(x + 5, box_y + 5)
        pdf.cell(bw - 10, 4, label)
        # Value
        pdf.set_font(fn, 'B', 15)
        pdf.set_text_color(*text_color)
        pdf.set_xy(x + 5, box_y + 11)
        pdf.cell(bw - 10, 8, val)
    pdf.set_y(box_y + box_h + 10)

    # ── TABLO ──
    if report_type == 'daily':
        headers = ['Kullanici', 'IDE Sure', 'Tarayici', 'AI Sure', 'Verimlilik', 'Saat Araligi', 'Projeler', 'Diller']
        widths = [38, 24, 24, 22, 20, 30, 60, 51]
    elif report_type == 'range':
        days_count = data.get('days', 1)
        headers = ['Kullanici', f'Aktif/{days_count}g', 'IDE Sure', 'Tarayici', 'AI Sure', 'Verimlilik', 'Projeler', 'Diller']
        widths = [38, 22, 26, 26, 22, 20, 58, 57]
    else:
        headers = ['Kullanici', 'Aktif Gun', 'IDE Sure', 'Tarayici', 'AI Sure', 'Verimlilik', 'Projeler', 'Diller']
        widths = [38, 20, 26, 26, 22, 20, 60, 57]

    table_w = sum(widths)
    table_x = MARGIN

    # Header row
    pdf.set_x(table_x)
    pdf.set_fill_color(15, 23, 42)
    pdf.set_text_color(148, 163, 184)
    pdf.set_font(fn, 'B', 7.5)
    for i, h in enumerate(headers):
        pdf.cell(widths[i], 9, h, border=0, fill=True, align='C')
    pdf.ln()
    # Separator line
    pdf.set_draw_color(56, 189, 248)
    pdf.line(table_x, pdf.get_y(), table_x + table_w, pdf.get_y())
    pdf.ln(0.5)

    # Data rows
    pdf.set_font(fn, '', 7.5)
    for idx, u in enumerate(data.get('users', [])):
        # Page break check
        if pdf.get_y() > 185:
            pdf.add_page()
            pdf.set_x(table_x)
            pdf.set_fill_color(15, 23, 42)
            pdf.set_text_color(148, 163, 184)
            pdf.set_font(fn, 'B', 7.5)
            for i, h in enumerate(headers):
                pdf.cell(widths[i], 9, h, border=0, fill=True, align='C')
            pdf.ln()
            pdf.set_draw_color(56, 189, 248)
            pdf.line(table_x, pdf.get_y(), table_x + table_w, pdf.get_y())
            pdf.ln(0.5)
            pdf.set_font(fn, '', 7.5)

        prod = u.get('productivity', 0)
        if report_type == 'daily':
            row = [
                u.get('user_id', ''),
                fmt(u.get('ide_coding_seconds', 0)),
                fmt(u.get('browsing_seconds', 0)),
                fmt(u.get('ai_coding_seconds', 0)),
                f"%{prod}",
                f"{u.get('first_active', '-')} - {u.get('last_active', '-')}",
                (u.get('projects', '-') or '-')[:34],
                (u.get('languages', '-') or '-')[:30],
            ]
        elif report_type == 'range':
            days_count = data.get('days', 1)
            row = [
                u.get('user_id', ''),
                f"{u.get('active_days', 0)}/{days_count}",
                fmt(u.get('ide_coding_seconds', 0)),
                fmt(u.get('browsing_seconds', 0)),
                fmt(u.get('ai_coding_seconds', 0)),
                f"%{prod}",
                (u.get('projects', '-') or '-')[:32],
                (u.get('languages', '-') or '-')[:30],
            ]
        else:
            row = [
                u.get('user_id', ''),
                f"{u.get('active_days', 0)}/7",
                fmt(u.get('ide_coding_seconds', 0)),
                fmt(u.get('browsing_seconds', 0)),
                fmt(u.get('ai_coding_seconds', 0)),
                f"%{prod}",
                (u.get('projects', '-') or '-')[:34],
                (u.get('languages', '-') or '-')[:30],
            ]
        # Alternating row colors
        if idx % 2 == 0:
            pdf.set_fill_color(30, 41, 59)
        else:
            pdf.set_fill_color(20, 30, 48)

        pdf.set_x(table_x)
        prod_idx = 4 if report_type == 'daily' else 5
        for i, val in enumerate(row):
            if i == prod_idx:
                if prod >= 70: pdf.set_text_color(74, 222, 128)
                elif prod >= 40: pdf.set_text_color(251, 191, 36)
                else: pdf.set_text_color(248, 113, 113)
            elif i == 0:
                pdf.set_text_color(56, 189, 248)
                pdf.set_font(fn, 'B', 7.5)
            elif i == 1 and report_type == 'daily':
                pdf.set_text_color(45, 212, 191)
            else:
                pdf.set_text_color(203, 213, 225)
            pdf.cell(widths[i], 8, str(val), border=0, fill=True)
            if i == 0:
                pdf.set_font(fn, '', 7.5)
        pdf.ln()
        # Thin separator
        pdf.set_draw_color(51, 65, 85)
        pdf.line(table_x, pdf.get_y(), table_x + table_w, pdf.get_y())

    # ── FOOTER ──
    pdf.ln(10)
    pdf.set_draw_color(56, 189, 248)
    pdf.line(MARGIN, pdf.get_y(), MARGIN + CONTENT_W, pdf.get_y())
    pdf.ln(4)
    pdf.set_font(fn, '', 7.5)
    pdf.set_text_color(100, 116, 139)
    pdf.set_x(MARGIN)
    pdf.cell(0, 5, f"Wakapi Admin  |  Olusturma: {now_tr().strftime('%d/%m/%Y %H:%M')}  |  Toplam {len(data.get('users', []))} kullanici", ln=True)

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    filename = f"wakapi_rapor_{report_type}_{date_str}.pdf"
    resp = make_response(buf.read())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return resp

@app.route('/api/reports/dates')
def api_report_dates():
    """Heartbeat verisi olan tarihleri döndür - rapor takvimi için."""
    dates = query("""
        SELECT DISTINCT DATE(time + INTERVAL '3 hours') as day,
            COUNT(*) as heartbeats,
            COUNT(DISTINCT user_id) as users
        FROM heartbeats
        WHERE time >= NOW() - INTERVAL '90 days'
        GROUP BY DATE(time + INTERVAL '3 hours')
        ORDER BY day DESC
    """)
    return jsonify({
        'dates': [{'day': d['day'].strftime('%Y-%m-%d'), 'heartbeats': d['heartbeats'], 'users': d['users']} for d in dates]
    })

# ───────────── VERİMLİLİK SKORU ─────────────
@app.route('/api/productivity')
def api_productivity():
    """Tüm kullanıcıların verimlilik skoru."""
    days = int(request.args.get('days', 7))
    ts = today_start_utc() - timedelta(days=days)
    ai_cond = _ai_editor_condition()

    stats = query(f"""
        WITH ordered AS (
            SELECT user_id, time, category, editor, user_agent,
                LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s
        )
        SELECT user_id,
            COUNT(*) as heartbeats,
            COUNT(DISTINCT DATE(time+INTERVAL '3 hours')) as active_days,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(category,'')) != 'browsing' AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ide_seconds,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(category,'')) = 'browsing' AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as browsing_seconds,
            COALESCE(SUM(CASE WHEN {ai_cond} AND time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as ai_seconds
        FROM ordered
        GROUP BY user_id ORDER BY ide_seconds DESC
    """, (ts,))

    result = []
    for s in stats:
        ide = s.get('ide_seconds', 0) or 0
        browsing = s.get('browsing_seconds', 0) or 0
        ai = s.get('ai_seconds', 0) or 0
        total = ide + browsing
        productivity = round(ide / max(total, 1) * 100) if total > 0 else 0
        # Tutarlılık: aktif gün / toplam gün * 100
        consistency = round((s.get('active_days', 0) or 0) / max(days, 1) * 100)
        # Genel skor: verimlilik %60, tutarlılık %40
        score = round(productivity * 0.6 + consistency * 0.4)
        result.append({
            'user_id': s['user_id'],
            'ide_seconds': ide,
            'browsing_seconds': browsing,
            'ai_seconds': ai,
            'productivity': productivity,
            'consistency': consistency,
            'score': score,
            'active_days': s.get('active_days', 0),
            'heartbeats': s['heartbeats']
        })

    result.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({'scores': result, 'days': days})

# ───────────── UYARILAR ─────────────
def _compute_alerts():
    """Dikkat gerektiren durumlar. İzinli günler filtrelenir, burnout sinyalleri dahildir."""
    ts_today = today_start_utc()
    ts_week = week_start_utc()
    today_date = now_tr().date()
    alerts = []

    # 1. Bugün hiç aktif olmayan kullanıcılar (saat 11'den sonra) — İZİNLİLER HARİÇ
    current_hour = now_tr().hour
    if current_hour >= 11:
        active_today = query("SELECT DISTINCT user_id FROM heartbeats WHERE time >= %s", (ts_today,))
        active_ids = {a['user_id'] for a in active_today}
        all_users = query("SELECT id FROM users")
        leave_map = leaves_by_user(today_date, today_date)
        on_leave_uids = {uid for uid, dates in leave_map.items() if today_date in dates}
        inactive = [u['id'] for u in all_users if u['id'] not in active_ids and u['id'] not in on_leave_uids]
        if inactive:
            alerts.append({
                'type': 'warning',
                'icon': '⚠️',
                'title': 'Bugün İnaktif Kullanıcılar',
                'message': f"{len(inactive)} kullanıcı bugün hiç aktif olmadı: {', '.join(inactive[:5])}{'...' if len(inactive) > 5 else ''}",
                'count': len(inactive)
            })
        if on_leave_uids:
            alerts.append({
                'type': 'info',
                'icon': '🏝️',
                'title': 'Bugün İzinli Kullanıcılar',
                'message': f"{len(on_leave_uids)} kişi izinli: {', '.join(list(on_leave_uids)[:5])}{'...' if len(on_leave_uids) > 5 else ''}",
                'count': len(on_leave_uids)
            })

    # 2. Yüksek eğlence sitesi kullanımı (bugün, tag bazlı)
    tags_data = query("SELECT * FROM domain_tags WHERE tag_name IN ('Eğlence', 'Sosyal Medya')")
    if tags_data:
        entertainment_patterns = [t['domain_pattern'].lower() for t in tags_data]
        ent_conds = ' OR '.join([f"LOWER(entity) LIKE '%%{p}%%'" for p in entertainment_patterns])
        if ent_conds:
            ent_users = query(f"""
                WITH ordered AS (
                    SELECT user_id, time,
                        LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
                    FROM heartbeats WHERE time >= %s AND type IN ('domain','url') AND ({ent_conds})
                )
                SELECT user_id,
                    COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as seconds
                FROM ordered GROUP BY user_id HAVING
                    COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0) > 3600
                ORDER BY seconds DESC
            """, (ts_today,))
            for eu in ent_users:
                mins = round((eu['seconds'] or 0) / 60)
                alerts.append({
                    'type': 'danger',
                    'icon': '🎮',
                    'title': f"{eu['user_id']} — Yüksek Eğlence Kullanımı",
                    'message': f"Bugün {mins} dakika eğlence/sosyal medya sitelerinde",
                    'user_id': eu['user_id']
                })

    # 3. Haftalık aktivitesi düşen kullanıcılar (izinliler hariç)
    prev_ws = prev_week_start_utc()
    leaves_this_week = leaves_by_user(today_date - timedelta(days=7), today_date)
    # Bu hafta ≥3 gün izinli olanlar düşüş uyarısından muaf
    excused = {uid for uid, dates in leaves_this_week.items() if len(dates) >= 3}
    comparison = query("""
        WITH this_week AS (
            SELECT user_id, COUNT(*) as hb FROM heartbeats WHERE time >= %s GROUP BY user_id
        ), prev_week AS (
            SELECT user_id, COUNT(*) as hb FROM heartbeats WHERE time >= %s AND time < %s GROUP BY user_id
        )
        SELECT p.user_id, p.hb as prev_hb, COALESCE(t.hb, 0) as this_hb
        FROM prev_week p LEFT JOIN this_week t ON p.user_id = t.user_id
        WHERE COALESCE(t.hb, 0) < p.hb * 0.3 AND p.hb > 100
    """, (ts_week, prev_ws, ts_week))
    for c in comparison:
        if c['user_id'] in excused:
            continue
        drop_pct = round((1 - c['this_hb'] / max(c['prev_hb'], 1)) * 100)
        alerts.append({
            'type': 'info',
            'icon': '📉',
            'title': f"{c['user_id']} — Aktivite Düşüşü",
            'message': f"Bu hafta %{drop_pct} daha az aktif (geçen hafta: {c['prev_hb']} HB, bu hafta: {c['this_hb']} HB)",
            'user_id': c['user_id']
        })

    # 4. Burnout: üst üste 3 gün ≥10 saat kod yazma (son 10 gün)
    ts_burnout = ts_today - timedelta(days=10)
    daily = query("""
        WITH ordered AS (
            SELECT user_id, DATE(time+INTERVAL '3 hours') as day, time,
                LAG(time) OVER (PARTITION BY user_id, DATE(time+INTERVAL '3 hours') ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s
        )
        SELECT user_id, day,
            COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as sec
        FROM ordered
        GROUP BY user_id, day
        ORDER BY user_id, day
    """, (ts_burnout,))
    by_user = {}
    for r in daily:
        by_user.setdefault(r['user_id'], []).append((r['day'], r['sec']))
    for uid, days_list in by_user.items():
        # üst üste ≥10 saat olan en uzun seri
        max_streak = 0
        cur_streak = 0
        prev_day = None
        for d, sec in days_list:
            if sec >= 36000:  # 10 saat
                if prev_day is not None and (d - prev_day).days == 1:
                    cur_streak += 1
                else:
                    cur_streak = 1
                max_streak = max(max_streak, cur_streak)
                prev_day = d
            else:
                cur_streak = 0
                prev_day = None
        if max_streak >= 3:
            alerts.append({
                'type': 'danger',
                'icon': '🔥',
                'title': f"{uid} — Burnout Riski",
                'message': f"Üst üste {max_streak} gün 10 saatin üzerinde kod yazmış. Mola önerisi yerinde olabilir.",
                'user_id': uid
            })

    # 5. Hafta sonu çalışması (Cmt/Pzr son 14 gün, >2 saat)
    weekend = query("""
        WITH ordered AS (
            SELECT user_id, DATE(time+INTERVAL '3 hours') as day, time,
                LAG(time) OVER (PARTITION BY user_id, DATE(time+INTERVAL '3 hours') ORDER BY time) as prev_time
            FROM heartbeats WHERE time >= %s
        ),
        daily AS (
            SELECT user_id, day,
                EXTRACT(DOW FROM day) as dow,
                COALESCE(SUM(CASE WHEN time - prev_time <= INTERVAL '10 minutes' THEN EXTRACT(EPOCH FROM (time - prev_time)) ELSE 0 END), 0)::int as sec
            FROM ordered GROUP BY user_id, day
        )
        SELECT user_id, SUM(sec)::int as weekend_sec, COUNT(DISTINCT day) as weekend_days
        FROM daily
        WHERE dow IN (0, 6) AND sec > 7200
        GROUP BY user_id
        ORDER BY weekend_sec DESC
    """, (ts_today - timedelta(days=14),))
    for w in weekend:
        hours = round((w['weekend_sec'] or 0) / 3600)
        alerts.append({
            'type': 'warning',
            'icon': '🏖️',
            'title': f"{w['user_id']} — Hafta Sonu Çalışması",
            'message': f"Son 14 günün hafta sonlarında toplam {hours} saat ({w['weekend_days']} gün). İş-yaşam dengesi kontrol edilebilir.",
            'user_id': w['user_id']
        })

    return {'alerts': alerts, 'checked_at': now_tr().strftime('%H:%M')}

@app.route('/api/alerts')
def api_alerts():
    data = cached_swr(('alerts',), 60, _compute_alerts)
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)