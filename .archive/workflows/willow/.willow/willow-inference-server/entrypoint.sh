#!/bin/bash
set -e

# Start NGINX
service nginx start

# Start the application
gunicorn main:app --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:19001 \
    --graceful-timeout 10 \
    --forwarded-allow-ips "*" \
    --log-level info -t 0 \
    --keyfile nginx/key.pem --certfile nginx/cert.pem --ssl-version TLSv1_2