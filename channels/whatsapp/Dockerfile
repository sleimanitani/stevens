FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --omit=dev

COPY tsconfig.json ./
COPY src ./src

# Build step (requires dev deps)
RUN npm install && npm run build

EXPOSE 8081

CMD ["node", "dist/index.js"]
