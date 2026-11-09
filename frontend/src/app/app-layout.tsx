import { Outlet } from "react-router-dom";

export function AppLayout() {
  return (
    <div className="min-h-dvh flex flex-col">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto max-w-6xl px-4 h-14 flex items-center">
          <span className="font-semibold tracking-tight">Veyra Recover</span>
        </div>
      </header>
      <main className="flex-1 mx-auto w-full max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
