#!/bin/sh
# Serve HTTPS when both NGINX_SSL_CERTFILE and NGINX_SSL_KEYFILE are set, plain HTTP otherwise.
set -eu

if [ -n "${NGINX_SSL_CERTFILE:-}" ] && [ -n "${NGINX_SSL_KEYFILE:-}" ]; then
  envsubst '${NGINX_SSL_CERTFILE} ${NGINX_SSL_KEYFILE}' \
    < /etc/nginx/rental-mgmt/https.conf.template > /etc/nginx/conf.d/default.conf
  echo "rental-mgmt: serving HTTPS with certificate ${NGINX_SSL_CERTFILE}"
elif [ -n "${NGINX_SSL_CERTFILE:-}" ] || [ -n "${NGINX_SSL_KEYFILE:-}" ]; then
  echo "rental-mgmt: NGINX_SSL_CERTFILE and NGINX_SSL_KEYFILE must both be set" >&2
  exit 1
else
  cp /etc/nginx/rental-mgmt/http.conf /etc/nginx/conf.d/default.conf
fi
