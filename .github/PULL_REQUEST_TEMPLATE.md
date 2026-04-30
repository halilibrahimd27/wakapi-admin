<!--
PR açmadan önce CONTRIBUTING varsa okuyun + README → "Sorun Giderme"
-->

## 📝 Ne yapıyor?

<!-- Kısa: ne, niye -->

## 🏷️ Tür

- [ ] 🐛 Bug fix
- [ ] ✨ Yeni feature (admin panel)
- [ ] 🤖 Yeni AI editor detection
- [ ] 🏷️ Domain tag sistemi iyileştirmesi
- [ ] ♻️ Refactor
- [ ] 📚 Documentation
- [ ] 🛡️ Security
- [ ] 🚀 Performance
- [ ] 🔧 Stack / compose / scripts

## ✅ Public-Safe Checklist

- [ ] Hardcoded credential, IP, domain yok (`${VAR}` env interpolation)
- [ ] `.env`, `prometheus/wakapi.token` git'e commit edilmedi
- [ ] Komut blokları test edildi
- [ ] Türkçe yorumlar/UI metinleri tutarlı (proje konvansiyonu)

## 🧪 Manuel test (admin paneli için)

```bash
docker build -t wakapi-admin:latest ./admin
docker compose up -d admin
curl http://localhost:8080/api/realtime
curl http://localhost:8080/api/tags
```

## 🔗 İlgili issue

Closes #
