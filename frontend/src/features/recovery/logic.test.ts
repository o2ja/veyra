import { describe, expect, it } from "vitest";

import {
  TRANSITION_STATUSES,
  amountText,
  batchIdFromDetails,
  newestFirst,
  notificationCaseLink,
  ownerText,
  reasonLabel,
  statusLabel,
  usDate,
  usDateTime,
  type EventOut,
  type NotificationOut,
} from "./logic";

const event = (id: string, createdAt: string): EventOut => ({
  id,
  actor_user_id: null,
  created_at: createdAt,
  event_type: "status_changed",
  from_status: "open",
  to_status: "to_reclaim",
  note: null,
  payload: null,
});

describe("recovery display helpers", () => {
  it("formats server dates in US style and never invents one", () => {
    expect(usDate("2026-11-13")).toBe("11/13/2026");
    expect(usDate(null)).toBe("—");
    expect(usDateTime("2026-11-13T14:05:00Z")).toBe("11/13/2026, 02:05 PM");
  });

  it("shows amounts exactly as the server sent them", () => {
    expect(amountText("150.00")).toBe("150.00");
    expect(amountText("0.00")).toBe("0.00");
    expect(amountText(null)).toBe("—");
  });

  it("offers every transition target except written_off", () => {
    expect(TRANSITION_STATUSES).toEqual(["open", "to_reclaim", "submitted", "recovered", "closed_no_action"]);
  });

  it("renders server labels and reason codes as text", () => {
    expect(statusLabel("closed_no_action")).toBe("Closed, no action");
    expect(statusLabel("not_a_status")).toBe("not_a_status");
    expect(reasonLabel("price_difference")).toBe("price difference");
  });

  it("names the owner from the session id only", () => {
    expect(ownerText(null, "u1")).toBe("Unassigned");
    expect(ownerText("u1", "u1")).toBe("You");
    expect(ownerText("u2", "u1")).toBe("u2");
  });

  it("orders the timeline newest first", () => {
    const rows = newestFirst([event("a", "2026-11-01T10:00:00Z"), event("b", "2026-11-02T10:00:00Z")]);
    expect(rows.map((row) => row.id)).toEqual(["b", "a"]);
  });

  it("links only variance-case notifications", () => {
    const base: NotificationOut = {
      id: "n1",
      title: "Reminder",
      body: null,
      kind: "deadline",
      due_date: "2026-11-13",
      created_at: "2026-11-01T10:00:00Z",
      read_at: null,
      entity_type: "variance_case",
      entity_id: "c1",
    };
    expect(notificationCaseLink(base)).toBe("/recovery/c1");
    expect(notificationCaseLink({ ...base, entity_type: "claim_batch" })).toBeNull();
  });

  it("finds a batch id only when the server includes one", () => {
    const uuid = "3f0a6f4e-2b7b-4c02-9f5d-4f6a1b2c3d4e";
    expect(batchIdFromDetails([{ field: "claim_batch_id", message: `batch ${uuid}` }])).toBe(uuid);
    expect(batchIdFromDetails([{ field: "reason", message: "included" }])).toBeNull();
  });
});
