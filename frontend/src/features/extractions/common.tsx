import { NavLink } from "react-router-dom";

const SECTIONS = [
  { to: "/extractions", label: "Extractions", end: true },
  { to: "/extractions/usage", label: "AI usage", end: false },
] as const;

export function ExtractionsNav() {
  return (
    <nav
      aria-label="Extractions"
      className="mb-6 flex w-fit max-w-full flex-wrap gap-1 rounded-xl border border-line bg-panel p-1 text-sm shadow-card"
    >
      {SECTIONS.map((s) => (
        <NavLink
          key={s.to}
          to={s.to}
          end={s.end}
          className={({ isActive }) =>
            isActive
              ? "rounded-lg bg-brand px-3 py-1.5 font-medium text-panel"
              : "rounded-lg px-3 py-1.5 text-muted hover:bg-brand-soft hover:text-ink"
          }
        >
          {s.label}
        </NavLink>
      ))}
    </nav>
  );
}
