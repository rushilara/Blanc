# Blanc frontend (Next.js) for `docker compose up` (see docker-compose.yml).
# Build from the repo root: the app imports the shared JSON Schemas in contracts/ and the
# synthetic fixtures in fixtures/. No evidence, database or .env is copied in.
FROM node:20-slim AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY contracts /app/contracts
COPY fixtures /app/fixtures
COPY frontend ./
# Browsers call the backend directly, so this is the address as seen from the laptop.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
WORKDIR /app/frontend
COPY --from=build /app/contracts /app/contracts
COPY --from=build /app/fixtures /app/fixtures
COPY --from=build --chown=node /app/frontend ./
# Sign-in users (BLA-60) live in /app/.blanc, mounted from the laptop.
RUN mkdir -p /app/.blanc && chown node /app/.blanc
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD node -e "fetch('http://127.0.0.1:3000/blanc/index.html').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
