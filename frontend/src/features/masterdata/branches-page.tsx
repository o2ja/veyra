import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Button, Card, ErrorAlert, Field, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, FormGrid, MasterDataNav, PagerControls, usePager } from "./common";
import { formatDateTime } from "../../lib/format";

export function BranchesPage() {
  const client = useQueryClient();
  const { can } = useSession();
  const pager = usePager();
  const [form, setForm] = useState({ name: "", code: "" });
  const list = useQuery({
    queryKey: ["branches", pager.cursor],
    queryFn: () =>
      unwrap(api.GET("/api/v1/branches", { params: { query: { cursor: pager.cursor, limit: 50 } } })),
  });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/branches", { body: form })),
    onSuccess: () => {
      setForm({ name: "", code: "" });
      void client.invalidateQueries({ queryKey: ["branches"] });
    },
  });
  const error = create.error as ApiError | null;
  return (
    <>
      <MasterDataNav />
      <div className="flex flex-col gap-6">
        {can("masterdata.write") && (
          <Card title="Add a branch">
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                create.mutate();
              }}
            >
              <FormGrid>
                <Field
                  label="Name"
                  required
                  value={form.name}
                  error={fieldError(error, "name")}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
                <Field
                  label="Code"
                  required
                  hint="As it appears in your sales files, for example BR01"
                  value={form.code}
                  error={fieldError(error, "code")}
                  onChange={(e) => setForm({ ...form, code: e.target.value })}
                />
              </FormGrid>
              {create.isSuccess && <Alert tone="ok">Branch added.</Alert>}
              <ErrorAlert error={error} />
              <div>
                <Button type="submit" disabled={create.isPending}>
                  Add branch
                </Button>
              </div>
            </form>
          </Card>
        )}
        <Card title="Branches">
          <DataTable
            caption="Branches"
            loading={list.isPending}
            error={list.error}
            rows={list.data?.data}
            empty="No branches yet."
            columns={[
              { header: "Code", cell: (b) => b.code },
              { header: "Name", cell: (b) => b.name },
              { header: "Added", cell: (b) => formatDateTime(b.created_at) },
            ]}
          />
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>
      </div>
    </>
  );
}
