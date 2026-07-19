import { MoonIcon, SunIcon } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { useTheme } from "@/hooks/use-theme"

export function ThemeToggle() {
  const { theme, toggleTheme, ready } = useTheme()

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-8 w-full justify-start gap-2 rounded-none px-0 text-caption font-medium tracking-[0.08em] text-muted-foreground uppercase hover:bg-transparent hover:text-foreground"
      aria-label={
        theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
      }
      disabled={!ready}
      onClick={toggleTheme}
    >
      {theme === "dark" ? (
        <SunIcon className="size-3.5" weight="bold" />
      ) : (
        <MoonIcon className="size-3.5" weight="bold" />
      )}
      {theme === "dark" ? "Light mode" : "Dark mode"}
    </Button>
  )
}
