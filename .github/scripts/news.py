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
MAX_PAGES = 30  # hoechstens so viele Event-Seiten pro Lauf (je Event hoechstens einmal am Tag)


def clean(s):
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


DETAILS_VERSION = 2  # erhoehen, wenn sich das Format aendert (dann werden alle Seiten neu gelesen)
PROMO = "https://leekduck.com/promo-codes/"
WEEKDAY = re.compile(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)")


def details(page):
    """
    Abschnitte einer LeekDuck-Eventseite: je Abschnitt Gruppen (Unterueberschrift + Pokemon), Boni fuer alle
    und fuer Ticket-Inhaber, Event-Attacken und Ticketpreis.
    """
    out = {"v": DETAILS_VERSION, "sections": [], "bonuses": [], "ticketBonuses": [], "moves": [], "ticket": ""}
    marks = [(m.start(), m.group(1)) for m in re.finditer(r'<h2 id="([a-z-]+)" class="event-section-header', page)]
    end_mark = page.find('id="related-event"')
    for i, (pos, sid) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else (end_mark if end_mark > pos else len(page))
        part = page[pos:stop]
        if sid == "bonuses":
            # "Bonuses for All Trainers" / "Additional Ticket Holder Bonuses" teilen den Abschnitt.
            pieces = re.split(r"<h2[^>]*>(.*?)</h2>", part[part.find(">") + 1:], flags=re.S)
            head = ""
            for j, piece in enumerate(pieces):
                if j % 2 == 1:
                    head = clean(piece)
                    continue
                texts = [clean(b) for b in re.findall(r'class="bonus-text">(.*?)</', piece, re.S)]
                key = "ticketBonuses" if "ticket" in head.lower() else "bonuses"
                out[key] += texts
            out["bonuses"] = out["bonuses"][:20]
            out["ticketBonuses"] = out["ticketBonuses"][:20]
            continue
        if sid == "moves":
            for card in re.findall(r'class="move-card[^"]*">(.*?)</ul>', part, re.S):
                mon = re.search(r'class="pkmn-name">(.*?)</', card, re.S)
                move = re.search(r'class="move-card-move">(.*?)</', card, re.S)
                cat = re.search(r'class="move-card-category">(.*?)</', card, re.S)
                if mon and move:
                    out["moves"].append({"pokemon": clean(mon.group(1)), "move": clean(move.group(1)),
                                         "fast": bool(cat and "Fast" in cat.group(1))})
            continue
        if sid == "sales":
            for h in re.findall(r"<h2[^>]*>(.*?)</h2>", part, re.S):
                t = clean(h)
                if "Ticket" in t and "$" in t:
                    out["ticket"] = t.split("—")[-1].strip()
                    break
            continue
        if sid not in SECTION_IDS:
            continue
        groups = []
        # Unterueberschriften (z. B. "Three-Star Raids", "Saturday, November 14") teilen den Abschnitt in Gruppen.
        pieces = re.split(r"<h[23][^>]*>(.*?)</h[23]>", part[part.find("</h2>") + 5:], flags=re.S)
        title = ""
        parent = ""
        parent_empty = False
        for j, piece in enumerate(pieces):
            if j % 2 == 1:
                t = clean(piece)
                # Tag direkt unter einer Ueberschrift ohne eigene Pokemon: "Six-Star Max Battles: Saturday, November 14".
                if WEEKDAY.match(t) and parent and parent_empty and not WEEKDAY.search(parent):
                    title = parent + ": " + t
                else:
                    title = t
                    if not WEEKDAY.match(t):
                        parent = t
                        parent_empty = True
                continue
            names = [clean(n) for n in re.findall(r'class="pkmn-name">(.*?)</', piece, re.S)]
            if names:
                groups.append({"title": title, "pokemon": names[:40]})
                if title == parent:
                    parent_empty = False
        if groups:
            out["sections"].append({"id": sid, "groups": groups})
    return out


def codes(page):
    """Aktuelle Promo-Codes (LeekDuck): Titel, Code (??? = noch nicht bekannt), Belohnungen, Ablauf."""
    out = []
    for card in page.split('<div class="promo-card')[1:]:
        title = re.search(r'<h3 class="title">(.*?)</h3>', card, re.S)
        code = re.search(r'class="code-display">\s*<p class="text">(.*?)</p>', card, re.S)
        desc = re.search(r'class="description">(.*?)</div>', card, re.S)
        exp = re.search(r'data-expires="([^"]*)"', card)
        rewards = []
        for m in re.finditer(r'<li class="reward"(.*?)</li>', card, re.S):
            body = m.group(1)
            label = re.search(r'class="reward-label">(.*?)</span>', body, re.S)
            qty = re.search(r'class="quantity">(.*?)</div>', body, re.S)
            if label:
                rewards.append({"name": clean(label.group(1)), "qty": clean(qty.group(1)).lstrip("×x ") if qty else ""})
        if not title or not code:
            continue
        out.append({
            "title": clean(title.group(1)),
            "code": clean(code.group(1)),
            "description": clean(desc.group(1)) if desc else "",
            "rewards": rewards,
            "expires": exp.group(1) if exp else "",
        })
    return out[:30]


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
        if e.get("heading") in ("GO Battle League", "Season", "GO Pass"):
            continue
        d = old_details.get(eid)
        stale = d is None or d.get("fetched") != today or d.get("v") != DETAILS_VERSION
        if stale and pages[0] > 0 and (e.get("link") or "").startswith("https://leekduck.com/"):
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
            d["ticketBonusesDe"] = [de(b) or b for b in d.get("ticketBonuses", [])]
            new_details[eid] = d

    # Promo-Codes (im Spiel bzw. unter store.pokemongo.com/offer-redemption einzugeben).
    try:
        promo = codes(get(PROMO))
        for c in promo:
            c["titleDe"] = de(c["title"]) or c["title"]
    except Exception:
        promo = old.get("codes", [])

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
        "codes": promo,
        # Nur noch Benutztes behalten, damit die Datei nicht waechst.
        "translations": used,
    }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
