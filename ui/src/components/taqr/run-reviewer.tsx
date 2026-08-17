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

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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
  CheckOutcome,
  Claim,
  ClaimVerification,
  Evidence,
  GoldResult,
  JudgeStatus,
  RunDetail,
  ToolCall,
} from "@/lib/api"
import { cn } from "@/lib/utils"

/** Human-readable explanations for verifier check ids shown in the UI. */
const CHECK_TOOLTIPS: Record<string, string> = {
  evidence_refs:
    "Every evidence id cited by the claim exists in the returned evidence set.",
  hash: "Replaying the evidence SQL produces the same result fingerprint as when the answer was generated.",
  row_count:
    "The number of rows returned by replaying the evidence SQL matches the stored evidence row_count.",
  sql_safety:
    "Evidence is exactly one parseable, read-only PostgreSQL query and replayed without error.",
  row_shape:
    "Every replayed row is as wide as the declared columns, so columns can be read by position.",
  columns:
    "Declared evidence column names agree with the SQL projection. A mismatch is a naming defect, not a wrong answer, so it is fragile rather than failed.",
  metric:
    "The claim’s metric resolves to a projected column of the cited evidence.",
  filters:
    "Every declared filter is visible in a predicate, a grouping key, or the replayed rows.",
  filters_conflict:
    "No declared filter is contradicted by an equality predicate on the same column.",
  top_k_sql_shape:
    "Evidence SQL orders by the claimed metric, by alias, ordinal, or expression. A missing ORDER BY fails; a LIMIT that disagrees with k is fragile.",
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
  top_k_non_negative: "All metric values in the ranking are non-negative.",
  top_k_filters:
    "Each ranking filter is visible in a predicate, a grouping key, or the replayed rows.",
  aggregation_contract:
    "The claim carries a matching typed aggregation contract. Without one, expected values cannot be checked, so the claim is fragile rather than failed.",
  aggregation_sql_shape:
    "SQL uses the declared aggregate operation in its outermost query.",
  aggregation_scope:
    "The declared scalar/grouped scope matches the shape of the evidence.",
  aggregation_columns: "The declared aggregate columns resolve unambiguously.",
  aggregation_cardinality:
    "The aggregation returns the expected number of rows.",
  aggregation_subject: "A grouped aggregate resolves the claimed subject once.",
  aggregation_value:
    "The replayed aggregate matches the declared expected value.",
  aggregation_invariant: "COUNT and opt-in domain constraints hold.",
  comparison_contract: "The claim has a matching typed comparison contract.",
  comparison_columns:
    "Comparison subject and value columns resolve unambiguously.",
  comparison_subjects: "Both distinct subjects resolve to exactly one value.",
  comparison_values: "Both replayed operands match their declared values.",
  comparison_relation: "The declared relation holds between replayed operands.",
  comparison_delta: "The absolute or percent difference recomputes correctly.",
  trend_contract: "The claim has a matching typed trend contract.",
  trend_sql_shape:
    "Multi-row trend evidence has a deterministic ascending order.",
  trend_columns: "Trend time and value columns resolve unambiguously.",
  trend_periods: "Trend periods are unique, present, and ordered start-to-end.",
  trend_values: "Replayed endpoint values match the typed trend contract.",
  trend_direction: "The endpoint direction agrees with the claim.",
  trend_change: "The absolute or percent change recomputes correctly.",
  trend_monotonic: "Every series step follows the required direction.",
  existence_contract: "The claim has a matching typed existence contract.",
  existence_sql_shape: "Absence evidence has definitive, non-offset semantics.",
  existence_polarity:
    "Rows, count, or boolean evidence agrees with exists/absent.",
  existence_subject:
    "A claimed present subject occurs in the replayed rows — in one column or across adjacent ones — or is pinned by the evidence predicates.",
  existence_value:
    "Count or boolean existence evidence is valid and unambiguous.",
  value_lookup_contract:
    "The claim carries a matching typed lookup contract naming the column read and the value expected.",
  value_lookup_subject:
    "The subject of the lookup occurs in the replayed rows or is pinned by the evidence predicates.",
  value_lookup_value:
    "The looked-up cell holds the claimed value, and the evidence resolves to exactly one such value.",
  distribution_contract:
    "The claim has a matching typed distribution contract.",
  distribution_sql_shape: "A complete distribution uses grouped aggregate SQL.",
  distribution_columns: "Category and value columns resolve unambiguously.",
  distribution_categories:
    "Categories are unique, non-null, and match the contract.",
  distribution_values:
    "Category values match and satisfy mode-specific bounds.",
  distribution_total: "A complete share/percent distribution sums to 1/100.",
  distribution_coverage:
    "The contract intentionally covers only part of a distribution.",
}

/**
 * Fallback for a claim with no usable typed contract: all that remains checkable
 * without expected values is whether the subject occurs in the evidence.
 */
const SUBJECT_GROUNDING_TOOLTIP =
  "With no typed contract to check expected values against, the claim’s subject was looked for in the replayed rows and the evidence predicates."

const PREVIEW_ROW_LIMIT = 8

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
  const judgeStatus = getJudgeStatus(run)

  return (
    <ScrollArea className="h-full">
      <main className="mx-auto flex max-w-5xl flex-col px-5 pt-8 pb-16 md:px-10 md:pt-12">
        <header className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <VerificationBadge status={run.verification?.status ?? "PENDING"} />
            {run.judge?.score && (
              <Badge variant="outline" className="uppercase">
                Judge {run.judge.score.replaceAll("_", " ")}
              </Badge>
            )}
            {judgeStatus === "running" && (
              <Badge variant="outline" className="uppercase">
                <CircleNotchIcon className="animate-spin" />
                Judging
              </Badge>
            )}
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
          <p className="text-xs text-muted-foreground">
            {run.claims.length} {run.claims.length === 1 ? "claim" : "claims"}
            {" · "}
            {run.evidence.length} evidence{" "}
            {run.evidence.length === 1 ? "set" : "sets"}
            {" · "}
            {run.tool_calls.length} tool{" "}
            {run.tool_calls.length === 1 ? "call" : "calls"}
          </p>
        </header>

        <JudgeReview run={run} status={judgeStatus} />

        {run.error && (
          <div className="mt-7 border border-destructive/40 p-4 text-sm text-destructive">
            {run.error}
          </div>
        )}

        <Separator className="my-8 md:my-10" />

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

        <SupportingDetails
          goldSql={run.gold_sql}
          goldResult={run.gold_result}
          calls={run.tool_calls}
        />
      </main>
    </ScrollArea>
  )
}

function JudgeReview({ run, status }: { run: RunDetail; status: JudgeStatus }) {
  if (status === "not_started") return null

  if (status === "running") {
    return (
      <section
        aria-live="polite"
        aria-busy="true"
        className="mt-7 flex items-start gap-3 border bg-muted/40 px-4 py-3"
      >
        <CircleNotchIcon className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />
        <div>
          <p className="text-xs font-medium">Independent review in progress</p>
          <p className="mt-1 text-caption leading-4 text-muted-foreground">
            The judge is checking the claims against the database. You can
            review this run while it works.
          </p>
        </div>
      </section>
    )
  }

  const report = run.judge
  if (!report) return null

  const failed = status === "failed"
  const claimsById = new Map(run.claims.map((claim) => [claim.id, claim]))

  return (
    <section id="judge-report" aria-live="polite" className="mt-7 border">
      <div className="flex flex-wrap items-center justify-between gap-3 bg-muted/40 px-4 py-3">
        <div className="flex items-center gap-2">
          {failed ? (
            <WarningCircleIcon className="size-4 text-destructive" />
          ) : (
            <CheckCircleIcon className="size-4 text-primary" weight="fill" />
          )}
          <div>
            <p className="text-xs font-medium">
              {failed ? "Judge review unavailable" : "Judge report ready"}
            </p>
            <p className="text-caption text-muted-foreground">
              Independent semantic review
            </p>
          </div>
        </div>
        <Badge
          variant={failed ? "destructive" : "outline"}
          className="uppercase"
        >
          {report.score.replaceAll("_", " ")}
        </Badge>
      </div>

      <Accordion>
        <AccordionItem value="judge-report" className="border-t">
          <AccordionTrigger className="px-4 py-3 text-primary">
            View judge report
          </AccordionTrigger>
          <AccordionContent className="flex flex-col gap-5 border-t px-4 py-4">
            <div>
              <p className="text-stat font-semibold tracking-[0.14em] text-muted-foreground uppercase">
                Reasoning
              </p>
              <p className="mt-2 text-sm leading-6">{report.reasoning}</p>
            </div>

            {report.claim_assessments.length > 0 && (
              <div>
                <p className="text-stat font-semibold tracking-[0.14em] text-muted-foreground uppercase">
                  Claim assessments
                </p>
                <ul className="mt-2 flex flex-col divide-y border">
                  {report.claim_assessments.map((assessment) => (
                    <li
                      key={assessment.claim_id}
                      className="flex items-start gap-3 px-3 py-3"
                    >
                      {assessment.supported ? (
                        <CheckCircleIcon
                          className="mt-0.5 size-4 shrink-0 text-primary"
                          weight="fill"
                        />
                      ) : (
                        <XCircleIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
                      )}
                      <div className="min-w-0">
                        <p className="text-xs leading-5 font-medium">
                          {claimsById.get(assessment.claim_id)?.claim_text ??
                            assessment.claim_id}
                        </p>
                        {assessment.notes && (
                          <p className="mt-1 text-caption leading-5 text-muted-foreground">
                            {assessment.notes}
                          </p>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </section>
  )
}

function getJudgeStatus(run: RunDetail): JudgeStatus {
  if (run.judge_status) return run.judge_status
  if (run.judge) return run.judge.error ? "failed" : "completed"
  return run.verification ? "running" : "not_started"
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
      <div className="flex gap-4 px-5 py-5 md:px-7 items-baseline">
        <span className="font-mono text-xs text-muted-foreground">
          {String(index + 1).padStart(2, "0")}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm leading-6 font-medium">{claim.claim_text}</p>
            {verification && (
              <VerificationBadge status={verification.status} compact />
            )}
          </div>
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
                  <CheckList verification={verification} />
                  {verification.failure_reason && (
                    <p className="border-l-2 border-destructive pl-3 text-caption break-words text-destructive">
                      Failure: {verification.failure_reason}
                    </p>
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

/** Icon and colour per outcome, so a check that did not pass never reads as passed. */
const CHECK_OUTCOME_STYLES: Record<
  CheckOutcome,
  { icon: typeof CheckCircleIcon; className: string; label: string }
> = {
  CONFIRMED: {
    icon: CheckCircleIcon,
    className: "text-primary",
    label: "confirmed by the evidence",
  },
  REFUTED: {
    icon: XCircleIcon,
    className: "text-destructive",
    label: "contradicted by the evidence",
  },
  INCONCLUSIVE: {
    icon: WarningCircleIcon,
    className: "text-yellow-600",
    label: "could not be established either way",
  },
  NOT_APPLICABLE: {
    icon: CircleIcon,
    className: "text-muted-foreground/50",
    label: "does not apply to this claim",
  },
}

function CheckList({ verification }: { verification: ClaimVerification }) {
  const results = verification.check_results ?? []

  // Runs verified before graded outcomes existed recorded ids alone; their
  // notes live in fragility_notes and cannot be attributed to a check.
  if (results.length === 0) {
    if (verification.checks.length === 0) return null
    return (
      <>
        <ul className="flex flex-col gap-2">
          {verification.checks.map((check, index) => (
            <li
              key={`${check}-${index}`}
              className="flex items-start gap-2 text-muted-foreground"
            >
              <CircleIcon className="mt-0.5 shrink-0 text-muted-foreground/50" />
              <VerificationCheckLabel check={check} />
            </li>
          ))}
        </ul>
        {verification.fragility_notes?.map((note, index) => (
          <p
            key={`${note}-${index}`}
            className="border-l-2 border-yellow-500 pl-3 text-caption break-words text-yellow-600/80"
          >
            Fragility note: {note}
          </p>
        ))}
      </>
    )
  }

  return (
    <ul className="flex flex-col gap-2">
      {results.map((result, index) => {
        const style = CHECK_OUTCOME_STYLES[result.outcome]
        const Icon = style.icon
        return (
          <li key={`${result.check}-${index}`} className="flex flex-col gap-1">
            <div className="flex items-start gap-2 text-muted-foreground">
              <Icon
                className={cn("mt-0.5 shrink-0", style.className)}
                aria-label={style.label}
              />
              <VerificationCheckLabel check={result.check} />
            </div>
            {result.detail && result.outcome !== "CONFIRMED" && (
              <p
                className={cn(
                  "ml-6 text-caption break-words",
                  result.outcome === "REFUTED"
                    ? "text-destructive"
                    : "text-yellow-600/80",
                )}
              >
                {result.detail}
              </p>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function VerificationCheckLabel({ check }: { check: string }) {
  const description = check.endsWith("_subject_grounding")
    ? SUBJECT_GROUNDING_TOOLTIP
    : CHECK_TOOLTIPS[check]
  if (!description) {
    return <span className="font-mono text-xs">{check}</span>
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span className="cursor-help border-b border-dotted border-muted-foreground/60 font-mono text-xs" />
        }
      >
        {check}
      </TooltipTrigger>
      <TooltipContent
        side="top"
        className="max-w-xs text-left normal-case selection:bg-primary/35 selection:text-white dark:selection:text-black"
      >
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

  const previewRows = result.rows.slice(0, PREVIEW_ROW_LIMIT)
  const totalRows = Math.max(result.row_count, result.rows.length)

  return (
    <div className="mt-3 bg-muted/30">
      <div className="overflow-x-auto">
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
            {previewRows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={result.columns.length}
                  className="h-12 text-center text-muted-foreground"
                >
                  No rows returned
                </TableCell>
              </TableRow>
            ) : (
              previewRows.map((row, rowIndex) => (
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
      {totalRows > previewRows.length && (
        <>
          <Separator />
          <p className="px-3 py-2 text-caption text-muted-foreground">
            Showing the first {previewRows.length} of {totalRows} rows.
          </p>
        </>
      )}
    </div>
  )
}

function EvidenceView({ evidence }: { evidence: Evidence }) {
  const previewRows = evidence.rows.slice(0, PREVIEW_ROW_LIMIT)
  const totalRows = Math.max(evidence.row_count, evidence.rows.length)

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
      <div className="overflow-x-auto border">
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
            {previewRows.map((row, rowIndex) => (
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
        {totalRows > previewRows.length && (
          <>
            <Separator />
            <p className="px-3 py-2 text-caption text-muted-foreground">
              Showing the first {previewRows.length} of {totalRows} rows.
            </p>
          </>
        )}
      </div>
      <p className="font-mono text-stat break-all text-muted-foreground">
        Fingerprint: {evidence.result_fingerprint ?? "not available"}
      </p>
    </div>
  )
}

function SupportingDetails({
  goldSql,
  goldResult,
  calls,
}: {
  goldSql?: string | null
  goldResult?: GoldResult | null
  calls: ToolCall[]
}) {
  const plannerCalls = calls.filter((call) => call.agent === "planner").length
  const judgeCalls = calls.filter((call) => call.agent === "judge").length

  return (
    <section className="mt-10" aria-labelledby="details-heading">
      <div className="mb-4">
        <p className="text-caption font-semibold tracking-[0.18em] text-muted-foreground uppercase">
          Supporting details
        </p>
        <h2
          id="details-heading"
          className="mt-1 font-heading text-xl font-medium"
        >
          Reference and diagnostics
        </h2>
      </div>
      <Accordion multiple className="border">
        {goldSql && (
          <AccordionItem value="benchmark" className="px-4">
            <AccordionTrigger>
              <span className="flex flex-col gap-0.5">
                <span>Gold standard SQL and output</span>
                <span className="text-caption font-normal text-muted-foreground">
                  Benchmark reference
                  {goldResult && !goldResult.error
                    ? ` · ${goldResult.row_count} ${goldResult.row_count === 1 ? "row" : "rows"}`
                    : ""}
                </span>
              </span>
            </AccordionTrigger>
            <AccordionContent className="pb-4">
              <pre className="overflow-x-auto border-l-2 border-primary bg-muted/50 px-4 py-3 font-mono text-caption leading-5 text-foreground">
                <code>{format(goldSql, { language: "postgresql" })}</code>
              </pre>
              <GoldResultView result={goldResult} />
            </AccordionContent>
          </AccordionItem>
        )}
        <AccordionItem value="tools" className="px-4">
          <AccordionTrigger>
            <span className="flex flex-col gap-0.5">
              <span>Tool call timeline</span>
              <span className="text-caption font-normal text-muted-foreground">
                {plannerCalls} planner · {judgeCalls} judge
              </span>
            </span>
          </AccordionTrigger>
          <AccordionContent className="pb-4">
            <ToolTimeline calls={calls} />
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </section>
  )
}

function ToolTimeline({ calls }: { calls: ToolCall[] }) {
  return (
    <ol className="flex flex-col divide-y">
      {calls.map((call) => (
        <li
          key={call.id}
          className={cn(
            "grid grid-cols-[20px_minmax(0,1fr)] items-start gap-3 py-3 pl-3 border-b last:border-b-0",
            call.agent === "judge"
              ? "border-l-2 border-l-foreground "
              : call.agent === "planner"
                ? "border-l-2 border-l-primary"
                : "border-l-2 border-l-muted-foreground/30"
          )}
        >
          <CodeIcon className="mt-0.5 text-muted-foreground" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <p className="font-mono text-xs font-medium">
                  {call.tool_name ?? "Unknown tool"}
                </p>
                <AgentBadge agent={call.agent} />
              </div>
              <Badge variant="outline" className="uppercase">
                {call.status}
              </Badge>
            </div>
            <p className="mt-1 truncate font-mono text-stat text-muted-foreground">
              {call.tool_call_id}
              {call.duration_ms !== null ? ` · ${call.duration_ms} ms` : ""}
            </p>
            <ToolPayload label="Parameters" value={call.parameters} />
            {call.output !== undefined && (
              <ToolPayload label="Output" value={call.output} />
            )}
            {call.error && (
              <p className="mt-2 text-xs text-destructive">{call.error}</p>
            )}
          </div>
        </li>
      ))}
      {calls.length === 0 && (
        <li className="px-5 py-8 text-center text-xs text-muted-foreground">
          No tool calls were recorded.
        </li>
      )}
    </ol>
  )
}

function AgentBadge({ agent }: { agent?: string | null }) {
  const label = agent || "system"
  return (
    <Badge
      variant={agent === "planner" ? "secondary" : "outline"}
      className="uppercase"
    >
      {label}
    </Badge>
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

function VerificationBadge({
  status,
  compact = false,
}: {
  status: string
  compact?: boolean
}) {
  return (
    <Badge
      variant={status.toUpperCase() === "FAILED" ? "destructive" : "secondary"}
      className="shrink-0 uppercase"
    >
      {!compact && "Verification: "}
      {status.replaceAll("_", " ")}
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
  const date = new Date(value + "Z")
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)
}
