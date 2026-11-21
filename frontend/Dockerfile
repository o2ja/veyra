# syntax=docker/dockerfile:1
# Targets: `dev` (Vite dev server, source bind-mounted) and `static` (built SPA served by Caddy).
FROM node:26.13.1-trixie-slim@sha256:e6bedef217677b276c591795e169840edd9096dbf13f29df8ef8d1856b8820bc AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit

FROM deps AS dev
RUN chown -R node:node /app
USER node
EXPOSE 5173
CMD ["npm", "run", "dev"]

FROM deps AS build
COPY . .
RUN npm run build

FROM caddy:2.11.9-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0 AS static
COPY --from=build /app/dist /srv/frontend
