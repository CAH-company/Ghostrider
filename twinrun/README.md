# TwinRun – overlay dwóch przejazdów (demo PAH)

Dwa przejazdy narciarskie nagrane z tego samego miejsca → jedno wideo z obydwoma zawodnikami
na tym samym śniegu + bieżąca różnica czasu i wykres strat/zysków.

## Jak to działa

1. **Konwersja** – iPhone nagrywa HDR (HLG); robimy tonemapping do SDR, kopia 960 px do analizy i 1280 px do renderu.
2. **Analiza każdego filmu** – YOLO11-seg wykrywa ludzi (z maską sylwetki); SIFT na tle (ludzie zamaskowani) +
   homografia RANSAC między kolejnymi klatkami = ruch kamery (pan/zoom).
3. **Tracking zawodnika** – programowanie dynamiczne po wszystkich detekcjach: zawodnik, którego śledzi kamera
   (blisko środka kadru, ciągły ruch w ustabilizowanych współrzędnych). Działa przy kilku osobach na trasie.
4. **Wyrównanie A↔B** – homografia klatka B → klatka A (bezpośrednie dopasowanie, z kontrolą spójności
   względem ruchu kamery), wygładzona w czasie.
5. **Różnica czasu** – pozycje stóp obu zawodników we wspólnym układzie, dopasowanie trajektorii DTW
   → „gdzie był B, gdy A był tutaj” → różnica w sekundach.
6. **Render** – sylwetka B (maska YOLO) wklejona w obraz A z niebieską obwódką, A zawsze na wierzchu, HUD + wykres.

## Uruchomienie na VPS

```bash
# z komputera: wyślij paczkę i zaloguj się na VPS (Ubuntu z Dockerem)
scp twinrun.tar.gz user@IP_VPS:~ && ssh user@IP_VPS
# już na VPS:
tar xzf twinrun.tar.gz && cd twinrun
echo "GL_PASSWORD=wymysl-haslo" > .env          # opcjonalne hasło na demo (login dowolny)
docker compose up -d --build                    # pierwszy build ~5-8 min
curl localhost:8000/healthz
```

Strona: `http://IP_VPS:8000`. Z domeną i HTTPS najprościej Caddy:

```
twinrun.twojadomena.pl {
    request_body { max_size 3GB }
    reverse_proxy localhost:8000
}
```

## Wydajność (CPU, bez GPU)

Dwa 20-sekundowe przejazdy 4K: ~8–12 min na 2 rdzeniach, ~4–6 min na 4–8 rdzeniach.
Największe koszty: dekodowanie 4K HEVC, YOLO na każdej klatce, SIFT. Z GPU (CUDA) YOLO spada do sekund.

## Ograniczenia prototypu

- Brak kont, płatności, czyszczenia starych plików (`data/` rośnie).
- Różnica czasu jest mniej pewna tam, gdzie trasa biegnie prosto na kamerę (z jednej kamery mało widać głębię).
- Start trzeba oznaczyć ręcznie w obu filmach (ten sam moment, np. bramka startowa); bez tego wynik
  jest liczony od początku plików.
- Jedno zadanie naraz (kolejka).

## Wdrożenie

Web (`web/`) działa na Vercelu (katalog główny: `web`), przetwarzanie na Modal (`twinrun/modal_app`), pliki w Cloudflare R2. Sekretów nie trzymamy w repo.
