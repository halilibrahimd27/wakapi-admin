#!/bin/bash
docker exec wakapi-db psql -U wakapi -d wakapi -c "
UPDATE users SET
    share_data_max_days = -1,
    share_projects = true,
    share_languages = true,
    share_editors = true,
    share_oss = true,
    share_machines = true,
    share_labels = true,
    public_leaderboard = true
WHERE share_data_max_days = 0 OR public_leaderboard = false;
"