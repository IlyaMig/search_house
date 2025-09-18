#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
house_watch.py
Controlla due ricerche (Idealista e Immobiliare.it) e invia una notifica Telegram
per ogni nuovo annuncio trovato.
Uso tipico:
  python house_watch.py --interval 600
  # oppure una singola esecuzione, per provarlo:
  python house_watch.py --once
Variabili d'ambiente richieste per Telegram:
  TELEGRAM_BOT_TOKEN  -> token del bot (da @BotFather)
  TELEGRAM_CHAT_ID    -> chat id dove inviare i messaggi
Suggerimento: la prima esecuzione salva lo stato senza notificare (per evitare spam).
Per ricevere notifiche anche al primo run, passare --notify-first-run
"""
import os
import re
import time
import json
import html
import hashlib
import argparse
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests

SOURCES = [
    {
        "name": "Idealista",
        "url": "https://www.idealista.it/point/affitto-case/44.48011/11.37555/12/con-prezzo_750,pubblicato_ultime-24-ore/lista-mappa",
        # pattern generico per link annuncio Idealista
        "pattern": r"https?://www\.idealista\.it/[^\s\"'>]+/(?:immobile|annuncio)[^\s\"'>]*"
    },
    {
        "name": "Immobiliare.it",
        "url": "https://www.immobiliare.it/search-list/?idContratto=2&idCategoria=1&prezzoMassimo=750&idTipologia[0]=4&__lang=it&mapCenter=44.500995%2C11.345773&zoom=13#geohash-srbj1s0",
        # pattern generico per link annuncio Immobiliare.it
        "pattern": r"https?://www\.immobiliare\.it/[^\s\"'>]*/annunci[^\s\"'>]*"
    }
]

STATE_FILE = os.environ.get("HOUSE_WATCH_STATE", "seen_listings.json")
USER_AGENT = os.environ.get("HOUSE_WATCH_UA", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0 Safari/537.36 HouseWatch/1.0")

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"seen": []}
    except Exception as e:
        print(f"[WARN] Impossibile leggere {STATE_FILE}: {e}")
        return {"seen": []}

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

def sha_of(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def fetch(url: str, retries: int = 2, timeout: int = 25) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "close",
    }
    last_err = None
    for i in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            else:
                last_err = f"HTTP {resp.status_code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Errore nel fetch di {url}: {last_err}")

def extract_links(html_text: str, pattern: str, domain: str) -> list:
    # Decodifica entità HTML per evitare &amp; nei link
    text = html.unescape(html_text)
    links = re.findall(pattern, text, flags=re.IGNORECASE)
    clean = []
    for link in links:
        # normalizza rimuovendo anchor/tracking
        link = link.split('#')[0]
        link = link.split('?utm_')[0]
        # filtra per dominio (paranoia)
        try:
            netloc = urlparse(link).netloc
        except Exception:
            continue
        if domain in netloc:
            clean.append(link)
    # Dedupe mantenendo ordine
    seen = set()
    deduped = []
    for l in clean:
        if l not in seen:
            seen.add(l)
            deduped.append(l)
    return deduped

def telegram_notify(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[INFO] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non impostati: salto la notifica.")
        return False
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(api, data=payload, timeout=20)
        if r.status_code != 200:
            print(f"[WARN] Telegram API HTTP {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"[WARN] Errore Telegram: {e}")
        return False

def run_once(notify_first_run: bool) -> int:
    state = load_state()
    seen = set(state.get("seen", []))
    newly_seen = []
    total_new = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for src in SOURCES:
        name = src["name"]
        url = src["url"]
        pattern = src["pattern"]
        domain = urlparse(url).netloc

        try:
            html_text = fetch(url)
        except Exception as e:
            print(f"[ERROR] {name}: {e}")
            continue

        links = extract_links(html_text, pattern, domain)
        print(f"[INFO] {name}: trovati {len(links)} link candidati.")

        for link in links:
            key = sha_of(link)
            if key not in seen:
                total_new += 1
                newly_seen.append(key)
                msg = f"🔔 Nuovo annuncio su <b>{name}</b>\n{link}"
                if notify_first_run or state.get("initialized", False):
                    telegram_notify(msg)
                else:
                    print(f"[DRYRUN] {msg}")

    # Aggiorna stato
    if not state.get("initialized", False):
        state["initialized"] = True
    state["seen"] = list(seen.union(newly_seen))
    save_state(state)
    print(f"[INFO] Run completato alle {now}. Nuovi: {total_new}. Stato in {STATE_FILE}.")
    return total_new

def main():
    parser = argparse.ArgumentParser(description="Controlla nuovi annunci Idealista / Immobiliare e invia notifiche Telegram.")
    parser.add_argument("--interval", type=int, default=None, help="Intervallo in secondi tra i controlli (se non specificato, esegue una sola volta).")
    parser.add_argument("--once", action="store_true", help="Esegui un singolo controllo e termina.")
    parser.add_argument("--notify-first-run", action="store_true", help="Invia notifiche anche alla prima esecuzione.")
    args = parser.parse_args()

    if args.once or not args.interval:
        run_once(notify_first_run=args.notify_first_run)
        return

    # Loop continuo
    print(f"[INFO] Avvio monitor. Intervallo: {args.interval}s")
    while True:
        try:
            run_once(notify_first_run=args.notify_first_run)
        except KeyboardInterrupt:
            print("Interrotto dall'utente.")
            break
        except Exception as e:
            print(f"[WARN] Errore nel ciclo: {e}")
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
