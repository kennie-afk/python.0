FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim AS runtime
ENV NODE_ENV=production \
    PORT=3200 \
    HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build /app/.next/standalone ./
USER node
EXPOSE 3200
CMD ["node", "server.js"]
