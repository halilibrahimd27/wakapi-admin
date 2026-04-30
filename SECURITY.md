# Security Policy

Bu repo Wakapi'yi self-hosted çalıştıran bir stack + custom Flask admin
paneli içerir. Stack birden fazla bileşen barındırır (Wakapi, PostgreSQL,
pgAdmin, Prometheus, Grafana, custom admin), her biri kendi saldırı
yüzeyine sahip.

## ⚠️ Bildirilmesi gerekenler

### Custom Admin Panel'de
- Auth bypass (admin paneli **default'ta auth'suzdur** — reverse proxy gerekli)
- SQL injection (psycopg2 raw query'ler)
- XSS (admin dashboard render edilen kullanıcı verisi)
- IDOR (kullanıcı detay endpoint'lerinde yetki kontrolü)
- CSRF (POST endpoint'lerinde token yok)

### Stack genelinde
- `.env` template'inde hardcoded credential
- pgAdmin / Grafana / Prometheus dış dünyaya açılmış
- Wakapi metrics token'ı plain text exposure
- Reverse proxy IP whitelist bypass

### Wakapi'nin kendisi
> Eğer açık Wakapi'nin upstream'inde ise (örn: heartbeat injection):
> [muety/wakapi/security](https://github.com/muety/wakapi/security)'e bildirin.
> Bu repo sadece Wakapi'yi paketler.

## 🚨 Nasıl bildirilir?

**Public issue açmayın** — önce private bildirim:

1. **GitHub Security Advisory** (önerilen):
   https://github.com/halilibrahimd27/wakapi-admin/security/advisories/new
2. Veya repo sahibi @halilibrahimd27'a GitHub DM

## ⏱️ Yanıt süresi (best-effort, hobby project)

- **24-72 saat**: ilk yanıt
- **7 gün**: triage
- **30 gün**: fix release veya kabul edilen mitigation

## 🛡️ Güvenli kullanım önerileri

### Asgari production hardening

```bash
# 1. Strong DB_PASSWORD
echo "WAKAPI_DB_PASSWORD=$(openssl rand -base64 32)" >> .env

# 2. Wakapi insecure cookies kapalı
WAKAPI_INSECURE_COOKIES=false

# 3. Reverse proxy + HTTPS (Caddy/Nginx)
# Admin paneli MUTLAKA basic-auth + IP whitelist arkasında
```

### docker-compose ports override

```yaml
# docker-compose.override.yml — DB ports'u host'a açma
services:
  postgres: { ports: [] }
  pgadmin:  { ports: [] }
  prometheus: { ports: [] }
```

### Reverse proxy zorunluluğu

| Servis | Auth | Zorunlu önlem |
|---|---|---|
| Wakapi (3000) | Built-in user system | HTTPS + signup captcha |
| Admin Panel (8080) | **YOK** | Reverse proxy basic-auth + IP whitelist |
| pgAdmin (5050) | Built-in | Reverse proxy + private network |
| Grafana (3001) | Built-in admin | HTTPS + güçlü parola |
| Prometheus (9090) | YOK | Public açma — sadece compose network |

## 🙏 Teşekkür

Bildirim yapanlar — istemezlerse anonim — README'nin "Acknowledgments" bölümünde anılır.
