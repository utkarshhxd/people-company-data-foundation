#!/bin/sh
# Render alertmanager.yml from its template, then start Alertmanager.
#
# Two things this exists to do, neither of which Alertmanager does itself:
#
#   * choose the route's receiver from whether a webhook is configured, so the
#     default really is "nothing leaves this machine" rather than a config that
#     silently fails to deliver;
#   * keep the webhook URL out of the config file. Alertmanager reads it from
#     `url_file`, which this writes with restrictive permissions from the
#     environment. The URL is a credential -- anyone holding it can post into
#     the channel -- and a credential in a mounted config file is a credential
#     in the repository the next time somebody copies it back.
#
# Alertmanager does not expand environment variables in its config, so without
# this the documented "set ALERTMANAGER_WEBHOOK_URL" would have been a variable
# nothing read.

set -eu

TEMPLATE=/etc/alertmanager/alertmanager.yml.tmpl
RENDERED=/alertmanager/alertmanager.yml
URL_FILE=/alertmanager/webhook-url

if [ -n "${ALERTMANAGER_WEBHOOK_URL:-}" ]; then
    RECEIVER=webhook
    # umask first: creating the file world-readable and then narrowing it leaves
    # a window in which it was world-readable.
    ( umask 077 && printf '%s' "$ALERTMANAGER_WEBHOOK_URL" > "$URL_FILE" )
    echo "alertmanager: routing to the webhook receiver (URL read from $URL_FILE)"
else
    RECEIVER=default
    # Removed rather than left behind, so a webhook that was configured once and
    # then unset does not leave a live credential in the volume.
    rm -f "$URL_FILE"
    echo "alertmanager: no ALERTMANAGER_WEBHOOK_URL set — alerts stay in the UI" \
         "at :9093 and nothing leaves this machine"
fi

sed "s/__RECEIVER__/$RECEIVER/g" "$TEMPLATE" > "$RENDERED"

exec /bin/alertmanager \
    --config.file="$RENDERED" \
    --storage.path=/alertmanager \
    "$@"
