import { AnimatePresence, motion } from "motion/react"
import {
  CheckCircleIcon,
  CircleNotchIcon,
  SparkleIcon,
} from "@phosphor-icons/react"
import { ThinkingOrb } from "thinking-orbs"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { ThinkingStep } from "@/lib/api"
import { cn } from "@/lib/utils"
import { useTheme } from "@/hooks/use-theme"

interface ThinkingTraceProps {
  question: string
  steps: ThinkingStep[]
  statusTitle?: string
  statusDetail?: string
  isActive: boolean
}

export function ThinkingTrace({
  question,
  steps,
  statusTitle = "Thinking",
  statusDetail,
  isActive,
}: ThinkingTraceProps) {
  const { theme } = useTheme()
  return (
    <ScrollArea className="h-full">
      <main className="mx-auto flex max-w-5xl flex-col px-5 pt-8 pb-16 md:px-10 md:pt-12">
        <header className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="uppercase">
              Live run
            </Badge>
            <span className="font-mono text-caption text-muted-foreground">
              Streaming planner trace
            </span>
          </div>
          <h1 className="max-w-4xl font-heading text-xl font-medium tracking-tight md:text-3xl md:leading-[1.05]">
            {question}
          </h1>
        </header>

        <section
          aria-live="polite"
          aria-busy={isActive}
          className="mt-8 border bg-muted/40 md:mt-10"
        >
          <div className="flex items-start gap-3 border-b px-4 py-3 md:px-5">
            <span className="mt-0.5 flex size-7 items-center justify-center bg-background text-primary">
              <ThinkingOrb
                size={64}
                theme={theme === "dark" ? "dark" : "light"}
                state="composing"
                style={{ backgroundColor: "transparent" }}
              />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs font-medium tracking-tight">
                  {statusTitle}
                </p>
                {isActive && <ThinkingDots />}
              </div>
              <p className="mt-1 text-caption leading-4 text-muted-foreground">
                {statusDetail ??
                  "Following the model as it explores schemas, drafts SQL, and forms claims."}
              </p>
            </div>
          </div>

          <ol className="flex flex-col px-4 py-2 md:px-5">
            <AnimatePresence initial={false}>
              {steps.map((step) => (
                <motion.li
                  key={step.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
                  className="grid grid-cols-[1rem_minmax(0,1fr)] gap-2.5 py-2.5"
                >
                  <StepGlyph status={step.status} />
                  <div className="min-w-0">
                    <p className="text-xs leading-4 font-medium">
                      {step.title}
                    </p>
                    {step.detail && (
                      <p
                        className={cn(
                          "mt-1 font-mono text-xs leading-4 text-muted-foreground",
                          step.phase === "run_query" ||
                            step.phase === "generate_query" ||
                            step.phase === "check_query"
                            ? "break-words whitespace-pre-wrap"
                            : "truncate"
                        )}
                        title={step.detail}
                      >
                        {step.detail}
                      </p>
                    )}
                  </div>
                </motion.li>
              ))}
            </AnimatePresence>

            {isActive && (
              <motion.li
                key="working"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="grid grid-cols-[1rem_minmax(0,1fr)] gap-2.5 py-2.5"
              >
                <span className="mt-0.5 flex size-4 items-center justify-center">
                  <SparkleIcon className="size-3.5 animate-pulse text-primary" />
                </span>
                <div>
                  <p className="text-xs leading-4 font-medium text-muted-foreground">
                    Still working
                  </p>
                  <p className="mt-1 text-stat text-muted-foreground">
                    Waiting for the next planner step…
                  </p>
                </div>
              </motion.li>
            )}

            {!isActive && steps.length === 0 && (
              <li className="py-8 text-center text-xs text-muted-foreground">
                No planner steps were streamed for this run.
              </li>
            )}
          </ol>
        </section>
      </main>
    </ScrollArea>
  )
}

function StepGlyph({ status }: { status: ThinkingStep["status"] }) {
  if (status === "started") {
    return (
      <span className="mt-0.5 flex size-4 items-center justify-center text-primary">
        <CircleNotchIcon className="size-3.5 animate-spin" />
      </span>
    )
  }
  return (
    <span className="mt-0.5 flex size-4 items-center justify-center text-primary">
      <CheckCircleIcon className="size-3.5" weight="fill" />
    </span>
  )
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-0.5" aria-hidden="true">
      {[0, 1, 2].map((index) => (
        <motion.span
          key={index}
          className="size-0.5 rounded-full bg-primary"
          animate={{ opacity: [0.25, 1, 0.25], y: [0, -1.5, 0] }}
          transition={{
            duration: 1,
            repeat: Infinity,
            delay: index * 0.15,
            ease: "easeInOut",
          }}
        />
      ))}
    </span>
  )
}
