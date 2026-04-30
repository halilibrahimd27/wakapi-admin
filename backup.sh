#!/bin/bash
BACKUP_DIR="/opt/wakapi/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

# Dump al
docker exec wakapi-db pg_dump -U wakapi wakapi | gzip > "$BACKUP_DIR/wakapi_${TIMESTAMP}.sql.gz"

# 7 günden eski backupları sil
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

echo "[$(date)] Backup tamamlandı: wakapi_${TIMESTAMP}.sql.gz"