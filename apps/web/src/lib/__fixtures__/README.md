# Demo View Fixture

Regenerate `backend-demo-view.json` from `apps/api`:

```bash
rtk uv run python -c 'from app.routes.dashboard_runs import read_demo_dashboard_view; import json; print(json.dumps(read_demo_dashboard_view(), indent=2, sort_keys=True))' > ../web/src/lib/__fixtures__/backend-demo-view.json
```
