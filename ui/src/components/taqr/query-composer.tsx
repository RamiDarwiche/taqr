import { useState } from "react"
import type { FormEvent, KeyboardEvent } from "react"
import { ArrowUpIcon, SpinnerGapIcon } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

interface QueryComposerProps {
  onSubmit: (question: string) => Promise<void>
  isSubmitting: boolean
}

export function QueryComposer({ onSubmit, isSubmitting }: QueryComposerProps) {
  const [question, setQuestion] = useState("")

  async function submit(event?: FormEvent) {
    event?.preventDefault()
    const value = question.trim()
    if (!value || isSubmitting) return
    await onSubmit(value)
    setQuestion("")
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  return (
    <div className="border-t bg-background/95 px-4 py-3 backdrop-blur md:px-8 md:py-4">
      <form
        onSubmit={(event) => void submit(event)}
        className="mx-auto flex max-w-4xl items-center gap-2 border bg-background p-2 focus-within:ring-2 focus-within:ring-ring/30"
      >
        <Textarea
          aria-label="Ask a research question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question about your data…"
          className="-mb-1 max-h-36 min-h-10 flex-1 resize-none border-0 bg-transparent px-2 py-2 shadow-none focus-visible:ring-0"
          rows={1}
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
        TAQR can make mistakes. Verify claims against the linked evidence.
      </p>
    </div>
  )
}
