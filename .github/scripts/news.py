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


RAKU = "https://pokemongo-raku.com/postcategory/max_battle"
NAMES_JA = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/pokemon_species_names.csv"
MAX_BOSS_PAGES = 40  # hoechstens so viele Boss-Seiten pro Lauf (danach nur noch neue und woechentlich)
KATAKANA = re.compile(r"[ァ-ヺー]+")


def max_hp(old_list):
    """
    Lebenspunkte der Dyna-Bosse (Wunsch des Besitzers 24.11.2026) von pokemongo-raku.com: die Kategorie „Max Battle“
    listet je Boss eine Seite („ダイマックスサンダー対策“), dort steht „HPは「13,000」“. Japanische Namen über die
    PokeAPI-Namensliste zur Pokedex-Nummer. Neue Seiten kommen dazu, bekannte werden hoechstens alle 7 Tage neu gelesen,
    ohne HP taeglich (der Boss kann spaeter ergaenzt werden).
    """
    dex_of = {}
    for line in get(NAMES_JA).splitlines()[1:]:
        parts = line.split(",")
        if len(parts) >= 3 and parts[1] in ("1", "11") and parts[0].isdigit():
            dex_of.setdefault(parts[2].strip(), int(parts[0]))
    posts = []
    for page in range(1, 15):
        url = RAKU if page == 1 else RAKU + "/page/%d" % page
        try:
            html = get(url)
        except Exception:
            break
        found = re.findall(r'href="https://pokemongo-raku.com/(post\d+)"><div class="post-list-inner">(?:(?!post-list-link).){0,2000}?alt="([^"]*)"', html, re.S)
        if not found:
            break
        posts += found
        time.sleep(1)
    by_post = {e.get("post"): e for e in old_list if e.get("post")}
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    week = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    budget = [MAX_BOSS_PAGES]
    out = []
    seen = set()
    for post, alt in posts:
        if post in seen:
            continue
        seen.add(post)
        giga = alt.startswith("キョダイマックス")
        rest = alt[len("キョダイマックス"):] if giga else alt[len("ダイマックス"):] if alt.startswith("ダイマックス") else alt
        m = KATAKANA.match(rest)
        if not m or ("対策" not in alt and "マックスバトル" not in alt):
            continue
        # Laengster bekannter Name am Anfang („ラティオスマックスバトル“ → „ラティオス“).
        run = m.group(0)
        name = next((run[:k] for k in range(len(run), 1, -1) if run[:k] in dex_of), None)
        if name is None:
            continue
        dex = dex_of[name]
        e = by_post.get(post) or {}
        fresh = e.get("checked", "") >= (week if e.get("hp") else today)
        if not fresh and budget[0] > 0:
            budget[0] -= 1
            try:
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", get("https://pokemongo-raku.com/" + post)))
                hp = re.search(r"HPは「([\d,]+)」(.{0,20})", text)
                tier = re.search(r"星(\d)", text)
                cpm = re.search(r"CPM[^「]{0,20}「([\d.]+)」", text)
                e = {
                    "post": post, "name": name, "dex": dex, "giga": giga,
                    "hp": int(hp.group(1).replace(",", "")) if hp else None,
                    "guess": bool(hp and "想定" in hp.group(2)),
                    "tier": int(tier.group(1)) if tier else None,
                    "cpm": float(cpm.group(1)) if cpm else None,
                    "checked": today,
                }
                time.sleep(1)
            except Exception:
                pass
        if e:
            out.append(e)
    return out


MAX_GBL_PAGES = 6  # hoechstens so viele Liga-Seiten pro Lauf (nur fuer Fang-Cups, je Woche einmal)
CATCH = re.compile(r"caught (?:between|from) ([A-Z][a-z]+ \d{1,2}, \d{4}).{0,60}? (?:and|to|until) ([A-Z][a-z]+ \d{1,2}, \d{4})", re.S)


def day(text):
    try:
        return datetime.strptime(text, "%B %d, %Y").strftime("%Y-%m-%d")
    except Exception:
        return None


def gbl(events, old_list):
    """
    Liga-Wochen der GO Kampfliga (Rueckmeldung 25.11.2026): LeekDuck/ScrapedDuck nahmen die laufende Woche mit dem
    Mega-Fang-Cup aus ihrer Liste. Darum merkt sich der Job jede Woche, bis sie vorbei ist. Bei Fang-Cups (Catch Cup)
    liest er einmal den Fang-Zeitraum von der LeekDuck-Seite ("caught between September 8, 2026 ... and December 1, 2026").
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    weeks = {}
    for w in old_list or []:
        if (w.get("end") or "") > now and w.get("start"):
            weeks[w["start"][:19]] = w
    for e in events:
        if e.get("eventType") != "go-battle-league" or not e.get("start") or not e.get("end"):
            continue
        k = e["start"][:19]
        prev = weeks.get(k, {})
        w = {"name": e.get("name") or "", "start": e["start"], "end": e["end"], "link": e.get("link") or prev.get("link", "")}
        for f in ("catchFrom", "catchTo"):
            if prev.get(f):
                w[f] = prev[f]
        weeks[k] = w
    pages = [MAX_GBL_PAGES]
    for w in weeks.values():
        if "catch" not in w["name"].lower() or w.get("catchFrom") or w.get("noWindow") or pages[0] <= 0:
            continue
        if not (w.get("link") or "").startswith("https://leekduck.com/"):
            continue
        pages[0] -= 1
        try:
            text = re.sub(r"\s+", " ", clean(get(w["link"])))
            m = CATCH.search(text)
            if m and day(m.group(1)) and day(m.group(2)):
                w["catchFrom"], w["catchTo"] = day(m.group(1)), day(m.group(2))
            else:
                w["noWindow"] = True
            time.sleep(1)
        except Exception:
            pass
    return sorted(weeks.values(), key=lambda w: w["start"])


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
    all_events = json.loads(get(EVENTS))
    for e in all_events:
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

    # Dyna-Boss-Lebenspunkte; bei einem Fehler bleibt die alte Liste.
    try:
        bosses = max_hp(old.get("maxHp", [])) or old.get("maxHp", [])
    except Exception:
        bosses = old.get("maxHp", [])

    # Liga-Wochen merken (auch wenn LeekDuck die laufende aus der Liste nimmt); bei einem Fehler bleibt die alte Liste.
    try:
        weeks = gbl(all_events, old.get("gbl", []))
    except Exception:
        weeks = old.get("gbl", [])

    result = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gbl": weeks,
        "maxHp": bosses,
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
