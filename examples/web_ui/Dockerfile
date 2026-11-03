FROM node:20-bookworm-slim AS build

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json
COPY backend/package.json backend/package.json
RUN pnpm install --frozen-lockfile

COPY . .

ARG VITE_AGENTSCOPE_API_PORT=8001
ENV VITE_AGENTSCOPE_API_PORT=${VITE_AGENTSCOPE_API_PORT}
ARG VITE_LOGTO_ENDPOINT=
ARG VITE_LOGTO_APP_ID=
ARG VITE_LOGTO_API_RESOURCE=
ENV VITE_LOGTO_ENDPOINT=${VITE_LOGTO_ENDPOINT}
ENV VITE_LOGTO_APP_ID=${VITE_LOGTO_APP_ID}
ENV VITE_LOGTO_API_RESOURCE=${VITE_LOGTO_API_RESOURCE}

RUN pnpm --filter frontend build

FROM docker.m.daocloud.io/library/nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
