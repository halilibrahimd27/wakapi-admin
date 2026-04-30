# Wakapi Admin Panel İyileştirme — Claude Code Prompt

## BAĞLAM

Wakapi admin panelimde iki büyük iyileştirme yapmam gerekiyor. Proje dosyaları `/opt/wakapi/admin/` altında:
- `app.py` — Flask backend
- `templates/dashboard.html` — Tek dosya frontend (vanilla JS, inline CSS)

Stack: Flask + psycopg2 + Gunicorn, PostgreSQL, Docker container olarak çalışıyor.
Compose: `/opt/wakapi/compose.yml` — admin servisi `wakapi-admin:latest` image'ı, port 8080, DB bilgileri env'den.
Zaman dilimi: UTC+3 (Türkiye), tüm sorgularda `time + INTERVAL '3 hours'` ile TR zamanına çevriliyor.

### Heartbeats Tablo Şeması:
```
id, user_id, entity, type, category, project, branch, language, is_write, editor, 
operating_system, machine, user_agent, time, hash, origin, origin_id, created_at,
lines, line_no, cursor_pos, line_deletions, line_additions, project_root_count
```

- `type` değerleri: `'file'` (26K), `'domain'` (49K), `'app'` (3K)
- `category` değerleri: `'browsing'`, `'coding'`, `'ai coding'`, `'building'`, `'debugging'`, `'writing docs'`, `'writing tests'`, `'code reviewing'`
- `type='domain'` olan entity'ler sadece domain düzeyinde: `https://wakapi.aysbulut.com`, `https://www.youtube.com` gibi. Full URL path YOK.

### Mevcut AI Tespit Sistemi:
```python
AI_EDITORS = ['cursor', 'windsurf', 'trae']
AI_USERAGENT_KEYWORDS = ['claudecode', 'copilot', 'codeium', 'tabnine', 'supermaven', 'cody']

def _ai_editor_condition():
    parts = []
    for e in AI_EDITORS:
        parts.append(f"LOWER(editor) LIKE '%%{e}%%'")
    for k in AI_USERAGENT_KEYWORDS:
        parts.append(f"LOWER(COALESCE(user_agent,'')) LIKE '%%{k}%%'")
    parts.append("LOWER(COALESCE(category,'')) = 'ai coding'")
    return '(' + ' OR '.join(parts) + ')'
```

### Mevcut Realtime Endpoint:
```python
@app.route('/api/realtime')
def api_realtime():
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    ai_cond = _ai_editor_condition()
    active = query(f\"""
        SELECT user_id,
            MAX(editor) as editor,
            MAX(project) as project,
            MAX(language) as language,
            COUNT(*) as recent_hb,
            to_char(MAX(time + INTERVAL '3 hours'), 'HH24:MI:SS') as last_seen,
            BOOL_OR({ai_cond}) as is_ai
        FROM heartbeats
        WHERE time >= %s
        GROUP BY user_id
        ORDER BY MAX(time) DESC
    \""", (cutoff,))
    return jsonify({'active': active, 'count': len(active), 'checked_at': now_tr().strftime('%H:%M:%S')})
```

---

## İYİLEŞTİRME 1: Siteler Tab'ı + Domain Tag Sistemi

Kullanıcı modal'ındaki "Siteler" tab'ını tamamen yeniden tasarla. Admin'in domain'leri etiketleyebildiği bir tag sistemi ekle.

### 1A. Domain Tag Sistemi (Admin Tarafı)

**Yeni DB Tablosu oluştur (app.py init'te CREATE TABLE IF NOT EXISTS):**
```sql
CREATE TABLE IF NOT EXISTS domain_tags (
    id SERIAL PRIMARY KEY,
    tag_name VARCHAR(50) NOT NULL,
    tag_color VARCHAR(7) NOT NULL,
    tag_icon VARCHAR(10) DEFAULT '🏷️',
    domain_pattern VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

Domain pattern matching mantığı: entity içinde `domain_pattern` geçiyorsa o tag'e ait sayılır.
Örnek: pattern `aysbulut.com` → `https://crm.aysbulut.com`, `https://wakapi.aysbulut.com` hepsi eşleşir.
Örnek: pattern `127.0.0.1` → `http://127.0.0.1:59838`, `http://127.0.0.1:54585` hepsi eşleşir.

**Varsayılan tag'ler (ilk çalışmada INSERT et, varsa atla):**

| tag_name | tag_color | tag_icon | domain_pattern örnekleri |
|----------|-----------|----------|--------------------------|
| İş | #4ade80 | 💼 | aysbulut.com, github.com, gitlab.com, stackoverflow.com, localhost, 127.0.0.1, 172., 192.168., 10. |
| Eğlence | #f87171 | 🎮 | youtube.com, netflix.com, twitch.tv, reddit.com, haber7.com, flo.com.tr |
| Sosyal Medya | #a78bfa | 📱 | instagram.com, twitter.com, x.com, linkedin.com, facebook.com |
| AI Araçları | #f472b6 | 🤖 | chatgpt.com, claude.ai, gemini.google.com, openrouter.ai, notebooklm.google.com, copilot.microsoft.com |
| Eğitim | #38bdf8 | 📚 | docs.google.com, translate.google.com, drive.google.com, figma.com, medium.com |

**Tag Yönetim API'leri:**
- `GET /api/tags` — Tüm tag'leri listele (tag_name'e göre gruplu)
- `POST /api/tags` — Yeni tag ekle `{tag_name, tag_color, tag_icon, domain_pattern}`
- `DELETE /api/tags/<id>` — Tag pattern sil
- `PUT /api/tags/<id>` — Tag güncelle

**Tag Yönetim Sayfası:**
- Sol sidebar'a "🏷️ Etiketler" menüsü ekle (5. sayfa)
- Üstte tag grupları: Her tag_name için bir section, içinde o tag'e ait pattern'ler listesi
- Her pattern satırında: pattern text, silme butonu
- Tag grubuna yeni pattern ekleme: input + "Ekle" butonu
- "Yeni Tag Grubu Oluştur" formu: tag adı, renk seçici (6-7 preset renk butonu), ikon seçici (emoji listesi)
- Altta "Etiketlenmemiş Domain'ler" bölümü:
  - Son 30 günde görülüp hiçbir tag'e uymayan unique domain'leri listele
  - Her birinin yanında dropdown ile mevcut tag'e atama butonu
  - Veya yeni pattern olarak ekleme butonu

### 1B. Siteler Tab'ı (Kullanıcı Modal'ında)

**Backend — `/api/user/<user_id>/sites` endpoint'i ekle:**
- Query param: `days` (default 7)
- Domain listesi: entity, heartbeats, seconds, first_visit, last_visit, visit_days
- Tag bilgisi: Python'da domain_tags tablosuyla matching yaparak her domain'e tag ata
- Kategori bazlı toplam süre istatistikleri
- Günlük domain breakdown

**Frontend — Modal "Siteler" Tab'ı Yeniden Tasarımı:**
1. **Zaman filtresi dropdown**: Son 24 saat / 7 gün / 14 gün / 30 gün (değişince API'yi tekrar çağır)
2. **Özet kartları** (mini-grid, her tag için 1 kart):
   - 💼 İş: X sa Y dk
   - 🎮 Eğlence: X sa Y dk
   - 🤖 AI Araçları: X sa Y dk
   - 📱 Sosyal Medya: X sa Y dk
   - 🏷️ Etiketlenmemiş: X sa Y dk
3. **Kategori dağılımı** — yatay stacked bar (tag renklerinde, yüzdelik)
4. **Tag filtre chip'leri** — üstte tüm tag'ler chip olarak, tıkla→sadece o tag'in domain'lerini göster
5. **Domain tablosu** — süreye göre sıralı:
   - Tag badge (renk + ikon + tag adı)
   - Domain linki (tıklanabilir, yeni sekmede açılır)
   - HB sayısı
   - Toplam süre
   - Ziyaret edilen gün sayısı
   - İlk/son ziyaret tarihi
   - Etiketlenmemiş domain'ler gri badge ile
6. **Günlük breakdown** (collapsible): Her gün hangi sitelere ne kadar süre

---

## İYİLEŞTİRME 2: Şu An Aktif Kullanıcılar Paneli

Mevcut "Şu An Aktif" banner'ı yerine dashboard'a detaylı aktif kullanıcı kartları ekle.

### Backend — `/api/realtime` genişlet:

- Cutoff'u 10 dk'ya çıkar
- Her kullanıcı için son heartbeat'in proje, dil, editör, type, entity bilgisini döndür
- `seconds_ago` alanı ekle (son heartbeat'ten kaç saniye geçti)
- Her kullanıcının bugünkü toplam coding_seconds'ını da döndür

### Frontend — Aktif Kullanıcılar Grid:

Banner'ı kaldır, stats-grid'in altına collapsible section olarak ekle:

**Her kullanıcı kartı (180px min genişlik, grid layout):**
- Sol border rengi durum göstergesi:
  - 🟢 Yeşil: < 2 dk önce
  - 🟡 Sarı: 2-5 dk önce  
  - 🟠 Turuncu: 5-10 dk önce
- Kullanıcı adı (tıklanabilir → openUser modal)
- Şu an: proje adı (type=file) veya site adı (type=domain)
- Dil + editör
- 🤖 AI badge (is_ai true ise)
- Bugünkü toplam süre + mini progress bar (referans: 8 saat = %100)
- "X dk önce" / "şimdi" text'i
- Grid: `repeat(auto-fill, minmax(180px, 1fr))`
- 15 sn otomatik refresh
- Collapse/expand butonu

---

## GENEL KURALLAR

- Tüm CSS inline olsun (tek dosya HTML, external CSS dosyası yok)
- Mevcut dark tema: #0f172a, #1e293b, #334155, #38bdf8, #4ade80, #f472b6
- Türkçe UI
- Responsive (mobil uyumlu)
- AI tespit: mevcut `_ai_editor_condition()` kullan
- Mevcut kodu bozma, sadece ekle/değiştir
- psycopg2 f-string LIKE'larda `%` → `%%` kullan
- CTE'lerde ai_cond kullanıyorsan editor, user_agent, category kolonlarını SELECT'e ekle

### Build & Test:
```bash
cd /opt/wakapi/admin && docker build -t wakapi-admin:latest . --no-cache && docker rm -f wakapi-admin && cd /opt/wakapi && docker compose up -d admin
```
```bash
curl -s http://localhost:8080/api/realtime | python3 -m json.tool
curl -s http://localhost:8080/api/tags | python3 -m json.tool
curl -s "http://localhost:8080/api/user/hasanyilmazgursoy/sites?days=7" | python3 -m json.tool
```

### ÖNCELİK SIRASI:
1. `domain_tags` tablo oluştur + varsayılan veriler (app.py init)
2. Tag CRUD API'leri
3. Siteler tab'ı backend + frontend
4. Tag yönetim sayfası (sidebar "🏷️ Etiketler")
5. Aktif kullanıcılar paneli
6. Her adımda build & test