import {
  ClockCounterClockwiseIcon,
  DatabaseIcon,
  SidebarSimpleIcon,
  SparkleIcon,
} from "@phosphor-icons/react"
import { motion } from "motion/react"

import { ThemeToggle } from "./theme-toggle"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import type { RunSummary, TableReference } from "@/lib/api"

export type WorkspaceView = "runs" | "tables"

interface NavigationSidebarProps {
  view: WorkspaceView
  runs: RunSummary[]
  tables: TableReference[]
  selectedRunId?: string
  selectedTable?: TableReference
  onViewChange: (view: WorkspaceView) => void
  onRunSelect: (id: string) => void
  onTableSelect: (table: TableReference) => void
  onNavigate?: () => void
  onToggleSidebar?: () => void
}

export function NavigationSidebar({
  view,
  runs,
  tables,
  selectedRunId,
  selectedTable,
  onViewChange,
  onRunSelect,
  onTableSelect,
  onNavigate,
  onToggleSidebar,
}: NavigationSidebarProps) {
  const groupedTables = tables.reduce<Record<string, TableReference[]>>(
    (groups, table) => {
      ;(groups[table.schema] ??= []).push(table)
      return groups
    },
    {}
  )

  return (
    <motion.aside className="flex h-full min-h-0 flex-col bg-sidebar">
      <div className="flex h-16 items-center justify-between gap-2 border-b px-5">
        <div className="flex w-full items-center gap-2">
          <div className="flex w-full items-center gap-2">
            <span className="flex size-7 items-center justify-center rounded-sm bg-foreground text-background">
              <SparkleIcon weight="fill" />
            </span>
            <div className="min-w-0">
              <p className="font-heading text-sm font-semibold tracking-tight">
                TAQR
              </p>
              <p className="text-caption tracking-[0.18em] text-muted-foreground uppercase">
                Research interface
              </p>
            </div>
          </div>
          <button
            type="button"
            aria-label="Close sidebar"
            className="cursor-pointer self-start text-foreground transition-colors hover:text-primary"
            onClick={onToggleSidebar}
          >
            <SidebarSimpleIcon className="size-5" weight="fill" mirrored />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 border-b">
        <Button
          variant="ghost"
          className={cn(
            "h-11 rounded-none border-r",
            view === "runs" &&
              "bg-sidebar-accent text-sidebar-accent-foreground"
          )}
          onClick={() => onViewChange("runs")}
        >
          <ClockCounterClockwiseIcon data-icon="inline-start" />
          Runs
        </Button>
        <Button
          variant="ghost"
          className={cn(
            "h-11 rounded-none",
            view === "tables" &&
              "bg-sidebar-accent text-sidebar-accent-foreground"
          )}
          onClick={() => onViewChange("tables")}
        >
          <DatabaseIcon data-icon="inline-start" />
          Tables
        </Button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        {view === "runs" ? (
          <nav aria-label="Run history" className="flex flex-col">
            <p className="px-5 pt-5 pb-2 text-caption font-semibold tracking-[0.16em] text-muted-foreground uppercase">
              Recent investigations
            </p>
            {runs.map((run) => (
              <button
                key={run.id}
                type="button"
                className={cn(
                  "flex min-w-0 flex-col gap-1 border-b px-5 py-3 text-left transition-colors hover:bg-sidebar-accent",
                  selectedRunId === run.id && "bg-sidebar-accent"
                )}
                onClick={() => {
                  onRunSelect(run.id)
                  onNavigate?.()
                }}
              >
                <span className="truncate text-xs font-medium">
                  {run.question}
                </span>
                <span className="flex items-center justify-between gap-2 text-caption text-muted-foreground">
                  <span className="capitalize">{run.status}</span>
                  <time>{formatDate(run.created_at)}</time>
                </span>
              </button>
            ))}
            {runs.length === 0 && (
              <p className="px-5 py-8 text-xs leading-relaxed text-muted-foreground">
                Submitted questions will appear here.
              </p>
            )}
          </nav>
        ) : (
          <nav aria-label="Table catalog" className="flex flex-col pb-5">
            {Object.entries(groupedTables)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([schema, schemaTables]) => (
                <div key={schema}>
                  <p className="px-5 pt-5 pb-2 text-caption font-semibold tracking-[0.16em] text-muted-foreground uppercase">
                    {schema}
                  </p>
                  {schemaTables.map((table) => (
                    <button
                      key={`${table.schema}.${table.name}`}
                      type="button"
                      className={cn(
                        "flex w-full items-center justify-between gap-3 px-5 py-2 text-left text-xs transition-colors hover:bg-sidebar-accent",
                        selectedTable?.schema === table.schema &&
                          selectedTable.name === table.name &&
                          "bg-sidebar-accent"
                      )}
                      onClick={() => {
                        onTableSelect(table)
                        onNavigate?.()
                      }}
                    >
                      <span className="truncate">{table.name}</span>
                      {table.row_count !== undefined && (
                        <span className="shrink-0 font-mono text-caption text-muted-foreground">
                          {table.row_count.toLocaleString()}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              ))}
          </nav>
        )}
      </ScrollArea>

      <div className="flex flex-col gap-1 border-t px-5 py-3">
        <ThemeToggle />
        <p className="text-caption text-muted-foreground">
          Evidence-led answers · local workspace
        </p>
      </div>
    </motion.aside>
  )
}

function formatDate(value: string) {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(date)
}
