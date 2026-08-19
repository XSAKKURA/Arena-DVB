#!/bin/sh
# Держит агента в эфире. Матч проигрывается по дедлайну, а не по позиции на
# доске: упавший процесс стоит партии так же верно, как плохой ход.
#
# Шаблон обязан совпадать с самим процессом, а не с чем угодно, где встречается
# такая строка: под `pgrep -f "arena_agent run"` попадает и оболочка, внутри
# команды которой эти слова просто написаны, — и тогда сторож считает живым
# агента, которого нет.
PATTERN='^python3 -m arena_agent run'
export ARENA_KEY=$(cat "$HOME/.arena-dvb/key")
cd /home/user/Arena-DVB || exit 1
while true; do
    if ! pgrep -f "$PATTERN" > /dev/null; then
        echo "$(date +%H:%M:%S) агент не найден — поднимаем" >> supervise.log
        setsid nohup python3 -m arena_agent run --rated-only \
            >> run.log 2>&1 < /dev/null &
    fi
    sleep 30
done
