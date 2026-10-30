#!/bin/sh
# Zabbix external check: проверява дали DNS сървърът резолвва дадено име.
# Употреба: dns_check.sh <IP на DNS сървър> <име>
# Резултат: 1 = DNS работи, 0 = DNS не отговаря / няма запис
SERVER="$1"
NAME="$2"
RESULT=$(dig +time=2 +tries=1 +short "@$SERVER" "$NAME" A 2>/dev/null \
         | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1)
if [ -n "$RESULT" ]; then
    echo 1
else
    echo 0
fi
