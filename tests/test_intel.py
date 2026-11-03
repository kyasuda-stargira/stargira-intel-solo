"""Offline tests for engine/intel.py. Run: python3 -m unittest discover tests"""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import intel  # noqa: E402

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Boiler Upgrade Scheme grant raised to &#163;7,500</title><link>https://ex.com/a</link>
<description>&lt;p&gt;Heat pump installers welcome the change.&lt;/p&gt;</description>
<pubDate>Wed, 21 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>Football results</title><link>https://ex.com/b</link><description>Leeds won.</description>
<pubDate>Wed, 21 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>g</title>
<entry><title>New import duty on steel cookware</title><link rel="alternate" href="https://gov.example/x"/>
<summary>HMRC tariff change for importers.</summary><updated>2026-10-22T10:00:00+01:00</updated></entry>
</feed>"""

PROFILE = {
    "business": {"name": "Test Plumbing", "what_we_sell": "boiler and heat pump installs"},
    "filter": {"terms": [{"term": "heat pump", "weight": 3}, {"term": "boiler", "weight": 2}],
               "entities": ["Boiler Upgrade Scheme"], "regions": ["Leeds"],
               "exclude": ["football"], "threshold": 3},
}


def run(home, *argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        intel.main(["--home", str(home)] + list(argv))
    return buf.getvalue()


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "store"
        run(self.home, "init")
        p = Path(self.tmp.name) / "profile.json"
        p.write_text(json.dumps(PROFILE))
        run(self.home, "profile", "save", str(p))

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_rss_and_atom(self):
        r = intel.parse_feed(RSS)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["summary"], "Heat pump installers welcome the change.")
        self.assertTrue(r[0]["published"].startswith("2026-10-21T09:00"))
        a = intel.parse_feed(ATOM)
        self.assertEqual(a[0]["url"], "https://gov.example/x")
        self.assertTrue(a[0]["published"].startswith("2026-10-22T09:00"))

    def test_scoring_and_exclusion(self):
        s, hits, ex = intel.score_item("Boiler Upgrade Scheme grant raised", "heat pumps", PROFILE["filter"])
        self.assertFalse(ex)
        self.assertGreaterEqual(s, 3)
        self.assertIn("heat pump", hits)  # plural matched
        s, hits, ex = intel.score_item("Football: Leeds won", "boiler room drama", PROFILE["filter"])
        self.assertTrue(ex)
        s, _, _ = intel.score_item("Leeds weather", "", PROFILE["filter"])
        self.assertEqual(s, 0)  # region alone never makes an item relevant

    def test_bad_profile_rejected(self):
        bad = {"business": {}, "filter": {"terms": []}}
        errs = intel.validate_profile(bad)
        self.assertEqual(len(errs), 2)

    def _load(self):
        con = intel.connect(self.home)
        for e in intel.parse_feed(RSS) + intel.parse_feed(ATOM):
            intel.insert_item(con, e["url"], e["title"], e["summary"], "test", e["published"])
        con.commit()
        intel.triage(con, PROFILE["filter"])
        return con

    def test_note_gap_search_report(self):
        con = self._load()
        rel = con.execute("SELECT id FROM items WHERE status='relevant'").fetchall()
        self.assertEqual(len(rel), 1)
        iid = rel[0]["id"]
        note = {"item_id": iid, "means": "Bigger grant means more heat pump enquiries this winter.",
                "impact": "opportunity", "urgency": "now", "confidence": "high",
                "action": "Get MCS certified.",
                "gaps": [{"kind": "capability", "title": "No MCS certification",
                          "why": "Grant jobs need an MCS installer.",
                          "close_steps": ["Book training", "Apply to a certification body"],
                          "effort": "weeks"}]}
        nf = Path(self.tmp.name) / "note.json"
        nf.write_text(json.dumps(note))
        run(self.home, "note", str(nf))
        # same gap raised again from another item merges, does not duplicate
        intel._upsert_gap(con, {"kind": "capability", "title": "no mcs certification"}, [99])
        con.commit()
        gaps = con.execute("SELECT * FROM gaps").fetchall()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(json.loads(gaps[0]["evidence"]), sorted([iid, 99]))
        self.assertEqual(json.loads(gaps[0]["close_steps"]), ["Book training", "Apply to a certification body"])
        out = run(self.home, "search", "certification")
        self.assertIn("gap:", out)
        out = run(self.home, "search", "enquiries winter")
        self.assertIn("note:%d" % iid, out)
        path = Path(run(self.home, "report", "--days", "3650").strip())
        md = path.read_text()
        self.assertIn("Bigger grant means", md)
        self.assertIn("1. Book training", md)

    def test_note_validation(self):
        con = self._load()
        iid = con.execute("SELECT id FROM items WHERE status='relevant'").fetchone()["id"]
        nf = Path(self.tmp.name) / "bad.json"
        nf.write_text(json.dumps({"item_id": iid, "means": "x", "impact": "huge", "urgency": "now",
                                  "confidence": "high"}))
        with self.assertRaises(SystemExit):
            run(self.home, "note", str(nf))

    def test_search_survives_odd_input(self):
        self._load()
        self.assertIn("no matches", run(self.home, "search", '"zzqx AND ('))
        out = run(self.home, "search", "boilers")  # porter stemming: boilers -> boiler
        self.assertIn("item:", out)

    def test_exclude_paths_respected(self):
        root = Path(self.tmp.name)
        (root / "keep").mkdir()
        (root / "private").mkdir()
        (root / "keep" / "a.md").write_text("price list for boilers")
        (root / "private" / "b.md").write_text("secret client file")
        (self.home / "config.json").write_text(json.dumps({"exclude_paths": [str(root / "private")]}))
        out = run(self.home, "doc", "add", str(root))
        self.assertIn("1 document(s) added", out)
        self.assertIn("1 skipped", out)
        self.assertIn("no matches", run(self.home, "search", "secret"))

    def test_rescore_after_profile_change(self):
        con = self._load()
        new = json.loads(json.dumps(PROFILE))
        new["filter"]["terms"].append({"term": "import duty", "weight": 4})
        p = Path(self.tmp.name) / "p2.json"
        p.write_text(json.dumps(new))
        run(self.home, "profile", "save", str(p), "--rescore")
        n = con.execute("SELECT count(*) FROM items WHERE status='relevant'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_rescore_updates_noted_items(self):
        # noted items must be rescored too, because score sets note order in the report
        ids = {}
        for url, title in (("https://ex.com/boil", "Boiler news"), ("https://ex.com/hp", "Heat pump news")):
            run(self.home, "add-item", "--url", url, "--title", title)
            con = intel.connect(self.home)
            ids[title] = con.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()["id"]
            nf = Path(self.tmp.name) / "n.json"
            nf.write_text(json.dumps({"item_id": ids[title], "means": "Means " + title, "impact": "opportunity",
                                      "urgency": "now", "confidence": "high"}))
            run(self.home, "note", str(nf))
        md = Path(run(self.home, "report", "--days", "3650").strip()).read_text()
        self.assertLess(md.index("Means Heat pump news"), md.index("Means Boiler news"))
        new = json.loads(json.dumps(PROFILE))
        new["filter"]["terms"] = [{"term": "heat pump", "weight": 3}, {"term": "boiler", "weight": 5}]
        p = Path(self.tmp.name) / "p3.json"
        p.write_text(json.dumps(new))
        run(self.home, "profile", "save", str(p), "--rescore")
        con = intel.connect(self.home)
        row = con.execute("SELECT score, status FROM items WHERE id=?", (ids["Boiler news"],)).fetchone()
        self.assertEqual(row["score"], 10)
        self.assertEqual(row["status"], "noted")  # rescoring never un-notes an item
        md = Path(run(self.home, "report", "--days", "3650").strip()).read_text()
        self.assertLess(md.index("Means Boiler news"), md.index("Means Heat pump news"))

    def test_field_test_fixes(self):
        # term + entity with the same phrase count once
        flt = {"terms": [{"term": "Boiler Upgrade Scheme", "weight": 5}], "entities": ["boiler upgrade scheme"]}
        s, hits, _ = intel.score_item("Boiler Upgrade Scheme cut?", "", flt)
        self.assertEqual(s, 10)
        # a spaced term matches slash or hyphen forms
        s, _, _ = intel.score_item("GBP/USD slips", "", {"terms": [{"term": "GBP USD", "weight": 3}]})
        self.assertEqual(s, 6)
        # an old item noted this week appears in this week's report, with its extra sources
        run(self.home, "add-item", "--url", "https://ex.com/old", "--title", "Heat pump grant guide",
            "--published", "2026-06-01")
        con = intel.connect(self.home)
        iid = con.execute("SELECT id FROM items WHERE url='https://ex.com/old'").fetchone()["id"]
        nf = Path(self.tmp.name) / "n.json"
        nf.write_text(json.dumps({"item_id": iid, "means": "Old but matters now.", "impact": "threat",
                                  "urgency": "now", "confidence": "medium",
                                  "sources": ["https://ex.com/second"]}))
        run(self.home, "note", str(nf))
        md = Path(run(self.home, "report", "--days", "7").strip()).read_text()
        self.assertIn("Old but matters now.", md)
        self.assertIn("https://ex.com/second", md)
        # --keep rescues an item that was already stored and dropped
        run(self.home, "add-item", "--url", "https://ex.com/z", "--title", "Unrelated headline")
        self.assertIn("now kept as relevant",
                      run(self.home, "add-item", "--url", "https://ex.com/z", "--title", "x", "--keep"))
        # bad sources rejected
        nf.write_text(json.dumps({"item_id": iid, "means": "x", "impact": "none", "urgency": "later",
                                  "confidence": "low", "sources": ["not a url"]}))
        with self.assertRaises(SystemExit):
            run(self.home, "note", str(nf))


if __name__ == "__main__":
    unittest.main()
