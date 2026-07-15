# Dependency Compatibility

Greysight pins the web stack to exact versions so local installs and Vercel builds are reproducible.

The current Tremor package, `@tremor/react@3.18.7`, declares a React peer dependency of `^18.0.0`. For that reason the MVP uses `react@18.3.1` and `react-dom@18.3.1`. Next.js is pinned to `next@16.2.7`, which still supports React 18 and avoids the advisories affecting the earlier Next 14 and 15 compatibility candidates.

Tremor 3 is Tailwind v3-oriented, so the MVP pins `tailwindcss@3.4.19` with `postcss@8.5.15` and `autoprefixer@10.5.0`. The Tailwind content paths include both application source and Tremor package files so Tremor utility classes are available during builds.

`eslint@9.39.4` is pinned because `eslint-config-next@16.2.7` supports ESLint 9+ and its React plugin stack is not compatible with the newer ESLint 10 API.

Install dependencies with lifecycle scripts disabled:

```bash
npm install --ignore-scripts
```

## Python

`adbc-driver-snowflake[dbapi]==1.11.0` is pinned across the shared connect
package, the API, and the worker. This version requires its matching
`adbc-driver-manager` and PyArrow native wheels, which are resolved
transitively via `uv.lock` in each package. The supported Python 3.12 Debian
deployment images must continue resolving binary wheels for
`adbc-driver-snowflake`, `adbc-driver-manager`, and `pyarrow`; if a future
version bump drops prebuilt wheels for the deployment platform, the pin must
be re-verified against the Docker builds before merging.
