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
    <nav aria-label="Agreements" className="mb-6 flex flex-wrap gap-4 text-sm">
      {SECTIONS.map((s) => (
        <NavLink
          key={s.to}
          to={s.to}
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

export function useManufacturerMap(): Map<string, ManufacturerOut> {
  const list = useManufacturers();
  const map = new Map<string, ManufacturerOut>();
  for (const m of list.data?.data ?? []) {
    map.set(m.id, m);
  }
  return map;
}
