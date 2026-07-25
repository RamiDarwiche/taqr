FROM node:20-slim

WORKDIR /app

RUN npm install -g bun

COPY package.json bun.lock ./
RUN bun install

COPY . .

EXPOSE 3000

CMD ["bun", "run", "dev"]