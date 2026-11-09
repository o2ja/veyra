import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { components } from "../../api/schema";
import { Alert, Button, Card, ErrorAlert, Field, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";

type Settings = components["schemas"]["SettingsOut"];

export function SettingsPage() {
  const query = useQuery({ queryKey: ["settings"], queryFn: () => unwrap(api.GET("/api/v1/settings")) });
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorAlert error={query.error as ApiError | null} />;
  return <SettingsForm initial={query.data} refetch={async () => (await query.refetch()).data} />;
}

function SettingsForm({
  initial,
  refetch,
}: {
  initial: Settings;
  refetch: () => Promise<Settings | undefined>;
}) {
  const client = useQueryClient();
  const { can } = useSession();
  const editable = can("settings.write");
  const [form, setForm] = useState<Settings>(initial);
  const [saved, setSaved] = useState(false);

  // after a conflict: load the server's current values (and revision) into the form
  async function reload() {
    const fresh = await refetch();
    if (fresh) setForm(fresh);
    save.reset();
  }

  const save = useMutation({
    mutationFn: (values: Settings) =>
      unwrap(
        api.PATCH("/api/v1/settings", {
          // optimistic concurrency: the revision we edited (API_SPECIFICATION.md §4)
          params: { header: { "if-match": `"${values.revision}"` } },
          body: {
            business_timezone: values.business_timezone,
            mfa_required: values.mfa_required,
            four_eyes_required: values.four_eyes_required,
            ai_processing_enabled: values.ai_processing_enabled,
            retention_years: values.retention_years,
          },
        }),
      ),
    onSuccess: (data) => {
      client.setQueryData(["settings"], data);
      setForm(data);
      setSaved(true);
    },
  });
  const error = save.error as ApiError | null;

  return (
    <Card title="Workspace settings">
      <form
        className="flex max-w-lg flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          setSaved(false);
          save.mutate(form);
        }}
      >
        {!editable && <Alert tone="info">Only admins can change settings.</Alert>}
        {saved && <Alert tone="ok">Settings saved.</Alert>}
        {error?.code === "precondition_failed" ? (
          <Alert>
            Someone else changed these settings.{" "}
            <button type="button" className="underline" onClick={() => void reload()}>
              Reload
            </button>{" "}
            and try again.
          </Alert>
        ) : (
          <ErrorAlert error={error} />
        )}
        <Field
          label="Business time zone"
          value={form.business_timezone}
          disabled={!editable}
          hint="IANA name, for example America/Chicago. Deadlines are computed in this zone."
          onChange={(e) => setForm({ ...form, business_timezone: e.target.value.trim() })}
        />
        <Field label="Currency" value={form.currency_code} disabled hint="USD only in this release." />
        {(
          [
            ["mfa_required", "Require two-factor authentication for everyone"],
            ["four_eyes_required", "Require a second person to approve (four-eyes)"],
            ["ai_processing_enabled", "Allow AI document reading (off by default)"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form[key]}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, [key]: e.target.checked })}
            />
            {label}
          </label>
        ))}
        <Field
          label="Retention (years)"
          type="number"
          min={1}
          max={30}
          value={form.retention_years}
          disabled={!editable}
          onChange={(e) => setForm({ ...form, retention_years: Number(e.target.value) })}
        />
        {editable && (
          <Button type="submit" disabled={save.isPending}>
            Save settings
          </Button>
        )}
      </form>
    </Card>
  );
}
