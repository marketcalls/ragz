# Frontend image: builds the Vite bundle, serves it from nginx.
#
# The SPA is static once built, so the runtime stage carries no Node at all --
# only nginx and the compiled assets. That is a large reduction in what has to
# be patched for something that ultimately serves files.

# --- builder -----------------------------------------------------------------
FROM node:20-bookworm-slim AS builder

WORKDIR /app

# pnpm comes from the pinned packageManager field, so the image, CI and
# developers all resolve the same version (see pnpm-workspace.yaml).
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack pnpm install --frozen-lockfile

COPY . .
# `build` runs tsc --noEmit before vite build, so a type error fails the IMAGE,
# not a later deploy.
RUN corepack pnpm build

# --- runtime -----------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

# nginx-unprivileged runs as uid 101 and listens on 8080 -- no root, and no
# CAP_NET_BIND_SERVICE needed for a low port.
COPY --from=builder /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080
