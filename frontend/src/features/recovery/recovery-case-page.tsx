import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  Alert,
  Button,
  Card,
  Empty,
  ErrorAlert,
  Loading,
  PermissionDenied,
  fieldError,
} from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { uploadWithProgress } from "../files/files-page";
import { ActionDialog, AttachmentDownloadButton, GenericTree, RecoveryNav } from "./common";
import {
  DEADLINE_BASIS_LABELS,
  TRANSITION_STATUSES,
  amountText,
  batchIdFromDetails,
  caseTypeLabel,
  newestFirst,
  ownerText,
  reasonLabel,
  statusLabel,
  usDate,
  usDateTime,
  type AttachmentOut,
  type CaseOut,
  type EventOut,
} from "./logic";

type DialogName = "assign" | "status" | "reclaim" | "writeoff" | null;

const isStale = (error: unknown): boolean =>
  error instanceof ApiError && (error.code === "precondition_failed" || error.status === 412);

export function RecoveryCasePage() {
  const { caseId = "" } = useParams();
  const { can, me } = useSession();
  const client = useQueryClient();

  const [dialog, setDialog] = useState<DialogName>(null);
  const [assignOwner, setAssignOwner] = useState("");
  const [statusTarget, setStatusTarget] = useState<string>(TRANSITION_STATUSES[0] ?? "open");
  const [statusNote, setStatusNote] = useState("");
  const [note, setNote] = useState("");
  const [writeOffReason, setWriteOffReason] = useState("");
  const [staleConflict, setStaleConflict] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [adjustmentId, setAdjustmentId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const writeOffKey = useRef<{ revision: number; key: string } | null>(null);

  const detail = useQuery({
    queryKey: ["recovery", "case", caseId],
    queryFn: () => unwrap(api.GET("/api/v1/cases/{case_id}", { params: { path: { case_id: caseId } } })),
  });

  const events = useQuery({
    queryKey: ["recovery", "case", caseId, "events"],
    queryFn: () =>
      unwrap(api.GET("/api/v1/cases/{case_id}/events", { params: { path: { case_id: caseId } } })),
  });

  const attachments = useQuery({
    queryKey: ["recovery", "case", caseId, "attachments"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/cases/{case_id}/attachments", {
          params: { path: { case_id: caseId } },
        }),
      ),
  });

  const members = useQuery({
    queryKey: ["recovery", "members"],
    queryFn: () => unwrap(api.GET("/api/v1/users")),
    enabled: can("user.manage") && dialog === "assign",
  });

  const detailError = detail.error as ApiError | null;

  function applyCase(fresh: CaseOut) {
    setStaleConflict(false);
    client.setQueryData(["recovery", "case", caseId], fresh);
    void client.invalidateQueries({ queryKey: ["recovery", "case", caseId, "events"] });
    void client.invalidateQueries({ queryKey: ["recovery", "cases"] });
    void client.invalidateQueries({ queryKey: ["recovery", "summary"] });
  }

  function reloadCase() {
    setStaleConflict(false);
    void detail.refetch();
    void events.refetch();
    void attachments.refetch();
  }

  const assign = useMutation({
    mutationFn: (ownerUserId: string | null) =>
      unwrap(
        api.PATCH("/api/v1/cases/{case_id}", {
          params: {
            path: { case_id: caseId },
            header: { "if-match": `"${detail.data?.revision ?? 0}"` },
          },
          body: { owner_user_id: ownerUserId },
        }),
      ),
    onSuccess: (fresh) => {
      applyCase(fresh);
      setDialog(null);
      setActionMsg("Owner updated.");
    },
    onError: (error) => {
      if (isStale(error)) setStaleConflict(true);
    },
  });

  const transition = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/cases/{case_id}/transition", {
          params: {
            path: { case_id: caseId },
            header: { "if-match": `"${detail.data?.revision ?? 0}"` },
          },
          body: {
            to_status: statusTarget as CaseOut["status"],
            note: statusNote.trim() === "" ? null : statusNote.trim(),
          },
        }),
      ),
    onSuccess: (fresh) => {
      applyCase(fresh);
      setDialog(null);
      setStatusNote("");
      setActionMsg(`Status changed to ${statusLabel(fresh.status)}.`);
    },
    onError: (error) => {
      if (isStale(error)) setStaleConflict(true);
    },
  });

  const reclaim = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/cases/{case_id}/reclaim", {
          params: {
            path: { case_id: caseId },
            header: { "if-match": `"${detail.data?.revision ?? 0}"` },
          },
        }),
      ),
    onSuccess: (result) => {
      applyCase(result.case);
      setAdjustmentId(result.adjustment_id);
      setDialog(null);
      setActionMsg("Reclaim adjustment created.");
    },
    onError: (error) => {
      if (isStale(error)) setStaleConflict(true);
    },
  });

  const addNote = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/cases/{case_id}/notes", {
          params: { path: { case_id: caseId } },
          body: { note },
        }),
      ),
    onSuccess: () => {
      setNote("");
      setActionMsg("Note added.");
      void client.invalidateQueries({ queryKey: ["recovery", "case", caseId, "events"] });
    },
  });

  const addAttachment = useMutation({
    mutationFn: async (file: File) => {
      setUploadProgress(0);
      const uploaded = await uploadWithProgress(file, "attachment", setUploadProgress);
      for (let attempt = 0; attempt < 60; attempt++) {
        const stored = await unwrap(
          api.GET("/api/v1/files/{file_id}", { params: { path: { file_id: uploaded.id } } }),
        );
        if (stored.scan_status === "clean") break;
        if (stored.scan_status !== "pending") {
          throw new ApiError(
            422,
            "file_not_clean",
            `The file scan finished as "${stored.scan_status}" and it cannot be attached.`,
            [],
            null,
          );
        }
        if (attempt === 59) {
          throw new ApiError(
            0,
            "scan_timeout",
            "The file is still being scanned. Try again in a moment.",
            [],
            null,
          );
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      const attachment = await unwrap(
        api.POST("/api/v1/cases/{case_id}/attachments", {
          params: { path: { case_id: caseId } },
          body: { file_id: uploaded.id },
        }),
      );
      return attachment;
    },
    onSuccess: () => {
      setUploadMsg("Attachment added.");
      setUploadProgress(null);
      if (fileInput.current) fileInput.current.value = "";
      void client.invalidateQueries({ queryKey: ["recovery", "case", caseId, "attachments"] });
    },
    onError: () => setUploadProgress(null),
  });

  const writeOff = useMutation({
    mutationFn: () => {
      const revision = detail.data?.revision ?? 0;
      if (!writeOffKey.current || writeOffKey.current.revision !== revision) {
        writeOffKey.current = { revision, key: crypto.randomUUID() };
      }
      return unwrap(
        api.POST("/api/v1/cases/{case_id}/write-off", {
          params: {
            path: { case_id: caseId },
            header: {
              "if-match": `"${revision}"`,
              "idempotency-key": writeOffKey.current.key,
            },
          },
          body: { reason: writeOffReason.trim() },
        }),
      );
    },
    onSuccess: (fresh) => {
      writeOffKey.current = null;
      applyCase(fresh);
      setDialog(null);
      setWriteOffReason("");
      setActionMsg(
        fresh.status === "written_off"
          ? "Case written off."
          : "Write-off proposed; a second approver must complete it.",
      );
    },
    onError: (error) => {
      if (isStale(error)) setStaleConflict(true);
    },
  });

  if (detailError?.status === 403 || detailError?.code === "forbidden") {
    return (
      <>
        <RecoveryNav />
        <PermissionDenied />
      </>
    );
  }

  if (detailError?.status === 404 || detailError?.code === "not_found") {
    return (
      <>
        <RecoveryNav />
        <Card title="Recovery case not found">
          <Alert>This recovery case could not be found.</Alert>
          <div className="mt-4">
            <Link to="/recovery" className="text-brand underline">
              Back to the recovery queue
            </Link>
          </div>
        </Card>
      </>
    );
  }

  const item = detail.data;
  const canWork = can("case.work");
  const canWriteOff = can("case.write_off");
  const proposalMine = Boolean(item?.write_off_proposed_by && item.write_off_proposed_by === me?.user.id);
  const proposalOther = Boolean(item?.write_off_proposed_by && item.write_off_proposed_by !== me?.user.id);
  const writeOffLabel = proposalMine
    ? "Complete write-off"
    : proposalOther
      ? "Approve write-off"
      : "Write off";
  const reclaimError = reclaim.error as ApiError | null;
  const includedBatchId = reclaimError ? batchIdFromDetails(reclaimError.details) : null;
  const eventRows: EventOut[] = events.data?.data ?? [];
  const attachmentRows: AttachmentOut[] = attachments.data ?? [];
  const noteError = addNote.error as ApiError | null;
  const uploadError = addAttachment.error as ApiError | null;

  function openDialog(next: Exclude<DialogName, null>) {
    setDialog(next);
    setActionMsg(null);
    if (next === "assign") {
      setAssignOwner(item?.owner_user_id ?? "");
      assign.reset();
    }
    if (next === "status") {
      transition.reset();
    }
    if (next === "reclaim") {
      reclaim.reset();
    }
    if (next === "writeoff") {
      writeOff.reset();
      setWriteOffReason(item?.write_off_reason ?? "");
    }
  }

  return (
    <>
      <RecoveryNav />
      <div className="flex flex-col gap-6">
        <Card
          title={item ? `Recovery case ${item.id.slice(0, 8)}` : "Recovery case"}
          actions={
            item && (
              <div className="flex flex-wrap gap-2">
                {canWork && (
                  <Button variant="secondary" onClick={() => openDialog("assign")}>
                    Assign
                  </Button>
                )}
                {canWork && (
                  <Button variant="secondary" onClick={() => openDialog("status")}>
                    Change status
                  </Button>
                )}
                {canWork && (
                  <Button variant="secondary" onClick={() => openDialog("reclaim")}>
                    Reclaim
                  </Button>
                )}
                {canWriteOff && item.status !== "written_off" && (
                  <Button
                    variant="danger"
                    disabled={proposalMine}
                    onClick={() => openDialog("writeoff")}
                    title={
                      proposalMine
                        ? "A write-off proposed by you must be completed by a different approver."
                        : undefined
                    }
                  >
                    {writeOffLabel}
                  </Button>
                )}
              </div>
            )
          }
        >
          <ErrorAlert error={detailError} />
          {detail.isPending && <Loading label="Loading recovery case…" />}
          {staleConflict && (
            <div className="mb-4">
              <Alert>
                Someone else changed this case.{" "}
                <button type="button" className="underline" onClick={reloadCase}>
                  Reload
                </button>{" "}
                and try again.
              </Alert>
            </div>
          )}
          {proposalMine && item?.status !== "written_off" && (
            <div className="mb-4">
              <Alert tone="info">
                You proposed this write-off. A different approver must complete it.
              </Alert>
            </div>
          )}
          {actionMsg && (
            <div className="mb-4">
              <Alert tone="ok">
                {actionMsg}{" "}
                {adjustmentId && <span className="font-mono text-xs">Adjustment {adjustmentId}</span>}
              </Alert>
            </div>
          )}

          {item && (
            <div className="flex flex-col gap-5">
              <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <Amount label="Claimed" value={item.claimed_amount} />
                <Amount label="Paid" value={item.paid_amount} />
                <Amount label="Variance" value={item.variance_amount} />
                <Amount label="Recovered" value={item.recovered_amount} />
                <Amount label="Written off" value={item.written_off_amount} />
                <div>
                  <dt className="text-muted">Deadline</dt>
                  <dd className="font-medium" aria-label="Case deadline">
                    {item.deadline_unknown ? (
                      <span className="rounded border border-line px-1.5 py-0.5 text-xs font-medium">
                        Unknown deadline
                      </span>
                    ) : (
                      usDate(item.deadline_date)
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Deadline basis</dt>
                  <dd aria-label="Deadline basis">
                    {item.deadline_basis ? DEADLINE_BASIS_LABELS[item.deadline_basis] : "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Status</dt>
                  <dd className="font-medium" aria-label="Case status">
                    {statusLabel(item.status)}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Type</dt>
                  <dd aria-label="Case type">{caseTypeLabel(item.case_type)}</dd>
                </div>
                <div>
                  <dt className="text-muted">Reason</dt>
                  <dd aria-label="Case reason">
                    {reasonLabel(item.reason_code)}
                    {item.external_reason_code && (
                      <span className="block text-xs text-muted">{item.external_reason_code}</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Owner</dt>
                  <dd aria-label="Case owner">{ownerText(item.owner_user_id, me?.user.id)}</dd>
                </div>
                <div>
                  <dt className="text-muted">Revision</dt>
                  <dd className="font-mono" aria-label="Case revision">
                    {item.revision}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Created</dt>
                  <dd>{usDateTime(item.created_at)}</dd>
                </div>
                <div>
                  <dt className="text-muted">Closed</dt>
                  <dd>{item.closed_at ? usDateTime(item.closed_at) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted">Written off</dt>
                  <dd>{item.written_off_at ? usDateTime(item.written_off_at) : "—"}</dd>
                </div>
                {item.write_off_reason && (
                  <div className="sm:col-span-2">
                    <dt className="text-muted">Write-off reason</dt>
                    <dd>{item.write_off_reason}</dd>
                  </div>
                )}
              </dl>

              {proposalMine && (
                <Alert tone="info">
                  You proposed this write-off
                  {item.write_off_proposed_at ? ` on ${usDateTime(item.write_off_proposed_at)}` : ""}. A
                  different approver must complete it.
                </Alert>
              )}
              {proposalOther && (
                <Alert tone="info">
                  A write-off was proposed
                  {item.write_off_proposed_at ? ` on ${usDateTime(item.write_off_proposed_at)}` : ""} and
                  awaits a second approver.
                </Alert>
              )}
            </div>
          )}
        </Card>

        {item && (
          <Card title="Dispute assessment">
            <GenericTree
              data={item.dispute_assessment}
              label="Dispute assessment"
              empty="No dispute assessment recorded."
            />
          </Card>
        )}

        <Card title="Timeline">
          <ErrorAlert error={events.error as ApiError | null} />
          {events.isPending ? (
            <Loading label="Loading timeline…" />
          ) : eventRows.length === 0 ? (
            <Empty>No events yet.</Empty>
          ) : (
            <ol className="flex flex-col gap-4">
              {newestFirst(eventRows).map((event) => (
                <li key={event.id} className="border-l-2 border-line pl-4 text-sm">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-medium">{reasonLabel(event.event_type)}</span>
                    <span className="text-xs text-muted">{usDateTime(event.created_at)}</span>
                  </div>
                  {(event.from_status || event.to_status) && (
                    <p className="mt-1">
                      {event.from_status ? statusLabel(event.from_status) : "—"} →{" "}
                      {event.to_status ? statusLabel(event.to_status) : "—"}
                    </p>
                  )}
                  {event.note && <p className="mt-1 whitespace-pre-wrap">{event.note}</p>}
                  <p className="mt-1 text-xs text-muted">By {event.actor_user_id ?? "System"}</p>
                  {event.payload && Object.keys(event.payload).length > 0 && (
                    <div className="mt-2">
                      <GenericTree
                        data={event.payload}
                        label={`Payload for ${event.event_type}`}
                        empty="No payload."
                      />
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}
        </Card>

        {item && canWork && (
          <Card title="Add a note">
            <form
              className="flex flex-col gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                addNote.mutate();
              }}
            >
              <label htmlFor="case-note" className="text-sm font-medium">
                Note
              </label>
              <textarea
                id="case-note"
                className="min-h-24 rounded-md border border-line bg-panel px-3 py-2 text-sm"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                aria-invalid={fieldError(noteError, "note") ? true : undefined}
                aria-describedby={fieldError(noteError, "note") ? "case-note-error" : undefined}
              />
              {fieldError(noteError, "note") && (
                <p id="case-note-error" className="text-xs text-danger">
                  {fieldError(noteError, "note")}
                </p>
              )}
              <div className="flex items-center gap-3">
                <Button type="submit" disabled={addNote.isPending}>
                  {addNote.isPending ? "Adding…" : "Add note"}
                </Button>
                {actionMsg === "Note added." && <span className="text-sm text-ok">Note added.</span>}
              </div>
              <ErrorAlert error={noteError} />
            </form>
          </Card>
        )}

        <Card title="Attachments">
          <ErrorAlert error={attachments.error as ApiError | null} />
          {item && canWork && (
            <form
              className="mb-5 flex flex-col gap-3 border-b border-line pb-5"
              onSubmit={(event) => {
                event.preventDefault();
                const file = fileInput.current?.files?.[0];
                if (file) addAttachment.mutate(file);
              }}
            >
              <label htmlFor="case-attachment" className="text-sm font-medium">
                Attachment file
              </label>
              <input
                id="case-attachment"
                ref={fileInput}
                type="file"
                accept=".pdf,.csv,.xlsx"
                className="text-sm"
              />
              {uploadProgress !== null && (
                <progress className="w-full" max={100} value={uploadProgress} aria-label="Upload progress">
                  {uploadProgress}%
                </progress>
              )}
              <div className="flex items-center gap-3">
                <Button type="submit" disabled={addAttachment.isPending}>
                  {addAttachment.isPending ? "Uploading…" : "Upload attachment"}
                </Button>
                {uploadMsg && <span className="text-sm text-ok">{uploadMsg}</span>}
              </div>
              <ErrorAlert error={uploadError} />
            </form>
          )}
          {attachments.isPending ? (
            <Loading label="Loading attachments…" />
          ) : attachmentRows.length === 0 ? (
            <Empty>No attachments.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Case attachments</caption>
                <thead className="text-muted">
                  <tr>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      File
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Added
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Added by
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {attachmentRows.map((attachment) => (
                    <tr key={attachment.id} className="border-t border-line">
                      <td className="py-2 pr-4 font-mono text-xs">{attachment.file_id}</td>
                      <td className="py-2 pr-4">{usDateTime(attachment.added_at)}</td>
                      <td className="py-2 pr-4 font-mono text-xs">{attachment.added_by}</td>
                      <td className="py-2 pr-4">
                        <AttachmentDownloadButton fileId={attachment.file_id} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {item && canWork && (
          <ActionDialog
            open={dialog === "assign"}
            onOpenChange={(open) => setDialog(open ? "assign" : null)}
            title="Assign this case"
            description="Pick an owner for the recovery work."
            confirmLabel="Save assignment"
            busy={assign.isPending}
            onConfirm={() => assign.mutate(assignOwner || null)}
          >
            <div className="flex flex-col gap-3">
              <label htmlFor="assign-owner" className="text-sm font-medium">
                Owner
              </label>
              <select
                id="assign-owner"
                className="rounded-md border border-line bg-panel px-3 py-2 text-sm"
                value={assignOwner}
                onChange={(event) => setAssignOwner(event.target.value)}
              >
                <option value="">Unassigned</option>
                {me?.user.id && <option value={me.user.id}>Assign to me</option>}
                {can("user.manage") &&
                  (members.data?.data ?? [])
                    .filter((member) => member.user_id !== me?.user.id)
                    .map((member) => (
                      <option key={member.user_id} value={member.user_id}>
                        {member.full_name || member.email}
                      </option>
                    ))}
              </select>
              {members.isPending && <p className="text-xs text-muted">Loading members…</p>}
              {members.error && <ErrorAlert error={members.error as ApiError} />}
              <ErrorAlert error={assign.error as ApiError | null} />
            </div>
          </ActionDialog>
        )}

        {item && canWork && (
          <ActionDialog
            open={dialog === "status"}
            onOpenChange={(open) => setDialog(open ? "status" : null)}
            title="Change status"
            description="The server decides which changes are valid and refuses the rest."
            confirmLabel="Change status"
            busy={transition.isPending}
            onConfirm={() => transition.mutate()}
          >
            <div className="flex flex-col gap-3">
              <label htmlFor="status-target" className="text-sm font-medium">
                New status
              </label>
              <select
                id="status-target"
                className="rounded-md border border-line bg-panel px-3 py-2 text-sm"
                value={statusTarget}
                onChange={(event) => setStatusTarget(event.target.value)}
              >
                {TRANSITION_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {statusLabel(status)}
                  </option>
                ))}
              </select>
              <label htmlFor="status-note" className="text-sm font-medium">
                Note (optional)
              </label>
              <textarea
                id="status-note"
                className="min-h-20 rounded-md border border-line bg-panel px-3 py-2 text-sm"
                value={statusNote}
                onChange={(event) => setStatusNote(event.target.value)}
              />
              <ErrorAlert error={transition.error as ApiError | null} />
            </div>
          </ActionDialog>
        )}

        {item && canWork && (
          <ActionDialog
            open={dialog === "reclaim"}
            onOpenChange={(open) => setDialog(open ? "reclaim" : null)}
            title="Reclaim this variance"
            description="The server creates a claim adjustment from this case."
            confirmLabel="Reclaim"
            busy={reclaim.isPending}
            onConfirm={() => reclaim.mutate()}
          >
            <div className="flex flex-col gap-3">
              {reclaimError?.code === "reclaim_included" ? (
                <Alert>
                  {reclaimError.message}{" "}
                  {includedBatchId ? (
                    <Link to={`/claims/batches/${includedBatchId}`} className="font-medium underline">
                      View batch
                    </Link>
                  ) : (
                    <Link to="/claims/batches" className="font-medium underline">
                      View claim batches
                    </Link>
                  )}
                </Alert>
              ) : (
                <ErrorAlert error={reclaimError} />
              )}
            </div>
          </ActionDialog>
        )}

        {item && canWriteOff && (
          <ActionDialog
            open={dialog === "writeoff"}
            onOpenChange={(open) => setDialog(open ? "writeoff" : null)}
            title={
              proposalMine
                ? "Complete write-off"
                : proposalOther
                  ? "Approve write-off"
                  : "Write off this case"
            }
            description={
              proposalOther
                ? "A different approver already proposed this write-off. Approving it completes the four-eyes flow."
                : "The first approval records a proposal; a different approver must complete it."
            }
            confirmLabel={writeOffLabel}
            confirmVariant="danger"
            confirmDisabled={proposalMine || writeOffReason.trim() === ""}
            busy={writeOff.isPending}
            onConfirm={() => writeOff.mutate()}
          >
            <div className="flex flex-col gap-3">
              <label htmlFor="writeoff-reason" className="text-sm font-medium">
                Reason
              </label>
              <textarea
                id="writeoff-reason"
                required
                className="min-h-24 rounded-md border border-line bg-panel px-3 py-2 text-sm"
                value={writeOffReason}
                onChange={(event) => setWriteOffReason(event.target.value)}
              />
              {proposalMine && (
                <Alert tone="info">
                  You proposed this write-off. A different approver must complete it.
                  {item.write_off_reason ? ` Reason: ${item.write_off_reason}` : ""}
                </Alert>
              )}
              <ErrorAlert error={writeOff.error as ApiError | null} />
            </div>
          </ActionDialog>
        )}
      </div>
    </>
  );
}

function Amount({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="font-mono font-medium" aria-label={`${label} amount`}>
        {amountText(value)}
      </dd>
    </div>
  );
}
