import HealthPanel from "@/components/HealthPanel";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 px-4 py-10">
      <header>
        <h1 className="font-mono text-2xl font-semibold tracking-tight text-zinc-50">
          MemoryOps
        </h1>
        <p className="mt-1 text-sm text-zinc-400">Self-Learning Production Incident Commander</p>
      </header>
      <HealthPanel />
      <p className="font-mono text-xs text-zinc-600">
        Phase 0 scaffold — dashboard, incident panel, memory browser and learning curves arrive in
        later phases.
      </p>
    </main>
  );
}
