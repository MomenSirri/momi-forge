# History Portal Architecture

## Objective

Move History out of Gradio widgets into a dedicated web UI with API-driven data access, while keeping generation workflows in Gradio.

## High-level design

- **Gradio (`app.py`)**
  - Keeps generation surfaces:
    - `5K Upscaler + Flux`
    - `General Enhancement v04`
  - Replaces old History widget tree with an embedded **History Portal** iframe (web shell only).
  - Keeps admin analytics tab.

- **History Portal (`history_portal/`)**
  - `server.js` (Node.js + Express)
    - Company-domain login (`@brickvisual.com` by default).
    - Session-cookie auth.
    - SQLite-backed history APIs.
    - Favorite/favorite-category APIs.
    - Safe local image serving through `/api/asset`.
  - `public/index.html`
    - Semantic, clean app layout.
  - `public/styles.css`
    - Professional gallery-focused visual system.
  - `public/app.js`
    - API-driven state management for filters, pagination, details, favorites.

## API surface

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/history`
- `GET /api/history/:taskId`
- `POST /api/history/:taskId/favorite`
- `GET /api/favorite-categories`
- `POST /api/favorite-categories`
- `GET /api/asset?path=...`

## Data model integration

The portal consumes existing SQLite tables:

- `tasks`
- `task_favorites`
- `favorite_categories`
- `workflows`
- `users`

No data migration is required for the current schema.

## Security model

- Login restricted to company email domain.
- Password validation uses bcrypt hashes from `users` table.
- Session cookies are HttpOnly and scoped per user.
- History and favorite operations are always user-scoped (`LOWER(user_email)=LOWER(session_email)`).
- Local asset route validates allowed roots and file type before serving.

## Performance model

- Server-side pagination (`page`, `page_size`) and SQL filtering.
- Facet queries for workflow/category/status selector population.
- Thumbnail-first rendering in grid (`loading="lazy"`).
- Detail modal fetches item details on demand.

## UX states handled

- Login required
- Loading history
- Empty history / no matching filters
- API failure states
- Missing favorite category/name validation
- Modal metadata + actions

## Migration notes

1. Start History portal (`npm start` in `history_portal/`).
2. Set `HISTORY_PORTAL_URL` in `.env` if not default.
3. Start Gradio app (`python app.py`).
4. History tab now embeds portal instead of Gradio widgets.

## Future extension points

- Role-based admin route expansion (`admin` session role).
- Infinite-scroll API mode.
- Tagging and notes editor endpoints.
- Aggregation endpoints for dashboard charts.
- Optional SSO bridge between Gradio auth and portal auth.
