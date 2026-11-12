import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker, useParams } from "react-router-dom";

import { Alert, Button, Card, Empty, ErrorAlert, Field, Loading, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { uploadWithProgress } from "../files/files-page";
import { Checkbox, FormGrid, SelectField, orNull, useManufacturers, utc } from "../masterdata/common";
import { DraftPreviewPanel } from "../calculations/draft-preview-panel";
import { AgreementsNav } from "./common";
import {
  DRAFT_STATUS_LABELS,
  ITEMS_PAGE_SIZE,
  type CustomerRef,
  type DraftOut,
  type Issue,
  type Item,
  type ItemsImportOut,
  type Payload,
  type RebateRules,
  type SpaRules,
  type TierIn,
  emptyCustomer,
  emptyItem,
  emptyTier,
  formatValue,
  issueFieldId,
  itemIndexFromIssuePath,
  pageForItemIndex,
  paginateItems,
  payloadFromDraft,
  switchPayloadType,
  applyEdit,
  viewPayload,
  intOrRaw,
} from "./logic";

/** Disabled controls cannot take focus; focus their own label or row instead (never the wrapping fieldset), with temporary tabIndex. */
function focusEl(el: HTMLElement) {
  let t: HTMLElement = el;
  if (el.matches(":disabled")) {
    const label = el.id ? document.querySelector<HTMLElement>(`label[for="${CSS.escape(el.id)}"]`) : null;
    t = label ?? (el.closest("label, td, tr") as HTMLElement | null) ?? el;
  }
  if (t !== el && !t.hasAttribute("tabindex")) {
    t.tabIndex = -1;
    t.addEventListener("blur", () => t.removeAttribute("tabindex"), { once: true });
  }
  t.focus();
  t.scrollIntoView({ block: "center" });
}

function HeaderSection({
  payload,
  editable,
  error,
  onChange,
}: {
  payload: Payload;
  editable: boolean;
  error: ApiError | null;
  onChange: (next: Payload) => void;
}) {
  const manufacturers = useManufacturers();
  const h = payload.header;
  const setHeader = (patch: Partial<Payload["header"]>) =>
    onChange({ ...payload, header: { ...h, ...patch } });

  return (
    <Card title="Header">
      <fieldset disabled={!editable} className="flex flex-col gap-3">
        <FormGrid>
          <SelectField
            id="field-header-agreement_type"
            label="Agreement type"
            value={h.agreement_type}
            onChange={(e) => onChange(switchPayloadType(payload, e.target.value as "spa" | "rebate"))}
          >
            <option value="spa">SPA</option>
            <option value="rebate">Rebate</option>
          </SelectField>
          <Field
            id="field-header-external_number"
            label="External number"
            required
            value={h.external_number}
            error={
              fieldError(error, "payload.header.external_number") ??
              fieldError(error, "header.external_number")
            }
            onChange={(e) => setHeader({ external_number: e.target.value })}
          />
          <Field
            id="field-header-manufacturer_code"
            label="Manufacturer code"
            list="agreement-manufacturer-codes"
            required
            value={h.manufacturer_code}
            error={
              fieldError(error, "payload.header.manufacturer_code") ??
              fieldError(error, "header.manufacturer_code")
            }
            onChange={(e) => setHeader({ manufacturer_code: e.target.value })}
          />
          <Field
            id="field-header-name"
            label="Name"
            required
            value={h.name}
            error={fieldError(error, "payload.header.name") ?? fieldError(error, "header.name")}
            onChange={(e) => setHeader({ name: e.target.value })}
          />
          <Field
            id="field-header-priority"
            label="Priority"
            type="number"
            value={h.priority}
            error={fieldError(error, "payload.header.priority") ?? fieldError(error, "header.priority")}
            onChange={(e) => setHeader({ priority: intOrRaw(e.target.value) as never })}
          />
          <Field
            id="field-header-valid_from"
            label="Valid from"
            type="date"
            required
            value={h.valid_from}
            error={fieldError(error, "payload.header.valid_from") ?? fieldError(error, "header.valid_from")}
            onChange={(e) => setHeader({ valid_from: e.target.value })}
          />
          <Field
            id="field-header-valid_to"
            label="Valid to"
            type="date"
            required
            value={h.valid_to}
            error={fieldError(error, "payload.header.valid_to") ?? fieldError(error, "header.valid_to")}
            onChange={(e) => setHeader({ valid_to: e.target.value })}
          />
        </FormGrid>
        <datalist id="agreement-manufacturer-codes">
          {manufacturers.data?.data.map((m) => (
            <option key={m.id} value={m.code}>
              {m.name}
            </option>
          ))}
        </datalist>
      </fieldset>
    </Card>
  );
}

function RulesSection({
  rules,
  editable,
  error,
  onChange,
}: {
  rules: SpaRules | RebateRules;
  editable: boolean;
  error: ApiError | null;
  onChange: (next: SpaRules | RebateRules) => void;
}) {
  const setRounding = (patch: Partial<SpaRules["rounding"]>) =>
    onChange({ ...rules, rounding: { ...rules.rounding, ...patch } });

  return (
    <Card title={rules.type === "spa" ? "SPA rules" : "Rebate rules"}>
      <fieldset disabled={!editable} className="flex flex-col gap-4">
        {rules.type === "spa" ? (
          <>
            <FormGrid>
              <SelectField
                id="field-rules-date_basis"
                label="Date basis"
                value={rules.date_basis}
                onChange={(e) => onChange({ ...rules, date_basis: e.target.value as SpaRules["date_basis"] })}
              >
                <option value="ship_date">Ship date</option>
                <option value="invoice_date">Invoice date</option>
              </SelectField>
              <SelectField
                id="field-rules-calculation-basis"
                label="Calculation basis"
                value={rules.calculation.basis}
                onChange={(e) =>
                  onChange({
                    ...rules,
                    calculation: {
                      ...rules.calculation,
                      basis: e.target.value as SpaRules["calculation"]["basis"],
                    },
                  })
                }
              >
                <option value="price_difference">Price difference</option>
                <option value="percent_of_sell">Percent of sell</option>
                <option value="percent_of_cost">Percent of cost</option>
                <option value="flat_per_unit">Flat per unit</option>
                <option value="guaranteed_margin">Guaranteed margin</option>
              </SelectField>
              <SelectField
                id="field-rules-calculation-cost_source"
                label="Cost source"
                value={rules.calculation.cost_source}
                onChange={(e) =>
                  onChange({
                    ...rules,
                    calculation: {
                      ...rules.calculation,
                      cost_source: e.target.value as SpaRules["calculation"]["cost_source"],
                    },
                  })
                }
              >
                <option value="agreement_reference">Agreement reference</option>
                <option value="line_cost">Line cost</option>
                <option value="replacement_cost">Replacement cost</option>
              </SelectField>
              <SelectField
                id="field-rules-customer_scope-mode"
                label="Customer scope"
                value={rules.customer_scope.mode}
                onChange={(e) =>
                  onChange({
                    ...rules,
                    customer_scope: { mode: e.target.value as SpaRules["customer_scope"]["mode"] },
                  })
                }
              >
                <option value="listed">Listed</option>
                <option value="all">All</option>
              </SelectField>
              <SelectField
                id="field-rules-product_scope-mode"
                label="Product scope"
                value={rules.product_scope.mode}
                onChange={(e) => onChange({ ...rules, product_scope: { mode: e.target.value as "listed" } })}
              >
                <option value="listed">Listed</option>
              </SelectField>
              <SelectField
                id="field-rules-price_check-mode"
                label="Price check mode"
                value={rules.price_check.mode}
                onChange={(e) =>
                  onChange({
                    ...rules,
                    price_check: {
                      ...rules.price_check,
                      mode: e.target.value as SpaRules["price_check"]["mode"],
                    },
                  })
                }
              >
                <option value="flag">Flag</option>
                <option value="ignore">Ignore</option>
              </SelectField>
              <Field
                id="field-rules-price_check-tolerance"
                label="Price check tolerance"
                inputMode="decimal"
                value={rules.price_check.tolerance}
                error={fieldError(error, "payload.rules.price_check.tolerance")}
                onChange={(e) =>
                  onChange({ ...rules, price_check: { ...rules.price_check, tolerance: e.target.value } })
                }
              />
              <SelectField
                id="field-rules-quantity-cap_scope"
                label="Quantity cap scope"
                value={rules.quantity.cap_scope}
                onChange={(e) =>
                  onChange({
                    ...rules,
                    quantity: { cap_scope: e.target.value as SpaRules["quantity"]["cap_scope"] },
                  })
                }
              >
                <option value="per_item">Per item</option>
                <option value="per_agreement">Per agreement</option>
                <option value="none">None</option>
              </SelectField>
              <Field
                id="field-rules-claim_uom"
                label="Default claim UoM"
                value={rules.claim_uom ?? ""}
                onChange={(e) => onChange({ ...rules, claim_uom: orNull(e.target.value) })}
              />
              <Field
                id="field-rules-adapter"
                label="Adapter"
                value={rules.adapter ?? ""}
                onChange={(e) => onChange({ ...rules, adapter: orNull(e.target.value) })}
              />
            </FormGrid>
            <Checkbox
              label="SPA reference required"
              checked={rules.spa_reference.required}
              onChange={(checked) => onChange({ ...rules, spa_reference: { required: checked } })}
            />
          </>
        ) : (
          <FormGrid>
            <SelectField
              id="field-rules-volume_basis"
              label="Volume basis"
              value={rules.volume_basis}
              onChange={(e) =>
                onChange({ ...rules, volume_basis: e.target.value as RebateRules["volume_basis"] })
              }
            >
              <option value="purchases">Purchases</option>
              <option value="sales">Sales</option>
            </SelectField>
            <SelectField
              id="field-rules-tier_metric"
              label="Tier metric"
              value={rules.tier_metric}
              onChange={(e) =>
                onChange({ ...rules, tier_metric: e.target.value as RebateRules["tier_metric"] })
              }
            >
              <option value="amount">Amount</option>
              <option value="quantity">Quantity</option>
            </SelectField>
            <SelectField
              id="field-rules-tier_mode"
              label="Tier mode"
              value={rules.tier_mode}
              onChange={(e) => onChange({ ...rules, tier_mode: e.target.value as RebateRules["tier_mode"] })}
            >
              <option value="retroactive">Retroactive</option>
              <option value="incremental">Incremental</option>
            </SelectField>
            <SelectField
              id="field-rules-spa_lines"
              label="SPA lines"
              value={rules.spa_lines}
              onChange={(e) => onChange({ ...rules, spa_lines: e.target.value as RebateRules["spa_lines"] })}
            >
              <option value="include">Include</option>
              <option value="qualify_only">Qualify only</option>
              <option value="exclude">Exclude</option>
            </SelectField>
            <SelectField
              id="field-rules-product_scope-mode"
              label="Product scope"
              value={rules.product_scope.mode}
              onChange={(e) =>
                onChange({
                  ...rules,
                  product_scope: { mode: e.target.value as RebateRules["product_scope"]["mode"] },
                })
              }
            >
              <option value="all">All</option>
              <option value="listed">Listed</option>
            </SelectField>
            <SelectField
              id="field-rules-returns"
              label="Returns"
              value={rules.returns}
              onChange={(e) => onChange({ ...rules, returns: e.target.value as "reduce_volume" })}
            >
              <option value="reduce_volume">Reduce volume</option>
            </SelectField>
            <Field
              id="field-rules-measurement_period-kind"
              label="Measurement period kind"
              value={rules.measurement_period.kind}
              error={fieldError(error, "payload.rules.measurement_period.kind")}
              onChange={(e) =>
                onChange({
                  ...rules,
                  measurement_period: { ...rules.measurement_period, kind: e.target.value },
                })
              }
            />
            <Field
              id="field-rules-measurement_period-start"
              label="Measurement period start"
              type="date"
              value={rules.measurement_period.start}
              error={fieldError(error, "payload.rules.measurement_period.start")}
              onChange={(e) =>
                onChange({
                  ...rules,
                  measurement_period: { ...rules.measurement_period, start: e.target.value },
                })
              }
            />
            <Field
              id="field-rules-measurement_period-end"
              label="Measurement period end"
              type="date"
              value={rules.measurement_period.end}
              error={fieldError(error, "payload.rules.measurement_period.end")}
              onChange={(e) =>
                onChange({
                  ...rules,
                  measurement_period: { ...rules.measurement_period, end: e.target.value },
                })
              }
            />
          </FormGrid>
        )}

        <FormGrid>
          <SelectField
            id="field-rules-rounding-mode"
            label="Rounding mode"
            value={rules.rounding.mode}
            onChange={(e) => setRounding({ mode: e.target.value as SpaRules["rounding"]["mode"] })}
          >
            <option value="half_up">Half up</option>
            <option value="half_even">Half even</option>
            <option value="down">Down</option>
          </SelectField>
          <Field
            id="field-rules-rounding-scale"
            label="Rounding scale"
            type="number"
            min={0}
            value={rules.rounding.scale}
            onChange={(e) => setRounding({ scale: intOrRaw(e.target.value) as never })}
          />
          <SelectField
            id="field-rules-rounding-timing"
            label="Rounding timing"
            value={rules.rounding.timing}
            onChange={(e) => setRounding({ timing: e.target.value as SpaRules["rounding"]["timing"] })}
          >
            <option value="per_line">Per line</option>
            <option value="per_document">Per document</option>
          </SelectField>
        </FormGrid>
      </fieldset>
    </Card>
  );
}

function CustomersSection({
  customers,
  editable,
  onChange,
}: {
  customers: CustomerRef[];
  editable: boolean;
  onChange: (next: CustomerRef[]) => void;
}) {
  return (
    <Card
      title={`Customers (${customers.length})`}
      actions={
        editable ? (
          <Button variant="secondary" onClick={() => onChange([...customers, emptyCustomer()])}>
            Add customer
          </Button>
        ) : undefined
      }
    >
      {customers.length === 0 ? (
        <Empty>No customers listed.</Empty>
      ) : (
        <div className="flex flex-col gap-3">
          {customers.map((c, i) => (
            <fieldset
              key={i}
              disabled={!editable}
              className="grid gap-2 sm:grid-cols-[2fr_1fr_auto] sm:items-end"
            >
              <legend className="sr-only">Customer {i + 1}</legend>
              <Field
                id={`field-customers-${i}-customer_code`}
                label="Customer code"
                value={c.customer_code}
                onChange={(e) =>
                  onChange(
                    customers.map((row, j) => (j === i ? { ...row, customer_code: e.target.value } : row)),
                  )
                }
              />
              <SelectField
                id={`field-customers-${i}-scope`}
                label="Scope"
                value={c.scope}
                onChange={(e) =>
                  onChange(
                    customers.map((row, j) =>
                      j === i ? { ...row, scope: e.target.value as CustomerRef["scope"] } : row,
                    ),
                  )
                }
              >
                <option value="include">Include</option>
                <option value="exclude">Exclude</option>
              </SelectField>
              {editable && (
                <Button
                  variant="secondary"
                  aria-label={`Remove customer ${i + 1}`}
                  onClick={() => onChange(customers.filter((_, j) => j !== i))}
                >
                  Remove
                </Button>
              )}
            </fieldset>
          ))}
        </div>
      )}
    </Card>
  );
}

function ItemsSection({
  items,
  page,
  editable,
  onPageChange,
  onChange,
}: {
  items: Item[];
  page: number;
  editable: boolean;
  onPageChange: (nextPage: number) => void;
  onChange: (next: Item[]) => void;
}) {
  const { slice, startIndex, page: currentPage, totalPages } = paginateItems(items, page, ITEMS_PAGE_SIZE);

  function updateItem(absIdx: number, patch: Partial<Item>) {
    onChange(items.map((it, j) => (j === absIdx ? { ...it, ...patch } : it)));
  }

  function addItem() {
    const next = [...items, emptyItem()];
    onChange(next);
    onPageChange(pageForItemIndex(next.length - 1, ITEMS_PAGE_SIZE));
  }

  return (
    <Card
      title={`Items (${items.length})`}
      actions={
        editable ? (
          <Button variant="secondary" onClick={addItem}>
            Add item
          </Button>
        ) : undefined
      }
    >
      {items.length === 0 ? (
        <Empty>No items yet.</Empty>
      ) : (
        <>
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Draft items">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Draft items</caption>
              <thead className="text-muted">
                <tr>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    #
                  </th>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    SKU
                  </th>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    Ref cost
                  </th>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    Deviated cost
                  </th>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    Sell price
                  </th>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    Rate
                  </th>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    Flat amount
                  </th>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    Max qty
                  </th>
                  <th scope="col" className="py-2 pr-2 font-medium">
                    Claim UoM
                  </th>
                  {editable && (
                    <th scope="col" className="py-2 font-medium">
                      <span className="sr-only">Actions</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {slice.map((it, relIdx) => {
                  const absIdx = startIndex + relIdx;
                  const cellCls =
                    "w-24 rounded border border-line bg-panel px-2 py-1 text-sm disabled:opacity-60";
                  return (
                    <tr key={absIdx} className="border-t border-line align-middle">
                      <td className="py-1.5 pr-2 text-muted">{absIdx + 1}</td>
                      <td className="py-1.5 pr-2">
                        <input
                          id={`field-items-${absIdx}-sku`}
                          aria-label={`Item ${absIdx + 1} SKU`}
                          className="w-32 rounded border border-line bg-panel px-2 py-1 text-sm disabled:opacity-60"
                          disabled={!editable}
                          value={it.sku}
                          onChange={(e) => updateItem(absIdx, { sku: e.target.value })}
                        />
                      </td>
                      <td className="py-1.5 pr-2">
                        <input
                          id={`field-items-${absIdx}-reference_cost`}
                          aria-label={`Item ${absIdx + 1} reference cost`}
                          inputMode="decimal"
                          className={cellCls}
                          disabled={!editable}
                          value={it.reference_cost ?? ""}
                          onChange={(e) => updateItem(absIdx, { reference_cost: orNull(e.target.value) })}
                        />
                      </td>
                      <td className="py-1.5 pr-2">
                        <input
                          id={`field-items-${absIdx}-deviated_cost`}
                          aria-label={`Item ${absIdx + 1} deviated cost`}
                          inputMode="decimal"
                          className={cellCls}
                          disabled={!editable}
                          value={it.deviated_cost ?? ""}
                          onChange={(e) => updateItem(absIdx, { deviated_cost: orNull(e.target.value) })}
                        />
                      </td>
                      <td className="py-1.5 pr-2">
                        <input
                          id={`field-items-${absIdx}-sell_price`}
                          aria-label={`Item ${absIdx + 1} sell price`}
                          inputMode="decimal"
                          className={cellCls}
                          disabled={!editable}
                          value={it.sell_price ?? ""}
                          onChange={(e) => updateItem(absIdx, { sell_price: orNull(e.target.value) })}
                        />
                      </td>
                      <td className="py-1.5 pr-2">
                        <input
                          id={`field-items-${absIdx}-rate`}
                          aria-label={`Item ${absIdx + 1} rate`}
                          inputMode="decimal"
                          className={cellCls}
                          disabled={!editable}
                          value={it.rate ?? ""}
                          onChange={(e) => updateItem(absIdx, { rate: orNull(e.target.value) })}
                        />
                      </td>
                      <td className="py-1.5 pr-2">
                        <input
                          id={`field-items-${absIdx}-flat_amount`}
                          aria-label={`Item ${absIdx + 1} flat amount`}
                          inputMode="decimal"
                          className={cellCls}
                          disabled={!editable}
                          value={it.flat_amount ?? ""}
                          onChange={(e) => updateItem(absIdx, { flat_amount: orNull(e.target.value) })}
                        />
                      </td>
                      <td className="py-1.5 pr-2">
                        <input
                          id={`field-items-${absIdx}-max_quantity`}
                          aria-label={`Item ${absIdx + 1} max quantity`}
                          inputMode="decimal"
                          className={cellCls}
                          disabled={!editable}
                          value={it.max_quantity ?? ""}
                          onChange={(e) => updateItem(absIdx, { max_quantity: orNull(e.target.value) })}
                        />
                      </td>
                      <td className="py-1.5 pr-2">
                        <input
                          id={`field-items-${absIdx}-claim_uom`}
                          aria-label={`Item ${absIdx + 1} claim UoM`}
                          className="w-20 rounded border border-line bg-panel px-2 py-1 text-sm disabled:opacity-60"
                          disabled={!editable}
                          value={it.claim_uom ?? ""}
                          onChange={(e) => updateItem(absIdx, { claim_uom: orNull(e.target.value) })}
                        />
                      </td>
                      {editable && (
                        <td className="py-1.5">
                          <Button
                            variant="secondary"
                            aria-label={`Remove item ${absIdx + 1}`}
                            onClick={() => onChange(items.filter((_, j) => j !== absIdx))}
                          >
                            Remove
                          </Button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <nav aria-label="Item pages" className="mt-4 flex items-center gap-3 text-sm">
              <Button
                variant="secondary"
                disabled={currentPage <= 1}
                onClick={() => onPageChange(currentPage - 1)}
              >
                Previous
              </Button>
              <span>
                Page {currentPage} of {totalPages}
              </span>
              <Button
                variant="secondary"
                disabled={currentPage >= totalPages}
                onClick={() => onPageChange(currentPage + 1)}
              >
                Next
              </Button>
            </nav>
          )}
        </>
      )}
    </Card>
  );
}

function TiersSection({
  tiers,
  editable,
  onChange,
}: {
  tiers: TierIn[];
  editable: boolean;
  onChange: (next: TierIn[]) => void;
}) {
  return (
    <Card
      title={`Tiers (${tiers.length})`}
      actions={
        editable ? (
          <Button variant="secondary" onClick={() => onChange([...tiers, emptyTier(tiers.length + 1)])}>
            Add tier
          </Button>
        ) : undefined
      }
    >
      {tiers.length === 0 ? (
        <Empty>No tiers yet.</Empty>
      ) : (
        <div className="flex flex-col gap-3">
          {tiers.map((t, i) => (
            <fieldset
              key={i}
              disabled={!editable}
              className="grid gap-2 sm:grid-cols-[1fr_2fr_2fr_auto] sm:items-end"
            >
              <legend className="sr-only">Tier {i + 1}</legend>
              <Field
                id={`field-tiers-${i}-tier_number`}
                label="Tier number"
                type="number"
                min={1}
                value={t.tier_number}
                onChange={(e) =>
                  onChange(
                    tiers.map((row, j) =>
                      j === i ? { ...row, tier_number: intOrRaw(e.target.value) as never } : row,
                    ),
                  )
                }
              />
              <Field
                id={`field-tiers-${i}-lower_bound`}
                label="Lower bound"
                inputMode="decimal"
                value={t.lower_bound}
                onChange={(e) =>
                  onChange(tiers.map((row, j) => (j === i ? { ...row, lower_bound: e.target.value } : row)))
                }
              />
              <Field
                id={`field-tiers-${i}-rate`}
                label="Rate"
                inputMode="decimal"
                value={t.rate}
                onChange={(e) =>
                  onChange(tiers.map((row, j) => (j === i ? { ...row, rate: e.target.value } : row)))
                }
              />
              {editable && (
                <Button
                  variant="secondary"
                  aria-label={`Remove tier ${i + 1}`}
                  onClick={() => onChange(tiers.filter((_, j) => j !== i))}
                >
                  Remove
                </Button>
              )}
            </fieldset>
          ))}
        </div>
      )}
    </Card>
  );
}

function ValidationPanel({
  draft,
  acknowledgedWarnings,
  canEdit,
  validating,
  onValidate,
  onToggleWarning,
  onFocusIssue,
}: {
  draft: DraftOut;
  acknowledgedWarnings: Set<string>;
  canEdit: boolean;
  validating: boolean;
  onValidate: () => void;
  onToggleWarning: (id: string, checked: boolean) => void;
  onFocusIssue: (issue: Issue) => void;
}) {
  const v = draft.validation;
  return (
    <Card
      title="Validation"
      actions={
        canEdit ? (
          <Button variant="secondary" disabled={validating} onClick={onValidate}>
            Validate
          </Button>
        ) : undefined
      }
    >
      {!v ? (
        <Empty>Not validated yet. Save changes and run Validate.</Empty>
      ) : (
        <div className="flex flex-col gap-4">
          <Alert tone={v.valid ? "ok" : "danger"}>
            {v.valid ? "Validation passed." : `${v.error_count} validation error(s).`}
            {v.warnings.length > 0 ? ` ${v.warnings.length} warning(s).` : ""}
            <span className="ml-2 text-xs">Checked {utc(v.validated_at)}</span>
          </Alert>

          {(v.references.unresolved_customers.length > 0 || v.references.unresolved_skus.length > 0) && (
            <div className="rounded-md border border-line p-3 text-sm">
              {v.references.unresolved_customers.length > 0 && (
                <p>
                  <span className="font-medium">Unresolved customers:</span>{" "}
                  {v.references.unresolved_customers.join(", ")}
                </p>
              )}
              {v.references.unresolved_skus.length > 0 && (
                <p>
                  <span className="font-medium">Unresolved SKUs:</span>{" "}
                  {v.references.unresolved_skus.join(", ")}
                </p>
              )}
            </div>
          )}

          {v.errors.length > 0 && (
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold text-danger">Errors ({v.errors.length})</h2>
              <ul className="flex flex-col gap-2">
                {v.errors.map((issue, idx) => {
                  const target = issueFieldId(issue.field);
                  return (
                    <li
                      key={`${issue.code}-${issue.field}-${idx}`}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-danger px-3 py-2 text-sm text-danger"
                    >
                      <div>
                        <span className="font-mono text-xs">{issue.field}</span>: {issue.message}
                      </div>
                      {target && (
                        <Button variant="secondary" onClick={() => onFocusIssue(issue)}>
                          Go to field
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {v.warnings.length > 0 && (
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold">Warnings ({v.warnings.length})</h2>
              <ul className="flex flex-col gap-2">
                {v.warnings.map((issue, idx) => {
                  const target = issueFieldId(issue.field);
                  const warnId = issue.id;
                  return (
                    <li
                      key={`${issue.code}-${issue.field}-${idx}`}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line bg-surface px-3 py-2 text-sm"
                    >
                      <div className="flex flex-col gap-1">
                        <div>
                          <span className="font-mono text-xs">{issue.field}</span>: {issue.message}
                        </div>
                        {warnId && canEdit && (
                          <Checkbox
                            label="Acknowledge warning"
                            checked={acknowledgedWarnings.has(warnId)}
                            onChange={(checked) => onToggleWarning(warnId, checked)}
                          />
                        )}
                      </div>
                      {target && (
                        <Button variant="secondary" onClick={() => onFocusIssue(issue)}>
                          Go to field
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function BulkItemsImport({
  draftId,
  revision,
  onReloadNeeded,
  onImported,
}: {
  draftId: string;
  revision: number;
  onReloadNeeded: () => void;
  onImported: (draft: DraftOut) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [rateFormat, setRateFormat] = useState<"fraction" | "percent">("fraction");
  const [sheet, setSheet] = useState("");
  const [headerRow, setHeaderRow] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [uploadedFileId, setUploadedFileId] = useState<string | null>(null);
  const [preview, setPreview] = useState<ItemsImportOut | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function waitForCleanFile(fileId: string): Promise<void> {
    for (let i = 0; i < 60; i++) {
      const f = await unwrap(api.GET("/api/v1/files/{file_id}", { params: { path: { file_id: fileId } } }));
      if (f.scan_status === "clean") return;
      if (f.scan_status === "infected" || f.scan_status === "error") {
        throw new ApiError(422, "file_scan_failed", `File scan status: ${f.scan_status}`, [], null);
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new ApiError(408, "scan_timeout", "File scan is taking longer than expected.", [], null);
  }

  async function runImport(dryRun: boolean) {
    setError(null);
    setBusy(true);
    try {
      let fileId = uploadedFileId;
      if (!fileId) {
        const file = fileRef.current?.files?.[0];
        if (!file) return;
        setProgress(0);
        const uploaded = await uploadWithProgress(file, "import", setProgress);
        setProgress(null);
        await waitForCleanFile(uploaded.id);
        fileId = uploaded.id;
        setUploadedFileId(fileId);
      }
      const res = await unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/items:import", {
          params: { path: { draft_id: draftId }, header: { "if-match": `"${revision}"` } },
          body: {
            file_id: fileId,
            rate_format: rateFormat,
            dry_run: dryRun,
            sheet: orNull(sheet),
            header_row: headerRow.trim() === "" ? null : Number(headerRow),
          },
        }),
      );
      if (dryRun) {
        setPreview(res);
      } else if (res.draft) {
        setPreview(null);
        setUploadedFileId(null);
        if (fileRef.current) fileRef.current.value = "";
        onImported(res.draft);
      }
    } catch (e) {
      setProgress(null);
      if (e instanceof ApiError) {
        if (e.code === "precondition_failed" || e.status === 412) {
          onReloadNeeded();
        }
        setError(e);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Bulk import items">
      <div className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex flex-col gap-1">
            <label htmlFor="items-import-file" className="text-sm font-medium">
              Spreadsheet (CSV or XLSX)
            </label>
            <input
              id="items-import-file"
              ref={fileRef}
              type="file"
              accept=".csv,.xlsx"
              className="text-sm"
              onChange={() => {
                setUploadedFileId(null);
                setPreview(null);
              }}
            />
          </div>
          <SelectField
            label="Rate format"
            value={rateFormat}
            onChange={(e) => setRateFormat(e.target.value as "fraction" | "percent")}
          >
            <option value="fraction">Fraction (0.05)</option>
            <option value="percent">Percent (5)</option>
          </SelectField>
          <Field label="Sheet (optional)" value={sheet} onChange={(e) => setSheet(e.target.value)} />
          <Field
            label="Header row (optional)"
            type="number"
            min={1}
            value={headerRow}
            onChange={(e) => setHeaderRow(e.target.value)}
          />
        </div>
        {progress !== null && (
          <progress className="w-full" max={100} value={progress} aria-label="Upload progress">
            {progress}%
          </progress>
        )}
        <ErrorAlert error={error} />
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" disabled={busy} onClick={() => void runImport(true)}>
            Preview import
          </Button>
          {preview && (
            <Button disabled={busy} onClick={() => void runImport(false)}>
              Apply imported items ({preview.rows})
            </Button>
          )}
        </div>
        {preview && (
          <div className="mt-2 flex flex-col gap-2">
            <Alert tone="info">
              Dry-run preview: {preview.rows} row(s). Showing first {Math.min(10, preview.items.length)}.
            </Alert>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <caption className="sr-only">Import preview</caption>
                <thead className="text-muted">
                  <tr>
                    <th scope="col" className="py-1 pr-3">
                      SKU
                    </th>
                    <th scope="col" className="py-1 pr-3">
                      Ref cost
                    </th>
                    <th scope="col" className="py-1 pr-3">
                      Deviated cost
                    </th>
                    <th scope="col" className="py-1 pr-3">
                      Sell price
                    </th>
                    <th scope="col" className="py-1 pr-3">
                      Rate
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {preview.items.slice(0, 10).map((row, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="py-1 pr-3">{formatValue(row.sku)}</td>
                      <td className="py-1 pr-3">{formatValue(row.reference_cost)}</td>
                      <td className="py-1 pr-3">{formatValue(row.deviated_cost)}</td>
                      <td className="py-1 pr-3">{formatValue(row.sell_price)}</td>
                      <td className="py-1 pr-3">{formatValue(row.rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

export function DraftEditorPage() {
  const { draftId = "" } = useParams();
  const query = useQuery({
    queryKey: ["agreement-drafts", draftId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/agreement-drafts/{draft_id}", { params: { path: { draft_id: draftId } } })),
  });
  const loadErr = query.error as ApiError | null;

  if (loadErr?.status === 404 || loadErr?.code === "not_found") {
    return (
      <>
        <AgreementsNav />
        <Card title="Draft not found">
          <Alert>This draft could not be found.</Alert>
          <div className="mt-4">
            <Link to="/agreement-drafts" className="text-brand underline">
              Back to drafts
            </Link>
          </div>
        </Card>
      </>
    );
  }

  if (query.isPending || !query.data) {
    return (
      <>
        <AgreementsNav />
        <Card title="Agreement draft">
          <ErrorAlert error={loadErr} />
          {query.isPending && <Loading />}
        </Card>
      </>
    );
  }

  return <DraftEditorForm key={query.data.id} initial={query.data} />;
}

function DraftEditorForm({ initial }: { initial: DraftOut }) {
  const { can, me } = useSession();
  const client = useQueryClient();
  const draftId = initial.id;

  const [draft, setDraft] = useState<DraftOut>(initial);
  const [payload, setPayload] = useState<Payload>(() => payloadFromDraft(initial.payload));
  const [changeSummary, setChangeSummary] = useState("");
  const [dirty, setDirty] = useState(() => {
    const p = payloadFromDraft(initial.payload);
    return JSON.stringify(p) !== JSON.stringify(viewPayload(p));
  });
  const [staleConflict, setStaleConflict] = useState(false);
  const [itemsPage, setItemsPage] = useState(1);
  const pendingFocusRef = useRef<string | null>(null);
  const [acknowledgedWarnings, setAcknowledgedWarnings] = useState<Set<string>>(new Set());
  const [returnComment, setReturnComment] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const approveKeyRef = useRef<{ revision: number; key: string } | null>(null);
  const draftRevisionRef = useRef(draft.revision);
  useEffect(() => {
    draftRevisionRef.current = draft.revision;
  }, [draft.revision]);

  const editable = draft.status === "draft" && can("agreement.draft");
  const view = useMemo(() => viewPayload(payload), [payload]);
  const missingDefaults = useMemo(() => JSON.stringify(payload) !== JSON.stringify(view), [payload, view]);

  useEffect(() => {
    if (!dirty || !editable) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty, editable]);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && editable && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    const id = pendingFocusRef.current;
    if (!id) return;
    const el = document.getElementById(id);
    if (el) {
      focusEl(el);
      pendingFocusRef.current = null;
    }
  }, [itemsPage]);

  function applyServerDraft(fresh: DraftOut) {
    const nextPayload = payloadFromDraft(fresh.payload);
    setDraft(fresh);
    setPayload(nextPayload);
    setDirty(JSON.stringify(nextPayload) !== JSON.stringify(viewPayload(nextPayload)));
    setStaleConflict(false);
    setAcknowledgedWarnings(new Set());
    approveKeyRef.current = null;
    client.setQueryData(["agreement-drafts", draftId], fresh);
  }

  async function reloadDraft() {
    const fresh = await unwrap(
      api.GET("/api/v1/agreement-drafts/{draft_id}", { params: { path: { draft_id: draftId } } }),
    );
    applyServerDraft(fresh);
    save.reset();
    approve.reset();
  }

  function updatePayload(next: Payload) {
    setPayload(applyEdit(payload, view, next) as Payload);
    setDirty(true);
    setActionMsg(null);
  }

  const save = useMutation({
    mutationFn: () => {
      if (!draft || !payload) throw new Error("Draft not loaded");
      return unwrap(
        api.PUT("/api/v1/agreement-drafts/{draft_id}", {
          params: { path: { draft_id: draftId }, header: { "if-match": `"${draft.revision}"` } },
          body: {
            payload: (missingDefaults ? view : payload) as unknown as Record<string, unknown>,
            change_summary: orNull(changeSummary),
          },
        }),
      );
    },
    onSuccess: (fresh) => {
      applyServerDraft(fresh);
      setChangeSummary("");
      setActionMsg("Draft saved.");
    },
    onError: (e) => {
      if (e instanceof ApiError && (e.code === "precondition_failed" || e.status === 412)) {
        setStaleConflict(true);
      }
    },
  });

  const validate = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/validate", {
          params: { path: { draft_id: draftId } },
        }),
      ),
    onSuccess: (fresh) => {
      // a response for an older revision (a save landed while this validate was in flight) must not overwrite the draft
      if (fresh.revision < draftRevisionRef.current) return;
      setDraft(fresh);
      client.setQueryData(["agreement-drafts", draftId], fresh);
    },
  });

  const submit = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/submit", {
          params: { path: { draft_id: draftId } },
          body: { acknowledged_warnings: Array.from(acknowledgedWarnings) },
        }),
      ),
    onSuccess: (fresh) => {
      applyServerDraft(fresh);
      setActionMsg("Draft submitted for approval.");
      void client.invalidateQueries({ queryKey: ["agreement-drafts"] });
    },
  });

  const approve = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error("Draft not loaded");
      if (!approveKeyRef.current || approveKeyRef.current.revision !== draft.revision) {
        approveKeyRef.current = { revision: draft.revision, key: crypto.randomUUID() };
      }
      return unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/approve", {
          params: {
            path: { draft_id: draftId },
            header: {
              "if-match": `"${draft.revision}"`,
              "idempotency-key": approveKeyRef.current.key,
            },
          },
        }),
      );
    },
    onSuccess: (fresh) => {
      applyServerDraft(fresh);
      setActionMsg("Draft approved.");
      void client.invalidateQueries({ queryKey: ["agreement-drafts"] });
      void client.invalidateQueries({ queryKey: ["agreements"] });
    },
    onError: (e) => {
      if (e instanceof ApiError && (e.code === "precondition_failed" || e.status === 412)) {
        setStaleConflict(true);
      }
    },
  });

  const returnDraft = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/return", {
          params: { path: { draft_id: draftId } },
          body: { comment: returnComment },
        }),
      ),
    onSuccess: (fresh) => {
      applyServerDraft(fresh);
      setReturnComment("");
      setActionMsg("Draft returned to author.");
      void client.invalidateQueries({ queryKey: ["agreement-drafts"] });
    },
  });

  const rejectDraft = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/reject", {
          params: { path: { draft_id: draftId } },
          body: { reason: rejectReason },
        }),
      ),
    onSuccess: (fresh) => {
      applyServerDraft(fresh);
      setRejectReason("");
      setActionMsg("Draft rejected.");
      void client.invalidateQueries({ queryKey: ["agreement-drafts"] });
    },
  });

  function focusIssue(issue: Issue) {
    const id = issueFieldId(issue.field);
    if (!id) return;
    const itemIdx = itemIndexFromIssuePath(issue.field);
    const targetPage = itemIdx !== null ? pageForItemIndex(itemIdx, ITEMS_PAGE_SIZE) : itemsPage;
    if (targetPage !== itemsPage) {
      pendingFocusRef.current = id;
      setItemsPage(targetPage);
      return;
    }
    const el = document.getElementById(id);
    if (el) focusEl(el);
  }

  const saveErr = save.error as ApiError | null;
  const actionErr =
    (validate.error as ApiError | null) ??
    (submit.error as ApiError | null) ??
    (approve.error as ApiError | null) ??
    (returnDraft.error as ApiError | null) ??
    (rejectDraft.error as ApiError | null);

  const isAuthor = Boolean(me?.user.id && draft.created_by === me.user.id);
  const canApprove = draft.status === "pending_approval" && can("agreement.approve");

  return (
    <>
      <AgreementsNav />
      <div className="flex flex-col gap-6">
        {blocker.state === "blocked" && (
          <Card title="Unsaved changes">
            <p className="mb-3 text-sm">You have unsaved edits on this draft. Leave without saving?</p>
            <div className="flex gap-3">
              <Button variant="danger" onClick={() => blocker.proceed()}>
                Leave without saving
              </Button>
              <Button variant="secondary" onClick={() => blocker.reset()}>
                Stay on draft
              </Button>
            </div>
          </Card>
        )}

        <Card title={`Draft ${draft.id.slice(0, 8)} · ${DRAFT_STATUS_LABELS[draft.status] ?? draft.status}`}>
          <div className="flex flex-col gap-3 text-sm">
            <div className="flex flex-wrap gap-6 text-muted">
              <span>Revision {draft.revision}</span>
              <span>Source: {draft.source}</span>
              <span>Updated {utc(draft.updated_at)}</span>
              {draft.agreement_id && (
                <Link to={`/agreements/${draft.agreement_id}`} className="text-brand underline">
                  Agreement
                </Link>
              )}
              {draft.resulting_version_id && (
                <Link
                  to={`/agreements/versions/${draft.resulting_version_id}`}
                  className="text-brand underline"
                >
                  View approved version
                </Link>
              )}
            </div>

            {draft.review_comment && <Alert tone="info">Review comment: {draft.review_comment}</Alert>}
            {draft.rejection_reason && (
              <Alert tone="danger">Rejection reason: {draft.rejection_reason}</Alert>
            )}
            {!editable && draft.status === "draft" && (
              <Alert tone="info">Read-only: you do not have permission to edit drafts.</Alert>
            )}
            {draft.status !== "draft" && (
              <Alert tone="info">
                This draft is {DRAFT_STATUS_LABELS[draft.status] ?? draft.status} and cannot be edited.
              </Alert>
            )}
            {staleConflict && (
              <Alert>
                Someone else changed this draft.{" "}
                <button type="button" className="underline" onClick={() => void reloadDraft()}>
                  Reload
                </button>{" "}
                and try again.
              </Alert>
            )}
            {actionMsg && <Alert tone="ok">{actionMsg}</Alert>}
            <ErrorAlert error={saveErr?.code === "precondition_failed" ? null : saveErr} />
            <ErrorAlert error={actionErr?.code === "precondition_failed" ? null : actionErr} />
          </div>
        </Card>

        <HeaderSection payload={view} editable={editable} error={saveErr} onChange={updatePayload} />

        <RulesSection
          rules={view.rules}
          editable={editable}
          error={saveErr}
          onChange={(rules) => updatePayload({ ...view, rules })}
        />

        <CustomersSection
          customers={view.customers}
          editable={editable}
          onChange={(customers) => updatePayload({ ...view, customers })}
        />

        <ItemsSection
          items={view.items}
          page={itemsPage}
          editable={editable}
          onPageChange={setItemsPage}
          onChange={(items) => updatePayload({ ...view, items })}
        />

        {view.rules.type === "rebate" && (
          <TiersSection
            tiers={view.tiers}
            editable={editable}
            onChange={(tiers) => updatePayload({ ...view, tiers })}
          />
        )}

        {editable && (
          <BulkItemsImport
            draftId={draft.id}
            revision={draft.revision}
            onReloadNeeded={() => setStaleConflict(true)}
            onImported={(fresh) => {
              applyServerDraft(fresh);
              setActionMsg("Items imported into draft.");
            }}
          />
        )}

        <ValidationPanel
          draft={draft}
          acknowledgedWarnings={acknowledgedWarnings}
          canEdit={editable}
          validating={validate.isPending}
          onValidate={() => validate.mutate()}
          onToggleWarning={(id, checked) => {
            setAcknowledgedWarnings((prev) => {
              const next = new Set(prev);
              if (checked) next.add(id);
              else next.delete(id);
              return next;
            });
          }}
          onFocusIssue={focusIssue}
        />

        {editable && (
          <Card title="Save & submit">
            <div className="flex flex-col gap-4">
              <div className="max-w-md">
                <Field
                  label="Change summary (optional)"
                  value={changeSummary}
                  onChange={(e) => {
                    setChangeSummary(e.target.value);
                    setDirty(true);
                  }}
                />
              </div>
              {missingDefaults && (
                <p className="text-xs text-muted">
                  Some fields were missing and show defaults; save to store them.
                </p>
              )}
              {dirty && (
                <p className="text-xs text-muted">
                  Save your edits before running Validate or Submit so the server checks the latest values.
                </p>
              )}
              <div className="flex flex-wrap gap-3">
                <Button disabled={save.isPending} onClick={() => save.mutate()}>
                  Save draft
                </Button>
                <Button variant="secondary" disabled={validate.isPending} onClick={() => validate.mutate()}>
                  Validate
                </Button>
                <Button
                  variant="secondary"
                  disabled={submit.isPending || dirty}
                  onClick={() => submit.mutate()}
                >
                  Submit for approval
                </Button>
              </div>
            </div>
          </Card>
        )}

        {canApprove && (
          <Card title="Approval decision">
            <div className="flex flex-col gap-6">
              <div>
                {isAuthor && (
                  <p className="mb-2 text-sm text-muted">
                    Four-eyes rule: you created this draft, so a second approver must approve it.
                  </p>
                )}
                <Button disabled={isAuthor || approve.isPending} onClick={() => approve.mutate()}>
                  Approve draft
                </Button>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                <form
                  className="flex flex-col gap-3 rounded-md border border-line p-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    returnDraft.mutate();
                  }}
                >
                  <Field
                    label="Return comment"
                    required
                    value={returnComment}
                    error={fieldError(returnDraft.error as ApiError | null, "comment")}
                    onChange={(e) => setReturnComment(e.target.value)}
                  />
                  <div>
                    <Button type="submit" variant="secondary" disabled={returnDraft.isPending}>
                      Return to draft
                    </Button>
                  </div>
                </form>

                <form
                  className="flex flex-col gap-3 rounded-md border border-line p-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    rejectDraft.mutate();
                  }}
                >
                  <Field
                    label="Rejection reason"
                    required
                    value={rejectReason}
                    error={fieldError(rejectDraft.error as ApiError | null, "reason")}
                    onChange={(e) => setRejectReason(e.target.value)}
                  />
                  <div>
                    <Button type="submit" variant="danger" disabled={rejectDraft.isPending}>
                      Reject draft
                    </Button>
                  </div>
                </form>
              </div>
            </div>
          </Card>
        )}

        {can("calculation.run") && <DraftPreviewPanel draftId={draft.id} />}

        <div>
          <Link to="/agreement-drafts" className="text-brand underline">
            Back to drafts
          </Link>
        </div>
      </div>
    </>
  );
}
