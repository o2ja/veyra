import { NavLink } from "react-router-dom";

import type { components } from "../../api/schema";
import { useManufacturers } from "../masterdata/common";

type ManufacturerOut = components["schemas"]["ManufacturerOut"];

const SECTIONS = [
  { to: "/agreements", label: "Agreements" },
  { to: "/agreement-drafts", label: "Drafts" },
] as const;

export function AgreementsNav() {
  return (
    <nav
      aria-label="Agreements"
      className="mb-6 flex w-fit max-w-full flex-wrap gap-1 rounded-xl border border-line bg-panel p-1 text-sm shadow-card"
    >
      {SECTIONS.map((s) => (
        <NavLink
          key={s.to}
          to={s.to}
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

export function useManufacturerMap(): Map<string, ManufacturerOut> {
  const list = useManufacturers();
  const map = new Map<string, ManufacturerOut>();
  for (const m of list.data?.data ?? []) {
    map.set(m.id, m);
  }
  return map;
}
