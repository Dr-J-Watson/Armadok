# ---------- front ----------
FROM --platform=$BUILDPLATFORM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- runtime ----------
FROM python:3.12-slim
ARG APP_VERSION=dev
# FORWARDED_ALLOW_IPS : seuls ces proxys (réseaux Docker / privés) sont crus sur X-Forwarded-For ; un visiteur ne peut donc pas falsifier son adresse
# (verrou de connexion, bannissements, journal). Resserrez-le à l'adresse de Traefik si vous la connaissez.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=${APP_VERSION} \
    FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
WORKDIR /srv
# requirements.lock : versions exactes ET empreintes (pip refuse tout paquet modifié) ; il se régénère depuis requirements.txt avec pip-compile --generate-hashes
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY backend/app ./app
COPY --from=web /web/dist ./static
# Git (projets liés à un dépôt : état, pull, retour à une version, clonage) et client SSH
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*
# CLI Docker + plugins compose et buildx : « docker compose up / pull / build » des projets, via le socket monté (seulement si DOCKER_MANAGE=true)
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=docker:cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
# /data existe (propriété 1000) : un volume nommé vide en hérite au premier montage, sans chown préalable
RUN mkdir /data && chown 1000:1000 /data
USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
