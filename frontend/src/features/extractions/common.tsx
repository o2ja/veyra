import { NavLink } from "react-router-dom";

const SECTIONS = [
  { to: "/extractions", label: "Extractions", end: true },
  { to: "/extractions/usage", label: "AI usage", end: false },
] as const;

export function ExtractionsNav() {
  return (
    <nav aria-label="Extractions" className="mb-6 flex flex-wrap gap-4 text-sm">
      {SECTIONS.map((s) => (
        <NavLink
          key={s.to}
          to={s.to}
          end={s.end}
          className={({ isActive }) =>
            isActive ? "font-medium text-ink underline underline-offset-4" : "text-muted hover:text-ink"
          }
        >
          {s.label}
        </NavLink>
      ))}
    </nav>
  );
}
