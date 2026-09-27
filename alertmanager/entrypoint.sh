#!/bin/sh
# Start Alertmanager with Telegram when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
# are set, or with a no-op receiver otherwise. The secrets go into files in
# /tmp (a tmpfs, readable only by this container's user), never into the
# mounted config directory.
set -eu

CONFIG=/etc/alertmanager/alertmanager.yml

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    case "$TELEGRAM_CHAT_ID" in
        -[0-9]*|[0-9]*) ;;
        *) echo "TELEGRAM_CHAT_ID must be a number, e.g. 123456789 or -1001234567890" >&2; exit 1 ;;
    esac
    umask 077
    printf '%s' "$TELEGRAM_BOT_TOKEN" > /tmp/telegram_bot_token
    printf '%s' "$TELEGRAM_CHAT_ID" > /tmp/telegram_chat_id
else
    echo "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set: alerts will only show in the Alertmanager UI" >&2
    CONFIG=/etc/alertmanager/alertmanager-noop.yml
fi

exec /bin/alertmanager \
    --config.file="$CONFIG" \
    --storage.path=/alertmanager \
    --web.listen-address=127.0.0.1:9093 \
    --cluster.listen-address= \
    "$@"
