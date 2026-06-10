import ApiHealth from "../../components/api-health";

export default function DashboardPage() {
  return (
    <main className="min-h-screen bg-slate-100 text-slate-950">
      <div className="mx-auto flex min-h-screen max-w-7xl">
        <aside className="hidden w-64 border-r border-slate-200 bg-white px-6 py-6 md:block">
          <div className="text-sm font-semibold text-slate-950">Greysight</div>
          <nav className="mt-8" aria-label="Primary navigation">
            <a
              className="block rounded-md bg-slate-100 px-3 py-2 text-sm font-medium text-slate-950"
              href="/dashboard"
            >
              Dashboard
            </a>
          </nav>
        </aside>

        <section className="flex min-w-0 flex-1 flex-col">
          <header className="border-b border-slate-200 bg-white px-5 py-4 sm:px-8">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500">Snowflake cost observability</p>
                <h1 className="mt-1 text-2xl font-semibold text-slate-950">Cost dashboard</h1>
              </div>
              <ApiHealth />
            </div>
          </header>

          <div className="grid gap-6 px-5 py-6 sm:px-8 lg:grid-cols-[minmax(0,1fr)_320px]">
            <section className="rounded-lg border border-slate-200 bg-white p-5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <h2 className="text-base font-semibold text-slate-950">Overview</h2>
                  <p className="mt-1 text-sm text-slate-500">Dashboard datasets will render here.</p>
                </div>
              </div>
              <div className="mt-5 h-64 rounded-md border border-dashed border-slate-300 bg-slate-50" />
            </section>

            <aside className="rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-base font-semibold text-slate-950">Run status</h2>
              <p className="mt-2 text-sm text-slate-500">Waiting for dashboard run integration.</p>
            </aside>
          </div>
        </section>
      </div>
    </main>
  );
}
