import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { components } from "../../api/schema";
import {
  Alert,
  Button,
  Card,
  ErrorAlert,
  Field,
  Loading,
  PermissionDenied,
  fieldError,
} from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, PagerControls, SelectField, usePager } from "../masterdata/common";
import { AgreementsNav, useManufacturerMap } from "./common";
import { buildAmendmentPayload, type ItemOut, VERSION_STATUS_LABELS } from "./logic";
import { formatDate, formatDateTime } from "../../lib/format";

async function fetchAllVersionItems(versionId: string): Promise<ItemOut[]> {
  const items: ItemOut[] = [];
  let cursor: string | null = null;
  for (;;) {
    const page: components["schemas"]["ItemList"] = await unwrap(
      api.GET("/api/v1/agreement-versions/{version_id}/items", {
        params: { path: { version_id: versionId }, query: { limit: 200, cursor } },
      }),
    );
    items.push(...page.data);
    if (!page.page.has_more || !page.page.next_cursor) break;
    cursor = page.page.next_cursor;
  }
  return items;
}

export function AgreementDetailPage() {
  const { agreementId = "" } = useParams();
  const { can } = useSession();
  const navigate = useNavigate();
  const client = useQueryClient();
  const pager = usePager();
  const manufacturers = useManufacturerMap();

  const [cancelOpen, setCancelOpen] = useState(false);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (cancelOpen) document.getElementById("cancel-effective-date")?.focus();
    else if (wasOpen.current) document.getElementById("cancel-toggle")?.focus();
    wasOpen.current = cancelOpen;
  }, [cancelOpen]);
  const [effectiveDate, setEffectiveDate] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const [compareFrom, setCompareFrom] = useState("");
  const [compareTo, setCompareTo] = useState("");

  const agreement = useQuery({
    queryKey: ["agreements", agreementId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreements/{agreement_id}", { params: { path: { agreement_id: agreementId } } }),
      ),
  });

  const versions = useQuery({
    queryKey: ["agreements", agreementId, "versions", pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreements/{agreement_id}/versions", {
          params: { path: { agreement_id: agreementId }, query: { limit: 50, cursor: pager.cursor } },
        }),
      ),
  });

  const amend = useMutation({
    mutationFn: async (versionId: string) => {
      const version = await unwrap(
        api.GET("/api/v1/agreement-versions/{version_id}", { params: { path: { version_id: versionId } } }),
      );
      let mfgCode = manufacturers.get(version.manufacturer_id)?.code ?? "";
      if (!mfgCode) {
        const mfg = await unwrap(
          api.GET("/api/v1/manufacturers/{manufacturer_id}", {
            params: { path: { manufacturer_id: version.manufacturer_id } },
          }),
        );
        mfgCode = mfg.code;
      }
      const items = await fetchAllVersionItems(versionId);
      const payload = buildAmendmentPayload(version, mfgCode, items);
      return unwrap(
        api.POST("/api/v1/agreement-drafts", {
          body: {
            source: "manual",
            agreement_id: agreementId,
            amends_version_id: versionId,
            payload: payload as unknown as Record<string, unknown>,
          },
        }),
      );
    },
    onSuccess: (draft) => {
      navigate(`/agreement-drafts/${draft.id}`);
    },
  });

  const cancel = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/agreements/{agreement_id}/cancel", {
          params: { path: { agreement_id: agreementId } },
          body: { effective_date: effectiveDate, reason: cancelReason },
        }),
      ),
    onSuccess: () => {
      setCancelOpen(false);
      setEffectiveDate("");
      setCancelReason("");
      void client.invalidateQueries({ queryKey: ["agreements", agreementId] });
      void client.invalidateQueries({ queryKey: ["agreements"] });
    },
  });

  const agreementErr = agreement.error as ApiError | null;
  const cancelErr = cancel.error as ApiError | null;
  const amendErr = amend.error as ApiError | null;
  const versionRows = versions.data?.data ?? [];

  const resolvedFrom = compareFrom || versionRows[1]?.id || versionRows[0]?.id || "";
  const resolvedTo = compareTo || versionRows[0]?.id || "";

  if (agreementErr?.status === 403 || agreementErr?.code === "forbidden") {
    return (
      <>
        <AgreementsNav />
        <PermissionDenied />
      </>
    );
  }

  return (
    <>
      <AgreementsNav />
      <div className="flex flex-col gap-6">
        {agreementErr?.status === 404 || agreementErr?.code === "not_found" ? (
          <Card title="Agreement not found">
            <Alert>This agreement could not be found.</Alert>
            <div className="mt-4">
              <Link to="/agreements" className="text-brand underline">
                Back to agreements
              </Link>
            </div>
          </Card>
        ) : (
          <>
            <Card
              title={
                agreement.data ? `${agreement.data.external_number} · ${agreement.data.name}` : "Agreement"
              }
              actions={
                can("agreement.approve") && agreement.data && !agreement.data.cancelled_at ? (
                  <Button
                    id="cancel-toggle"
                    variant="danger"
                    aria-expanded={cancelOpen}
                    aria-controls="cancel-form"
                    onClick={() => setCancelOpen((v) => !v)}
                  >
                    Cancel agreement
                  </Button>
                ) : undefined
              }
            >
              <ErrorAlert error={agreementErr} />
              {agreement.isPending ? (
                <Loading />
              ) : (
                agreement.data && (
                  <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div>
                      <dt className="text-muted">External number</dt>
                      <dd className="font-medium">{agreement.data.external_number}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Type</dt>
                      <dd className="font-medium">{agreement.data.agreement_type.toUpperCase()}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Manufacturer</dt>
                      <dd className="font-medium">
                        {(() => {
                          const m = manufacturers.get(agreement.data.manufacturer_id);
                          return m ? `${m.name} (${m.code})` : agreement.data.manufacturer_id;
                        })()}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted">Status</dt>
                      <dd className="font-medium">
                        {agreement.data.cancelled_at ? (
                          <span className="text-danger">
                            Cancelled ({formatDateTime(agreement.data.cancelled_at)})
                          </span>
                        ) : agreement.data.current_version ? (
                          (VERSION_STATUS_LABELS[agreement.data.current_version.status] ??
                          agreement.data.current_version.status)
                        ) : (
                          "No active version"
                        )}
                      </dd>
                    </div>
                  </dl>
                )
              )}

              {cancelOpen && (
                <form
                  id="cancel-form"
                  className="mt-6 flex max-w-lg flex-col gap-3 rounded-md border border-line p-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    cancel.mutate();
                  }}
                >
                  <h2 className="text-base font-semibold">Cancel agreement</h2>
                  <Field
                    id="cancel-effective-date"
                    label="Effective date"
                    type="date"
                    required
                    value={effectiveDate}
                    error={fieldError(cancelErr, "effective_date")}
                    onChange={(e) => setEffectiveDate(e.target.value)}
                  />
                  <Field
                    label="Reason"
                    required
                    value={cancelReason}
                    error={fieldError(cancelErr, "reason")}
                    onChange={(e) => setCancelReason(e.target.value)}
                  />
                  <ErrorAlert error={cancelErr} />
                  <div className="flex gap-3">
                    <Button type="submit" variant="danger" disabled={cancel.isPending}>
                      Confirm cancel
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setCancelOpen(false);
                        cancel.reset();
                      }}
                    >
                      Close
                    </Button>
                  </div>
                </form>
              )}
            </Card>

            <Card title="Version history">
              <ErrorAlert error={amendErr} />
              {versionRows.length >= 2 && (
                <div className="mb-4 flex flex-wrap items-end gap-3">
                  <SelectField
                    label="Compare from"
                    value={resolvedFrom}
                    onChange={(e) => setCompareFrom(e.target.value)}
                  >
                    {versionRows.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number} ({v.status})
                      </option>
                    ))}
                  </SelectField>
                  <SelectField
                    label="Compare to"
                    value={resolvedTo}
                    onChange={(e) => setCompareTo(e.target.value)}
                  >
                    {versionRows.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number} ({v.status})
                      </option>
                    ))}
                  </SelectField>
                  <Button
                    variant="secondary"
                    disabled={!resolvedFrom || !resolvedTo || resolvedFrom === resolvedTo}
                    onClick={() => navigate(`/agreements/versions/${resolvedFrom}/diff/${resolvedTo}`)}
                  >
                    Compare versions
                  </Button>
                </div>
              )}

              <DataTable
                caption="Version history"
                loading={versions.isPending}
                error={versions.error}
                rows={versions.data?.data}
                empty="No approved versions yet."
                columns={[
                  {
                    header: "Version",
                    cell: (v) => (
                      <Link to={`/agreements/versions/${v.id}`} className="text-brand underline">
                        v{v.version_number}
                      </Link>
                    ),
                  },
                  { header: "Status", cell: (v) => VERSION_STATUS_LABELS[v.status] ?? v.status },
                  { header: "Valid from", cell: (v) => formatDate(v.valid_from) },
                  { header: "Valid to", cell: (v) => formatDate(v.valid_to) },
                  { header: "Activated", cell: (v) => formatDateTime(v.activated_at) },
                  {
                    header: "Lineage",
                    cell: (v) => (
                      <div className="flex flex-wrap gap-2">
                        {v.supersedes_version_id && (
                          <Link
                            to={`/agreements/versions/${v.supersedes_version_id}/diff/${v.id}`}
                            className="text-brand underline"
                          >
                            Diff prior
                          </Link>
                        )}
                        {v.derived_from_version_id && (
                          <Link
                            to={`/agreements/versions/${v.derived_from_version_id}`}
                            className="text-brand underline"
                          >
                            Derived from
                          </Link>
                        )}
                        {!v.supersedes_version_id && !v.derived_from_version_id && "—"}
                      </div>
                    ),
                  },
                  {
                    header: "Actions",
                    cell: (v) =>
                      can("agreement.draft") && (v.status === "active" || v.status === "expired") ? (
                        <Button
                          variant="secondary"
                          disabled={amend.isPending}
                          onClick={() => amend.mutate(v.id)}
                        >
                          Amend version
                        </Button>
                      ) : (
                        "—"
                      ),
                  },
                ]}
              />
              <PagerControls pager={pager} page={versions.data?.page} />
            </Card>
          </>
        )}

        <div>
          <Link to="/agreements" className="text-brand underline">
            Back to agreements
          </Link>
        </div>
      </div>
    </>
  );
}
