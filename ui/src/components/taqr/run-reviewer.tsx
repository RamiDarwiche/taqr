import type { ReactNode } from "react"
import { format } from "sql-formatter"
import {
  CheckCircleIcon,
  CircleIcon,
  CircleNotchIcon,
  CodeIcon,
  LinkSimpleIcon,
  WarningCircleIcon,
  XCircleIcon,
} from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type {
  Claim,
  ClaimVerification,
  Evidence,
  GoldResult,
  RunDetail,
  ToolCall,
} from "@/lib/api"

/** Human-readable explanations for verifier check ids shown in the UI. */
const CHECK_TOOLTIPS: Record<string, string> = {
  evidence_refs:
    "Every evidence id cited by the claim exists in the returned evidence set.",
  hash: "Replaying the evidence SQL produces the same result fingerprint as when the answer was generated.",
  row_count:
    "The number of rows returned by replaying the evidence SQL matches the stored evidence row_count.",
  metric:
    "The claim’s metric name appears in at least one cited evidence SQL statement.",
  top_k_sql_shape:
    "Evidence SQL includes ORDER BY and a LIMIT that matches the claim’s k (required for a ranking).",
  top_k_k: "The ranking claim includes a required k (top-k size).",
  top_k_row_count:
    "The number of replayed rows matches claim k. Fewer or more rows is treated as fragile underspecification.",
  top_k_null_subject:
    "No NULL values appear in the subject (first) column of the ranking result.",
  top_k_subject:
    "Claimed subject(s) match the replayed ranking in order on the first column (rank 1…k).",
  top_k_monotonic:
    "Metric scores are monotonic in the ORDER BY direction (non-increasing for DESC, non-decreasing for ASC).",
  top_k_ties:
    "Adjacent equal metric scores make the ranking under-specified; marked fragile rather than failed.",
  top_k_non_negative:
    "All metric values in the ranking are non-negative.",
  top_k_filters:
    "Each claim filter value appears as a substring in the cited evidence SQL.",
}

interface RunReviewerProps {
  run?: RunDetail
  isLoading: boolean
  error?: string
}

export function RunReviewer({ run, isLoading, error }: RunReviewerProps) {
  if (isLoading) return <ReviewerSkeleton />
  if (error) {
    return (
      <CenteredMessage
        title="Unable to load this run"
        description={error}
        icon={<WarningCircleIcon />}
      />
    )
  }
  if (!run) {
    return (
      <CenteredMessage
        title="Begin an investigation"
        description="Load a random BIRD MiniDev question below, or choose a previous run from the sidebar."
        icon={<CircleNotchIcon />}
      />
    )
  }

  const evidenceById = new Map(run.evidence.map((item) => [item.id, item]))
  const verificationByClaim = new Map(
    run.verification?.claim_results.map((result) => [
      result.claim_id,
      result,
    ]) ?? []
  )

  return (
    <ScrollArea className="h-full">
      <main className="mx-auto flex max-w-5xl flex-col px-5 pt-8 pb-16 md:px-10 md:pt-12">
        <header className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <VerificationBadge status={run.verification?.status ?? "PENDING"} />
            <Badge variant="outline" className="capitalize">
              RUN {run.status}
            </Badge>
            <span className="font-mono text-caption text-muted-foreground">
              RUN {run.id.slice(0, 8).toUpperCase()}
            </span>
            {run.created_at && (
              <time className="text-caption text-muted-foreground">
                {formatTimestamp(run.created_at)}
              </time>
            )}
          </div>
          <h1 className="max-w-4xl font-heading text-xl font-medium tracking-tight md:text-3xl md:leading-[1.05]">
            {run.question}
          </h1>
          {(run.question_id || run.db_id || run.difficulty) && (
            <div className="flex flex-wrap items-center gap-2 text-caption text-muted-foreground">
              {run.question_id != null && (
                <Badge variant="outline">#{run.question_id}</Badge>
              )}
              {run.db_id && <Badge variant="outline">{run.db_id}</Badge>}
              {run.difficulty && (
                <Badge variant="outline" className="capitalize">
                  {run.difficulty}
                </Badge>
              )}
            </div>
          )}
          {run.claims.length > 0 && (
            <ol className="flex max-w-3xl list-decimal flex-col gap-2 pl-5 text-base leading-7 text-muted-foreground">
              {run.claims.map((claim) => (
                <li key={claim.id}>{claim.claim_text}</li>
              ))}
            </ol>
          )}
        </header>

        <Separator className="my-8 md:my-10" />

        {run.gold_sql && (
          <section aria-labelledby="gold-heading" className="mb-10">
            <div className="mb-4 flex items-end justify-between gap-4">
              <div>
                <p className="text-caption font-semibold tracking-[0.18em] text-muted-foreground uppercase">
                  Benchmark reference
                </p>
                <h2
                  id="gold-heading"
                  className="mt-1 font-heading text-xl font-medium"
                >
                  Gold standard SQL
                </h2>
              </div>
              {run.gold_result && !run.gold_result.error && (
                <Badge variant="outline">
                  {run.gold_result.row_count}{" "}
                  {run.gold_result.row_count === 1 ? "row" : "rows"}
                </Badge>
              )}
            </div>
            <pre className="overflow-x-auto border bg-muted px-4 py-3 font-mono text-caption leading-5 text-foreground">
              <code>{format(run.gold_sql, { language: "postgresql" })}</code>
            </pre>
            <GoldResultView result={run.gold_result} />
          </section>
        )}

        <section aria-labelledby="review-heading">
          <div className="mb-5 flex items-end justify-between gap-4">
            <div>
              <p className="text-caption font-semibold tracking-[0.18em] text-muted-foreground uppercase">
                Review path
              </p>
              <h2
                id="review-heading"
                className="mt-1 font-heading text-xl font-medium"
              >
                Claims and verification
              </h2>
            </div>
            <span className="text-xs text-muted-foreground">
              {run.claims.length} {run.claims.length === 1 ? "claim" : "claims"}
            </span>
          </div>

          <div className="border">
            {run.claims.map((claim, index) => (
              <ClaimReview
                key={claim.id}
                claim={claim}
                evidence={claim.evidence_ids.flatMap((id) => {
                  const item = evidenceById.get(id)
                  return item ? [item] : []
                })}
                missingEvidenceIds={claim.evidence_ids.filter(
                  (id) => !evidenceById.has(id)
                )}
                verification={verificationByClaim.get(claim.id)}
                index={index}
              />
            ))}
            {run.claims.length === 0 && (
              <p className="px-5 py-12 text-center text-sm text-muted-foreground">
                No structured claims were returned for this run.
              </p>
            )}
          </div>
        </section>

        <ToolTimeline calls={run.tool_calls} />

        {run.error && (
          <div className="mt-8 border border-destructive/40 p-4 text-sm text-destructive">
            {run.error}
          </div>
        )}
      </main>
    </ScrollArea>
  )
}

// TODO: add typing for status
function VerificationTabIcon({ status }: { status: string }) {
  if (status.toUpperCase() === "FAILED") {
    return <XCircleIcon className="size-3.5 text-stat text-destructive" />
  } else if (status.toUpperCase() === "NOT_VERIFIED") {
    return <CircleIcon className="size-3.5 text-stat text-muted-foreground" />
  } else {
    return <CheckCircleIcon className="size-3.5 text-stat text-primary" />
  }
}

function ClaimReview({
  claim,
  evidence,
  missingEvidenceIds,
  verification,
  index,
}: {
  claim: Claim
  evidence: Evidence[]
  missingEvidenceIds: string[]
  verification?: ClaimVerification
  index: number
}) {
  return (
    <article className="border-b last:border-b-0">
      <div className="flex gap-4 px-5 py-5 md:px-7">
        <span className="font-mono text-xs text-muted-foreground">
          {String(index + 1).padStart(2, "0")}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-6 font-medium">{claim.claim_text}</p>
          <Tabs defaultValue="claim" className="mt-4">
            <TabsList
              variant="line"
              className="text-primary"
              aria-label={`Review claim ${index + 1}`}
            >
              <TabsTrigger value="claim">Claim</TabsTrigger>
              <TabsTrigger value="evidence">
                Evidence
                <span className="font-mono text-stat">
                  {claim.evidence_ids.length}
                </span>
              </TabsTrigger>
              <TabsTrigger
                value="verification"
                className="flex items-center gap-1"
              >
                Verification
                {verification?.status && (
                  <VerificationTabIcon status={verification.status} />
                )}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="claim" className="pt-3">
              <dl className="grid gap-x-6 gap-y-3 text-xs sm:grid-cols-2">
                <ClaimField label="Type" value={claim.claim_type} />
                <ClaimField
                  label="Subject"
                  value={formatValue(claim.subject)}
                />
                <ClaimField label="Metric" value={claim.metric ?? "—"} />
                <ClaimField label="Top K" value={claim.k?.toString() ?? "—"} />
                <ClaimField
                  label="Filters"
                  value={formatValue(claim.filters)}
                  wide
                />
              </dl>
            </TabsContent>

            <TabsContent value="evidence" className="pt-3">
              <div className="flex flex-col gap-4">
                {evidence.map((item) => (
                  <EvidenceView key={item.id} evidence={item} />
                ))}
                {missingEvidenceIds.map((id) => (
                  <p key={id} className="text-destructive">
                    Referenced evidence {id} was not returned.
                  </p>
                ))}
                {claim.evidence_ids.length === 0 && (
                  <p className="text-muted-foreground">No evidence linked.</p>
                )}
              </div>
            </TabsContent>

            <TabsContent value="verification" className="pt-3">
              {verification ? (
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-2">
                    <VerificationBadge status={verification.status} />
                  </div>
                  {verification.checks.length > 0 && (
                    <ul className="flex flex-col gap-2">
                      {verification.checks.map((check, checkIndex) => (
                        <li
                          key={`${check}-${checkIndex}`}
                          className="flex items-start gap-2 text-muted-foreground"
                        >
                          <CheckCircleIcon className="mt-0.5 shrink-0 text-primary" />
                          <VerificationCheckLabel check={check} />
                        </li>
                      ))}
                    </ul>
                  )}
                  {verification.failure_reason && (
                    <p className="border-l-2 border-destructive pl-3 text-caption text-destructive">
                      Failure: {verification.failure_reason}
                    </p>
                  )}
                  {verification.fragility_notes && (
                    <ul className="flex flex-col gap-2 border-l-2 border-yellow-500 pl-3">
                      {verification.fragility_notes.map((note, noteIndex) => (
                        <li
                          key={`${note}-${noteIndex}`}
                          className="text-caption text-yellow-600/80"
                        >
                          Fragility note: {note}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <p className="text-muted-foreground">
                  No verification result was returned for this claim.
                </p>
              )}
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </article>
  )
}

function VerificationCheckLabel({ check }: { check: string }) {
  const description = CHECK_TOOLTIPS[check]
  if (!description) {
    return <span className="font-mono text-xs">{check}</span>
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span className="cursor-help border-b border-dotted border-muted-foreground/60 font-mono text-xs " />
        }
      >
        {check}
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-left normal-case selection:bg-primary/35 selection:text-white dark:selection:text-black">
        {description}
      </TooltipContent>
    </Tooltip>
  )
}

function ClaimField({
  label,
  value,
  wide = false,
}: {
  label: string
  value: string
  wide?: boolean
}) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <dt className="mb-1 text-stat font-semibold tracking-[0.14em] text-muted-foreground uppercase">
        {label}
      </dt>
      <dd className="font-mono text-[0.6875rem] text-foreground">{value}</dd>
    </div>
  )
}

function GoldResultView({ result }: { result?: GoldResult | null }) {
  if (!result) {
    return (
      <p className="mt-3 text-sm text-muted-foreground">
        Gold standard results were not available for this run.
      </p>
    )
  }

  if (result.error) {
    return (
      <p className="mt-3 border-l-2 border-destructive pl-3 text-sm text-destructive">
        Failed to execute gold SQL: {result.error}
      </p>
    )
  }

  if (result.columns.length === 0) {
    return (
      <p className="mt-3 text-sm text-muted-foreground">
        Gold SQL returned no columns.
      </p>
    )
  }

  return (
    <div className="mt-3 overflow-x-auto border">
      <Table>
        <TableHeader>
          <TableRow>
            {result.columns.map((column) => (
              <TableHead key={column} className="h-8 font-mono text-stat">
                {column}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {result.rows.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={result.columns.length}
                className="h-12 text-center text-muted-foreground"
              >
                No rows returned
              </TableCell>
            </TableRow>
          ) : (
            result.rows.map((row, rowIndex) => (
              <TableRow key={rowIndex}>
                {result.columns.map((column, columnIndex) => (
                  <TableCell
                    key={`${column}-${columnIndex}`}
                    className="max-w-56 truncate font-mono text-stat"
                    title={formatValue(row[columnIndex])}
                  >
                    {formatValue(row[columnIndex])}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  )
}

function EvidenceView({ evidence }: { evidence: Evidence }) {
  return (
    <div className="flex min-w-0 flex-col gap-3 border-l-2 border-primary/40 pl-3">
      <div className="flex flex-wrap items-center gap-2">
        <LinkSimpleIcon />
        <span className="font-mono text-caption font-medium">
          {evidence.id}
        </span>
        <Badge variant="outline">{evidence.row_count} rows</Badge>
      </div>
      <pre className="overflow-x-auto bg-muted px-3 py-2 font-mono text-caption leading-5 text-foreground">
        <code>{format(evidence.sql, { language: "postgresql" })}</code>
      </pre>
      <Table>
        <TableHeader>
          <TableRow>
            {evidence.columns.map((column) => (
              <TableHead key={column} className="h-8 font-mono text-stat">
                {column}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {evidence.rows.map((row, rowIndex) => (
            <TableRow key={rowIndex}>
              {evidence.columns.map((column, columnIndex) => (
                <TableCell
                  key={`${column}-${columnIndex}`}
                  className="max-w-56 truncate font-mono text-stat"
                  title={formatValue(row[columnIndex])}
                >
                  {formatValue(row[columnIndex])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="font-mono text-stat break-all text-muted-foreground">
        Fingerprint: {evidence.result_fingerprint ?? "not available"}
      </p>
    </div>
  )
}

function ToolTimeline({ calls }: { calls: ToolCall[] }) {
  return (
    <section className="mt-10" aria-labelledby="timeline-heading">
      <div className="mb-5">
        <p className="text-caption font-semibold tracking-[0.18em] text-muted-foreground uppercase">
          Execution trace
        </p>
        <h2
          id="timeline-heading"
          className="mt-1 font-heading text-xl font-medium"
        >
          Tool call timeline
        </h2>
      </div>
      <div className="border">
        {calls.map((call) => (
          <div
            key={call.id}
            className="grid grid-cols-[24px_minmax(0,1fr)_auto] items-start gap-3 border-b px-5 py-4 last:border-b-0"
          >
            <CodeIcon className="mt-0.5 text-muted-foreground" />
            <div className="min-w-0">
              <p className="font-mono text-xs font-medium">
                {call.tool_name ?? "Unknown tool"}
              </p>
              <p className="mt-1 font-mono text-stat text-muted-foreground">
                {call.tool_call_id}
              </p>
              <ToolPayload label="Parameters" value={call.parameters} />
              {call.output !== undefined && (
                <ToolPayload label="Output" value={call.output} />
              )}
              {call.error && (
                <p className="mt-2 text-xs text-destructive">{call.error}</p>
              )}
            </div>
            <div className="text-right">
              <Badge variant="outline" className="uppercase">
                {call.status}
              </Badge>
              {call.duration_ms !== null && (
                <p className="mt-1 font-mono text-stat text-muted-foreground">
                  {call.duration_ms} ms
                </p>
              )}
            </div>
          </div>
        ))}
        {calls.length === 0 && (
          <p className="px-5 py-8 text-center text-xs text-muted-foreground">
            No tool calls were recorded.
          </p>
        )}
      </div>
    </section>
  )
}

function ToolPayload({ label, value }: { label: string; value: unknown }) {
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-caption font-medium text-muted-foreground hover:text-primary hover:underline">
        {label}
      </summary>
      <pre className="mt-1 max-h-48 overflow-auto bg-muted p-2 font-mono text-stat leading-4">
        {formatValue(value)}
      </pre>
    </details>
  )
}

function VerificationBadge({ status }: { status: string }) {
  return (
    <Badge
      variant={status.toUpperCase() === "FAILED" ? "destructive" : "secondary"}
      className={`uppercase ${status.toUpperCase() === "FAILED" ? "bg-destructive text-white" : "bg-primary text-white"}`}
    >
      Verification: {status.replace("_", " ")}
    </Badge>
  )
}

function ReviewerSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-5 pt-12 md:px-10">
      <Skeleton className="h-5 w-28" />
      <Skeleton className="h-12 w-4/5" />
      <Skeleton className="h-5 w-2/3" />
      <Skeleton className="mt-8 h-64 w-full" />
    </div>
  )
}

function CenteredMessage({
  title,
  description,
  icon,
}: {
  title: string
  description: string
  icon: ReactNode
}) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center">
      <div className="flex max-w-sm flex-col items-center gap-3">
        <span className="flex size-10 items-center justify-center border text-muted-foreground">
          {icon}
        </span>
        <h1 className="font-heading text-xl font-medium">{title}</h1>
        <p className="text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}

function formatValue(value: unknown): string {
  if (value === null) return "NULL"
  if (value === undefined) return "—"
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function formatTimestamp(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)
}
