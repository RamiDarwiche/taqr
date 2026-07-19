import {
  ArrowLeftIcon,
  ArrowRightIcon,
  DatabaseIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { TablePage, TableReference } from "@/lib/api"

interface TableExplorerProps {
  table?: TableReference
  page?: TablePage
  isLoading: boolean
  error?: string
  onPageChange: (offset: number) => void
}

export function TableExplorer({
  table,
  page,
  isLoading,
  error,
  onPageChange,
}: TableExplorerProps) {
  if (!table) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <div className="flex max-w-sm flex-col items-center gap-3">
          <span className="flex size-10 items-center justify-center border text-muted-foreground">
            <DatabaseIcon />
          </span>
          <h1 className="font-heading text-xl font-medium">
            Explore source data
          </h1>
          <p className="text-sm leading-6 text-muted-foreground">
            Choose a table in the public or provenance schema to inspect its
            rows.
          </p>
        </div>
      </div>
    )
  }

  return (
    <main className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b px-5 py-5 md:px-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <Badge variant="outline">{table.schema}</Badge>
              <span className="text-caption tracking-[0.16em] text-muted-foreground uppercase">
                Data catalog
              </span>
            </div>
            <h1 className="font-heading text-2xl font-medium tracking-tight md:text-3xl">
              {table.name}
            </h1>
            {table.description && (
              <p className="mt-1 text-sm text-muted-foreground">
                {table.description}
              </p>
            )}
          </div>
          <p className="font-mono text-caption text-muted-foreground">
            {page?.total !== undefined
              ? `${page.total.toLocaleString()} rows`
              : table.row_count !== undefined
                ? `${table.row_count.toLocaleString()} rows`
                : "Row count unavailable"}
          </p>
        </div>
      </header>

      {error ? (
        <div className="flex flex-1 items-center justify-center gap-2 p-6 text-sm text-destructive">
          <WarningCircleIcon />
          {error}
        </div>
      ) : isLoading ? (
        <div className="flex flex-1 flex-col gap-2 p-5 md:p-8">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="min-w-max">
            <Table className="border-collapse">
              <TableHeader className="sticky top-0 bg-background">
                <TableRow>
                  <TableHead className="w-12 border-r text-center font-mono text-stat">
                    #
                  </TableHead>
                  {page?.columns.map((column) => (
                    <TableHead
                      key={column.name}
                      className="min-w-40 border-r px-4 last:border-r-0"
                    >
                      <span className="block text-caption font-semibold tracking-[0.12em] text-foreground uppercase">
                        {column.name}
                      </span>
                      {column.type && (
                        <span className="font-mono text-stat font-normal text-muted-foreground">
                          {column.type}
                        </span>
                      )}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {page?.rows.map((row, rowIndex) => (
                  <TableRow key={rowIndex}>
                    <TableCell className="border-r text-center font-mono text-stat text-muted-foreground">
                      {page.offset + rowIndex + 1}
                    </TableCell>
                    {page.columns.map((column) => (
                      <TableCell
                        key={column.name}
                        className="max-w-80 border-r px-4 font-mono text-caption last:border-r-0"
                        title={formatCell(row[column.name])}
                      >
                        <span className="block max-w-80 truncate">
                          {formatCell(row[column.name])}
                        </span>
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {page?.rows.length === 0 && (
              <p className="p-10 text-center text-sm text-muted-foreground">
                This page contains no rows.
              </p>
            )}
          </div>
        </ScrollArea>
      )}

      <footer className="flex h-14 shrink-0 items-center justify-between gap-3 border-t px-5 md:px-8">
        <p className="font-mono text-caption text-muted-foreground">
          {page
            ? `${page.offset + 1}–${page.offset + page.rows.length}${page.total ? ` of ${page.total}` : ""}`
            : "—"}
        </p>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            disabled={!page || page.offset === 0 || isLoading}
            onClick={() =>
              page && onPageChange(Math.max(0, page.offset - page.limit))
            }
          >
            <ArrowLeftIcon data-icon="inline-start" />
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={
              !page ||
              isLoading ||
              page.rows.length < page.limit ||
              (page.total !== undefined &&
                page.offset + page.rows.length >= page.total)
            }
            onClick={() => page && onPageChange(page.offset + page.limit)}
          >
            Next
            <ArrowRightIcon data-icon="inline-end" />
          </Button>
        </div>
      </footer>
    </main>
  )
}

function formatCell(value: unknown): string {
  if (value === null) return "NULL"
  if (value === undefined) return "—"
  if (typeof value === "object") {
    try {
      return JSON.stringify(value)
    } catch {
      return "[object]"
    }
  }
  return String(value)
}
