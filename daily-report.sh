#!/bin/bash

TODAY=$(date +%Y-%m-%d)

echo "========================================="
echo "Wakapi Günlük Rapor - $TODAY"
echo "========================================="

docker exec wakapi-db psql -U wakapi -d wakapi -t -A -F'|' -c "
SELECT
    user_id,
    COUNT(*) as heartbeats,
    string_agg(DISTINCT project, ', ') as projects,
    string_agg(DISTINCT language, ', ') as languages,
    to_char(MIN(time + INTERVAL '3 hours'), 'HH24:MI') as ilk,
    to_char(MAX(time + INTERVAL '3 hours'), 'HH24:MI') as son
FROM heartbeats
WHERE (time + INTERVAL '3 hours') >= '$TODAY'::date
GROUP BY user_id
ORDER BY heartbeats DESC;
" | while IFS='|' read -r user hb projects langs ilk son; do
    echo ""
    echo "👤 $user"
    echo "   Heartbeats: $hb"
    echo "   Aktif: $ilk - $son"
    echo "   Projeler: $projects"
    echo "   Diller: $langs"
done

echo ""
echo "--- Bugün aktif olmayan kullanıcılar ---"
docker exec wakapi-db psql -U wakapi -d wakapi -t -A -c "
SELECT id FROM users
WHERE id NOT IN (
    SELECT DISTINCT user_id FROM heartbeats
    WHERE (time + INTERVAL '3 hours') >= '$TODAY'::date
);
" | while read -r user; do
    [ -n "$user" ] && echo "⚠️  $user - bugün aktivite yok"
done

echo ""
echo "========================================="