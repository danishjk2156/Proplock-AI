import { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type CellStyle,
  type ColDef,
  type ICellRendererParams,
  type RowClickedEvent,
} from "ag-grid-community";
import { Pill } from "../../components";
import { dateTime, kindLabel, money } from "../../format";
import type { ClosingSummary, LedgerEntry } from "../../types";

ModuleRegistry.registerModules([AllCommunityModule]);

// The grid takes the app's own colour tokens, so light and dark follow the rest of the app.
const theme = themeQuartz.withParams({
  backgroundColor: "var(--surface)",
  foregroundColor: "var(--ink)",
  headerBackgroundColor: "var(--surface-2)",
  headerTextColor: "var(--muted)",
  borderColor: "var(--line)",
  rowHoverColor: "var(--surface-2)",
  selectedRowBackgroundColor: "var(--action-soft)",
  accentColor: "var(--action)",
  oddRowBackgroundColor: "var(--surface)",
  fontFamily: "Inter, system-ui, sans-serif",
  fontSize: 13,
  headerFontWeight: 600,
  wrapperBorderRadius: 16,
  spacing: 7,
});

const StatusCell = (p: ICellRendererParams) => (p.value ? <Pill status={p.value} /> : <span className="muted">—</span>);
function moneyCol<T extends { currency: string }>(colId: string, headerName: string, get: (row: T) => string | undefined,
  extra: Partial<ColDef<T>> = {}): ColDef<T> {
  return {
    colId, headerName, type: "rightAligned", width: 130,
    valueGetter: (p) => (p.data ? get(p.data) : undefined),
    comparator: (a, b) => Number(a) - Number(b),
    valueFormatter: (p) => money(p.value, p.data?.currency),
    ...extra,
  };
}

export function ClosingsGrid({ rows, quickFilter, onOpen, showBroker, highlight }: {
  rows: ClosingSummary[];
  quickFilter: string;
  onOpen: (id: string) => void;
  showBroker: boolean;
  highlight: Set<string> | null;
}) {
  const cols = useMemo<ColDef<ClosingSummary>[]>(() => [
    { field: "tenant_name", headerName: "Tenant", pinned: "left", width: 150 },
    { field: "title", headerName: "Flat", flex: 1, minWidth: 180, tooltipField: "title" },
    { field: "property_id", headerName: "Property", width: 130 },
    ...(showBroker ? [{ field: "broker_name", headerName: "Broker", width: 120 } as ColDef<ClosingSummary>] : []),
    { field: "status", headerName: "Closing", width: 135, cellRenderer: StatusCell },
    { colId: "invoice_status", headerName: "Invoice", width: 145, valueGetter: (p) => p.data?.invoice?.status, cellRenderer: StatusCell },
    { colId: "number", headerName: "Invoice no.", width: 125, valueGetter: (p) => p.data?.invoice?.number ?? "—" },
    moneyCol<ClosingSummary>("gross", "Deposit + brokerage", (r) => r.gross),
    moneyCol<ClosingSummary>("token", "Token deducted", (r) => r.invoice?.token_deduction),
    moneyCol<ClosingSummary>("received", "Received", (r) => r.received),
    moneyCol<ClosingSummary>("outstanding", "Outstanding", (r) => r.outstanding,
      { cellStyle: (p) => (Number(p.value) > 0 ? ({ fontWeight: 700 } as CellStyle) : null) }),
    { colId: "reminders", headerName: "Reminders", width: 105, type: "rightAligned", valueGetter: (p) => p.data?.invoice?.reminders_sent ?? 0 },
    { field: "created_at", headerName: "Closed", width: 140, valueFormatter: (p) => dateTime(p.value), sort: "desc" },
  ], [showBroker]);

  return (
    <div style={{ height: 420 }}>
      <AgGridReact<ClosingSummary>
        theme={theme}
        rowData={highlight ? rows.filter((r) => highlight.has(r.id)) : rows}
        columnDefs={cols}
        defaultColDef={{ sortable: true, filter: true, resizable: true }}
        quickFilterText={quickFilter}
        getRowId={(p) => p.data.id}
        onRowClicked={(e: RowClickedEvent<ClosingSummary>) => e.data && onOpen(e.data.id)}
        rowStyle={{ cursor: "pointer" }}
        overlayNoRowsTemplate="No closings yet"
        pagination={rows.length > 50}
        paginationPageSize={50}
      />
    </div>
  );
}

const DIRECTION_LABEL = { in: "In", out: "Out", info: "Info" } as const;

export function EntriesGrid({ rows, quickFilter, titles, highlight }: {
  rows: LedgerEntry[];
  quickFilter: string;
  titles: Map<string, string>;
  highlight: Set<string> | null;
}) {
  const cols = useMemo<ColDef<LedgerEntry>[]>(() => [
    { field: "occurred_at", headerName: "When", width: 150, valueFormatter: (p) => dateTime(p.value), sort: "desc" },
    { field: "kind", headerName: "Event", width: 190, valueFormatter: (p) => kindLabel(p.value) },
    { field: "direction", headerName: "Money", width: 95, valueFormatter: (p) => DIRECTION_LABEL[p.value as LedgerEntry["direction"]] },
    moneyCol<LedgerEntry>("amount", "Amount", (r) => r.amount, {
      cellStyle: (p) => (p.data?.direction === "info" ? ({ color: "var(--muted)" } as CellStyle) : ({ fontWeight: 700 } as CellStyle)),
    }),
    { colId: "flat", headerName: "Closing", width: 170, valueGetter: (p) => (p.data?.closing_id ? titles.get(p.data.closing_id) ?? "—" : "Before closing") },
    { field: "paypal_ref", headerName: "PayPal ref", width: 190 },
    { field: "source", headerName: "Source", width: 105 },
    { field: "note", headerName: "Note", flex: 1, minWidth: 200, tooltipField: "note" },
  ], [titles]);

  return (
    <div style={{ height: 420 }}>
      <AgGridReact<LedgerEntry>
        theme={theme}
        rowData={highlight ? rows.filter((r) => highlight.has(r.id)) : rows}
        columnDefs={cols}
        defaultColDef={{ sortable: true, filter: true, resizable: true }}
        quickFilterText={quickFilter}
        getRowId={(p) => p.data.id}
        overlayNoRowsTemplate="No money events yet"
        pagination={rows.length > 100}
        paginationPageSize={100}
      />
    </div>
  );
}
