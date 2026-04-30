#!/bin/bash
docker exec wakapi-db psql -U wakapi -d wakapi -c "
DELETE FROM leaderboard_items WHERE interval = '7_days';
INSERT INTO leaderboard_items (user_id, interval, by, total, key, created_at)
SELECT
    h.user_id, '7_days', NULL,
    COALESCE(SUM(
        CASE WHEN h.time - prev_time <= INTERVAL '10 minutes'
        THEN EXTRACT(EPOCH FROM (h.time - prev_time)) * 1000000000
        ELSE 0 END
    ), 0)::bigint,
    NULL, NOW()
FROM (
    SELECT user_id, time,
        LAG(time) OVER (PARTITION BY user_id ORDER BY time) as prev_time
    FROM heartbeats WHERE time >= NOW() - INTERVAL '7 days'
) h
JOIN users u ON u.id = h.user_id AND u.public_leaderboard = true
GROUP BY h.user_id;
"
docker restart wakapi