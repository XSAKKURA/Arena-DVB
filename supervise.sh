#!/bin/sh
# Держит агента в эфире.
#
# Матч проигрывается по дедлайну, а не по позиции на доске: упавший процесс
# стоит партии так же верно, как плохой ход, — и стоил, когда машину пересобрали
# и агент просто не поднялся. Сторож занимает одну строку в crontab (@reboot) или
# запускается из systemd; ни того, ни другого он не требует.
#
# Использование:  ./supervise.sh [аргументы для `arena_agent run`]
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

# Шаблон обязан совпадать с самим процессом, а не с чем угодно, где встречается
# такая строка: под `pgrep -f "arena_agent run"` попадает и оболочка, внутри
# команды которой эти слова просто написаны, — и тогда сторож считает живым
# агента, которого нет.
PATTERN='^python3 -m arena_agent run'
INTERVAL=${SUPERVISE_INTERVAL:-30}

# Ключ берётся из окружения, а если его там нет — из каталога состояния.
if [ -z "${ARENA_KEY:-}" ]; then
    KEY_FILE="${ARENA_STATE_DIR:-$HOME/.arena-dvb}/key"
    [ -r "$KEY_FILE" ] && ARENA_KEY=$(cat "$KEY_FILE") && export ARENA_KEY
fi

while true; do
    if ! pgrep -f "$PATTERN" > /dev/null 2>&1; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') агент не найден — поднимаем" >&2
        setsid nohup python3 -m arena_agent run "$@" >> "$ROOT/run.log" 2>&1 < /dev/null &
    fi
    sleep "$INTERVAL"
done
