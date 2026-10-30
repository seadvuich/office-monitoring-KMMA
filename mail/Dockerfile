# Mail сървър: Postfix (SMTP) на Debian
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN echo "postfix postfix/main_mailer_type select Local only" | debconf-set-selections \
 && echo "postfix postfix/mailname string mail.office.local" | debconf-set-selections \
 && apt-get update \
 && apt-get install -y --no-install-recommends postfix procps \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -s /bin/bash admin \
 && useradd -m -s /bin/bash user1 \
 && touch /var/log/mail.log
COPY main.cf /etc/postfix/main.cf
# Без chroot (контейнерът вече е изолиран)
RUN postconf -F '*/*/chroot = n' && newaliases
EXPOSE 25
CMD ["sh", "-c", "postfix start && tail -F /var/log/mail.log"]
