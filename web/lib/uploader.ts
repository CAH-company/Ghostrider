// Resumable multipart upload straight to R2 via presigned URLs.
// State of finished parts is kept in localStorage so a reload / lost connection resumes where it stopped.

export const PART_SIZE = 10 * 1024 * 1024;
const CONCURRENCY = 3;
const RETRIES = 3;

export type UploadState = {
  status: "uploading" | "paused" | "done" | "error";
  pct: number;
  key?: string;
  error?: string;
};

type Saved = { key: string; uploadId: string; parts: Record<number, string> };

const fp = (f: File) => `tr_up:${f.name}:${f.size}:${f.lastModified}`;
const load = (f: File): Saved | null => {
  try {
    return JSON.parse(localStorage.getItem(fp(f)) || "null");
  } catch {
    return null;
  }
};
const save = (f: File, s: Saved | null) => {
  try {
    if (s) localStorage.setItem(fp(f), JSON.stringify(s));
    else localStorage.removeItem(fp(f));
  } catch {}
};

async function api<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function putPart(url: string, blob: Blob, onProgress: (loaded: number) => void, signal: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const etag = xhr.getResponseHeader("ETag");
        etag ? resolve(etag) : reject(new Error("no ETag (CORS ExposeHeaders?)"));
      } else reject(Object.assign(new Error(`part ${xhr.status}`), { status: xhr.status }));
    };
    xhr.onerror = () => reject(new Error("network"));
    xhr.ontimeout = () => reject(new Error("timeout"));
    signal.addEventListener("abort", () => xhr.abort());
    xhr.send(blob);
  });
}

async function waitOnline() {
  if (navigator.onLine) {
    await sleep(3000);
    return;
  }
  await new Promise<void>((res) => window.addEventListener("online", () => res(), { once: true }));
}

/** Uploads `file`; reports state through `onState`. Returns a cancel function. */
export function startUpload(file: File, onState: (s: UploadState) => void): () => void {
  const ctrl = new AbortController();
  const total = Math.max(1, Math.ceil(file.size / PART_SIZE));

  (async () => {
    let saved = load(file);
    let restarted = false;

    const init = async () => {
      const r = await api<{ key: string; uploadId: string }>("/api/upload/init", {
        filename: file.name,
        contentType: file.type || "video/quicktime",
      });
      saved = { key: r.key, uploadId: r.uploadId, parts: {} };
      save(file, saved);
    };

    try {
      if (!saved) await init();
      for (;;) {
        if (ctrl.signal.aborted) return;
        const s = saved!;
        const inflight: Record<number, number> = {};
        const report = () => {
          let bytes = 0;
          for (const n of Object.keys(s.parts)) bytes += Math.min(PART_SIZE, file.size - (+n - 1) * PART_SIZE);
          for (const v of Object.values(inflight)) bytes += v;
          onState({ status: "uploading", pct: Math.min(99, Math.round((bytes / file.size) * 100)), key: s.key });
        };
        report();

        const todo: number[] = [];
        for (let n = 1; n <= total; n++) if (!s.parts[n]) todo.push(n);

        try {
          // presigned URLs in batches
          const urls: Record<number, string> = {};
          for (let i = 0; i < todo.length; i += 100) {
            const batch = todo.slice(i, i + 100);
            const r = await api<{ urls: Record<number, string> }>("/api/upload/part-urls", {
              key: s.key,
              uploadId: s.uploadId,
              parts: batch,
            });
            Object.assign(urls, r.urls);
          }

          const queue = [...todo];
          const worker = async () => {
            while (queue.length && !ctrl.signal.aborted) {
              const n = queue.shift()!;
              const blob = file.slice((n - 1) * PART_SIZE, Math.min(file.size, n * PART_SIZE));
              let lastErr: unknown;
              for (let attempt = 0; attempt < RETRIES; attempt++) {
                try {
                  const etag = await putPart(urls[n], blob, (l) => {
                    inflight[n] = l;
                    report();
                  }, ctrl.signal);
                  delete inflight[n];
                  s.parts[n] = etag;
                  save(file, s);
                  report();
                  lastErr = null;
                  break;
                } catch (e) {
                  lastErr = e;
                  delete inflight[n];
                  if (ctrl.signal.aborted) return;
                  if ((e as any)?.status === 403 || (e as any)?.status === 404) throw e; // expired URL / gone upload
                  await sleep(1000 * 3 ** attempt); // 1s, 3s, 9s
                }
              }
              if (lastErr) throw lastErr;
            }
          };
          await Promise.all(Array.from({ length: CONCURRENCY }, worker));
          if (ctrl.signal.aborted) return;

          await api("/api/upload/complete", {
            key: s.key,
            uploadId: s.uploadId,
            parts: Object.entries(s.parts).map(([n, ETag]) => ({ PartNumber: +n, ETag })),
          });
          save(file, null);
          onState({ status: "done", pct: 100, key: s.key });
          return;
        } catch (e: any) {
          if (ctrl.signal.aborted) return;
          if ((e?.status === 403 || e?.status === 404) && !restarted) {
            // upload expired on the server: start over once
            restarted = true;
            save(file, null);
            await init();
            continue;
          }
          // network trouble: pause, wait for connection, resume with the parts we already have
          onState({ status: "paused", pct: 0, key: s.key, error: String(e?.message || e) });
          await waitOnline();
        }
      }
    } catch (e: any) {
      onState({ status: "error", pct: 0, error: String(e?.message || e) });
    }
  })();

  return () => ctrl.abort();
}
