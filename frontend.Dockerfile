FROM node:alpine3.22

ENV BACKEND_URL="http://asgraph-backend:4000"
ENV GENERATOR_URL="http://asgraph-generator:4001"
ENV BASE_URL="http://localhost:8080/api"

RUN mkdir /frontend
COPY ./frontend /frontend
WORKDIR /frontend

RUN npm install && chown -R node:node /frontend

# node:alpine ships an unprivileged "node" user (uid 1000) — no reason for
# this process to run as root, it doesn't write outside /frontend.
USER node

# Bulk loading removed - use frontend Data Management page to load data
CMD [ "npm", "run", "deploy" ]