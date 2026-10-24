You are debugging a Hermes Agent Desktop sidebar plugin written in PLAIN JS (no JSX transform; it must use jsx()/jsxs() from react/jsx-runtime).

## CONFIRMED SYMPTOM (from screenshot)
The plugin page renders, but FOUR things are wrong:
1. "Projects in rotation" shows: "Nothing yet - open the picker and tick some repos." -- while the picker on the SAME page says "3 projects selected", and the backing script really does return 3 projects. So the table renders EMPTY.
2. A RED ERROR BOX shows "unexpected output:" followed by a fragment that looks like the MIDDLE of a JSON blob, e.g.:
   unexpected output: s_rmg verbatim, ambience_chatter inline) into world.seeded.seeded_rmg; gate 180 pass/3 skip; commit 9bdf86("", "duration": "1.6s", "note": "", "kind": "code", "icon": "lu83dudc40", "text": "import re, glob, ast, collections", ...
3. "Chain of thought" says "nothing in flight / No round in flight" even though the loop IS running and the worker log is fresh (log_age_s ~45).
4. "Loop settings" and "Discovery" render CORRECT values.

## DATA PATH
The plugin calls host.requestProfile(route, "shell.exec", {command}, timeoutMs) and JSON.parses stdout.
All four commands verified from a normal shell to return PURE JSON:
  activity.py               -> {"ts":..,"running":true,"projects":[...],"rows":[...],"cot":{...}}
  discover.py report --json -> {"stats":{...}}
  loopctl.py status --json  -> {"running":..,"projects":[..],"rounds_done":..}
  checkpoint.py all         -> {...}

activity.py rows:
  {"name":"tombs-and-tyrants","never_rotated":false,"active_task":"t_42fa5ba4","active_status":"running","elapsed":"1.7h","open_cards":1,"rounds":3,"running":true}
activity.py cot:
  {"steps":[{"kind":"think","icon":"..","text":"..","duration":"","note":""},...],"task":"t_42fa5ba4","project":"tombs-and-tyrants","steps_total":238,"log_age_s":45,"log_bytes":73772,"log_path":"C:\\..."}
NOTE: there is NO "live" key in cot.

## YOUR JOB
Read the attached plugin.js (and cot-card.js = the CoT region). Find the ROOT CAUSE of ALL FOUR symptoms. Be specific: quote exact lines, explain WHY each fails.

Focus on:
- How rows/actRows is derived; whether a renamed/stale variable is read (e.g. act0 vs act0.data).
- Which hook emits "unexpected output:", and why a JSON blob would appear truncated mid-string with a partial object appended.
- Whether cot.live is ever populated.
- Any property read that does not exist on the real payload.

Then give the MINIMAL fix per defect. Do NOT rewrite the whole file.
OUTPUT FORMAT: numbered defects, each with (a) exact quoted code, (b) why it breaks, (c) minimal patch.
