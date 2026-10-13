FROM node:22-alpine AS build
WORKDIR /app
COPY miniapp/package*.json ./
RUN npm ci
COPY miniapp/index.html ./
COPY miniapp/src ./src
RUN npm run build
FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY deploy/miniapp/Caddyfile /etc/caddy/Caddyfile
EXPOSE 8080
