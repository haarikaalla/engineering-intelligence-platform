FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S eip && adduser -S eip -G eip
COPY --from=build --chown=eip:eip /app/.next/standalone ./
COPY --from=build --chown=eip:eip /app/.next/static ./.next/static
USER eip
EXPOSE 3000
# BACKEND_URL and EIP_API_KEY are read at runtime by the /api proxy route
CMD ["node", "server.js"]
