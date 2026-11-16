"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { startUpload, type UploadState } from "@/lib/uploader";

export type SlotData = { upload: UploadState | null; start: number | null; end: number | null; name: string; hasFile: boolean };

const THEME = {
  A: { ring: "ring-orange-400", bg: "bg-orange-500", soft: "bg-orange-50", text: "text-orange-700", title: "A — przejazd główny" },
  B: { ring: "ring-cyan-400", bg: "bg-cyan-500", soft: "bg-cyan-50", text: "text-cyan-700", title: "B — duch (porównanie)" },
} as const;

const fmt = (t: number) => t.toFixed(2);

export default function Slot({
  side, active, onActivate, onChange,
}: {
  side: "A" | "B";
  active: boolean;
  onActivate: () => void;
  onChange: (d: SlotData) => void;
}) {
  const th = THEME[side];
  const videoRef = useRef<HTMLVideoElement>(null);
  const cancelRef = useRef<(() => void) | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [upload, setUpload] = useState<UploadState | null>(null);
  const [fps, setFps] = useState(30);
  const [time, setTime] = useState(0);
  const [start, setStart] = useState<number | null>(null);
  const [end, setEnd] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [drag, setDrag] = useState(false);

  useEffect(() => {
    onChange({ upload, start, end, name, hasFile: !!file });
  }, [upload, start, end, name, file]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = useCallback((f: File | undefined | null) => {
    if (!f) return;
    cancelRef.current?.();
    setFile(f);
    setStart(null);
    setEnd(null);
    setTime(0);
    setUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return URL.createObjectURL(f);
    });
    setUpload({ status: "uploading", pct: 0 });
    cancelRef.current = startUpload(f, setUpload);
  }, []);

  useEffect(() => () => cancelRef.current?.(), []);

  // estimate real fps from presented frames (needed for frame stepping)
  const detectFps = useCallback(async () => {
    const v = videoRef.current as any;
    if (!v || !v.requestVideoFrameCallback) return;
    const times: number[] = [];
    await new Promise<void>((resolve) => {
      const cb = (_: number, md: { mediaTime: number }) => {
        times.push(md.mediaTime);
        if (times.length < 12) v.requestVideoFrameCallback(cb);
        else resolve();
      };
      v.muted = true;
      v.requestVideoFrameCallback(cb);
      v.play().catch(() => resolve());
      setTimeout(resolve, 1500);
    });
    v.pause();
    const d = times.slice(1).map((t, i) => t - times[i]).filter((x) => x > 0);
    if (d.length) {
      const f = 1 / Math.min(...d);
      const snap = [24, 25, 30, 50, 60, 120, 240].find((s) => Math.abs(s - f) / s < 0.06);
      setFps(snap ?? Math.round(f));
    }
    v.currentTime = 0;
  }, []);

  const seekFrames = useCallback((delta: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.pause();
    const frame = Math.floor(v.currentTime * fps + 1e-6) + delta;
    v.currentTime = Math.max(0, Math.min(v.duration || 0, (frame + 0.5) / fps));
  }, [fps]);

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    v.paused ? v.play() : v.pause();
  };

  useEffect(() => {
    if (!active) return;
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "ArrowLeft") { e.preventDefault(); seekFrames(e.shiftKey ? -10 : -1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); seekFrames(e.shiftKey ? 10 : 1); }
      else if (e.key === " ") { e.preventDefault(); togglePlay(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [active, seekFrames]);

  const frameNo = Math.floor(time * fps + 1e-6);
  const btn = "rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-medium hover:bg-slate-100 active:bg-slate-200";

  return (
    <section
      onClick={onActivate}
      className={`flex min-w-0 flex-col gap-3 rounded-2xl bg-white p-4 shadow-sm ring-2 ${active ? th.ring : "ring-transparent"}`}
    >
      <h2 className={`flex items-center gap-2 font-semibold ${th.text}`}>
        <span className={`inline-block h-3 w-3 rounded-full ${th.bg}`} />
        {th.title}
      </h2>

      {!file ? (
        <label
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
          className={`flex aspect-video cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed p-4 text-center text-sm ${
            drag ? `${th.soft} border-slate-500` : "border-slate-300 text-slate-500"
          }`}
        >
          <span className="text-base font-medium text-slate-700">Przeciągnij film tutaj</span>
          <span>albo kliknij, żeby wybrać plik</span>
          <input type="file" accept="video/*" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
        </label>
      ) : (
        <>
          <video
            ref={videoRef}
            src={url ?? undefined}
            playsInline
            muted
            preload="auto"
            className="w-full rounded-xl bg-black"
            style={{ aspectRatio: "16 / 9" }}
            onLoadedMetadata={(e) => {
              const v = e.currentTarget;
              if (v.videoWidth && v.videoHeight) v.style.aspectRatio = `${v.videoWidth} / ${v.videoHeight}`;
              detectFps();
            }}
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
            onSeeked={(e) => setTime(e.currentTarget.currentTime)}
            onClick={togglePlay}
          />
          <div className="flex items-center justify-between text-sm tabular-nums text-slate-600">
            <span>{fmt(time)} s · klatka {frameNo} · {fps} fps</span>
            <label className={`cursor-pointer underline ${th.text}`}>
              zmień plik
              <input type="file" accept="video/*" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
            </label>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button className={btn} onClick={() => seekFrames(-10)}>−10</button>
            <button className={btn} onClick={() => seekFrames(-1)}>−1</button>
            <button className={btn} onClick={togglePlay}>▶ / ❚❚</button>
            <button className={btn} onClick={() => seekFrames(1)}>+1</button>
            <button className={btn} onClick={() => seekFrames(10)}>+10</button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => setStart(videoRef.current?.currentTime ?? 0)}
              className={`rounded-lg px-3 py-2 text-sm font-semibold text-white ${th.bg}`}
            >
              Start {start !== null ? `· ${fmt(start)} s` : ""}
            </button>
            <button
              onClick={() => setEnd(videoRef.current?.currentTime ?? 0)}
              className="rounded-lg bg-slate-700 px-3 py-2 text-sm font-semibold text-white"
            >
              Koniec {end !== null ? `· ${fmt(end)} s` : ""}
            </button>
          </div>
          {end !== null && start !== null && end <= start && (
            <p className="text-sm text-red-600">Koniec musi być po starcie.</p>
          )}
        </>
      )}

      <input
        value={name}
        maxLength={12}
        onChange={(e) => setName(e.target.value)}
        placeholder="Nazwa zawodnika (max 12 znaków)"
        className="rounded-lg border border-slate-300 px-3 py-2 text-base"
      />

      {upload && (
        <div className="text-sm">
          <div className="h-2 overflow-hidden rounded-full bg-slate-200">
            <div
              className={`h-full transition-all ${upload.status === "error" ? "bg-red-500" : upload.status === "done" ? "bg-emerald-500" : th.bg}`}
              style={{ width: `${upload.status === "done" ? 100 : upload.pct}%` }}
            />
          </div>
          <p className="mt-1 text-slate-600">
            {upload.status === "uploading" && `Wysyłanie… ${upload.pct}%`}
            {upload.status === "paused" && "Brak połączenia — wznowię automatycznie, gdy wróci sieć."}
            {upload.status === "done" && "Wysłane ✓"}
            {upload.status === "error" && `Błąd wysyłania: ${upload.error}`}
          </p>
        </div>
      )}
    </section>
  );
}
