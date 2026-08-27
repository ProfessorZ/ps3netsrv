Default share for `docker-compose.yml`.

Put your `GAMES`, `PS3ISO`, `PSXISO` (etc.) folders here, or point the compose
volume somewhere else. The directory must be readable by the uid/gid the
container runs as (`user:` in `docker-compose.yml`, `1000:1000` by default).
