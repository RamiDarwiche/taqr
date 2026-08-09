import { useEffect, useState } from "react"
import type { FormEvent, KeyboardEvent } from "react"
import {
  ArrowUpIcon,
  ShieldWarningIcon,
  ShuffleIcon,
  SpinnerGapIcon,
} from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import type { AdversarialQuestion, BenchmarkQuestion } from "@/lib/api"

type SuggestionKind = "benchmark" | "adversarial"

export interface SubmitQuestionInput {
  question: string
  question_id?: number
}

interface QueryComposerProps {
  onSubmit: (input: SubmitQuestionInput) => Promise<void>
  isSubmitting: boolean
}

export function QueryComposer({ onSubmit, isSubmitting }: QueryComposerProps) {
  const [question, setQuestion] = useState("")
  const [activeQuestionId, setActiveQuestionId] = useState<number>()
  const [suggested, setSuggested] = useState<BenchmarkQuestion>()
  const [suggestionKind, setSuggestionKind] =
    useState<SuggestionKind>("benchmark")
  const [suggestionLoading, setSuggestionLoading] = useState(false)
  const [suggestionError, setSuggestionError] = useState<string>()

  useEffect(() => {
    void loadSuggestion()
  }, [])

  async function loadSuggestion(kind: SuggestionKind = "benchmark") {
    setSuggestionLoading(true)
    setSuggestionKind(kind)
    setSuggestionError(undefined)
    try {
      const next =
        kind === "adversarial"
          ? await api.getRandomAdversarialQuestion()
          : await api.getRandomBenchmarkQuestion()
      setSuggested(next)
      setQuestion(next.question)
      setActiveQuestionId(next.question_id)
    } catch (error) {
      setSuggestionError(
        error instanceof Error
          ? error.message
          : `Could not load ${
              kind === "adversarial" ? "an adversarial" : "a benchmark"
            } question.`
      )
    } finally {
      setSuggestionLoading(false)
    }
  }

  async function submit(event?: FormEvent) {
    event?.preventDefault()
    const value = question.trim()
    if (!value || isSubmitting) return
    const questionId =
      suggested && value === suggested.question
        ? suggested.question_id
        : activeQuestionId
    await onSubmit({
      question: value,
      question_id: questionId,
    })
    await loadSuggestion(suggestionKind)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  function handleQuestionChange(value: string) {
    setQuestion(value)
    if (suggested && value !== suggested.question) {
      setActiveQuestionId(undefined)
    } else if (suggested && value === suggested.question) {
      setActiveQuestionId(suggested.question_id)
    }
  }

  return (
    <div className="border-t bg-background/95 px-4 py-3 backdrop-blur md:px-8 md:py-4">
      <div className="mx-auto mb-2 flex max-w-4xl flex-nowrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-caption font-semibold tracking-[0.14em] text-muted-foreground uppercase">
            {suggestionKind === "adversarial"
              ? "Adversarial verifier challenge"
              : "BIRD MiniDev question"}
          </p>
          {suggested ? (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              #{suggested.question_id} · {suggested.db_id} ·{" "}
              {suggested.difficulty}
              {suggestionKind === "adversarial" &&
              "adversarial_tags" in suggested
                ? ` · ${(suggested as AdversarialQuestion).adversarial_tags
                    .slice(0, 2)
                    .join(", ")}`
                : null}
            </p>
          ) : suggestionError ? (
            <p className="mt-0.5 text-xs text-destructive">{suggestionError}</p>
          ) : (
            <p className="mt-0.5 text-xs text-muted-foreground">
              Loading a random benchmark question…
            </p>
          )}
          {suggestionKind === "adversarial" &&
          suggested &&
          "attack" in suggested &&
          (suggested as AdversarialQuestion).attack ? (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
              Trap: {(suggested as AdversarialQuestion).attack}
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={suggestionLoading || isSubmitting}
            onClick={() => void loadSuggestion("adversarial")}
          >
            {suggestionLoading && suggestionKind === "adversarial" ? (
              <SpinnerGapIcon
                data-icon="inline-start"
                className="animate-spin"
              />
            ) : (
              <ShieldWarningIcon data-icon="inline-start" />
            )}
            Adversarial question
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={suggestionLoading || isSubmitting}
            onClick={() => void loadSuggestion("benchmark")}
          >
            {suggestionLoading && suggestionKind === "benchmark" ? (
              <SpinnerGapIcon
                data-icon="inline-start"
                className="animate-spin"
              />
            ) : (
              <ShuffleIcon data-icon="inline-start" />
            )}
            Another question
          </Button>
        </div>
      </div>
      <form
        onSubmit={(event) => void submit(event)}
        className="mx-auto flex max-w-4xl items-center gap-2 border bg-background p-2 focus-within:ring-2 focus-within:ring-ring/30"
      >
        <Textarea
          aria-label="Ask a research question"
          value={question}
          onChange={(event) => handleQuestionChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a BIRD MiniDev question…"
          className="-mb-1 max-h-36 min-h-10 flex-1 resize-none border-0 bg-transparent px-2 py-2 shadow-none focus-visible:ring-0"
          rows={2}
        />
        <div className="flex items-center gap-2">
          <span className="hidden text-caption text-muted-foreground sm:inline">
            Enter to send
          </span>
          <Button
            type="submit"
            size="icon"
            disabled={!question.trim() || isSubmitting}
            aria-label="Submit question"
          >
            {isSubmitting ? (
              <SpinnerGapIcon className="animate-spin" />
            ) : (
              <ArrowUpIcon weight="bold" />
            )}
          </Button>
        </div>
      </form>
      <p className="mx-auto mt-2 max-w-4xl text-center text-caption text-muted-foreground">
        TAQR can make mistakes. Compare agent SQL against the MiniDev gold
        standard.
      </p>
    </div>
  )
}
