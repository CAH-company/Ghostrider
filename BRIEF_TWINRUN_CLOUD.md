# BRIEF: TwinRun Cloud — Vercel + Modal (GPU) + Cloudflare R2

> Brief dla Claude Code. Masz w repo rozpakowany `twinrun.tar.gz`: działający prototyp (FastAPI + Python).
> Zadanie: przenieść go do chmury tak, żeby trenerzy na wyjazdach wgrywali filmy z MacBooków
> przez przeglądarkę, a przetwarzanie szło na GPU w Modal i kosztowało grosze lub nic.
> Środowisko dewelopera: **Windows**. Nic ciężkiego nie uruchamiamy lokalnie: przetwarzanie testujemy od razu na Modal.

---

## 1. Co już jest i działa (NIE przepisywać logiki)

Prototyp zrobił poprawny overlay na prawdziwych nagraniach (iPhone, 4K HEVC HDR, GS, kamera z ręki, pan + zoom,
kilku zawodników na trasie). 574/574 klatek wyrównanych bezpośrednio. Silnik zostaje **bez zmian w algorytmach**.

| Plik | Rola |
|---|---|
| `twinrun/ingest.py` | ffprobe, **tonemapping HDR→SDR** (iPhone HLG: `zscale`+`tonemap=hable`), przycięcie start/koniec, kopia 960 px (analiza) + 1280 px (render). Skalowanie PRZED tonemapem (szybciej). |
| `twinrun/analyze.py` | Strumieniowo: YOLO11n-seg (ludzie + maski przycięte do boxa), SIFT na tle z zamaskowanymi ludźmi, homografia klatka→klatka (MAGSAC), łańcuch do klatki 0. ~1,4 GB RAM. |
| `twinrun/track.py` | Wybór zawodnika, którego śledzi kamera: DP po wszystkich detekcjach (bliskość środka kadru, ruch znormalizowany do wysokości sylwetki, przerwy do 12 klatek). Interpolacja boxów. |
| `twinrun/overlay.py` | Homografia B→A per klatka (bezpośrednio + kontrola spójności z ruchem kamer + wygładzanie narożników), różnica czasu przez **DTW** trajektorii stóp, render (maska B, niebieska obwódka, A zawsze na wierzchu, HUD, wykres gap). |
| `twinrun/pipeline.py` | Orkiestracja + `status.json` z postępem. |
| `app.py`, `static/index.html` | Stary serwer/UI — **do zastąpienia** (na Macu był ucięty i niestabilny). |

Znane ograniczenia (zostają, nie naprawiamy teraz): gap mniej pewny tam, gdzie trasa biegnie prosto na kamerę;
start trzeba oznaczyć ręcznie.

---

## 2. Docelowa architektura

```
MacBook (Safari/Chrome)
   │ 1. wybiera 2 pliki, oznacza START/KONIEC lokalnie (podgląd z pliku, zero uploadu)
   │ 2. multipart upload prosto do R2 (presigned URLs, wznawianie)
   ▼
Vercel (Next.js, App Router, TypeScript)
   - UI + API routes: presign, create-job, job-status, job-list
   - prosta ochrona hasłem klubowym (middleware + cookie)
   │ 3. POST create-job → wywołuje Modal web endpoint (Bearer token)
   ▼
Modal (Python, GPU L4)
   - pobiera oba pliki z R2 → istniejący pipeline (ingest/analyze/overlay) → overlay.mp4 do R2
   - status.json do R2 na bieżąco (stage, progress, error, wynik)
   ▼
Cloudflare R2 (S3 API, bez opłat za transfer)
   uploads/{uploadId}/source.mov
   jobs/{jobId}/status.json | overlay.mp4 | meta.json
```

Kluczowa decyzja: **oznaczanie startu odbywa się lokalnie w przeglądarce na oryginalnym pliku** (`URL.createObjectURL`).
Safari i Chrome na macOS odtwarzają HEVC z iPhone'a natywnie, więc nie trzeba generować podglądu po stronie serwera.
Upload leci w tle, a użytkownik w tym czasie ustawia znaczniki.

---

## 3. Modal — przetwarzanie

`modal_app/app.py` (+ skopiowany pakiet `twinrun/`):

- **Image**: `modal.Image.debian_slim(python_version="3.11")`
  - `apt_install("ffmpeg", "libgl1", "libglib2.0-0")` — **zweryfikuj**, że ffmpeg ma filtr `zscale`
    (`ffmpeg -filters | grep zscale`). Bez niego HDR z iPhone'a wychodzi wyprany i dopasowanie się psuje.
    Jeśli Debianowy nie ma — użyj statycznego buildu ffmpeg (np. johnvansickle) w obrazie.
  - `pip_install("torch", "torchvision", "ultralytics==8.3.75", "opencv-python-headless==4.11.0.86", "numpy<2.3", "boto3")`
  - wypiecz wagi w obrazie: `run_commands("python -c \"from ultralytics import YOLO; YOLO('yolo11n-seg.pt')\"")`
  - `add_local_python_source("twinrun")`
- **Funkcja `process_job(job_id, a_key, b_key, a_start, a_end, b_start, b_end, name_a, name_b)`**
  - `gpu="L4"`, `cpu=8`, `memory=8192`, `timeout=1200`
  - pobiera z R2 do `/tmp`, wywołuje `pipeline.process(...)`, wynik `overlay.mp4` + `status.json` wysyła do R2
  - `pipeline._write` przerobić tak, by **status.json szedł do R2** (throttling: max 1 zapis / 2 s)
- **Web endpoint** `submit` (`@modal.fastapi_endpoint(method="POST")`): sprawdza `Authorization: Bearer $TWINRUN_TOKEN`,
  robi `process_job.spawn(...)`, zwraca `{job_id}` natychmiast.
- **Zmiany w silniku (minimalne):**
  1. `analyze.get_model()` → `.to("cuda")` gdy dostępne; `predict(..., device=0, half=True)`.
  2. **Analiza A i B równolegle** (dwa wątki albo `.map` na dwóch kontenerach) — prawie 2× szybciej.
  3. Różne fps w filmach A i B: w `ingest.normalize` wymuś wspólny fps (`-r` = fps filmu A), żeby klatki parowały się 1:1.
  4. `GL_RENDER_WIDTH` domyślnie **1920** (na GPU nas stać).
- Sekrety Modal: `twinrun-r2` (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET), `twinrun-token` (TWINRUN_TOKEN).
- Zalogować czasy etapów + `modal` koszt per job do `meta.json` (do raportu wydajności).

## 4. Cloudflare R2

- Bucket `twinrun`.
- **CORS**: `PUT` i `GET` z domeny Vercela (+ `http://localhost:3000`), `ExposeHeaders: ["ETag"]` (potrzebne do multipart).
- Lifecycle: `uploads/` usuwaj po 14 dniach; `jobs/` zostaw (lub 90 dni).
- Wynik serwuj przez presigned GET (ważny 7 dni) — link do udostępnienia zawodnikowi.

## 5. Vercel — Next.js

API routes (`/app/api/...`, runtime Node):
- `POST /api/upload/init` → `CreateMultipartUpload`, zwraca `uploadId`, `key`
- `POST /api/upload/part-urls` → presigned URL-e dla numerów części (części po **10 MB**)
- `POST /api/upload/complete` → `CompleteMultipartUpload`
- `POST /api/jobs` → zapisuje `jobs/{id}/meta.json`, woła Modal `submit`
- `GET /api/jobs/[id]` → czyta `status.json` z R2, przy `done` dokłada presigned URL do `overlay.mp4`
- `GET /api/jobs` → lista ostatnich jobów (prefix `jobs/`, sort po dacie)

Ochrona: zmienna `CLUB_PASSWORD`; middleware przepuszcza `/login` i ustawia httpOnly cookie po poprawnym haśle.

Env Vercel: `R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, MODAL_SUBMIT_URL, TWINRUN_TOKEN, CLUB_PASSWORD`.

## 6. UI — wymagania (tu był główny problem na Macu)

Priorytet: **MacBook, Safari i Chrome, ekran 13"**. Musi też dać się używać na iPhonie.
- Nic nie może być ucięte: layout `max-width` + responsywny grid, wideo zawsze `width:100%; aspect-ratio` z pliku.
  Przetestuj w 1280×800 i 1440×900 oraz 390×844.
- **Krok 1 — dwa panele A (główny, orange) i B (duch, cyan):** drag & drop / wybór pliku → od razu lokalny podgląd.
- **Krok 2 — znaczniki:** przyciski −10 / −1 / ▶ / +1 / +10 klatek, **Start**, **Koniec**, skróty ← → (Shift = 10),
  wyświetlany czas `0.00 s` i numer klatki, nazwa zawodnika (max 12 znaków). Sugestia w UI: „ten sam moment w obu filmach,
  np. przejazd przez bramkę startową”.
- Upload rusza **od razu po wybraniu pliku** (w tle), z paskiem %, retry każdej części 3× z backoffem i **wznawianiem**
  po zerwaniu sieci (stan części w `localStorage`). Jak upload się nie skończył — przycisk „Nałóż” czeka.
- **Krok 3:** „Nałóż przejazdy” → pasek postępu z etapem (Przygotowanie / Analiza A / Analiza B / Wyrównanie / Render)
  i szacowanym czasem. Zamknięcie karty nie zabija zadania — wynik jest na liście.
- **Wynik:** odtwarzacz, „Pobierz MP4”, „Kopiuj link” (presigned, 7 dni), tekst „Na mecie: Ola szybciej o 0,36 s niż Zosia”.
- **Historia:** lista ostatnich overlayów (nazwy, data, miniatura opcjonalnie), klik = wynik.
- Błędy pokazujemy po ludzku (np. „Nie udało się dopasować kamer — czy oba filmy są nagrane z tego samego miejsca?”).
- Styl: czysty, jasny, bez frameworków UI cięższych niż Tailwind. Język: polski.

## 7. Kolejność pracy (milestones)

1. **Modal headless**: obraz + `process_job`; test `modal run` na dwóch plikach wrzuconych ręcznie do R2
   (pliki testowe: `IMG_1620.mov`, `IMG_1621.mov`). Kryterium: overlay wygląda jak demo z prototypu
   (duch na śniegu w tym samym miejscu, obwódka, HUD), raport czasów etapów i kosztu.
2. Web endpoint `submit` + status w R2.
3. Next.js: upload multipart + znaczniki + job + polling + wynik.
4. Hasło, historia, linki do udostępnienia, deploy na Vercel.
5. Test z MacBooka przez LTE/hotspot: 2 pliki 1080p60 i 2 pliki 4K.

## 8. Kryteria akceptacji

- [ ] Para 20-s przejazdów (1080p60) → wynik w **≤ 3 min** od kliknięcia (z zimnym startem). Zmierzyć i wpisać do README.
- [ ] Koszt jednego overlayu w Modal zmierzony i wpisany do README (oczekiwanie: kilka centów).
- [ ] HDR z iPhone'a ma normalne kolory (śnieg z fakturą, nie wyprany).
- [ ] Upload 2 × 300 MB przeżywa wyłączenie Wi-Fi na 30 s w połowie.
- [ ] UI bez ucinania w Safari i Chrome na MacBooku 13" i na iPhonie.
- [ ] Bez hasła nie da się nic wgrać ani obejrzeć.
- [ ] Brak sekretów w repo (`.env.local`, sekrety Modal).

## 9. Poza zakresem (na później)

Konta per trener, automatyczna synchronizacja startu, splity na bramkach, wiele duchów naraz, aplikacja mobilna.

## 10. Uwagi

- Licencja: Ultralytics/YOLO jest **AGPL-3.0** — do użytku wewnątrz klubu OK. Przy sprzedaży innym klubom
  trzeba podmienić model (np. Mask R-CNN z torchvision) albo kupić licencję.
- Modal Starter: $0 abonamentu, $30 kredytów/mies., rozliczanie co do sekundy (L4 ≈ $0,80/h).
  Ustaw limit wydatków w panelu Modal.
- Nazwa „GhostLine” to marka konkurenta — nie używać jej w UI ani w domenie.
