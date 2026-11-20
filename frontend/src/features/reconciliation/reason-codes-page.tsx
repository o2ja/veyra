import { useSearchParams } from "react-router-dom";

import { Card } from "../../components/ui";
import { ReasonCodeMappings } from "../masterdata/manufacturers-pages";
import { ManufacturerSelect } from "../masterdata/common";
import { ReconciliationNav } from "./common";

/** Same editor as on the manufacturer page; this route just adds a manufacturer picker in front of it. */
export function ReasonCodesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const manufacturerId = searchParams.get("manufacturer_id") ?? "";

  function setManufacturer(mfg: string) {
    const next = new URLSearchParams(searchParams);
    if (mfg) next.set("manufacturer_id", mfg);
    else next.delete("manufacturer_id");
    setSearchParams(next, { replace: true });
  }

  return (
    <>
      <ReconciliationNav />
      <Card title="Reason code mappings">
        <div className="mb-4 max-w-sm">
          <ManufacturerSelect
            value={manufacturerId}
            onChange={setManufacturer}
            allowAny="Select a manufacturer"
          />
        </div>
        {manufacturerId ? (
          <ReasonCodeMappings key={manufacturerId} manufacturerId={manufacturerId} />
        ) : (
          <p className="text-sm text-muted">
            Select a manufacturer to see its reason codes, including any unknown codes that arrived on
            remittances.
          </p>
        )}
      </Card>
    </>
  );
}
