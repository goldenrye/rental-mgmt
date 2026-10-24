import { Children, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { paginateEntries } from "./pagination";
import { labelTableRows } from "./mobileTable";

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
        <nav className="pagination" aria-label={`${label} pagination`} onClick={(event) => event.stopPropagation()}>
          <span className="pagination-summary" role="status">
            {result.first}-{result.last} of {result.total} | Page {result.page} of {result.pageCount}
          </span>
          <div className="pagination-buttons">
            <button type="button" disabled={result.page === 1} onClick={() => setPage(result.page - 1)} aria-label={`Previous page of ${label}`}>Previous</button>
            {Array.from({ length: result.pageCount }, (_, index) => index + 1).map((page) => (
              <button
                key={page}
                type="button"
                aria-label={`${label} page ${page}`}
                aria-current={page === result.page ? "page" : undefined}
                onClick={() => setPage(page)}
              >
                {page}
              </button>
            ))}
            <button type="button" disabled={result.page === result.pageCount} onClick={() => setPage(result.page + 1)} aria-label={`Next page of ${label}`}>Next</button>
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
  return (
    <PaginatedEntries
      label={label}
      render={(entries) => entries.length ? (
        <div className="table-wrap">
          <table className={`${className ?? ""}${headers ? " mobile-table" : ""}`} aria-label={label} role="table">
            {headers && <thead role="rowgroup"><tr role="row">{headers.map((header) => <th role="columnheader" scope="col" key={header}>{header}</th>)}</tr></thead>}
            <tbody role="rowgroup">{headers ? labelTableRows(entries, headers) : entries}</tbody>
          </table>
        </div>
      ) : <p className="muted">{empty}</p>}
    >
      {children}
    </PaginatedEntries>
  );
}
