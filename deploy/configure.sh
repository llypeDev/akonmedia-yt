#!/bin/sh
# Writes the .env of a production installation behind HTTPS, from the domain
# the portal will answer on and an e-mail address for the certificate.
#
#   sh deploy/configure.sh aulas.minhaescola.com.br ti@minhaescola.com.br
#
# Secrets are generated here and never leave the server. An existing .env is
# never overwritten: its database password is the one the database volume was
# created with, and a new one would lock the portal out.
set -eu

cd "$(dirname "$0")/.."

usage() {
    echo "usage: sh deploy/configure.sh <domain> <e-mail>" >&2
    echo "example: sh deploy/configure.sh aulas.minhaescola.com.br ti@minhaescola.com.br" >&2
    exit 2
}

[ $# -eq 2 ] || usage

# Accept "https://aulas.example.com/" as well as the bare name.
domain=$(printf '%s' "$1" | sed -e 's#^[a-zA-Z]*://##' -e 's#/.*$##' | tr '[:upper:]' '[:lower:]')
email=$2

case $domain in
    "" | *[!a-z0-9.-]* | .* | *. | *..* | localhost | [0-9]*.[0-9]*.[0-9]*.[0-9]*)
        echo "error: '$1' is not a domain name such as aulas.minhaescola.com.br" >&2
        echo "(an IP address does not get a certificate: point a name at the server first)" >&2
        exit 1 ;;
    *.*) ;;
    *)
        echo "error: '$1' is not a full domain name such as aulas.minhaescola.com.br" >&2
        exit 1 ;;
esac

case $email in
    *@*.*) ;;
    *) echo "error: '$email' is not an e-mail address" >&2; exit 1 ;;
esac

if [ -e .env ]; then
    echo "error: .env already exists, and it is left as it is." >&2
    echo "To change the domain, edit the lines that mention it in .env and run" >&2
    echo "'docker compose up -d'. Never change POSTGRES_PASSWORD there: the" >&2
    echo "database keeps the password it was created with." >&2
    exit 1
fi

# Hexadecimal only: nothing that a shell or docker compose would interpret.
secret() {
    od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'
}

umask 077
cat > .env <<EOF
# Production settings for https://$domain, written by deploy/configure.sh on
# $(date +%F). Keep this file private: it holds the passwords of this
# installation. Values here override the defaults in .env.docker.

# docker compose reads both files, so plain "docker compose ..." commands use
# the production setup (Caddy with HTTPS, see docker-compose.prod.yml).
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml

# The certificate: Caddy gets it from Let's Encrypt for this name.
AKON_DOMAIN=$domain
ACME_EMAIL=$email

SECRET_KEY=$(secret 32)
ALLOWED_HOSTS=$domain,web,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://$domain
SITE_URL=https://$domain
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
# Caddy already sends http:// to https://.
SECURE_SSL_REDIRECT=False
# Caddy sends the media files; the portal only decides how (X-Accel-Redirect).
MEDIA_ACCEL_REDIRECT=/_media/
TIME_ZONE=America/Sao_Paulo

# Only used when the database volume is first created.
POSTGRES_PASSWORD=$(secret 24)

LIVE_HOOK_SECRET=$(secret 24)
LIVE_RTMP_URL=rtmp://$domain/live
LIVE_HLS_URL=https://$domain/hls

# E-mail for password resets and confirmations. Without it, messages only go
# to the log of the web service. Use port 587: Oracle Cloud blocks port 25.
#EMAIL_HOST=smtp.example.com
#EMAIL_PORT=587
#EMAIL_HOST_USER=
#EMAIL_HOST_PASSWORD=
#DEFAULT_FROM_EMAIL=AkonMedia <no-reply@$domain>

# The portal's name, shown in the header, in e-mails and in shared links.
#PORTAL_NAME=AkonMedia
EOF

echo "Wrote .env for https://$domain"

resolved=$(getent ahostsv4 "$domain" 2>/dev/null | awk 'NR == 1 { print $1 }' || true)
if [ -n "$resolved" ]; then
    echo "$domain points to $resolved: it must be this server's public IP address."
else
    echo "warning: $domain does not resolve yet. Create its DNS record (type A, the"
    echo "server's public IP address) before starting: the certificate depends on it."
fi
echo
echo "Next:  docker compose up -d --build"
