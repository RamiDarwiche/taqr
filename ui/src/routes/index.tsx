import { createFileRoute } from "@tanstack/react-router"

import { TaqrApp } from "@/components/taqr/app-shell"

export const Route = createFileRoute("/")({ component: App })

function App() {
  return <TaqrApp />
}
