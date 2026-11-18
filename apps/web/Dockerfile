FROM node:22-alpine AS build
WORKDIR /web
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY index.html tsconfig.json ./
COPY src ./src
RUN pnpm build

FROM caddy:2-alpine
COPY --from=build /web/dist /srv
