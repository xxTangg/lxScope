FROM node:20-bookworm-slim AS build

WORKDIR /app
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY apps/admin-web/package.json apps/admin-web/package.json
RUN corepack enable && pnpm install --filter admin-web... --frozen-lockfile=false
COPY apps/admin-web/ apps/admin-web/
RUN pnpm --filter admin-web build

FROM nginx:1.27-alpine
COPY apps/admin-web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/admin-web/dist /usr/share/nginx/html
EXPOSE 80
