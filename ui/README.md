# TAQR interface

TanStack Start frontend for reviewing TAQR runs, tracing claims to evidence,
and browsing source and provenance tables.

## Development

Install dependencies and start the frontend:

```sh
bun install
bun run dev
```

The app is served at `http://localhost:3000`. Requests under `/api` are proxied
to `http://localhost:8000` by default. Override the backend URL when needed:

```sh
TAQR_API_URL=http://localhost:8080 bun run dev
```

## Verification

```sh
bun run typecheck
bun run lint
bun run build
```

## UI components

Use Bun when adding shadcn components:

```sh
bunx --bun shadcn@latest add <component>
```
