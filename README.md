# Stargira Intel Solo

A personal intelligence system for a sole trader, or a very small team that shares one
computer. It runs inside Claude Code on your own Claude subscription.

It does five things:

1. **Learns your business.** A ten-minute interview: what you sell, to whom, where,
   your suppliers, competitors, rules, goals and worries.
2. **Filters the news to you.** It turns your answers into a filter (weighted words,
   named schemes, regulators and suppliers, places, and topics to ignore), reads the
   feeds you choose, and adds a short web search each week.
3. **Says what each item means for you.** For the few items that matter, a note of 2 to 4
   sentences: what happened, then the effect on your customers, costs, suppliers or
   rules, with one next step.
4. **Finds your gaps.** Knowledge gaps (what you need to know and don't) and capability
   gaps (what you need to be able to do and can't yet), each with 2 to 5 steps to close it.
5. **Remembers.** Everything goes into a local, searchable store, so you can ask
   "what do we know about X?" months later.

## What it runs, sends and stores

- **Runs:** one Python script, `engine/intel.py` (Python 3.9 or later, standard library
  only; nothing to `pip install`).
- **Sends:** nothing. The script's only network use is reading the RSS or Atom feeds you
  add. Claude's web searches go through Claude Code's normal search tool.
- **Stores:** one folder on your computer, `~/IntelSolo` by default (change it with the
  `INTEL_SOLO_HOME` environment variable). It holds a SQLite database, your profile, and
  Markdown reports. Delete the folder to remove everything.
- **Reads your files:** only the files or folders you name with `doc add`. Folders listed
  under `exclude_paths` in `config.json` are always skipped.

## Install

From the plugin marketplace in this repo:

```
/plugin install stargira-intel-solo --marketplace kyasuda-stargira/stargira-intel-solo
```

Or, for one session, from a copy of this repo:

```
claude --plugin-dir /path/to/stargira-intel-solo
```

Then say "set up my intel system", or run `/stargira-intel-solo:intel-solo`.

Each week, say "run my weekly intel". Claude fetches, filters, writes the notes, updates
the gaps, and saves a report in `~/IntelSolo/reports/`.

## Example prompts

1. "Set up my intel system. I run a four-person plumbing and heating firm in Leeds."
2. "Run my weekly intel and tell me the three things I should act on."
3. "What gaps do we have before we can start selling into Germany?"
4. "What do we know about the Boiler Upgrade Scheme?"

## Limits

- Search is by keyword (with word stemming), not by meaning. Claude rewords your
  question two or three ways before searching, which narrows the difference.
- The notes are only as good as the sources. For anything costly (a contract, a loan, a
  legal duty), read the source and take proper advice.
- One store per computer login, with no sign-in or per-person access rules.
- Google News and Bing News feeds are not added by default: their terms limit them to
  personal, non-commercial reading.

## When you outgrow it

Intel Solo stays a one-computer tool. It has no sign-in, no per-person access and no
search by meaning, and the sector packs below do not add them.

What Stargira plans to sell is sector packs: a ready-made start for one trade. A pack
holds a tuned filter, a list of feeds tested to load, and gap starters (gaps that firms
in that trade often have). It is a file you load into this plugin, so your data still
stays on your computer. Packs are not on sale yet. See stargira.com for news of them.

## Run the tests

```
python3 -m unittest discover tests
```

## Support

Email support@stargira.com with questions or problems.

## Licence

Apache License 2.0. See `LICENSE`. Copyright 2026 Stargira Advisors Ltd.
