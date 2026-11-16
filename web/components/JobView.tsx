"use client";
import { useEffect, useRef, useState } from "react";

type Job = {
  id: string; name_a: string; name_b: string; created: string;
  state: "queued" | "running" | "done" | "error";
  stage?: string; progress?: number; error?: string;
  result?: { final_gap: number | null; aligned_ratio: number; frames: number } | null;
  video_url?: string; download_url?: string;
};

const STAGES: [string, string][] = [
  ["prepare", "Przygotowanie"],
  ["analyze", "Analiza A i B"],
  ["align", "Wyrównanie"],
  ["render", "Render"],
];

const fmtSec = (s: number) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);

export function finishLine(j: { name_a: string; name_b: string; result?: { final_gap: number | null } | null }) {
  const g = j.result?.final_gap;
  if (g === null || g === undefined) return "Nie udało się wyliczyć różnicy czasu na mecie.";
  const d = Math.abs(g).toFixed(2).replace(".", ",");
  if (Math.abs(g) < 0.005) return `Na mecie: ${j.name_a} i ${j.name_b} — remis.`;
  // gap > 0: A slower than B
  return g > 0
    ? `Na mecie: ${j.name_b} szybciej o ${d} s niż ${j.name_a}`
    : `Na mecie: ${j.name_a} szybciej o ${d} s niż ${j.name_b}`;
}

function humanError(e?: string) {
  return `Nie udało się dopasować kamer — czy oba filmy są nagrane z tego samego miejsca? (${e ?? "nieznany błąd"})`;
}

export default function JobView({ id, onClose }: { id: string; onClose: () => void }) {
  const [job, setJob] = useState<Job | null>(null);
  const [copied, setCopied] = useState(false);
  const t0 = useRef(Date.now());

  useEffect(() => {
    let stop = false;
    t0.current = Date.now();
    const tick = async () => {
      try {
        const r = await fetch(`/api/jobs/${id}`, { cache: "no-store" });
        if (r.ok) {
          const j: Job = await r.json();
          if (!stop) setJob(j);
          if (j.state === "done" || j.state === "error") return;
        }
      } catch {}
      if (!stop) setTimeout(tick, 2000);
    };
    tick();
    return () => { stop = true; };
  }, [id]);

  if (!job) return <p className="text-slate-500">Wczytywanie…</p>;

  const running = job.state === "queued" || job.state === "running";
  const p = job.progress ?? 0;
  const elapsed = (Date.now() - new Date(job.created).getTime()) / 1000;
  const eta = p > 0.08 ? Math.max(0, elapsed / p - elapsed) : null;

  return (
    <section className="flex flex-col gap-3 rounded-2xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">{job.name_a} vs {job.name_b}</h2>
        <button onClick={onClose} className="text-sm text-slate-500 underline">zamknij</button>
      </div>

      {running && (
        <>
          <ol className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {STAGES.map(([k, label]) => {
              const idx = STAGES.findIndex(([s]) => s === job.stage);
              const me = STAGES.findIndex(([s]) => s === k);
              return (
                <li key={k} className={me === idx ? "font-semibold text-slate-900" : me < idx ? "text-emerald-600" : "text-slate-400"}>
                  {me < idx ? "✓ " : ""}{label}
                </li>
              );
            })}
          </ol>
          <div className="h-3 overflow-hidden rounded-full bg-slate-200">
            <div className="h-full bg-slate-900 transition-all" style={{ width: `${Math.round(p * 100)}%` }} />
          </div>
          <p className="text-sm text-slate-600">
            {Math.round(p * 100)}%{eta !== null ? ` · zostało ok. ${fmtSec(eta)}` : " · uruchamianie GPU (pierwszy start potrafi trwać chwilę)"}
          </p>
          <p className="text-xs text-slate-500">Możesz zamknąć kartę — wynik pojawi się potem na liście przejazdów.</p>
        </>
      )}

      {job.state === "error" && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{humanError(job.error)}</p>}

      {job.state === "done" && job.video_url && (
        <>
          <video src={job.video_url} controls playsInline className="w-full rounded-xl bg-black" />
          <p className="text-lg font-semibold">{finishLine(job)}</p>
          {job.result && job.result.aligned_ratio < 0.8 && (
            <p className="text-sm text-amber-700">
              Wyrównanie kamer udało się tylko w {Math.round(job.result.aligned_ratio * 100)}% klatek — wynik może być mniej dokładny.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <a href={job.download_url} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">Pobierz MP4</a>
            <button
              onClick={async () => { await navigator.clipboard.writeText(job.video_url!); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
              className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold"
            >
              {copied ? "Skopiowano ✓" : "Kopiuj link (7 dni)"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
