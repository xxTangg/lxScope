FROM node:20-bookworm-slim

WORKDIR /app
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY services/sales-api/package.json services/sales-api/package.json
RUN corepack enable && pnpm install --filter sales-api... --frozen-lockfile=false
COPY services/sales-api/ services/sales-api/
RUN pnpm --filter sales-api build

ENV NODE_ENV=production
ENV SALES_API_PORT=44100
EXPOSE 44100
CMD ["node", "services/sales-api/dist/index.js"]
