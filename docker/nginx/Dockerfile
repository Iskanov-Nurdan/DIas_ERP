# syntax=docker/dockerfile:1.7
# nginx: reverse proxy к Django + раздача статики.
# SPA внутрь образа НЕ собирается: готовый билд заливается на сервер (scp/rsync)
# и подключается bind-mount'ом в /usr/share/nginx/html (см. FRONTEND_DIST в .env.prod).
FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY docker/nginx/nginx.conf /etc/nginx/conf.d/dias.conf
COPY docker/nginx/snippets/ /etc/nginx/snippets/

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/nginx-health || exit 1
