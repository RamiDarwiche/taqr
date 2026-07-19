export type RunStatus = "queued" | "running" | "completed" | "failed" | string

export interface RunSummary {
  id: string
  question: string
  status: RunStatus
  created_at: string
  updated_at?: string
}

export interface Evidence {
  id: string
  sql: string
  rows: unknown[][]
  row_count: number
  columns: string[]
  result_fingerprint: string | null
}

export interface Claim {
  id: string
  claim_text: string
  claim_type: string
  subject: string | string[] | null
  metric: string | null
  k: number | null
  filters: Record<string, unknown>
  evidence_ids: string[]
}

export interface ClaimVerification {
  claim_id: string
  status: string
  failure_reason?: string | null
  fragility_notes?: string[] | null
  checks: string[]
}

export interface Verification {
  status: string
  claim_results: ClaimVerification[]
}

export interface ToolCall {
  id: string
  tool_name: string | null
  tool_call_id: string
  parameters: Record<string, unknown>
  output?: unknown
  status: string
  duration_ms: number | null
  started_at: string
  error?: string
}

export interface RunDetail extends RunSummary {
  claims: Claim[]
  evidence: Evidence[]
  verification: Verification | null
  tool_calls: ToolCall[]
  error?: string
  session_id?: string
}

export interface TableReference {
  schema: string
  name: string
  description?: string
  row_count?: number
  columns?: Array<{ name: string; type?: string }>
}

export interface TablePage {
  schema: string
  table: string
  columns: Array<{ name: string; type?: string }>
  rows: Array<Record<string, unknown>>
  total?: number
  limit: number
  offset: number
}

export interface CreateRunInput {
  question: string
  session_id?: string
}

const API_ROOT = import.meta.env.TAQR_API_URL || "/api"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `Request failed with ${response.status}`)
  }

  return response.json() as Promise<T>
}

function unwrapArray<T>(
  value: T[] | { items?: T[]; runs?: T[]; tables?: T[] }
): T[] {
  if (Array.isArray(value)) return value
  return value.items ?? value.runs ?? value.tables ?? []
}

function normalizeTable(value: string | TableReference): TableReference {
  if (typeof value !== "string") return value
  const [schema = "public", name = value] = value.includes(".")
    ? value.split(".", 2)
    : ["public", value]
  return { schema, name }
}

export const api = {
  async createRun(input: CreateRunInput) {
    return request<RunDetail>("/runs", {
      method: "POST",
      body: JSON.stringify(input),
    })
  },

  async listRuns() {
    const payload = await request<
      RunSummary[] | { items?: RunSummary[]; runs?: RunSummary[] }
    >("/runs")
    return unwrapArray(payload)
  },

  getRun(id: string) {
    return request<RunDetail>(`/runs/${encodeURIComponent(id)}`)
  },

  async listTables() {
    const payload = await request<
      | Array<string | TableReference>
      | {
          items?: Array<string | TableReference>
          tables?: Array<string | TableReference>
        }
    >("/tables")
    return unwrapArray(payload).map(normalizeTable)
  },

  async getTable(schema: string, table: string, limit = 50, offset = 0) {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    })
    const payload = await request<
      | TablePage
      | {
          rows: Array<Record<string, unknown>>
          columns?: Array<string | { name: string; type?: string }>
          total?: number
        }
    >(
      `/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}?${params}`
    )
    const rows = payload.rows
    const columns =
      payload.columns?.map((column) =>
        typeof column === "string" ? { name: column } : column
      ) ?? Object.keys(rows[0] ?? {}).map((name) => ({ name }))

    return {
      schema,
      table,
      rows,
      columns,
      total: payload.total,
      limit,
      offset,
    } satisfies TablePage
  },
}
