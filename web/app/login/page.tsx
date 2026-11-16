"use client";
import { useState } from "react";

export default function Login() {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    if (r.ok) window.location.href = "/";
    else {
      setErr("Złe hasło");
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-4 px-5">
      <h1 className="text-2xl font-bold">TwinRun</h1>
      <p className="text-sm text-slate-600">Wpisz hasło klubowe, żeby wgrać i obejrzeć przejazdy.</p>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <input
          type="password"
          autoFocus
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="Hasło"
          className="rounded-lg border border-slate-300 px-3 py-2 text-base"
        />
        {err && <p className="text-sm text-red-600">{err}</p>}
        <button disabled={busy || !pw} className="rounded-lg bg-slate-900 px-4 py-2 font-medium text-white disabled:opacity-50">
          Wejdź
        </button>
      </form>
    </main>
  );
}
