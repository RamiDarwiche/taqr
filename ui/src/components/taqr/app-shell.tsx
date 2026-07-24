import { useCallback, useEffect, useState } from "react"
import { ListIcon, SidebarSimpleIcon } from "@phosphor-icons/react"
import { AnimatePresence, motion } from "motion/react"

import { NavigationSidebar } from "./navigation-sidebar"
import type { WorkspaceView } from "./navigation-sidebar"
import { QueryComposer } from "./query-composer"
import type { SubmitQuestionInput } from "./query-composer"
import { RunReviewer } from "./run-reviewer"
import { TableExplorer } from "./table-explorer"
import { ThinkingTrace } from "./thinking-trace"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { api } from "@/lib/api"
import type {
  RunDetail,
  RunSummary,
  TablePage,
  TableReference,
  ThinkingStatus,
  ThinkingStep,
} from "@/lib/api"

export function TaqrApp() {
  const [view, setView] = useState<WorkspaceView>("runs")
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [tables, setTables] = useState<TableReference[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string>()
  const [selectedTable, setSelectedTable] = useState<TableReference>()
  const [run, setRun] = useState<RunDetail>()
  const [tablePage, setTablePage] = useState<TablePage>()
  const [runLoading, setRunLoading] = useState(false)
  const [tableLoading, setTableLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [runError, setRunError] = useState<string>()
  const [tableError, setTableError] = useState<string>()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [sessionId, setSessionId] = useState<string>()
  const [liveQuestion, setLiveQuestion] = useState<string>()
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([])
  const [thinkingStatus, setThinkingStatus] = useState<ThinkingStatus>()

  useEffect(() => {
    setSessionId(getOrCreateSessionId())
    let active = true
    void Promise.allSettled([api.listRuns(), api.listTables()]).then(
      ([runsResult, tablesResult]) => {
        if (!active) return
        if (runsResult.status === "fulfilled") {
          setRuns(runsResult.value)
          if (runsResult.value[0]) setSelectedRunId(runsResult.value[0].id)
        } else {
          setRunError(errorMessage(runsResult.reason))
        }
        if (tablesResult.status === "fulfilled") {
          setTables(tablesResult.value)
        } else {
          setTableError(errorMessage(tablesResult.reason))
        }
      }
    )
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!selectedRunId || submitting) return
    let active = true
    setRunLoading(true)
    setRunError(undefined)
    void api
      .getRun(selectedRunId)
      .then((detail) => {
        if (active) setRun(detail)
      })
      .catch((error: unknown) => {
        if (active) setRunError(errorMessage(error))
      })
      .finally(() => {
        if (active) setRunLoading(false)
      })
    return () => {
      active = false
    }
  }, [selectedRunId, submitting])

  const loadTable = useCallback(
    async (table: TableReference, offset: number) => {
      setTableLoading(true)
      setTableError(undefined)
      try {
        setTablePage(await api.getTable(table.schema, table.name, 50, offset))
      } catch (error) {
        setTableError(errorMessage(error))
      } finally {
        setTableLoading(false)
      }
    },
    []
  )

  useEffect(() => {
    if (!selectedTable) return
    void loadTable(selectedTable, 0)
  }, [selectedTable, loadTable])

  async function submitQuestion(input: SubmitQuestionInput) {
    setSubmitting(true)
    setView("runs")
    setRunError(undefined)
    setLiveQuestion(input.question)
    setThinkingSteps([])
    setThinkingStatus({
      title: "Thinking",
      detail: "Connecting to the planner…",
      phase: "thinking",
    })
    setSelectedRunId(undefined)
    setRun(undefined)
    try {
      const created = await api.createRunStream(
        {
          question: input.question,
          question_id: input.question_id,
          session_id: sessionId ?? getOrCreateSessionId(),
        },
        {
          onStatus: (status) => setThinkingStatus(status),
          onStep: (step) =>
            setThinkingSteps((current) => {
              const without = current.filter((item) => item.id !== step.id)
              return [...without, step]
            }),
        }
      )
      setRun(created)
      setSelectedRunId(created.id)
      setRuns((current) => [
        created,
        ...current.filter((item) => item.id !== created.id),
      ])
    } catch (error) {
      setRunError(errorMessage(error))
    } finally {
      setSubmitting(false)
      setLiveQuestion(undefined)
      setThinkingSteps([])
      setThinkingStatus(undefined)
    }
  }

  const sidebar = (
    <NavigationSidebar
      view={view}
      runs={runs}
      tables={tables}
      selectedRunId={selectedRunId}
      selectedTable={selectedTable}
      onViewChange={setView}
      onRunSelect={(id) => {
        setView("runs")
        setSelectedRunId(id)
      }}
      onTableSelect={(table) => {
        setView("tables")
        setSelectedTable(table)
      }}
      onNavigate={() => setMobileOpen(false)}
      onToggleSidebar={() => {
        setSidebarOpen(false)
        setMobileOpen(false)
      }}
    />
  )

  return (
    <div className="flex h-svh min-h-0 bg-background">
      <AnimatePresence initial={false}>
        {sidebarOpen && (
          <motion.div
            key="sidebar"
            initial={{ width: 0 }}
            animate={{ width: "17rem" }}
            exit={{ width: 0 }}
            transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
            className="hidden min-h-0 overflow-hidden border-r lg:block"
          >
            <div className="h-full w-[17rem]">{sidebar}</div>
          </motion.div>
        )}
      </AnimatePresence>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent
          side="left"
          showCloseButton={false}
          className="w-[17rem] p-0 shadow-none"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>Workspace navigation</SheetTitle>
            <SheetDescription>Choose a run or database table.</SheetDescription>
          </SheetHeader>
          {sidebar}
        </SheetContent>
      </Sheet>

      <section className="relative grid min-h-0 min-w-0 flex-1 grid-rows-[3rem_minmax(0,1fr)_auto] lg:grid-rows-[minmax(0,1fr)_auto]">
        <header className="flex items-center justify-between border-b px-4 lg:hidden">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Open navigation"
            onClick={() => setMobileOpen(true)}
          >
            <ListIcon />
          </Button>
          <p className="font-heading text-sm font-semibold">TAQR</p>
          <span className="size-8" aria-hidden="true" />
        </header>

        <AnimatePresence>
          {!sidebarOpen && (
            <motion.button
              key="sidebar-open"
              type="button"
              aria-label="Open sidebar"
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.2 }}
              className="absolute top-5 left-4 z-10 hidden cursor-pointer text-foreground transition-colors hover:text-primary lg:block"
              onClick={() => setSidebarOpen(true)}
            >
              <SidebarSimpleIcon className="size-5" weight="fill" mirrored />
            </motion.button>
          )}
        </AnimatePresence>

        <div className="relative min-h-0 min-w-0">
          {view === "runs" ? (
            <AnimatePresence mode="wait">
              {submitting && liveQuestion ? (
                <motion.div
                  key="thinking"
                  className="h-full"
                  initial={{ opacity: 0, y: 10, filter: "blur(4px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, y: -12, filter: "blur(4px)" }}
                  transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
                >
                  <ThinkingTrace
                    question={liveQuestion}
                    steps={thinkingSteps}
                    statusTitle={thinkingStatus?.title}
                    statusDetail={thinkingStatus?.detail}
                    isActive={submitting}
                  />
                </motion.div>
              ) : (
                <motion.div
                  key="review"
                  className="h-full"
                  initial={{ opacity: 0, y: 14, filter: "blur(4px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, y: 8, filter: "blur(2px)" }}
                  transition={{ duration: 0.36, ease: [0.22, 1, 0.36, 1] }}
                >
                  <RunReviewer
                    run={run}
                    isLoading={runLoading}
                    error={runError}
                  />
                </motion.div>
              )}
            </AnimatePresence>
          ) : (
            <TableExplorer
              table={selectedTable}
              page={tablePage}
              isLoading={tableLoading}
              error={tableError}
              onPageChange={(offset) => {
                if (selectedTable) void loadTable(selectedTable, offset)
              }}
            />
          )}
        </div>

        <QueryComposer onSubmit={submitQuestion} isSubmitting={submitting} />
      </section>
    </div>
  )
}

function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "An unexpected error occurred."
}

const SESSION_STORAGE_KEY = "taqr.session_id"

function getOrCreateSessionId() {
  if (typeof window === "undefined") return undefined

  const existing = window.localStorage.getItem(SESSION_STORAGE_KEY)
  if (existing) return existing

  const created = window.crypto.randomUUID()
  window.localStorage.setItem(SESSION_STORAGE_KEY, created)
  return created
}
