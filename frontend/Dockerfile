FROM node:22-alpine AS build

WORKDIR /app

ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY package.json package-lock.json* ./
RUN npm install

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.http.conf /etc/nginx/rental-mgmt/http.conf
COPY nginx.https.conf.template /etc/nginx/rental-mgmt/https.conf.template
COPY --chmod=755 select-nginx-config.sh /docker-entrypoint.d/40-select-nginx-config.sh
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80 443
