"""
Stündlich von GitHub Actions ausgeführt (siehe ../workflows/news.yml).

Lädt die News von Pokémon GO Hub und den Event-Kalender von ScrapedDuck, übersetzt Überschriften
und Event-Namen ins Deutsche (MyMemory, kostenlos, ohne Konto) und schreibt news.json für die
App und die Web-App. Schon Übersetztes wird gemerkt, damit das Tageslimit des Dienstes reicht.
"""
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

FEED = "https://pokemongohub.net/feed/"
EVENTS = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.min.json"
OUT = "news.json"
UA = {"User-Agent": "Mozilla/5.0 (Pogo IV news mirror)"}
MAX_NEW = 60  # hoechstens so viele neue Uebersetzungen pro Lauf


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8")


def translate(text):
    url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": text, "langpair": "en|de"})
    data = json.loads(get(url))
    out = (data.get("responseData") or {}).get("translatedText") or ""
    if data.get("quotaFinished") or not out or "MYMEMORY WARNING" in out.upper():
        return None
    return out


def main():
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        old = {}
    cache = old.get("translations", {})
    used = {}
    budget = [MAX_NEW]

    def de(text):
        if not text:
            return text
        if text in cache:
            used[text] = cache[text]
            return cache[text]
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        try:
            t = translate(text)
            time.sleep(1)
        except Exception:
            t = None
        if t:
            cache[text] = t
            used[text] = t
        return t

    news = []
    root = ET.fromstring(get(FEED))
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        news.append({
            "title": title,
            "titleDe": de(title),
            "link": (item.findtext("link") or "").strip(),
            "date": (item.findtext("pubDate") or "").strip(),
            "categories": [c.text or "" for c in item.findall("category")],
        })

    events = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    for e in json.loads(get(EVENTS)):
        end = e.get("end") or ""
        if end and end < cutoff:
            continue
        name = e.get("name") or ""
        t = de(name)
        if t:
            events[e.get("eventID") or name] = t

    result = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "news": news,
        "events": events,
        # Nur noch Benutztes behalten, damit die Datei nicht waechst.
        "translations": used,
    }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
