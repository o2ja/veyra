import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { components } from "../../api/schema";
import { Alert, Button, Card, ErrorAlert, Field, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { STATUS_TEXT as SCAN_TEXT, uploadWithProgress } from "../files/files-page";
import { SelectField } from "../masterdata/common";
import { TYPE_TEXT } from "./logic";

type ImportType = components["schemas"]["ImportIn"]["import_type"];

/** Wizard step 1: pick or upload the file, choose the import type, start. The rest is on the import page. */
export function NewImportPage() {
  const navigate = useNavigate();
  const { can } = useSession();
  const input = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<"upload" | "existing">("upload");
  const [fileId, setFileId] = useState("");
  const [type, setType] = useState<ImportType>("sales");
  const [system, setSystem] = useState("ERP1");
  const [template, setTemplate] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  // one key per attempt: a retried submit after a network error cannot start a second import
  const idempotencyKey = useRef(crypto.randomUUID());

  const files = useQuery({
    queryKey: ["files"],
    queryFn: () => unwrap(api.GET("/api/v1/files")),
    enabled: source === "existing",
  });
  const importable = files.data?.data.filter(
    (f) => f.kind === "import" && (f.scan_status === "clean" || f.scan_status === "pending"),
  );
  const templates = useQuery({
    queryKey: ["mapping-templates", type],
    queryFn: () => unwrap(api.GET("/api/v1/mapping-templates", { params: { query: { import_type: type } } })),
    enabled: can("mapping.write"),
  });

  async function start(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      let id = fileId;
      if (source === "upload") {
        const file = input.current?.files?.[0];
        if (!file) return;
        setProgress(0);
        id = (await uploadWithProgress(file, "import", setProgress)).id;
        setFileId(id); // a failed start can be retried without uploading again
        setSource("existing");
      }
      const r = await unwrap(
        api.POST("/api/v1/imports", {
          params: { header: { "idempotency-key": idempotencyKey.current } },
          body: { file_id: id, import_type: type, source_system: system, mapping_template_id: template || null },
        }),
      );
      navigate(`/imports/${r.import.id}`);
    } catch (e) {
      // refused by the server: a corrected retry is a new request. A network error keeps the key (the
      // request may have arrived), so retrying replays it instead of starting a second import.
      if (!(e instanceof ApiError && e.status === 0)) idempotencyKey.current = crypto.randomUUID();
      setError(e instanceof ApiError ? e : new ApiError(0, "internal_error", "Something went wrong.", [], null));
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  return (
    <Card title="New import">
      <form className="flex max-w-2xl flex-col gap-4" onSubmit={(e) => void start(e)}>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">File</legend>
          <label className="flex items-center gap-2 text-sm">
            <input type="radio" name="source" checked={source === "upload"} onChange={() => setSource("upload")} />
            Upload a new file (CSV or XLSX)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="source"
              checked={source === "existing"}
              onChange={() => setSource("existing")}
            />
            Use a file that is already uploaded (for example to import it again)
          </label>
        </fieldset>
        {source === "upload" ? (
          <div className="flex flex-col gap-1">
            <label htmlFor="import-file" className="text-sm font-medium">
              File to upload
            </label>
            <input id="import-file" ref={input} type="file" accept=".csv,.xlsx" required className="text-sm" />
          </div>
        ) : (
          <SelectField label="Uploaded file" required value={fileId} onChange={(e) => setFileId(e.target.value)}>
            <option value="">{files.isPending ? "Loading…" : "Choose…"}</option>
            {importable?.map((f) => (
              <option key={f.id} value={f.id}>
                {f.original_name} · {SCAN_TEXT[f.scan_status] ?? f.scan_status} · {f.created_at.slice(0, 10)}
              </option>
            ))}
          </SelectField>
        )}
        <SelectField label="What the file contains" value={type} onChange={(e) => setType(e.target.value as ImportType)}>
          {Object.entries(TYPE_TEXT).map(([v, t]) => (
            <option key={v} value={v}>
              {t}
            </option>
          ))}
        </SelectField>
        <Field
          label="Source system"
          required
          hint="Name of the ERP or system the file came from (letters, digits, . _ -)"
          value={system}
          error={fieldError(error, "source_system")}
          onChange={(e) => setSystem(e.target.value.trim())}
        />
        {can("mapping.write") && (
          <SelectField label="Column mapping template" value={template} onChange={(e) => setTemplate(e.target.value)}>
            <option value="">Automatic (reuse a saved template for these headers, else map now)</option>
            {templates.data?.data.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} (version {t.version})
              </option>
            ))}
          </SelectField>
        )}
        {progress !== null && (
          <progress className="w-full" max={100} value={progress} aria-label="Upload progress">
            {progress}%
          </progress>
        )}
        {error?.code === "duplicate_file" ? (
          <Alert>
            This file was already uploaded. Choose “Use a file that is already uploaded” and pick it from the list.
          </Alert>
        ) : (
          <ErrorAlert error={error} />
        )}
        <div>
          <Button type="submit" disabled={busy}>
            Start import
          </Button>
        </div>
      </form>
    </Card>
  );
}
