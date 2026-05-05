FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN corepack enable && corepack prepare pnpm@9 --activate && pnpm i --no-frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
