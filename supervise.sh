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
PROXY_NOTE=${CCR_PROXY_NOTE:-/root/.ccr/README.md}

# Ключ берётся из окружения, а если его там нет — из каталога состояния.
if [ -z "${ARENA_KEY:-}" ]; then
    KEY_FILE="${ARENA_STATE_DIR:-$HOME/.arena-dvb}/key"
    [ -r "$KEY_FILE" ] && ARENA_KEY=$(cat "$KEY_FILE") && export ARENA_KEY
fi

# Адрес исходящего прокси живёт не дольше машины: при пересборке он меняется,
# а у сторожа в окружении остаётся прежний — и поднятый им агент уходит стучаться
# в порт, который никто не слушает. Текущий адрес контейнер записывает в свою
# памятку, поэтому перед каждым запуском мы перечитываем его оттуда. Файла нет
# или строка не найдена — окружение остаётся как есть.
refresh_proxy() {
    [ -r "$PROXY_NOTE" ] || return 0
    addr=$(sed -n 's|.*local proxy at \(http://127\.0\.0\.1:[0-9]*\).*|\1|p' "$PROXY_NOTE" | head -1)
    [ -n "$addr" ] || return 0
    [ "$addr" = "${HTTPS_PROXY:-}" ] && return 0
    echo "$(date '+%Y-%m-%d %H:%M:%S') прокси сменился на $addr — обновляем окружение" >&2
    HTTPS_PROXY=$addr https_proxy=$addr
    export HTTPS_PROXY https_proxy
}

while true; do
    if ! pgrep -f "$PATTERN" > /dev/null 2>&1; then
        refresh_proxy
        echo "$(date '+%Y-%m-%d %H:%M:%S') агент не найден — поднимаем" >&2
        setsid nohup python3 -m arena_agent run "$@" >> "$ROOT/run.log" 2>&1 < /dev/null &
    fi
    sleep "$INTERVAL"
done
