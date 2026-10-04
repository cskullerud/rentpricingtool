#!/usr/bin/with-contenv bashio
# Thin launcher. The logic (options, secrets, validation) is in entrypoint.py so it can be
# tested without Home Assistant. Do not print options or environment here: they hold secrets.
bashio::log.info "Starting Rent Pricing"
cd /opt/app
exec python /opt/app/entrypoint.py
