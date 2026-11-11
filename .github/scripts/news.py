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
RESEARCH = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/research.min.json"
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


import re
import html as htmllib

SECTION_IDS = ["spawns", "raids", "eggs", "research", "field-research", "timed-research", "special-research",
               "max-battles", "featured-pokemon", "shiny", "bonuses", "incense", "lures", "photobomb"]
MAX_PAGES = 12  # hoechstens so viele Event-Seiten pro Lauf (je Event hoechstens einmal am Tag)


def clean(s):
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def details(page):
    """Abschnitte einer LeekDuck-Eventseite: je Abschnitt Gruppen (Unterueberschrift + Pokemon), dazu Boni."""
    out = {"sections": [], "bonuses": []}
    marks = [(m.start(), m.group(1)) for m in re.finditer(r'id="([a-z-]+)"', page) if m.group(1) in SECTION_IDS]
    end_mark = page.find('id="related-event"')
    for i, (pos, sid) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else (end_mark if end_mark > pos else len(page))
        part = page[pos:stop]
        if sid == "bonuses":
            out["bonuses"] = [clean(b) for b in re.findall(r'class="bonus-text">(.*?)</', part, re.S)][:12]
            continue
        groups = []
        # Unterueberschriften (z. B. "Three-Star Raids") teilen den Abschnitt in Gruppen.
        pieces = re.split(r"<h3[^>]*>(.*?)</h3>", part, flags=re.S)
        title = ""
        for j, piece in enumerate(pieces):
            if j % 2 == 1:
                title = clean(piece)
                continue
            names = [clean(n) for n in re.findall(r'class="pkmn-name">(.*?)</', piece, re.S)]
            if names:
                groups.append({"title": title, "pokemon": names[:40]})
        if groups:
            out["sections"].append({"id": sid, "groups": groups})
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
    # Details der Eventseiten (wilde Pokemon, Raids, Boni), je Event hoechstens einmal am Tag geladen.
    old_details = old.get("details", {})
    new_details = {}
    pages = [MAX_PAGES]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    for e in json.loads(get(EVENTS)):
        end = e.get("end") or ""
        if end and end < cutoff:
            continue
        name = e.get("name") or ""
        eid = e.get("eventID") or name
        t = de(name)
        if t:
            events[eid] = t
        if e.get("heading") in ("GO Battle League", "Season"):
            continue
        d = old_details.get(eid)
        if (d is None or d.get("fetched") != today) and pages[0] > 0 and (e.get("link") or "").startswith("https://leekduck.com/"):
            pages[0] -= 1
            try:
                fresh = details(get(e["link"]))
                fresh["fetched"] = today
                d = fresh
                time.sleep(1)
            except Exception:
                pass
        if d:
            d["bonusesDe"] = [de(b) or b for b in d.get("bonuses", [])]
            new_details[eid] = d

    # Feldforschungen: Aufgaben ins Deutsche (die App zeigt sie im Kalender unter "Forschung").
    research = {}
    try:
        for t in json.loads(get(RESEARCH)):
            text = clean(t.get("text") or "")
            if not text:
                continue
            d = de(text)
            if d:
                research[text] = d
    except Exception:
        research = old.get("research", {})

    result = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "news": news,
        "events": events,
        "details": new_details,
        "research": research,
        # Nur noch Benutztes behalten, damit die Datei nicht waechst.
        "translations": used,
    }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
