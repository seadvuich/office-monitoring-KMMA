# Zabbix сървър 7.0 + инструменти за DNS проверка (dig)
FROM zabbix/zabbix-server-pgsql:alpine-7.0-latest
USER root
RUN apk add --no-cache bind-tools
COPY externalscripts/dns_check.sh /usr/lib/zabbix/externalscripts/dns_check.sh
RUN chmod 755 /usr/lib/zabbix/externalscripts/dns_check.sh
USER 1997
