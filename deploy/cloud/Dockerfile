FROM node:24.7.0-alpine3.22 AS web-builder
WORKDIR /src/webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci --ignore-scripts
COPY webui/ ./
RUN npm run build

FROM python:3.11.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
WORKDIR /app/backend
COPY backend/requirements.cloud.txt ./requirements.cloud.txt
RUN pip install --no-cache-dir --requirement requirements.cloud.txt \
    && groupadd --gid 10001 platform \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin platform
ARG RELEASE_SHA
ENV PLATFORM_RELEASE_SHA=${RELEASE_SHA}
COPY --chown=10001:10001 backend/app ./app
COPY --chown=10001:10001 backend/migrations ./migrations
COPY --chown=10001:10001 backend/control_migrations ./control_migrations
COPY --chown=10001:10001 deploy/cloud/registry.yaml /app/registry.yaml
COPY --chown=10001:10001 deploy/cloud/metabot.runtime-contract.json /app/metabot.runtime-contract.json
COPY --chown=10001:10001 deploy/cloud/brain-model.release.json /app/brain-model.release.json
COPY --from=web-builder --chown=10001:10001 /src/webui/dist ./app/static
RUN install -d -o 10001 -g 10001 -m 700 /run/office-recipient \
    && install -o 10001 -g 10001 -m 600 /dev/null /run/office-recipient/platform-office-recipient-bearer
USER platform:platform
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2).read()"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers", "--no-access-log"]
