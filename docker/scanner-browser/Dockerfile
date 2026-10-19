# The scanner's browser. It runs untrusted web pages, so it is locked down in docker-compose.yml:
# internal network only (its sole neighbour is the egress proxy), read-only filesystem, no
# capabilities, memory/CPU/process limits. The proxy and the browser flags are fixed here, on the
# server side, so the worker that connects to it cannot weaken them.
FROM mcr.microsoft.com/playwright:v1.64.0-noble
WORKDIR /srv
RUN npm init -y >/dev/null \
 && npm install --no-audit --no-fund playwright@1.64.0
COPY server.mjs /srv/server.mjs
USER pwuser
CMD ["node", "/srv/server.mjs"]
