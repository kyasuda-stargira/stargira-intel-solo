---
name: intel-solo
description: Build and run a personal business-intelligence system for a sole trader or a very small team sharing one computer. Interviews the owner about the business, builds a news filter from the answers, gathers the week's relevant news, writes a "what this means for you" note on each item, finds knowledge gaps and capability gaps with steps to close each, and keeps everything in a local searchable store (the second brain). Use when the user asks to "set up my intel system", "what does this week's news mean for my business", "run my weekly intel", "what am I missing", "find my gaps", "search my intel", or runs /intel-solo.
---

# Intel Solo

This skill drives a small engine that lives in the same repo as this file. Below,
`<repo>` means the repo root: `${CLAUDE_PLUGIN_ROOT}` when this skill is installed as a
plugin, otherwise the folder two levels above this `SKILL.md`. Run the engine as
`python3 <repo>/engine/intel.py <command>`. Do not rewrite or copy the engine.

Needs Python 3.9 or later. No packages to install. All data stays in one folder on this
computer: `$INTEL_SOLO_HOME`, or `~/IntelSolo` if that is not set. The engine's only
network calls fetch the feeds the user has added. Your own web searches run through
Claude Code's search tool, as usual.

## The job, in one line

Turn the week's news into a short list of "this matters to you because..., so do...",
and keep a running list of what the business does not yet know or cannot yet do, with
a way to close each gap.

## Rules

1. **Only claim what the source says.** Every fact in a note must appear in the item's
   own page, title or summary, or in another page you opened, listed in the note's
   `sources`. If you did not open the item's page, say "from the headline" in the note.
   Never invent figures, dates or names, and never carry a fact over from a page you
   do not list.
2. **Write for the owner, not an analyst.** Short sentences. Name the effect on *their*
   customers, costs, prices, staff, suppliers or rules. Define any jargon once.
3. **Fewer, better notes.** Write notes only for items that change what the owner might
   do. Mark the rest `none` impact or leave them unread. Five good notes beat twenty thin ones.
4. **A gap must be specific and closable.** "Learn about AI" is not a gap. "No way to
   quote heat-pump jobs, so grant-funded leads are lost" is. Each gap gets 2 to 5
   concrete steps, cheapest first. Name a course, body, scheme, product or helpline only
   if a page you opened this session shows it exists; otherwise describe the kind of
   thing ("a trade quoting app") and add "check options" to the step.
5. **The engine is the memory.** Save every note and gap through the engine, so the
   owner can search them later. Never keep the only copy in the chat.
6. **Stay in the store.** Add documents only when the user names them. Never index a
   whole home folder or another person's files. Respect `exclude_paths` in
   `config.json`.

## First run: onboarding (about 10 minutes)

Check `python3 <repo>/engine/intel.py status`. If there is no store, run `init`.

### 1. Interview

Ask these in two or three rounds, not all at once. Skip anything the user already said.

1. What do you sell, and to whom? Who are your three biggest kinds of customer?
2. Where do you work (towns, regions, countries)? Online, in person, or both?
3. How big is the business (people, rough yearly turnover band)?
4. Who do you buy from? Which suppliers, materials or countries matter most?
5. Who are your main competitors (names, or the kind of firm)?
6. Which rules, licences, schemes or regulators affect you?
7. What are you trying to do in the next 12 months (grow, add a service, hire, export)?
8. What worries you most right now?
9. What are you good at? What do you wish you or your team could do but cannot yet?
10. What news would be a waste of your time? (This builds the exclusions.)

### 2. Build the profile and filter

Write a JSON file in the store's `inbox/` folder with this shape, then save it:

```json
{
  "business": {
    "name": "...", "what_we_sell": "...", "customers": "...", "where": "...",
    "size": "...", "suppliers": "...", "competitors": "...", "rules": "...",
    "goals_12m": "...", "worries": "...", "strengths": "...", "capabilities_lack": "..."
  },
  "filter": {
    "terms": [{"term": "heat pump", "weight": 3}],
    "entities": ["Boiler Upgrade Scheme", "Gas Safe Register"],
    "regions": ["Leeds", "West Yorkshire"],
    "exclude": ["football"],
    "threshold": 3
  }
}
```

```
python3 <repo>/engine/intel.py profile save <file>
```

How to build the filter:
- **terms** (15 to 40): the products, services, materials, customer types, costs and
  rules the owner named, plus the trade words a news headline would use for them.
  Weight 5 = always matters (their core product or a rule that binds them), 3 = usually
  matters, 1 = context. Use the singular; the engine also matches a plural "s".
  Avoid single short words that appear in unrelated news ("gas", "tax", "price") unless
  paired: "gas boiler", "import VAT". Broad phrases cause the same trouble: "building
  regulations" pulls in every part of the regulations; name the parts that matter
  ("Part L", "Part J").
- **entities**: named schemes, regulators, suppliers, competitors, trade bodies. Each
  counts as weight 4. Two traps: (1) do not list the publisher of a feed you add as an
  entity ("HMRC" with an HMRC feed makes every item from that feed relevant); (2) pair
  names that are also common words ("Amazon seller", "Amazon FBA", not "Amazon").
- **regions**: places they work. A region alone never makes an item relevant; it only
  adds a point to an item that already matched.
- **exclude**: topics the owner called a waste of time.
- **threshold**: 3 to start. Raise it if the queue is flooded; lower it if it is empty.
- To change the filter later, edit and save again, putting the flag after the file:
  `profile save <file> --rescore`.

### 3. Add sources

Feeds the engine reads (RSS or Atom). Pick 6 to 15:
- **GOV.UK organisation feeds** (UK businesses; Open Government Licence): each
  department or regulator has a feed at
  `https://www.gov.uk/government/organisations/<name>.atom`, for example
  `https://www.gov.uk/government/organisations/hm-revenue-customs.atom`. Add the two to
  four bodies that regulate the business; the filter picks out the relevant items. Do
  not use GOV.UK search feeds with `?keywords=`: they ignore the keywords and return
  all recent GOV.UK news.
- Expect routine reference pages from these feeds (code lists, manuals, data tables).
  If they keep scoring, add their tell-tale words to `exclude` ("Data Element",
  "manual", "transparency data").
- **Trade press and trade bodies** for the sector. Find their feed links with a web
  search and check each loads before adding it.
- **General business news**, for example `https://feeds.bbci.co.uk/news/business/rss.xml`.
- Feeds the owner already reads.

Do not add Google News or Bing News search feeds by default: their terms limit use to
personal, non-commercial reading. If the owner wants them, tell them that first.

```
python3 <repo>/engine/intel.py source add <url> --label "<short name>"
```

### 4. Seed the second brain (optional)

If the owner has price lists, service descriptions, past proposals or notes in plain
text or Markdown, add them by name: `doc add <file-or-folder>`. Ask before adding a
folder. These let you check news against what the business actually offers.

Then run the first weekly cycle below.

## Weekly cycle (the main use)

1. **Fetch:** `python3 <repo>/engine/intel.py fetch --days 7`. The engine stores new
   items and scores them against the filter.
2. **Web sweep:** feeds miss things. Run 3 to 6 web searches on the owner's top-weight
   terms and entities for the past week. Store each useful result:
   `add-item --url <u> --title "<t>" --summary "<one or two sentences from the result>" --published <date> --source "<site>"`.
   Add `--keep` only if the item clearly matters but the filter would drop it (it also
   works on an item already stored), and then fix the filter: that is a filter fault.
   Check the date of every search result; search engines return old pages too.
3. **Read the queue:** `queue --limit 15`. For each item, decide: does this change what
   the owner might do in the next 3 months? Open the page when the headline alone is
   not enough to say what it means for them, and when you plan to rely on a figure.
4. **Write notes.** One JSON object per item (a list is fine), saved with
   `note <file>`:

   ```json
   {
     "item_id": 12,
     "means": "2 to 4 sentences: what happened, then what it means for this business.",
     "impact": "opportunity | threat | watch | none",
     "urgency": "now | this_month | later",
     "confidence": "high | medium | low",
     "action": "One concrete next step, or empty for none.",
     "sources": ["any other page you opened and used, as a URL"],
     "gaps": [
       {"kind": "knowledge | capability", "title": "...", "why": "...",
        "close_steps": ["...", "..."], "effort": "hours | days | weeks | months"}
     ]
   }
   ```

   - `means`: the first sentence says what happened (from the source). The rest says
     what it means for *this* business, using the profile. Name the customer, cost,
     supplier or rule affected.
   - `confidence`: high = you read the source page and the link to the business is
     direct; medium = headline and summary only, or the link takes one step of
     reasoning; low = a guess worth watching.
   - Mark an item `none` if, on reading, it does not matter. That teaches you the filter
     needs tightening.
   - If a page will not open (an access error), write the note from the headline and
     summary, say so, and set confidence to `low`.
5. **Find gaps.** After the notes, read the profile's `goals_12m`, `worries`,
   `strengths` and `capabilities_lack`, the week's notes, and `gap list`. Ask two questions:
   - **Knowledge gap:** what would the owner need to *know* to act on this week's news,
     that nothing in the store answers? Check with `search` first; if the store already
     answers it, it is not a gap.
   - **Capability gap:** what would the business need to *be able to do* (a skill, a
     certificate, a tool, a supplier, a process, a person) to take the opportunity or
     handle the threat?
   Record each with `gap add <file>` (same gap fields as above, plus
   `"evidence_item_ids": [..]`). Re-using an existing title merges the evidence instead
   of making a duplicate. Close finished gaps: `gap set <id> --status closed`.
6. **Report:** `report --days 7` writes a Markdown report into `reports/` and prints
   its path. It includes every note written in the period, even on an older item. Give the owner the five most important lines in chat, then the path.

## Answering questions from the store

For "what do we know about X" or "what did we say about Y":
1. Write the question 2 to 3 other ways (synonyms, trade terms), then run
   `search "<question>" --also "<wording 2>" --also "<wording 3>"`. This makes up for
   the store having keyword search only.
2. Read the hits and answer, citing each fact as `item:12`, `note:12`, `gap:3` or
   `doc:4`. If the store does not answer it, say so, and offer a web search.

## Tuning

- **Too much noise:** raise `threshold`, or move noisy words into `exclude`, then
  `profile save <file> --rescore`.
- **Missing things:** add the missing words or names as terms or entities; add a feed.
- **A source keeps failing:** `source list` shows the last error; remove it with
  `source remove <id>`.

## Limits to tell the user about

- Search is keyword-based (with word stemming), not meaning-based. The rewording step
  above narrows the difference.
- Notes are as good as the sources. On anything costly (a contract, a loan, a legal
  duty), the owner should read the source and take proper advice.
- One store per computer user. A small team can share it by sharing that login or
  pointing `INTEL_SOLO_HOME` at a shared folder; there is no sign-in or per-person access.
- Teams that need several people, shared access rules, or their own documents searched
  by meaning outgrow this tool. Stargira plans paid sector packs for single trades (a
  tuned filter, tested feeds and gap starters); they are not on sale yet and add none of
  those features. See stargira.com.
