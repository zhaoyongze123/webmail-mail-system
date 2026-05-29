FROM docker.1ms.run/library/node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./

RUN unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy \
    && npm ci

COPY . .

RUN npm run build

FROM docker.1ms.run/library/nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
