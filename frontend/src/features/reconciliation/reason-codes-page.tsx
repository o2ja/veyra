import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { ManufacturerSelect, SelectField } from "../masterdata/common";
import { ReconciliationNav, ValidationDetailsAlert } from "./common";
import { REASON_CODE_LABELS, REASON_CODES, type ReasonCodeMappingIn } from "./logic";

export function ReasonCodesPage() {
  const { can } = useSession();
  const client = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const manufacturerId = searchParams.get("manufacturer_id") ?? "";
  const [localEdits, setLocalEdits] = useState<ReasonCodeMappingIn[] | null>(null);
  const [savedSuccess, setSavedSuccess] = useState(false);

  const query = useQuery({
    queryKey: ["reason-code-mappings", manufacturerId],
    enabled: Boolean(manufacturerId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/manufacturers/{manufacturer_id}/reason-code-mappings", {
          params: { path: { manufacturer_id: manufacturerId } },
        }),
      ),
  });

  const mappings: ReasonCodeMappingIn[] =
    localEdits ??
    query.data?.data.map((m) => ({
      external_code: m.external_code,
      external_text: m.external_text,
      reason_code: m.reason_code,
    })) ??
    [];

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!manufacturerId) throw new Error("No manufacturer selected");
      return unwrap(
        api.PUT("/api/v1/manufacturers/{manufacturer_id}/reason-code-mappings", {
          params: { path: { manufacturer_id: manufacturerId } },
          body: { mappings },
        }),
      );
    },
    onSuccess: () => {
      setSavedSuccess(true);
      setLocalEdits(null);
      void client.invalidateQueries({ queryKey: ["reason-code-mappings", manufacturerId] });
      setTimeout(() => setSavedSuccess(false), 4000);
    },
  });

  function setManufacturer(mfg: string) {
    const next = new URLSearchParams(searchParams);
    if (mfg) next.set("manufacturer_id", mfg);
    else next.delete("manufacturer_id");
    setSearchParams(next, { replace: true });
    setSavedSuccess(false);
    setLocalEdits(null);
  }

  function addRow() {
    setLocalEdits([
      ...mappings,
      {
        external_code: "",
        external_text: "",
        reason_code: "unknown",
      },
    ]);
  }

  function updateRow(index: number, patch: Partial<ReasonCodeMappingIn>) {
    setLocalEdits(mappings.map((item, idx) => (idx === index ? { ...item, ...patch } : item)));
  }

  function removeRow(index: number) {
    setLocalEdits(mappings.filter((_, idx) => idx !== index));
  }

  const queryErr = query.error as ApiError | null;
  const saveErr = saveMutation.error as ApiError | null;
  const canWrite = can("masterdata.write");

  return (
    <>
      <ReconciliationNav />

      <div className="flex flex-col gap-6">
        <Card title="Reason code mappings">
          <div className="mb-4 max-w-sm">
            <ManufacturerSelect
              value={manufacturerId}
              onChange={setManufacturer}
              allowAny="Select a manufacturer"
            />
          </div>

          {!manufacturerId && (
            <p className="text-sm text-muted">
              Select a manufacturer above to view and configure external reason code mappings.
            </p>
          )}

          {manufacturerId && (
            <div className="flex flex-col gap-4">
              {savedSuccess && <Alert tone="ok">Reason code mappings saved successfully.</Alert>}
              {queryErr && <ErrorAlert error={queryErr} />}
              {saveErr && <ValidationDetailsAlert error={saveErr} />}

              {query.isPending ? (
                <Loading />
              ) : (
                <div className="flex flex-col gap-4">
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <caption className="sr-only">Reason code mappings</caption>
                      <thead className="border-b border-line text-muted">
                        <tr>
                          <th scope="col" className="py-2 pr-4 font-medium">
                            External code <span className="text-danger">*</span>
                          </th>
                          <th scope="col" className="py-2 pr-4 font-medium">
                            External description
                          </th>
                          <th scope="col" className="py-2 pr-4 font-medium">
                            Controlled reason code <span className="text-danger">*</span>
                          </th>
                          {canWrite && (
                            <th scope="col" className="py-2 pr-4 font-medium">
                              Actions
                            </th>
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {mappings.length === 0 ? (
                          <tr>
                            <td colSpan={4} className="py-4 text-center text-muted">
                              No reason code mappings configured for this manufacturer.
                            </td>
                          </tr>
                        ) : (
                          mappings.map((m, idx) => (
                            <tr key={idx} className="border-b border-line">
                              <td className="py-2 pr-4">
                                <input
                                  type="text"
                                  disabled={!canWrite}
                                  value={m.external_code}
                                  placeholder="e.g. 01, REJ-01"
                                  className="w-full rounded border border-line bg-surface px-2 py-1 text-sm font-mono"
                                  aria-label={`External code row ${idx + 1}`}
                                  onChange={(e) => updateRow(idx, { external_code: e.target.value })}
                                />
                              </td>
                              <td className="py-2 pr-4">
                                <input
                                  type="text"
                                  disabled={!canWrite}
                                  value={m.external_text ?? ""}
                                  placeholder="e.g. Price discrepancy"
                                  className="w-full rounded border border-line bg-surface px-2 py-1 text-sm"
                                  aria-label={`External description row ${idx + 1}`}
                                  onChange={(e) => updateRow(idx, { external_text: e.target.value })}
                                />
                              </td>
                              <td className="py-2 pr-4">
                                <SelectField
                                  label={`Controlled reason code row ${idx + 1}`}
                                  id={`reason-code-select-${idx}`}
                                  disabled={!canWrite}
                                  value={m.reason_code}
                                  onChange={(e) =>
                                    updateRow(idx, {
                                      reason_code: e.target.value as ReasonCodeMappingIn["reason_code"],
                                    })
                                  }
                                >
                                  {REASON_CODES.map((rc) => (
                                    <option key={rc} value={rc}>
                                      {REASON_CODE_LABELS[rc] ?? rc}
                                    </option>
                                  ))}
                                </SelectField>
                              </td>
                              {canWrite && (
                                <td className="py-2 pr-4">
                                  <Button
                                    variant="danger"
                                    type="button"
                                    aria-label={`Delete mapping row ${idx + 1}`}
                                    onClick={() => removeRow(idx)}
                                  >
                                    Delete
                                  </Button>
                                </td>
                              )}
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>

                  {canWrite && (
                    <div className="flex flex-wrap items-center gap-2">
                      <Button variant="secondary" type="button" onClick={addRow}>
                        Add mapping
                      </Button>
                      <Button
                        type="button"
                        disabled={saveMutation.isPending}
                        onClick={() => saveMutation.mutate()}
                      >
                        {saveMutation.isPending ? "Saving…" : "Save mappings"}
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
