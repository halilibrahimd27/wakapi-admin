# Wakapi Admin — Self-Hosted Coding Time Analytics Stack

> **Tek `docker compose up` ile;** kendi sunucunuzda çalışan Wakapi
> [(github.com/muety/wakapi)](https://github.com/muety/wakapi) instance'ı,
> üzerine geliştirdiğimiz **özel admin paneli**, **PostgreSQL**, **pgAdmin**,
> **Prometheus** ve **Grafana** dashboardları.
>
> WakaTime alternatifi olarak ekibinizin/şirketinizin kodlama süresini, dil
> dağılımını, projelerini ve "şu an kim ne yapıyor"u tek pencereden takip
> etmenizi sağlar.

![Stack](https://img.shields.io/badge/stack-Wakapi%20%2B%20Postgres%20%2B%20Flask%20%2B%20Grafana-1f6feb?style=flat-square)
![Python](https://img.shields.io/badge/admin-Python%203.12-3776ab?style=flat-square&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/deploy-Docker%20Compose-2496ed?style=flat-square&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## İçindekiler

- [Genel Bakış](#genel-bakış)
- [Mimari](#mimari)
- [Servisler](#servisler)
- [Admin Panel Özellikleri](#admin-panel-özellikleri)
- [Hızlı Başlangıç](#hızlı-başlangıç)
- [Konfigürasyon](#konfigürasyon)
- [Operasyonel Scriptler](#operasyonel-scriptler)
- [Monitoring (Prometheus + Grafana)](#monitoring-prometheus--grafana)
- [Backup & Restore](#backup--restore)
- [Reverse Proxy Önerisi](#reverse-proxy-önerisi)
- [Güvenlik Notları](#güvenlik-notları)
- [Sorun Giderme](#sorun-giderme)
- [Lisans](#lisans)

---

## Genel Bakış

Bu repo, **Wakapi**'yi kişisel kullanım sınırının dışına taşıyıp **küçük/orta
ölçek ekipler için kurumsal seviyede** çalışır hale getirmek için gerekli her
şeyi tek bir yerde toplar:

| Sorun (vanilla Wakapi) | Bu repo'nun çözümü |
|---|---|
| Yöneticinin görebileceği "kim, ne, ne kadar?" raporu yok | `wakapi-admin` Flask paneli — dashboard, kullanıcı detay modal'ı, raporlar |
| AI editör/aracı kullanımını ayırt etme yok | `editor`, `user_agent` ve `category` üzerinden AI tespiti (Cursor, Windsurf, Copilot, Claude Code, Codeium...) |
| Domain bazlı kategorilendirme yok | **Domain Tag Sistemi** — pattern bazlı tag'leme (İş / Eğlence / Sosyal Medya / AI Araçları / Eğitim) |
| "Şu an aktif" görünürlüğü zayıf | **Realtime Active Users** paneli — 15 sn'de bir refresh, durum rengi, mevcut proje/dosya |
| İzin / mola düzeltmeleri için altyapı yok | `adjustments` ve `leaves` tabloları + UI |
| PDF rapor yok | `fpdf2` ile günlük/haftalık PDF raporu |
| Backup, leaderboard refresh, public ayarı manuel | `backup.sh`, `refresh-leaderboard.sh`, `force-public.sh`, `daily-report.sh` |
| Operasyonel görünürlük yok | Prometheus scrape + hazır Grafana dashboard'ı |

---

## Mimari

```
                       ┌────────────────────────────────────────────┐
                       │             Reverse Proxy (Caddy/Nginx)    │
                       │   wakapi.example.com    admin.example.com  │
                       │   grafana-wakapi.example.com               │
                       └───────────────┬────────────────────────────┘
                                       │
   ┌───────────────────────────────────┼────────────────────────────────────┐
   │ docker network: wakapi-net        │                                    │
   │                                   ▼                                    │
   │   ┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌────┐  │
   │   │ wakapi  │───▶│  admin  │───▶│ Postgres │◀───│ pgAdmin │    │... │  │
   │   │ :3000   │    │ Flask   │    │   :5432  │    │  :5050  │    │    │  │
   │   └────┬────┘    │ :8080   │    └──────────┘    └─────────┘    └────┘  │
   │        │         └─────────┘                                            │
   │        │ /api/metrics (bearer)                                          │
   │        ▼                                                                │
   │   ┌──────────────┐    ┌──────────┐                                      │
   │   │ Prometheus   │───▶│ Grafana  │                                      │
   │   │   :9090      │    │  :3001   │                                      │
   │   └──────────────┘    └──────────┘                                      │
   └────────────────────────────────────────────────────────────────────────┘
```

---

## Servisler

| Servis | Image | Port (host) | Açıklama |
|---|---|---|---|
| **wakapi** | `ghcr.io/muety/wakapi:latest` | `3000` | Wakapi çekirdek API + UI. WakaTime API uyumlu. |
| **postgres** | `postgres:16-alpine` | `5432` | Heartbeat'lerin tutulduğu birincil veri deposu. Tuned parametreler (`shared_buffers=512MB`, `effective_cache_size=1536MB`). |
| **pgadmin** | `dpage/pgadmin4:latest` | `5050` | DB'ye web üzerinden erişim. |
| **admin** | `wakapi-admin:latest` (yerel build) | `8080` | Flask + psycopg2 + Gunicorn + fpdf2. Bu repo'nun `admin/` klasöründen build edilir. |
| **prometheus** | `prom/prometheus:latest` | `9090` | Wakapi `/api/metrics` endpoint'ini scrape eder. |
| **grafana** | `grafana/grafana:latest` | `3001` | `wakapi.json` dashboard'ı otomatik provision edilir. |

---

## Admin Panel Özellikleri

`admin/app.py` 2.000+ satırlık Flask uygulaması. Önemli özellikler:

### Dashboard
- **Stats grid:** toplam kullanıcı, bugünkü aktif kullanıcı, bu hafta toplam kodlama, AI kullanım oranı.
- **Realtime Active Users panel:** Son 10 dk içinde heartbeat atan kullanıcıların kart grid'i.
  - Sol border rengi: 🟢 < 2 dk · 🟡 2–5 dk · 🟠 5–10 dk
  - Şu anki proje / dosya / dil / editör
  - 🤖 AI badge (editor adı veya user_agent eşleşmesinden)
  - Bugünkü toplam süre + 8 saatlik mini progress bar
  - 15 saniyede bir oto-refresh.

### Kullanıcı Modal'ı (sekmeler)
1. **Genel** — günlük/haftalık özet, dil dağılımı, editör dağılımı.
2. **Projeler** — proje bazlı süre, son aktivite.
3. **Diller** — dil bazlı dağılım, trend.
4. **Siteler** — `type='domain'` heartbeat'lerinden domain bazlı süre + Tag sistemi:
   - Zaman filtresi: 24 saat / 7 / 14 / 30 gün
   - Tag özet kartları (İş/Eğlence/AI/Sosyal Medya/Eğitim)
   - Yatay stacked bar — kategori dağılımı
   - Tag chip filtreleri — tıklayınca sadece o tag.
5. **Düzeltmeler** — manuel saat ekleme/çıkarma (`adjustments` tablosu).
6. **İzinler** — gün bazlı izin tanımlama (`leaves` tablosu).

### Tag Yönetim Sayfası (`/tags`)
- Pattern bazlı domain matching: `aysbulut.com` pattern'i `https://crm.aysbulut.com`'u da yakalar.
- Renk + emoji ikon ile özelleştirme.
- "Etiketlenmemiş Domain'ler" bölümü — son 30 günde görülüp hiçbir tag'e uymayanlar.
- Default tag'ler init'te seed edilir; istediğinizi silip ekleyebilirsiniz.

### AI Tespit Mantığı
```python
AI_EDITORS = ['cursor', 'windsurf', 'trae']
AI_USERAGENT_KEYWORDS = ['claudecode', 'copilot', 'codeium',
                         'tabnine', 'supermaven', 'cody']
# + LOWER(category) = 'ai coding'
```

### Cache Katmanı
`cached_swr` (Stale-While-Revalidate) — pahalı SQL'leri TTL ile cache'ler,
TTL bitince eski değeri döndürüp arka planda yeniler. Kullanıcıya hep "anında"
yanıt; DB'ye düşük yük.

### Endpoint Listesi (özet)

```
GET  /                              Dashboard
GET  /projects   /languages         Listeleme sayfaları
GET  /users      /tags  /reports

GET  /api/realtime                  Aktif kullanıcılar (10 dk window)
GET  /api/summary                   Stats grid verisi
GET  /api/projects                  Proje breakdown
GET  /api/languages                 Dil breakdown
GET  /api/users                     Kullanıcı listesi

GET  /api/user/<id>                 Kullanıcı detay (modal)
GET  /api/user/<id>/sites           Domain breakdown + tag matching

GET  /api/tags                      Tag listesi
POST /api/tags                      Tag ekle
PUT  /api/tags/<id>                 Tag güncelle
DELETE /api/tags/<id>               Tag sil
GET  /api/untagged-domains          Etiketlenmemiş domain'ler

GET  /api/user/<id>/adjustments     İzin/düzeltme listesi
POST /api/user/<id>/adjustments     Yeni düzeltme
GET  /api/user/<id>/leaves          İzin günleri
POST /api/user/<id>/leaves          Yeni izin
GET  /api/reports/daily             PDF raporu
```

---

## Hızlı Başlangıç

### Ön Gereksinimler
- Docker 24+ ve Docker Compose v2
- 2 vCPU / 2 GB RAM minimum (önerilen 4 GB)
- Linux/macOS host (Windows için WSL2)

### Adım 1 — Repo'yu klonla
```bash
git clone https://github.com/halilibrahimd27/wakapi-admin.git
cd wakapi-admin
```

### Adım 2 — Env dosyasını hazırla
```bash
cp .env.example .env
# .env dosyasını editör ile aç ve şifreleri/secret'ları doldur:
#   WAKAPI_PASSWORD_SALT  → openssl rand -base64 48
#   WAKAPI_DB_PASSWORD    → güçlü bir parola
#   PGADMIN_DEFAULT_PASSWORD
#   GF_ADMIN_PASSWORD
```

### Adım 3 — Admin image'ını build et
```bash
docker build -t wakapi-admin:latest ./admin
```

### Adım 4 — Stack'i ayağa kaldır
```bash
docker compose up -d
docker compose ps
```

### Adım 5 — İlk Wakapi kullanıcısını oluştur
1. `http://localhost:3000` → Sign up
2. Settings → Integrations → **Wakapi API key**'i (kullanıcı API key) ve
   ayrı olarak **Metrics token**'ı kopyala.

### Adım 6 — Prometheus için metrics token'ı yerleştir
```bash
echo "<wakapi-metrics-token>" > prometheus/wakapi.token
docker compose restart prometheus
```

### Adım 7 — Doğrulama
```bash
curl http://localhost:3000/api/health           # wakapi
curl http://localhost:8080/api/realtime         # admin
curl http://localhost:9090/-/ready              # prometheus
curl http://localhost:3001/api/health           # grafana
```

Erişim:
- **Wakapi UI:** http://localhost:3000
- **Admin Panel:** http://localhost:8080
- **pgAdmin:** http://localhost:5050
- **Grafana:** http://localhost:3001 (dashboard: *Wakapi → wakapi*)
- **Prometheus:** http://localhost:9090

### Adım 8 — Kendi makinenizi bağlayın
`~/.wakatime.cfg`:
```ini
[settings]
api_url = http://localhost:3000/api/heartbeat
api_key = <wakapi-api-key>
```
VS Code'un WakaTime eklentisi/Cursor/Windsurf bu konfigürasyonu okur ve
heartbeat'leri Wakapi'ye gönderir.

---

## Konfigürasyon

### `.env` Dosyası
Tüm secret değerler `.env` üzerinden geçer. `compose.yml` içinde **hiçbir
hardcoded credential yoktur** — `${VAR}` interpolasyonu kullanılır. Her değer
için `.env.example` referans alınmalıdır.

### Wakapi Davranış Ayarları
`compose.yml` içinde sabit/varsayılan olarak ayarlanan dikkate değer
parametreler:

| Parametre | Değer | Notu |
|---|---|---|
| `WAKAPI_ALLOW_SIGNUP` | `true` | Public signup açık. Kapalı ekip için `false` yapın. |
| `WAKAPI_INVITE_CODES` | `true` | Sadece davet kodu ile kayıt. |
| `WAKAPI_SIGNUP_CAPTCHA` | `true` | Bot kayıt engeli. |
| `WAKAPI_LEADERBOARD_ENABLED` | `true` | 7 gün scope, 15 dk'da bir refresh. |
| `WAKAPI_LEADERBOARD_REQUIRE_AUTH` | `true` | Sadece login'liler görür. |
| `WAKAPI_DATA_RETENTION_MONTHS` | `12` | 12 aydan eski heartbeat purge. |
| `WAKAPI_MAX_INACTIVE_MONTHS` | `6` | 6 aydır aktif değilse hesap pasifleştirilir. |
| `WAKAPI_TRUSTED_HEADER_AUTH` | `true` | Reverse proxy'den `X-Remote-User` ile SSO destekli. |
| `WAKAPI_TRUST_REVERSE_PROXY_IPS` | `172.0.0.0/8,127.0.0.1,10.0.0.0/8` | RP IP'leri. Production'da daraltın. |

> **Tüm Wakapi env'leri için:** [muety/wakapi config docs](https://github.com/muety/wakapi/blob/master/config.default.yml)

---

## Operasyonel Scriptler

`backup.sh`, `daily-report.sh`, `force-public.sh`, `refresh-leaderboard.sh`
script'leri host üzerinde cron ile zamanlanmaya yöneliktir.

### `backup.sh` — Günlük PostgreSQL Yedeği
```bash
chmod +x backup.sh
./backup.sh
# /opt/wakapi/backups/wakapi_<TIMESTAMP>.sql.gz oluşturur
# 7 günden eski yedekleri otomatik siler.
```

`crontab -e`:
```cron
0 3 * * *  /opt/wakapi/backup.sh >> /var/log/wakapi-backup.log 2>&1
```

### `refresh-leaderboard.sh` — Leaderboard Yeniden Hesabı
Wakapi'nin kendi leaderboard hesabı zaman zaman drift edebilir; bu script son
7 günün heartbeat'lerinden (10 dk gap kuralı ile) leaderboard'u temizden hesaplar
ve container'ı restart eder.

```cron
*/15 * * * * /opt/wakapi/refresh-leaderboard.sh
```

### `force-public.sh` — Bütün Kullanıcıları Public Yap
Ekipte herkesin leaderboard ve breakdown'ları görünmesi gereken senaryo için.
**Kullanıcı tercihlerini override eder, dikkatli kullanın.**

### `daily-report.sh` — Günlük Aktivite Raporu
Gün içinde her kullanıcının kaç heartbeat attığını, hangi proje/dil'lerde
çalıştığını, ilk/son aktivite saatini terminale basar. Slack webhook'a pipe
edebilirsiniz:

```bash
./daily-report.sh | curl -X POST -H 'Content-Type: text/plain' \
  --data-binary @- $SLACK_WEBHOOK_URL
```

---

## Monitoring (Prometheus + Grafana)

### Prometheus
`/api/metrics` endpoint'i Wakapi'nin bütün metriklerini Prometheus formatında
expose eder. Bearer token ile korunur — token `prometheus/wakapi.token`
dosyasından okunur (commit edilmez).

Token'ı almak için Wakapi UI:
> Settings → Integrations → **Metrics URL** içindeki `?token=` parametresi
> sizin metrics token'ınızdır.

### Grafana
Provisioning otomatiktir:
- **Datasource:** Prometheus (`http://prometheus:9090`)
- **Dashboard:** *Wakapi* (`grafana/dashboards/wakapi.json`)

İlk girişte `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD` ile login olun.

---

## Backup & Restore

### Manuel Yedek
```bash
docker exec wakapi-db pg_dump -U wakapi wakapi | gzip > wakapi_$(date +%F).sql.gz
```

### Restore
```bash
gunzip -c wakapi_2025-04-30.sql.gz | docker exec -i wakapi-db psql -U wakapi -d wakapi
```

### Tam Disaster Recovery
1. `docker compose down`
2. `docker volume rm wakapi_pgdata`
3. `docker compose up -d postgres` — boş DB ile ayağa kalksın
4. Restore komutunu çalıştır
5. `docker compose up -d`

---

## Reverse Proxy Önerisi

### Caddy (`Caddyfile`)
```
wakapi.example.com {
  reverse_proxy localhost:3000
}

admin.example.com {
  basicauth {
    yonetici JDJhJDEw...   # caddy hash-password
  }
  reverse_proxy localhost:8080
}

grafana-wakapi.example.com {
  reverse_proxy localhost:3001
}
```

### Nginx
Reverse proxy IP'lerini `WAKAPI_TRUST_REVERSE_PROXY_IPS` env'ine eklemeyi
unutmayın; aksi halde Wakapi gerçek client IP'sini göremez.

> **Admin paneli (8080)** built-in auth içermez. Mutlaka reverse proxy
> seviyesinde Basic Auth, OAuth Proxy veya benzeri ile koruyun ya da private
> network'te bırakın.

---

## Güvenlik Notları

- ✅ `compose.yml` tüm secret'lar için `${VAR}` interpolasyonu kullanır.
- ✅ `.env`, `prometheus/wakapi.token`, `backups/` `.gitignore` ile hariç.
- ⚠️ **Admin paneli (8080) auth'suzdur.** Public erişime kapatın.
- ⚠️ **pgAdmin (5050) ve Prometheus (9090) public açmayın.**
- ⚠️ Production'da `WAKAPI_INSECURE_COOKIES=false` ve HTTPS reverse proxy zorunlu.
- ⚠️ Postgres port 5432'yi host'a expose etmek **DEV içindir** — production'da `ports` satırını silin, sadece compose network üzerinden erişin.

### Önerilen Hardening
```yaml
postgres:
  ports: []                       # 5432'yi dışarı açma
pgadmin:
  ports: []                       # sadece compose network içinden
prometheus:
  ports: []                       # sadece grafana scrape eder
```

---

## Sorun Giderme

### Wakapi başlamıyor — `WAKAPI_PASSWORD_SALT` hatası
`.env` içinde tanımlı değil. Doldurun:
```bash
echo "WAKAPI_PASSWORD_SALT=$(openssl rand -base64 48)" >> .env
```

### Admin panel 500 — DB bağlantı hatası
```bash
docker compose logs admin --tail=50
docker exec wakapi-admin printenv | grep DB_
```
`DB_PASSWORD`'un Wakapi DB parolası ile aynı olduğundan emin olun.

### Prometheus "401 Unauthorized" / "no targets"
`prometheus/wakapi.token` dosyası yok veya yanlış. Wakapi UI → Integrations →
Metrics token'ı kopyala → dosyaya yaz → `docker compose restart prometheus`.

### Heartbeat'ler geliyor ama dashboard boş
Wakapi background job'ı her saatte özetleri hesaplar. Test için
`/api/summary?recompute=true` veya container restart.

### "AI badge" hiçbir kullanıcıda görünmüyor
Editor adınız `AI_EDITORS` listesinde değil olabilir. `admin/app.py`'da
`AI_EDITORS` ve `AI_USERAGENT_KEYWORDS` listelerini düzenleyin, image'ı tekrar
build edin.

```bash
docker build -t wakapi-admin:latest ./admin --no-cache
docker compose up -d admin
```

---

## Lisans

MIT — Wakapi'nin kendisi de MIT lisanslıdır
([muety/wakapi/LICENSE](https://github.com/muety/wakapi/blob/master/LICENSE)).
Bu repo'da Wakapi'yi paketleyip ek admin paneli ile sunuyoruz; çekirdek koda
ait tüm haklar **Ferdinand Mütsch (@muety)** ve katkıcılarına aittir.

---

## Katkı

Issue'lar ve PR'lar için: <https://github.com/halilibrahimd27/wakapi-admin/issues>

> Bir özellik eklerken `admin/app.py`'da yorumların **Türkçe**, kullanıcıya
> görünen UI metinlerinin de **Türkçe** olduğuna dikkat edin (proje konvansiyonu).
