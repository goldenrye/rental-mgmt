import { Children, Fragment, cloneElement, isValidElement } from "react";
import type { ReactNode } from "react";

type TableNodeProps = {
  children?: ReactNode;
  colSpan?: number;
  role?: string;
  "data-label"?: string;
  "data-full-width"?: boolean;
};

// Annotate only this table's cells; nested unit tables supply their own headers.
export function labelTableRows(children: ReactNode, headers: string[], columnKeys = headers): ReactNode {
  return Children.map(children, (row) => {
    if (!isValidElement<TableNodeProps>(row)) return row;
    if (row.type === Fragment) {
      return cloneElement(row, {}, labelTableRows(row.props.children, headers, columnKeys));
    }
    if (row.type !== "tr") return row;
    let column = 0;
    return cloneElement(row, { role: "row" }, Children.map(row.props.children, (cell) => {
      if (!isValidElement<TableNodeProps>(cell) || cell.type !== "td") return cell;
      const span = cell.props.colSpan ?? 1;
      const label = headers[column] ?? "";
      const isActions = columnKeys[column] === "Actions";
      column += span;
      return cloneElement(cell, {
        role: "cell",
        "data-label": span > 1 ? undefined : label,
        "data-full-width": span > 1 || isActions,
      });
    }));
  });
}
