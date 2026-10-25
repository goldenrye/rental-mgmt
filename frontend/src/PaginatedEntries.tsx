import { Children, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { paginateEntries } from "./pagination";
import { labelTableRows } from "./mobileTable";
import { t } from "./i18n";

function PaginatedEntries({ children, label, render }: {
  children: ReactNode;
  label: string;
  render: (entries: ReactNode[]) => ReactNode;
}) {
  const [requestedPage, setPage] = useState(1);
  // A keyed fragment is one entry, keeping a property and its unit rows together.
  const result = paginateEntries(Children.toArray(children), requestedPage);
  useEffect(() => {
    if (requestedPage !== result.page) setPage(result.page);
  }, [requestedPage, result.page]);

  return (
    <>
      {render(result.entries)}
      {result.pageCount > 1 && (
        <nav className="pagination" aria-label={t("{{label}} pagination", { label: t(label) })} onClick={(event) => event.stopPropagation()}>
          <span className="pagination-summary" role="status">
            {t("{{first}}-{{last}} of {{total}} | Page {{page}} of {{pages}}", { first: result.first, last: result.last, total: result.total, page: result.page, pages: result.pageCount })}
          </span>
          <div className="pagination-buttons">
            <button type="button" disabled={result.page === 1} onClick={() => setPage(result.page - 1)} aria-label={t("Previous page of {{label}}", { label: t(label) })}>{t("Previous")}</button>
            {Array.from({ length: result.pageCount }, (_, index) => index + 1).map((page) => (
              <button
                key={page}
                type="button"
                aria-label={t("{{label}} page {{page}}", { label: t(label), page })}
                aria-current={page === result.page ? "page" : undefined}
                onClick={() => setPage(page)}
              >
                {page}
              </button>
            ))}
            <button type="button" disabled={result.page === result.pageCount} onClick={() => setPage(result.page + 1)} aria-label={t("Next page of {{label}}", { label: t(label) })}>{t("Next")}</button>
          </div>
        </nav>
      )}
    </>
  );
}

export function PaginatedList({ children, label, className }: {
  children: ReactNode;
  label: string;
  className?: string;
}) {
  return <PaginatedEntries label={label} render={(entries) => <div className={className}>{entries}</div>}>{children}</PaginatedEntries>;
}

export function DataTable({ children, empty, headers, className, label = "Entries" }: {
  children: ReactNode;
  empty: string;
  headers?: string[];
  className?: string;
  label?: string;
}) {
  const translatedHeaders = headers?.map((header) => t(header));
  return (
    <PaginatedEntries
      label={label}
      render={(entries) => entries.length ? (
        <div className="table-wrap">
          <table className={`${className ?? ""}${headers ? " mobile-table" : ""}`} aria-label={t(label)} role="table">
            {translatedHeaders && <thead role="rowgroup"><tr role="row">{translatedHeaders.map((header) => <th role="columnheader" scope="col" key={header}>{header}</th>)}</tr></thead>}
            <tbody role="rowgroup">{translatedHeaders ? labelTableRows(entries, translatedHeaders, headers) : entries}</tbody>
          </table>
        </div>
      ) : <p className="muted">{t(empty)}</p>}
    >
      {children}
    </PaginatedEntries>
  );
}
