# Live ingest: nginx with the RTMP module, both from Debian's own packages
# (built for amd64 and arm64 alike, so it runs on an Ampere instance too).
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx-light libnginx-mod-rtmp gettext-base \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home app \
 && mkdir -p /var/lib/akon-live/hls /var/lib/akon-live/recordings /var/lib/akon-live/tmp \
 && chown -R app:app /var/lib/akon-live

COPY nginx.conf.template /etc/nginx/akon-live.conf.template
COPY entrypoint.sh /usr/local/bin/akon-live
RUN chmod +x /usr/local/bin/akon-live

# 1935: RTMP from encoders. 8080: HLS for viewers. 8081: control, internal only.
EXPOSE 1935 8080 8081

ENTRYPOINT ["/usr/local/bin/akon-live"]
