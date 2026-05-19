# Multi-stage build → distroless runtime per non-sdk-flow.md §6
# (recommended for non-Python extensions).
FROM golang:1.22-alpine AS build
WORKDIR /src
# go.sum* glob keeps the cache layer working when the module gains its
# first dependency (today the module is stdlib-only and ships no go.sum).
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/extension .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/extension /extension
EXPOSE 8000
USER nonroot:nonroot
ENTRYPOINT ["/extension"]
