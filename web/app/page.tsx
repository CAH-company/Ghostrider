"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Slot, { type SlotData } from "@/components/Slot";
import JobView, { finishLine } from "@/components/JobView";

const EMPTY: SlotData = { upload: null, start: null, end: null, name: "", hasFile: false };

type HistoryJob = {
  id: string; name_a: string; name_b: string; created: string; state: string; progress: number;
  result: { final_gap: number | null } | null;
};

export default function Home() {
  const [a, setA] = useState<SlotData>(EMPTY);
  const [b, setB] = useState<SlotData>(EMPTY);
  const [active, setActive] = useState<"A" | "B">("A");
  const [jobId, setJobId] = useState<string | null>(null);
  const [wantSubmit, setWantSubmit] = useState(false);
  const [err, setErr] = useState("");
  const [history, setHistory] = useState<HistoryJob[]>([]);
  const submitting = useRef(false);

  const loadHistory = useCallback(async () => {
    try {
      const r = await fetch("/api/jobs", { cache: "no-store" });
      if (r.ok) setHistory((await r.json()).jobs);
    } catch {}
  }, []);

  useEffect(() => {
    loadHistory();
    const t = setInterval(loadHistory, 6000);
    return () => clearInterval(t);
  }, [loadHistory]);

  const marksOk = (s: SlotData) => s.start !== null && (s.end === null || s.end > s.start);
  const uploaded = a.upload?.status === "done" && b.upload?.status === "done";
  const ready = a.hasFile && b.hasFile && marksOk(a) && marksOk(b);

  const submit = useCallback(async () => {
    if (submitting.current) return;
    submitting.current = true;
    setErr("");
    try {
      const r = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          a_key: a.upload!.key, b_key: b.upload!.key,
          a_start: a.start, a_end: a.end, b_start: b.start, b_end: b.end,
          name_a: a.name.trim() || "A", name_b: b.name.trim() || "B",
        }),
      });
      if (!r.ok) throw new Error(String(r.status));
      const { id } = await r.json();
      setJobId(id);
      loadHistory();
    } catch {
      setErr("Nie udało się uruchomić przetwarzania. Spróbuj ponownie za chwilę.");
    } finally {
      submitting.current = false;
      setWantSubmit(false);
    }
  }, [a, b, loadHistory]);

  // "Nałóż" clicked before the uploads finished: fire as soon as they are done
  useEffect(() => {
    if (wantSubmit && uploaded && ready) submit();
  }, [wantSubmit, uploaded, ready, submit]);

  const waiting = wantSubmit && !uploaded;

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-5 px-4 py-5 sm:px-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">TwinRun</h1>
        <button
          onClick={async () => { await fetch("/api/logout", { method: "POST" }); window.location.href = "/login"; }}
          className="text-sm text-slate-500 underline"
        >
          Wyloguj
        </button>
      </header>

      <p className="text-sm text-slate-600">
        Wybierz dwa filmy, w każdym ustaw <b>Start</b> na tym samym momencie (np. przejazd przez bramkę startową). Wysyłanie rusza od razu w tle.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <Slot side="A" active={active === "A"} onActivate={() => setActive("A")} onChange={setA} />
        <Slot side="B" active={active === "B"} onActivate={() => setActive("B")} onChange={setB} />
      </div>
      <p className="hidden text-xs text-slate-500 md:block">Skróty: ← → klatka, Shift + ← → 10 klatek, spacja play/pauza (dla aktywnego panelu).</p>

      <div className="flex flex-col items-start gap-2">
        <button
          disabled={!ready || waiting}
          onClick={() => (uploaded ? submit() : setWantSubmit(true))}
          className="rounded-xl bg-slate-900 px-6 py-3 text-base font-semibold text-white disabled:opacity-40"
        >
          {waiting ? "Czekam na koniec wysyłania…" : "Nałóż przejazdy"}
        </button>
        {!ready && (a.hasFile || b.hasFile) && (
          <p className="text-sm text-slate-500">Wybierz oba filmy i oznacz <b>Start</b> w każdym.</p>
        )}
        {err && <p className="text-sm text-red-600">{err}</p>}
      </div>

      {jobId && <JobView key={jobId} id={jobId} onClose={() => { setJobId(null); loadHistory(); }} />}

      <section className="flex flex-col gap-2">
        <h2 className="font-semibold">Ostatnie przejazdy</h2>
        {history.length === 0 && <p className="text-sm text-slate-500">Jeszcze nic tu nie ma.</p>}
        <ul className="flex flex-col gap-2">
          {history.map((j) => (
            <li key={j.id}>
              <button
                onClick={() => { setJobId(j.id); window.scrollTo({ top: 0, behavior: "smooth" }); }}
                className="flex w-full items-center justify-between rounded-xl bg-white px-4 py-3 text-left shadow-sm hover:bg-slate-50"
              >
                <span>
                  <span className="font-medium">{j.name_a} vs {j.name_b}</span>
                  <span className="ml-2 text-sm text-slate-500">{new Date(j.created).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" })}</span>
                </span>
                <span className="text-sm text-slate-600">
                  {j.state === "done" ? finishLine(j).replace("Na mecie: ", "") : j.state === "error" ? "błąd" : `${Math.round(j.progress * 100)}%`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
