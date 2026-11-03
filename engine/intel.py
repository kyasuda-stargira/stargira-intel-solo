#!/usr/bin/env python3
"""Stargira Intel Solo: a local intel store for one business on one computer.

The script does the storage and the mechanical work: it reads feeds, scores items
against the business's filter, keeps notes and gaps, searches, and writes reports.
Claude (in the user's own Claude Code session) does the thinking: the interview,
the "what this means for you" notes, and the gap finding. See skills/intel-solo/SKILL.md.

Standard library only. Python 3.9+. Everything lives in one folder (INTEL_SOLO_HOME,
default ~/IntelSolo). The only network calls are GETs to the feed URLs the user added.
"""
import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "0.2.1"
USER_AGENT = "stargira-intel-solo/%s (+local feed reader)" % VERSION

IMPACTS = ("opportunity", "threat", "watch", "none")
URGENCIES = ("now", "this_month", "later")
CONFIDENCES = ("high", "medium", "low")
GAP_KINDS = ("knowledge", "capability")
GAP_STATUSES = ("open", "in_progress", "closed")
EFFORTS = ("hours", "days", "weeks", "months")
URGENCY_ORDER = {"now": 0, "this_month": 1, "later": 2}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, label TEXT, added TEXT,
  active INTEGER DEFAULT 1, last_fetched TEXT, last_status TEXT);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, title TEXT NOT NULL, summary TEXT,
  source TEXT, published TEXT, fetched TEXT, score REAL DEFAULT 0,
  status TEXT DEFAULT 'new', matched TEXT);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, item_id INTEGER UNIQUE NOT NULL REFERENCES items(id),
  created TEXT, means TEXT NOT NULL, impact TEXT, urgency TEXT, action TEXT,
  confidence TEXT, sources TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS gaps (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT UNIQUE NOT NULL, why TEXT,
  close_steps TEXT, effort TEXT, status TEXT DEFAULT 'open', evidence TEXT DEFAULT '[]',
  created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS docs (
  id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT, body TEXT,
  sha256 TEXT, added TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  kind UNINDEXED, ref UNINDEXED, title, body, tokenize='porter unicode61');
"""


# ---------------------------------------------------------------- basics

def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def home_dir(args=None):
    h = getattr(args, "home", None) or os.environ.get("INTEL_SOLO_HOME") or "~/IntelSolo"
    return Path(h).expanduser()


def die(msg, code=2):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)


def connect(home):
    if not (home / "intel.db").exists():
        die("no store at %s. Run: intel.py init" % home)
    con = sqlite3.connect(str(home / "intel.db"))
    con.row_factory = sqlite3.Row
    cols = [r["name"] for r in con.execute("PRAGMA table_info(notes)")]
    if "sources" not in cols:  # stores made by v0.1.0
        con.execute("ALTER TABLE notes ADD COLUMN sources TEXT DEFAULT '[]'")
        con.commit()
    return con


def load_json_arg(path):
    """Read JSON from a file path, or from stdin when path is '-'."""
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        die("bad JSON in %s: %s" % (path, e))


def fts_put(con, kind, ref, title, body):
    con.execute("DELETE FROM fts WHERE kind=? AND ref=?", (kind, str(ref)))
    con.execute("INSERT INTO fts(kind, ref, title, body) VALUES (?,?,?,?)",
                (kind, str(ref), title or "", body or ""))


def config(home):
    p = home / "config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ---------------------------------------------------------------- profile + filter

def validate_profile(p):
    errs = []
    if not isinstance(p.get("business"), dict) or not p["business"].get("what_we_sell"):
        errs.append("business.what_we_sell is required")
    f = p.get("filter")
    if not isinstance(f, dict):
        errs.append("filter is required")
        return errs
    terms = f.get("terms")
    if not isinstance(terms, list) or not terms:
        errs.append("filter.terms must be a non-empty list")
    else:
        for t in terms:
            if not isinstance(t, dict) or not str(t.get("term", "")).strip():
                errs.append("each filter term needs a 'term': %r" % (t,))
            elif not isinstance(t.get("weight", 1), (int, float)) or not 1 <= t.get("weight", 1) <= 5:
                errs.append("weight must be 1-5 for %r" % t.get("term"))
    for key in ("entities", "regions", "exclude"):
        if key in f and not isinstance(f[key], list):
            errs.append("filter.%s must be a list" % key)
    if "threshold" in f and not isinstance(f["threshold"], (int, float)):
        errs.append("filter.threshold must be a number")
    return errs


def load_profile(home):
    p = home / "profile.json"
    if not p.exists():
        die("no profile yet. Run the onboarding interview, then: intel.py profile save <file>")
    return json.loads(p.read_text(encoding="utf-8"))


def _rx(term):
    # whole-word, case-insensitive, allows a plural "s"
    body = r"[\s/\-]+".join(re.escape(w) for w in term.split())
    return re.compile(r"(?<!\w)" + body + r"s?(?!\w)", re.I)


def score_item(title, summary, flt):
    """Return (score, matched_terms, excluded)."""
    title, summary = title or "", summary or ""
    for ex in flt.get("exclude", []):
        if _rx(ex).search(title) or _rx(ex).search(summary):
            return -1.0, ["-" + ex], True
    score, hits = 0.0, []
    weighted = [(t["term"], float(t.get("weight", 1))) for t in flt.get("terms", [])]
    weighted += [(e, 4.0) for e in flt.get("entities", [])]
    best = {}
    for term, w in weighted:  # a phrase listed twice counts once, at its higher weight
        k = term.strip().lower()
        if k not in best or w > best[k][1]:
            best[k] = (term, w)
    weighted = list(best.values())
    for term, w in weighted:
        rx = _rx(term)
        if rx.search(title):
            score += 2 * w
            hits.append(term)
        elif rx.search(summary):
            score += w
            hits.append(term)
    if score > 0:
        for r in flt.get("regions", []):
            if _rx(r).search(title) or _rx(r).search(summary):
                score += 1
                hits.append("@" + r)
                break
    return score, hits, False


def triage(con, flt, only_new=True):
    thr = float(flt.get("threshold", 3))
    where = "WHERE status='new'" if only_new else "WHERE status IN ('new','relevant','dropped','noted')"
    n_rel = n_drop = 0
    for row in con.execute("SELECT id, title, summary, status FROM items " + where).fetchall():
        s, hits, excluded = score_item(row["title"], row["summary"], flt)
        status = "relevant" if (not excluded and s >= thr) else "dropped"
        if row["status"] == "noted":  # refresh the score (it sets note order) but keep the note
            status = "noted"
        n_rel += status == "relevant"
        n_drop += status == "dropped"
        con.execute("UPDATE items SET score=?, matched=?, status=? WHERE id=?",
                    (s, ", ".join(hits), status, row["id"]))
    con.commit()
    return n_rel, n_drop


# ---------------------------------------------------------------- feeds

def _text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def clean(s, limit=700):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        d = None
    if d is None:
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_feed(data):
    """Parse RSS 2.0 or Atom bytes into a list of dicts (url, title, summary, published)."""
    root = ET.fromstring(data)
    out = []
    tag = root.tag.lower()
    if tag.endswith("rss") or root.find("channel") is not None:
        for it in root.iter("item"):
            out.append({"url": _text(it.find("link")) or _text(it.find("guid")),
                        "title": clean(_text(it.find("title")), 300),
                        "summary": clean(_text(it.find("description"))),
                        "published": parse_date(_text(it.find("pubDate")))})
    else:
        ns = "{http://www.w3.org/2005/Atom}"
        for e in root.iter(ns + "entry"):
            link = ""
            for l in e.findall(ns + "link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            out.append({"url": link,
                        "title": clean(_text(e.find(ns + "title")), 300),
                        "summary": clean(_text(e.find(ns + "summary")) or _text(e.find(ns + "content"))),
                        "published": parse_date(_text(e.find(ns + "updated")) or _text(e.find(ns + "published")))})
    return [o for o in out if o["url"] and o["title"]]


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def insert_item(con, url, title, summary, source, published):
    cur = con.execute(
        "INSERT OR IGNORE INTO items(url, title, summary, source, published, fetched) VALUES (?,?,?,?,?,?)",
        (url, title, summary, source, published, now_iso()))
    if cur.rowcount:
        fts_put(con, "item", cur.lastrowid, title, summary)
        return cur.lastrowid
    return None


# ---------------------------------------------------------------- commands

def cmd_init(a):
    home = home_dir(a)
    for sub in ("reports", "inbox"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(home / "intel.db"))
    con.executescript(SCHEMA)
    con.commit()
    if not (home / "config.json").exists():
        (home / "config.json").write_text(json.dumps({"exclude_paths": []}, indent=2) + "\n")
    print("store ready at %s" % home)


def cmd_profile(a):
    home = home_dir(a)
    if a.action == "show":
        print(json.dumps(load_profile(home), indent=2, ensure_ascii=False))
        return
    p = load_json_arg(a.file)
    errs = validate_profile(p)
    if errs:
        die("profile not saved:\n  - " + "\n  - ".join(errs))
    p.setdefault("saved", now_iso())
    (home / "profile.json").write_text(json.dumps(p, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    con = connect(home)
    biz = p["business"]
    fts_put(con, "profile", 1, biz.get("name", "Business profile"),
            "\n".join("%s: %s" % (k, v) for k, v in biz.items()))
    con.commit()
    if a.rescore:
        r, d = triage(con, p["filter"], only_new=False)
        print("rescored: %d relevant, %d dropped" % (r, d))
    print("profile saved (%d filter terms, %d entities)"
          % (len(p["filter"]["terms"]), len(p["filter"].get("entities", []))))


def cmd_source(a):
    con = connect(home_dir(a))
    if a.action == "add":
        for url in a.urls:
            cur = con.execute("INSERT OR IGNORE INTO sources(url, label, added) VALUES (?,?,?)",
                              (url, a.label or url, now_iso()))
            print(("added source %d: %s" % (cur.lastrowid, url)) if cur.rowcount else "already there: " + url)
        con.commit()
        return
    if a.action == "remove":
        for sid in a.urls:
            con.execute("UPDATE sources SET active=0 WHERE id=? OR url=?", (sid, sid))
            print("removed: %s" % sid)
        con.commit()
        return
    for r in con.execute("SELECT * FROM sources WHERE active=1 ORDER BY id"):
        print("%3d  %-40s  %s  [%s]" % (r["id"], (r["label"] or "")[:40], r["url"], r["last_status"] or "not fetched"))


def cmd_fetch(a):
    home = home_dir(a)
    con = connect(home)
    flt = load_profile(home)["filter"]
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.days)
    added = 0
    for s in con.execute("SELECT * FROM sources WHERE active=1").fetchall():
        try:
            entries = parse_feed(http_get(s["url"]))
            status = "ok %d" % len(entries)
        except Exception as e:  # network or parse error: record it, carry on
            entries, status = [], "error: %s" % str(e)[:120]
        n = 0
        for e in entries:
            if e["published"] and dt.datetime.fromisoformat(e["published"]) < cutoff:
                continue
            if insert_item(con, e["url"], e["title"], e["summary"], s["label"], e["published"]):
                n += 1
        con.execute("UPDATE sources SET last_fetched=?, last_status=? WHERE id=?",
                    (now_iso(), "%s, %d new" % (status, n), s["id"]))
        added += n
        print("%-40s %s, %d new" % ((s["label"] or s["url"])[:40], status, n))
    con.commit()
    r, d = triage(con, flt)
    print("added %d items; triage: %d relevant, %d dropped" % (added, r, d))


def cmd_add_item(a):
    home = home_dir(a)
    con = connect(home)
    iid = insert_item(con, a.url, clean(a.title, 300), clean(a.summary or ""),
                      a.source or "web search", parse_date(a.published) if a.published else None)
    if iid is None:
        row = con.execute("SELECT id, status FROM items WHERE url=?", (a.url,)).fetchone()
        if a.keep and row["status"] == "dropped":
            con.execute("UPDATE items SET status='relevant' WHERE id=?", (row["id"],))
            con.commit()
            print("item %d already stored; now kept as relevant" % row["id"])
        else:
            print("item %d already stored (%s)" % (row["id"], row["status"]))
        return
    con.commit()
    triage(con, load_profile(home)["filter"])
    r = con.execute("SELECT status, score FROM items WHERE id=?", (iid,)).fetchone()
    if a.keep and r["status"] == "dropped":
        con.execute("UPDATE items SET status='relevant' WHERE id=?", (iid,))
        con.commit()
        r = con.execute("SELECT status, score FROM items WHERE id=?", (iid,)).fetchone()
    print("item %d stored: %s (score %.0f)" % (iid, r["status"], r["score"]))


def cmd_queue(a):
    con = connect(home_dir(a))
    rows = con.execute(
        "SELECT i.* FROM items i LEFT JOIN notes n ON n.item_id=i.id "
        "WHERE i.status='relevant' AND n.id IS NULL ORDER BY i.score DESC, i.published DESC LIMIT ?",
        (a.limit,)).fetchall()
    if a.json:
        print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
        return
    for r in rows:
        print("[%d] (score %.0f; %s) %s\n     %s | %s\n     %s\n" % (
            r["id"], r["score"], r["matched"], r["title"], r["source"], (r["published"] or "")[:10],
            r["url"]))
    print("%d item(s) waiting for a note" % len(rows))


def _upsert_gap(con, g, evidence_ids=()):
    errs = []
    if g.get("kind") not in GAP_KINDS:
        errs.append("gap kind must be one of %s" % (GAP_KINDS,))
    if not str(g.get("title", "")).strip():
        errs.append("gap needs a title")
    steps = g.get("close_steps", [])
    if isinstance(steps, str):
        steps = [steps]
    if g.get("effort") and g["effort"] not in EFFORTS:
        errs.append("effort must be one of %s" % (EFFORTS,))
    if errs:
        die("; ".join(errs))
    row = con.execute("SELECT * FROM gaps WHERE lower(title)=lower(?)", (g["title"].strip(),)).fetchone()
    ev = set(json.loads(row["evidence"])) if row else set()
    ev.update(int(x) for x in list(g.get("evidence_item_ids", [])) + list(evidence_ids))
    if row:
        con.execute("UPDATE gaps SET why=coalesce(?,why), close_steps=?, effort=coalesce(?,effort), "
                    "evidence=?, updated=? WHERE id=?",
                    (g.get("why"), json.dumps(steps or json.loads(row["close_steps"] or "[]")),
                     g.get("effort"), json.dumps(sorted(ev)), now_iso(), row["id"]))
        gid = row["id"]
    else:
        gid = con.execute("INSERT INTO gaps(kind,title,why,close_steps,effort,evidence,created,updated) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (g["kind"], g["title"].strip(), g.get("why", ""), json.dumps(steps),
                           g.get("effort"), json.dumps(sorted(ev)), now_iso(), now_iso())).lastrowid
    r = con.execute("SELECT * FROM gaps WHERE id=?", (gid,)).fetchone()
    fts_put(con, "gap", gid, r["title"], (r["why"] or "") + "\n" + "\n".join(json.loads(r["close_steps"] or "[]")))
    return gid


def cmd_note(a):
    home = home_dir(a)
    con = connect(home)
    notes = load_json_arg(a.file)
    if isinstance(notes, dict):
        notes = [notes]
    for n in notes:
        iid = n.get("item_id")
        item = con.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        errs = []
        if not item:
            errs.append("no item %r" % iid)
        if not str(n.get("means", "")).strip():
            errs.append("'means' is required")
        for key, allowed in (("impact", IMPACTS), ("urgency", URGENCIES), ("confidence", CONFIDENCES)):
            if n.get(key) not in allowed:
                errs.append("%s must be one of %s" % (key, allowed))
        extra = n.get("sources", [])
        if not isinstance(extra, list) or not all(str(u).startswith("http") for u in extra):
            errs.append("'sources' must be a list of URLs")
        if errs:
            die("note for item %r not saved: %s" % (iid, "; ".join(errs)))
        con.execute("INSERT INTO notes(item_id, created, means, impact, urgency, action, confidence, sources) "
                    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET means=excluded.means, "
                    "impact=excluded.impact, urgency=excluded.urgency, action=excluded.action, "
                    "confidence=excluded.confidence, sources=excluded.sources, created=excluded.created",
                    (iid, now_iso(), n["means"].strip(), n["impact"], n["urgency"],
                     (n.get("action") or "").strip(), n["confidence"], json.dumps(extra)))
        con.execute("UPDATE items SET status='noted' WHERE id=?", (iid,))
        fts_put(con, "note", iid, item["title"], n["means"] + "\n" + (n.get("action") or ""))
        for g in n.get("gaps", []):
            _upsert_gap(con, g, evidence_ids=[iid])
        print("note saved for item %d (%s, %s)" % (iid, n["impact"], n["urgency"]))
    con.commit()


def cmd_gap(a):
    con = connect(home_dir(a))
    if a.action == "add":
        gs = load_json_arg(a.arg)
        for g in (gs if isinstance(gs, list) else [gs]):
            print("gap %d saved" % _upsert_gap(con, g))
        con.commit()
        return
    if a.action == "set":
        if a.status not in GAP_STATUSES:
            die("status must be one of %s" % (GAP_STATUSES,))
        con.execute("UPDATE gaps SET status=?, updated=? WHERE id=?", (a.status, now_iso(), a.arg))
        con.commit()
    for r in con.execute("SELECT * FROM gaps ORDER BY status, kind, id"):
        print("[%d] %s gap (%s, effort %s): %s  — evidence items %s"
              % (r["id"], r["kind"], r["status"], r["effort"] or "?", r["title"], r["evidence"]))


def cmd_doc(a):
    home = home_dir(a)
    con = connect(home)
    excluded = [Path(p).expanduser().resolve() for p in config(home).get("exclude_paths", [])]
    files = []
    for p in a.paths:
        p = Path(p).expanduser().resolve()
        files += sorted(x for x in p.rglob("*") if x.suffix.lower() in (".md", ".txt")) if p.is_dir() else [p]
    n = skipped = 0
    for f in files:
        if any(f == e or e in f.parents for e in excluded):
            skipped += 1
            continue
        body = f.read_text(encoding="utf-8", errors="replace")
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        row = con.execute("SELECT id, sha256 FROM docs WHERE path=?", (str(f),)).fetchone()
        if row and row["sha256"] == sha:
            continue
        if row:
            con.execute("UPDATE docs SET body=?, sha256=?, added=? WHERE id=?", (body, sha, now_iso(), row["id"]))
            did = row["id"]
        else:
            did = con.execute("INSERT INTO docs(path,title,body,sha256,added) VALUES (?,?,?,?,?)",
                              (str(f), f.stem, body, sha, now_iso())).lastrowid
        fts_put(con, "doc", did, f.stem, body)
        n += 1
    con.commit()
    print("%d document(s) added or updated; %d skipped by exclude_paths" % (n, skipped))


STOP = set("a an and are as at be by for from has have how i in is it its of on or our that the "
           "this to was we what when where which who why will with you your".split())


def fts_query(text, mode):
    words = re.findall(r"\w+", text)
    if mode == "or":  # the loose fallback ignores small common words
        words = [w for w in words if w.lower() not in STOP]
    return (" AND " if mode == "and" else " OR ").join('"%s"' % w for w in words)


def search(con, text, limit=10, kind=None):
    rows = []
    for mode in ("and", "or"):
        q = fts_query(text, mode)
        if not q:
            return []
        sql = ("SELECT kind, ref, title, snippet(fts, 3, '[', ']', '…', 14) AS snip, bm25(fts) AS rank "
               "FROM fts WHERE fts MATCH ?" + (" AND kind=?" if kind else "") + " ORDER BY rank LIMIT ?")
        rows = con.execute(sql, (q, kind, limit) if kind else (q, limit)).fetchall()
        if rows:
            break
    return rows


def cmd_search(a):
    con = connect(home_dir(a))
    seen, out = set(), []
    for q in [a.query] + (a.also or []):
        for r in search(con, q, a.limit, a.kind):
            key = (r["kind"], r["ref"])
            if key not in seen:
                seen.add(key)
                out.append(r)
    for r in out[: a.limit]:
        print("%s:%s  %s\n    %s" % (r["kind"], r["ref"], r["title"], r["snip"]))
    if not out:
        print("no matches")


def _gap_rows(con):
    return con.execute("SELECT * FROM gaps WHERE status!='closed' ORDER BY kind, id").fetchall()


def render_report(con, profile, days):
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    name = profile["business"].get("name") or "Your business"
    today = dt.date.today().isoformat()
    L = ["# Intel report: %s" % name, "",
         "Period: last %d days, to %s. Written by Stargira Intel Solo %s." % (days, today, VERSION), ""]
    noted = con.execute(
        "SELECT i.*, n.means, n.impact, n.urgency, n.action, n.confidence, n.sources, n.created AS noted_at "
        "FROM items i JOIN notes n ON n.item_id=i.id "
        "WHERE coalesce(i.published, i.fetched) >= ? OR n.created >= ?", (since, since)).fetchall()
    noted = sorted(noted, key=lambda r: (URGENCY_ORDER.get(r["urgency"], 9), -r["score"]))
    waiting = con.execute(
        "SELECT i.* FROM items i LEFT JOIN notes n ON n.item_id=i.id WHERE i.status='relevant' "
        "AND n.id IS NULL AND coalesce(i.published, i.fetched) >= ? ORDER BY score DESC", (since,)).fetchall()
    counts = con.execute("SELECT status, count(*) c FROM items WHERE coalesce(published, fetched) >= ? "
                         "GROUP BY status", (since,)).fetchall()
    c = {r["status"]: r["c"] for r in counts}
    L += ["## At a glance", "",
          "- New items read: %d. Kept as relevant: %d. Notes in this report: %d."
          % (sum(c.values()), c.get("relevant", 0) + c.get("noted", 0), len(noted)),
          "- Act now: %d. Opportunities: %d. Threats: %d."
          % (sum(r["urgency"] == "now" for r in noted), sum(r["impact"] == "opportunity" for r in noted),
             sum(r["impact"] == "threat" for r in noted)), ""]
    L += ["## What this week's news means for you", ""]
    if not noted:
        L += ["No notes yet for this period.", ""]
    for r in noted:
        L += ["### %s" % r["title"], "",
              "*%s, %s. %s, %s, confidence %s.* [Source](%s)"
              % (r["source"], (r["published"] or r["fetched"] or "")[:10], r["impact"].capitalize(),
                 r["urgency"].replace("_", " "), r["confidence"], r["url"]), "",
              r["means"], ""]
        extra = json.loads(r["sources"] or "[]")
        if extra:
            L += ["Also from: " + ", ".join("[%d](%s)" % (k + 1, u) for k, u in enumerate(extra)), ""]
        if r["action"]:
            L += ["**Do:** " + r["action"], ""]
    gaps = _gap_rows(con)
    L += ["## Gaps to close", ""]
    if not gaps:
        L += ["No open gaps recorded.", ""]
    for g in gaps:
        ev = json.loads(g["evidence"] or "[]")
        L += ["### %s gap: %s" % (g["kind"].capitalize(), g["title"]), "",
              "Status: %s. Effort: %s. Raised by items: %s." % (g["status"], g["effort"] or "not set",
                                                                 ", ".join(map(str, ev)) or "none"), "",
              g["why"] or "", ""]
        steps = json.loads(g["close_steps"] or "[]")
        L += ["%d. %s" % (i + 1, s) for i, s in enumerate(steps)] + [""]
    if waiting:
        L += ["## Kept but not yet read (%d)" % len(waiting), ""]
        L += ["- [%s](%s) (%s)" % (r["title"], r["url"], r["source"]) for r in waiting[:20]] + [""]
    return "\n".join(L)


def cmd_report(a):
    home = home_dir(a)
    con = connect(home)
    md = render_report(con, load_profile(home), a.days)
    out = home / "reports" / ("%s-intel-report.md" % dt.date.today().isoformat())
    out.write_text(md, encoding="utf-8")
    print(out)


def cmd_status(a):
    home = home_dir(a)
    con = connect(home)
    q = lambda sql: con.execute(sql).fetchone()[0]
    print("store: %s" % home)
    print("profile: %s" % ("yes" if (home / "profile.json").exists() else "not yet"))
    print("sources: %d active" % q("SELECT count(*) FROM sources WHERE active=1"))
    for r in con.execute("SELECT status, count(*) c FROM items GROUP BY status"):
        print("items %-9s %d" % (r["status"], r["c"]))
    print("notes: %d   gaps open: %d   documents: %d"
          % (q("SELECT count(*) FROM notes"), q("SELECT count(*) FROM gaps WHERE status!='closed'"),
             q("SELECT count(*) FROM docs")))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="intel.py", description=__doc__.split("\n")[0])
    ap.add_argument("--home", help="store folder (default $INTEL_SOLO_HOME or ~/IntelSolo)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init").set_defaults(fn=cmd_init)
    p = sp.add_parser("profile")
    p.add_argument("action", choices=("save", "show"))
    p.add_argument("file", nargs="?", default="-")
    p.add_argument("--rescore", action="store_true", help="re-score every stored item with the new filter")
    p.set_defaults(fn=cmd_profile)
    p = sp.add_parser("source")
    p.add_argument("action", choices=("add", "list", "remove"))
    p.add_argument("urls", nargs="*")
    p.add_argument("--label")
    p.set_defaults(fn=cmd_source)
    p = sp.add_parser("fetch")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_fetch)
    p = sp.add_parser("add-item", help="store an item found by web search")
    p.add_argument("--url", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--summary")
    p.add_argument("--published")
    p.add_argument("--source")
    p.add_argument("--keep", action="store_true", help="keep even if the filter would drop it")
    p.set_defaults(fn=cmd_add_item)
    p = sp.add_parser("queue")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_queue)
    p = sp.add_parser("note")
    p.add_argument("file", nargs="?", default="-")
    p.set_defaults(fn=cmd_note)
    p = sp.add_parser("gap")
    p.add_argument("action", choices=("add", "list", "set"))
    p.add_argument("arg", nargs="?", default="-", help="JSON file for add; gap id for set")
    p.add_argument("--status")
    p.set_defaults(fn=cmd_gap)
    p = sp.add_parser("doc")
    p.add_argument("action", choices=("add",))
    p.add_argument("paths", nargs="+")
    p.set_defaults(fn=cmd_doc)
    p = sp.add_parser("search")
    p.add_argument("query")
    p.add_argument("--also", action="append", help="another wording of the same question (repeatable)")
    p.add_argument("--kind", choices=("item", "note", "gap", "doc", "profile"))
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_search)
    p = sp.add_parser("report")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_report)
    sp.add_parser("status").set_defaults(fn=cmd_status)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
