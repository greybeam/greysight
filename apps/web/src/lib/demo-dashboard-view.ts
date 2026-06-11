import type {
  DashboardView,
  ServiceSpendViewModel,
} from "./dashboard-contracts";
import demoDashboardData from "./demo-dashboard-data";
import {
  buildDashboardViewModel,
  DEFAULT_WINDOW_DAYS,
} from "./dashboard-transforms";

const viewModel = buildDashboardViewModel(
  demoDashboardData,
  DEFAULT_WINDOW_DAYS,
);

const serviceSpend: ServiceSpendViewModel = {
  ...viewModel.serviceSpend,
  dailySeries: viewModel.serviceSpend.dailySeries.map((point) => {
    const values: Record<string, number> = {};

    for (const [key, value] of Object.entries(point)) {
      if (key !== "date" && typeof value === "number") {
        values[key] = value;
      }
    }

    return {
      date: point.date,
      values,
    };
  }),
};

const demoDashboardView: DashboardView = {
  schema_version: 1,
  run: demoDashboardData.run,
  range: {
    mode: "relative",
    windowDays: DEFAULT_WINDOW_DAYS,
    startDate: "2026-05-10",
    endDate: "2026-06-08",
  },
  projectionRange: {
    startDate: "2026-05-10",
    endDate: "2026-06-08",
  },
  header: viewModel.header,
  unsupported: viewModel.unsupported,
  totalSpend: viewModel.totalSpend,
  computeSpend: viewModel.computeSpend,
  storageSpend: viewModel.storageSpend,
  serviceSpend,
  detailTables: viewModel.detailTables,
};

export default demoDashboardView;
