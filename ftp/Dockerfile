# FTP сървър: vsftpd на Debian
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends vsftpd procps \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -s /bin/bash ftpuser \
 && echo 'ftpuser:ftp123' | chpasswd \
 && echo "Welcome to the office FTP server" > /home/ftpuser/readme.txt \
 && chown ftpuser:ftpuser /home/ftpuser/readme.txt \
 && mkdir -p /var/run/vsftpd/empty \
 && touch /var/log/vsftpd.log
COPY vsftpd.conf /etc/vsftpd.conf
EXPOSE 21 21100-21110
CMD ["sh", "-c", "vsftpd /etc/vsftpd.conf && tail -F /var/log/vsftpd.log"]
