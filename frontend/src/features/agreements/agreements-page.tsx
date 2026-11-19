import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Button, Card, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, ManufacturerSelect, PagerControls, SelectField, usePager } from "../masterdata/common";
import { AgreementsNav, useManufacturerMap } from "./common";
import { VERSION_STATUS_LABELS } from "./logic";
import { formatDate } from "../../lib/format";

export function AgreementsPage() {
  const { can } = useSession();
  const navigate = useNavigate();
  const pager = usePager();
  const [manufacturerId, setManufacturerId] = useState("");
  const [agreementType, setAgreementType] = useState<"" | "spa" | "rebate">("");
  const manufacturers = useManufacturerMap();

  const list = useQuery({
    queryKey: ["agreements", manufacturerId, agreementType, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreements", {
          params: {
            query: {
              manufacturer_id: manufacturerId || null,
              agreement_type: agreementType || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  if ((list.error as ApiError)?.status === 403) {
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
      <Card
        title="Agreements"
        actions={
          can("agreement.draft") ? (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => navigate("/extractions")}>
                Upload contract PDF
              </Button>
              <Button onClick={() => navigate("/agreement-drafts")}>New draft</Button>
            </div>
          ) : undefined
        }
      >
        <div className="mb-4 flex flex-wrap gap-4">
          <ManufacturerSelect
            allowAny="All manufacturers"
            value={manufacturerId}
            onChange={(id) => {
              setManufacturerId(id);
              pager.reset();
            }}
          />
          <SelectField
            label="Type"
            value={agreementType}
            onChange={(e) => {
              setAgreementType(e.target.value as "" | "spa" | "rebate");
              pager.reset();
            }}
          >
            <option value="">All types</option>
            <option value="spa">SPA</option>
            <option value="rebate">Rebate</option>
          </SelectField>
        </div>

        <DataTable
          caption="Agreements"
          loading={list.isPending}
          error={list.error}
          rows={list.data?.data}
          empty={manufacturerId || agreementType ? "No matching agreements." : "No agreements yet."}
          columns={[
            {
              header: "External number",
              cell: (a) => (
                <Link to={`/agreements/${a.id}`} className="text-brand underline">
                  {a.external_number}
                </Link>
              ),
            },
            { header: "Name", cell: (a) => a.name },
            { header: "Type", cell: (a) => a.agreement_type.toUpperCase() },
            {
              header: "Manufacturer",
              cell: (a) => {
                const m = manufacturers.get(a.manufacturer_id);
                return m ? `${m.name} (${m.code})` : a.manufacturer_id;
              },
            },
            {
              header: "Current version",
              cell: (a) =>
                a.current_version
                  ? `v${a.current_version.version_number} (${formatDate(a.current_version.valid_from)} – ${formatDate(a.current_version.valid_to)})`
                  : "None",
            },
            {
              header: "Status",
              cell: (a) => {
                if (a.cancelled_at) {
                  return <span className="font-medium text-danger">Cancelled</span>;
                }
                const st = a.current_version?.status;
                return st ? (VERSION_STATUS_LABELS[st] ?? st) : "—";
              },
            },
          ]}
        />
        <PagerControls pager={pager} page={list.data?.page} />
      </Card>
    </>
  );
}
