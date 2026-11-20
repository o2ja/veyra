import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Button, Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { type ApiError, unwrap } from "../../lib/api/errors";
import { Checkbox, PagerControls, usePager } from "../masterdata/common";
import { RecoveryNav } from "./common";
import { notificationCaseLink, type NotificationOut } from "./logic";
import { formatDate, formatDateTime } from "../../lib/format";

export function NotificationsPage() {
  const pager = usePager();
  const client = useQueryClient();
  const [unreadOnly, setUnreadOnly] = useState(true);

  const list = useQuery({
    queryKey: ["recovery", "notifications", unreadOnly, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/notifications", {
          params: { query: { unread: unreadOnly, limit: 50, cursor: pager.cursor } },
        }),
      ),
  });

  const markRead = useMutation({
    mutationFn: (notificationId: string) =>
      unwrap(
        api.POST("/api/v1/notifications/{notification_id}/read", {
          params: { path: { notification_id: notificationId } },
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["recovery", "notifications"] });
    },
  });

  const listError = list.error as ApiError | null;

  if (listError?.status === 403 || listError?.code === "forbidden") {
    return (
      <>
        <RecoveryNav />
        <PermissionDenied />
      </>
    );
  }

  return (
    <>
      <RecoveryNav />
      <div className="flex flex-col gap-6">
        <Card title="Notifications">
          <p className="mb-4 text-sm text-muted">
            Reminders are scheduled by the server&apos;s daily sweep. This page only lists them.
          </p>
          <div className="mb-4">
            <Checkbox
              label="Unread only"
              checked={unreadOnly}
              onChange={(checked) => {
                setUnreadOnly(checked);
                pager.reset();
              }}
            />
          </div>

          <ErrorAlert error={listError} />
          {list.isPending ? (
            <Loading label="Loading notifications…" />
          ) : !list.data?.data.length ? (
            <Empty>{unreadOnly ? "No unread notifications." : "No notifications yet."}</Empty>
          ) : (
            <ul className="flex flex-col gap-4">
              {list.data.data.map((notification: NotificationOut) => {
                const caseLink = notificationCaseLink(notification);
                return (
                  <li key={notification.id} className="rounded-md border border-line p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex flex-col gap-1">
                        <h2 className="font-semibold">{notification.title}</h2>
                        {notification.body && (
                          <p className="whitespace-pre-wrap text-sm">{notification.body}</p>
                        )}
                        <p className="text-xs text-muted">
                          Due {formatDate(notification.due_date)} · Created{" "}
                          {formatDateTime(notification.created_at)}
                        </p>
                        {caseLink && (
                          <Link to={caseLink} className="text-sm text-brand underline">
                            Open recovery case
                          </Link>
                        )}
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        {notification.read_at ? (
                          <span className="text-xs text-muted">
                            Read {formatDateTime(notification.read_at)}
                          </span>
                        ) : (
                          <Button
                            variant="secondary"
                            disabled={markRead.isPending}
                            aria-label={`Mark read: ${notification.title}`}
                            onClick={() => markRead.mutate(notification.id)}
                          >
                            Mark read
                          </Button>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <PagerControls pager={pager} page={list.data?.page} />
          <ErrorAlert error={markRead.error as ApiError | null} />
        </Card>
      </div>
    </>
  );
}
