import backendDemoView from "./__fixtures__/backend-demo-view.json";
import { parseDashboardView } from "./dashboard-contracts";

const demoDashboardView = parseDashboardView(backendDemoView);

export default demoDashboardView;
