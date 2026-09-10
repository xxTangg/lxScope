FROM node:22-alpine AS build

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json
COPY backend/package.json backend/package.json
RUN pnpm install --frozen-lockfile

COPY . .

ARG VITE_AGENTSCOPE_API_PORT=8001
ENV VITE_AGENTSCOPE_API_PORT=${VITE_AGENTSCOPE_API_PORT}

RUN pnpm --filter frontend build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
