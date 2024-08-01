import { ColumnSort } from "@tanstack/react-table";

import {
  SpanSort,
  TracesTableQuery$variables,
} from "./__generated__/TracesTableQuery.graphql";

export const ANNOTATIONS_COLUMN_PREFIX = "annotations";
export const ANNOTATIONS_KEY_SEPARATOR = ":";
export const DEFAULT_SORT: SpanSort = {
  col: "startTime",
  dir: "desc",
};

export function getGqlSort(
  sort: ColumnSort
): TracesTableQuery$variables["sort"] {
  let col = null,
    evalResultKey = null;
  if (sort.id && sort.id.startsWith(ANNOTATIONS_COLUMN_PREFIX)) {
    const [, attr, name] = sort.id.split(ANNOTATIONS_KEY_SEPARATOR);
    evalResultKey = {
      attr,
      name,
    } as SpanSort["evalResultKey"];
  } else {
    col = sort.id as SpanSort["col"];
  }

  return {
    col,
    evalResultKey,
    dir: sort.desc ? "desc" : "asc",
  };
}
