# -*- coding: utf-8 -*-
"""
🕵️ Pediatric Dark Pattern Audit  (Streamlit backend preserved from original Colab implementation)
=====================================================
Scans decoded Android layout XML files (from one or more apps, packed in .zip
files) for deceptive design ("dark patterns") that target children.

HOW TO USE IN COLAB
  1. Paste this whole file into ONE cell (or upload it and run:  %run file.py)
  2. Run the cell, then click "Choose files" and pick your .zip(s).
     No zip handy?  Cancel the upload and a small DEMO set is audited instead.

OUTPUTS
  - An interactive, parent-friendly dashboard shown right in the notebook
  - pediatric_audit_master_report.csv   (Excel / Power BI friendly)
  - pediatric_audit_report.html         (shareable, open in any browser)

NOTE: the zip must contain DECODED XML (e.g. produced with `apktool d app.apk`).
Raw binary XML straight from an .apk cannot be read and is skipped with a warning.
"""

import os
import re
import sys
import csv
import html
import shutil
import zipfile
import tempfile
import datetime
from collections import defaultdict

import xml.etree.ElementTree as _StdET
try:                                    # safer parser if available (preinstalled on most setups)
    from defusedxml import ElementTree as ET
except Exception:                       # pragma: no cover
    ET = _StdET

try:
    from IPython.display import display, HTML
except Exception:                       # pragma: no cover
    display = None
    HTML = None

try:
    from google.colab import files as colab_files
    IN_COLAB = True
except Exception:
    colab_files = None
    IN_COLAB = False

# ─────────────────────────────────────────────────────────────────────────────
# 1. SETTINGS (safe to tweak)
# ─────────────────────────────────────────────────────────────────────────────
CSV_NAME = "pediatric_audit_master_report.csv"
HTML_NAME = "pediatric_audit_report.html"
MAX_FILE_BYTES = 5 * 1024 * 1024        # skip absurdly large XML files
STRICT_CLOSE_ONLY = True                # DP-04: only flag tiny buttons that look like close/skip/dismiss
RATING_COUNTS = "categories"            # "categories" = distinct pattern types | "occurrences" = every hit
AUTO_DOWNLOAD = False                    # in Colab, download CSV + HTML automatically

# ─────────────────────────────────────────────────────────────────────────────
# 2. THE 15 RULES  (exactly as specified)
# ─────────────────────────────────────────────────────────────────────────────
RULES = {
    "DP-01": {"name": "Pop-Up Advertising Interruptions", "severity": "High",
              "keywords": ["promotional banner ad", "popup ad", "interstitial", "sponsored ad"]},
    "DP-02": {"name": "Coercive Rating & Review Nags", "severity": "Low",
              "keywords": ["rate us", "five stars", "enjoying the app", "leave feedback"]},
    "DP-03": {"name": "Monetized Upgrade Nags", "severity": "High",
              "keywords": ["unlock full version", "go ad-free", "upgrade now", "premium membership"]},
    "DP-04": {"name": "Micro-Dismiss / Hidden Close", "severity": "Medium",
              "tags": ["Button", "ImageView", "ImageButton"],
              "attributes": ["layout_width", "layout_height"],
              "values": ["10dp", "15dp", "20dp"]},
    "DP-05": {"name": "Obfuscated Virtual Currency", "severity": "High",
              "keywords": ["gems", "coins", "tokens", "stars", "credits", "elixir", "crystals"]},
    "DP-06": {"name": "Roach Motel / Account Deletion Friction", "severity": "Medium",
              "keywords": ["call support", "wait 30 days", "verify via email", "contact agent"]},
    "DP-07": {"name": "Forced Continuity & Free-Trial Traps", "severity": "High",
              "keywords": ["automatically renews", "then $", "free trial", "auto-renew"]},
    "DP-08": {"name": "Sneak into Basket / Forced Add-ons", "severity": "High",
              "keywords": ["added automatically", "bonus item", "gift package added", "auto-selected"]},
    "DP-09": {"name": "Parasocial Character Pressure", "severity": "Medium",
              "keywords": ["please", "don't leave", "help me", "sad", "crying"]},
    "DP-10": {"name": "Manipulative Reward Schedules", "severity": "Medium",
              "keywords": ["streak", "claim daily", "countdown", "limited time", "hourly chest"]},
    "DP-11": {"name": "False Hierarchy / Salience", "severity": "Medium",
              "keywords": ["instant vip upgrade", "best value", "recommended choice"]},
    "DP-12": {"name": "Disguised Ads (Native Blending)", "severity": "High",
              "keywords": ["sponsored content", "adchoices", "promoted posts"]},
    "DP-13": {"name": "Social Pyramid / Viral Coercion", "severity": "Medium",
              "keywords": ["invite friends", "share to unlock", "unlock with contacts"]},
    "DP-14": {"name": "Privacy Zuckering / Default Tracking", "severity": "High",
              "tags": ["CheckBox", "Switch"], "attributes": ["checked"], "values": ["true"],
              "keywords": ["sharing data", "tracking", "default", "personalize"]},
    "DP-15": {"name": "Gamified Grind / Watch-to-Unlock", "severity": "High",
              "keywords": ["watch ad to continue", "pay to skip", "watch video to play"]},
}

# Plain-language explanations shown to parents
PARENT_INFO = {
    "DP-01": "Full-screen or banner ads pop up and interrupt play. Children often tap them by accident, which can send them to other apps or websites.",
    "DP-02": "The app keeps nagging for ratings or reviews. Kids may not realise they can simply say no.",
    "DP-03": "Repeated pushes to buy the “full version” or remove ads, so free play feels incomplete or annoying on purpose.",
    "DP-04": "The close (✕) button is made tiny and hard to tap, so a child gets stuck looking at an ad or offer.",
    "DP-05": "Real money is hidden behind game “gems”, “coins” or similar. Children can’t tell what things really cost.",
    "DP-06": "Deleting an account or cancelling is made slow and awkward (waiting periods, support calls, extra checks).",
    "DP-07": "A “free trial” that quietly turns into a paid subscription which renews automatically.",
    "DP-08": "Extra items get added to a purchase without anyone choosing them.",
    "DP-09": "Cute characters beg, cry or look sad to make a child feel guilty about leaving or not buying.",
    "DP-10": "Daily streaks, countdown timers and timed chests create pressure to keep coming back every day.",
    "DP-11": "The design highlights the most expensive option (“best value”, “recommended”) to steer the choice.",
    "DP-12": "Ads are dressed up to look like normal app content, which children cannot tell apart.",
    "DP-13": "Children are pushed to invite friends or share their contacts in order to unlock things.",
    "DP-14": "Tracking or data-sharing switches are already turned ON before you or your child get to decide.",
    "DP-15": "Progress is blocked until the child watches ads or pays to skip the wait.",
}

# Why a text match counts, in plain words (shown to parents; DP IDs stay in the CSV only)
FOUND_WHY = {
    "DP-01": "This looks like an ad that pops up or sits on top of the app.",
    "DP-02": "The app is asking the child to rate or review it.",
    "DP-03": "The app is pushing a paid upgrade or the removal of ads.",
    "DP-05": "Game money is named here instead of showing real prices.",
    "DP-06": "The wording suggests that leaving or deleting an account is slow or awkward.",
    "DP-07": "The wording points to a trial that turns into automatic payments.",
    "DP-08": "The wording suggests something is added to a purchase automatically.",
    "DP-09": "A character is pleading or acting sad to influence the child.",
    "DP-10": "The wording creates pressure to come back every day or to hurry.",
    "DP-11": "One option is being singled out as the one to pick.",
    "DP-12": "Ad-style wording is used where it may look like normal app content.",
    "DP-13": "The child is being asked to invite friends or share contacts.",
    "DP-15": "Progress is tied to watching an ad or paying.",
}
ELEMENT_WORDS = [("imagebutton", "picture button"), ("imageview", "picture"), ("checkbox", "tick box"),
                 ("switch", "switch"), ("toggle", "switch"), ("textview", "piece of text"),
                 ("button", "button")]

SEV_RANK = {"High": 0, "Medium": 1, "Low": 2, "None": 3}
SEV_STYLE = {   # background, text colour
    "High": ("#c62828", "#ffffff"),
    "Medium": ("#f9a825", "#3e2c00"),
    "Low": ("#cfd8dc", "#37474f"),
}
RATING_STYLE = {   # label, accent, soft background
    "UNSAFE": ("🔴 UNSAFE", "#c62828", "#fdecea"),
    "MILD RISK": ("🟡 MILD RISK", "#f9a825", "#fff8e1"),
    "SAFE": ("🟢 SAFE", "#2e7d32", "#e8f5e9"),
}
RATING_ORDER = {"UNSAFE": 0, "MILD RISK": 1, "SAFE": 2}

ANDROID_NS = "http://schemas.android.com/apk/res/android"
TEXT_ATTRS = ("text", "hint", "contentDescription", "title", "label", "summary",
              "tooltipText", "description", "prompt")
CLOSE_RX = re.compile(r"(close|dismiss|cancel|skip|exit|cross|shut|remove|hide|\bx\b)")
RESOURCE_DIR_PREFIXES = ("values", "drawable", "mipmap", "anim", "animator", "color",
                         "font", "raw", "interpolator", "transition")
JUNK_PATH_PARTS = {"smali", "original", "meta-inf", "__macosx", "build", "kotlin"}
RESERVED_TOP = ("res", "layout", "values", "drawable", "mipmap", "smali", "assets", "lib",
                "original", "unknown", "kotlin", "meta-inf", "xml", "menu", "anim", "color")

DP04_TAGS = {t.lower() for t in RULES["DP-04"]["tags"]} | {
    "appcompatbutton", "appcompatimageview", "appcompatimagebutton", "materialbutton",
    "shapeableimageview", "floatingactionbutton", "imagebutton"}
DP14_TAGS = {t.lower() for t in RULES["DP-14"]["tags"]} | {
    "appcompatcheckbox", "materialcheckbox", "switchcompat", "switchmaterial", "materialswitch",
    "switchpreference", "switchpreferencecompat", "checkboxpreference", "togglebutton"}


# ─────────────────────────────────────────────────────────────────────────────
# 3. SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def esc(x):
    return html.escape(str(x), quote=True)


def normalize_text(s):
    """Make resource-style text comparable (curly quotes, \\' escapes, whitespace)."""
    s = (s or "").replace("\u2019", "'").replace("\u2018", "'").replace("\\'", "'")
    s = s.replace("\\n", " ").replace("\\u0020", " ")
    return re.sub(r"\s+", " ", s).strip()


def compile_keywords(keywords):
    return [(kw, re.compile(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", re.I)) for kw in keywords]


KEYWORD_RULES = {code: compile_keywords(r["keywords"])
                 for code, r in RULES.items() if "keywords" in r and code != "DP-14"}
DP14_KEYWORDS = compile_keywords(RULES["DP-14"]["keywords"])


def app_category(name):
    n = name.lower()
    if "game" in n or "play" in n:
        return "Games"
    if "edu" in n or "learn" in n:
        return "Education"
    if "social" in n or "chat" in n:
        return "Social"
    return "General / Unassigned"


def rate_app(findings):
    """SAFE / MILD RISK / UNSAFE according to the specification."""
    if not findings:
        return "SAFE"
    has_high = any(f["severity"] == "High" for f in findings)
    total = (len({f["code"] for f in findings}) if RATING_COUNTS == "categories" else len(findings))
    return "UNSAFE" if (has_high or total > 2) else "MILD RISK"


def friendly_name(s):
    """sdk_loading_panel / sdkLoadingPanel -> 'sdk loading panel'"""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "")
    return re.sub(r"[_\-\.]+", " ", s).strip().lower()


def element_word(tag):
    t = (tag or "").lower()
    for key, word in ELEMENT_WORDS:
        if key in t:
            return word
    return "screen item"


def friendly_finding(f):
    """Return (where_html, found_html) in everyday language."""
    ex = f.get("extra") or {}
    screen = friendly_name(f["screen"]).capitalize() or "Unnamed"
    kind = element_word(f["tag"])
    item = friendly_name(f["eid"])
    if item:
        where = f"On the <b>“{esc(screen)}”</b> screen, in the {esc(kind)} named <b>“{esc(item)}”</b>"
    else:
        where = f"On the <b>“{esc(screen)}”</b> screen, in a {esc(kind)} that has no name"

    if ex.get("kind") == "dims":
        d = ex.get("dims", {})
        nums = {k: float(v[:-2]) for k, v in d.items()}
        parts = []
        if "layout_width" in nums:
            parts.append(f"{nums['layout_width']:g} wide")
        if "layout_height" in nums:
            parts.append(f"{nums['layout_height']:g} tall")
        found = (f"This close/skip {esc(kind)} is very small — only {' and '.join(parts)} "
                 "(a recommended touch target is about 48dp). A child’s finger may miss it, "
                 "which may make it difficult to close. Sizes are in dp.")
    elif ex.get("kind") == "pre":
        words = ", ".join(f"“{h}”" for h in ex.get("hits", []))
        found = (f"This {esc(kind)} is already switched <b>ON</b> before anyone chooses. "
                 f"It is about: {esc(words)}.")
    else:
        snip = ex.get("snippet", "")
        snip = snip if len(snip) <= 90 else snip[:87] + "…"
        words = ", ".join(f"“{h}”" for h in ex.get("hits", []))
        found = (f"It says “{esc(snip)}”. Possible indicator: {esc(FOUND_WHY.get(f['code'], ''))} "
                 f"<span style='color:#78909c'>(trigger words: {esc(words)})</span>")
    return where, found


def short_tag(tag):
    tag = tag.rsplit("}", 1)[-1]
    return tag.rsplit(".", 1)[-1]


def local_attrs(attrib):
    """{'{ns}text': 'x'} -> {'text': 'x'}  (android: namespace wins on clashes)."""
    out = {}
    for k, v in attrib.items():
        local = k.rsplit("}", 1)[-1].rsplit(":", 1)[-1]
        if k.startswith("{" + ANDROID_NS + "}") or k.startswith("android:") or local not in out:
            out[local] = v
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 4. UNZIP + APP DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────
MAX_ZIP_BYTES = 80 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 150 * 1024 * 1024
MAX_XML_FILES = 6000
MAX_COMPRESSION_RATIO = 250

def safe_extract(zip_path, dest):
    """Extract decoded XML only; enforce file count, size, ratio, and safe paths."""
    base = os.path.realpath(dest)
    count, total = 0, 0
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            if m.is_dir():
                continue
            name = m.filename.replace("\\", "/")
            if name.startswith("__MACOSX/") or os.path.basename(name).startswith("._"):
                continue
            if not name.lower().endswith(".xml"):
                continue
            if m.file_size > MAX_FILE_BYTES:
                continue
            if m.file_size and (m.compress_size == 0 or m.file_size / m.compress_size > MAX_COMPRESSION_RATIO):
                raise ValueError("ZIP contains an unusually compressed XML file")
            if count >= MAX_XML_FILES or total + m.file_size > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("ZIP exceeds safe extraction limits")
            target = os.path.realpath(os.path.join(dest, name))
            if not target.startswith(base + os.sep):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(m) as input_file, open(target, "wb") as output_file:
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            count += 1
            total += m.file_size
    return count


def _is_resource_dir(name):
    n = name.lower()
    return n in RESERVED_TOP or n.startswith(("layout", "values", "drawable", "mipmap", "menu", "xml"))


def discover_apps(root, zip_stem):
    """Return {app_name: folder_path} using sensible heuristics for zip layouts."""
    entries = sorted(os.listdir(root))
    dirs = [d for d in entries if os.path.isdir(os.path.join(root, d)) and not d.startswith((".", "__"))]
    loose_xml = any(f.lower().endswith(".xml") for f in entries)

    if loose_xml or (dirs and all(_is_resource_dir(d) for d in dirs)):
        return {zip_stem: root}                       # zip *is* a single app
    if len(dirs) == 1:                                # maybe a wrapper folder around several apps
        inner = os.path.join(root, dirs[0])
        subs = [d for d in sorted(os.listdir(inner))
                if os.path.isdir(os.path.join(inner, d)) and not _is_resource_dir(d) and not d.startswith(".")]
        if len(subs) >= 2:
            return {s: os.path.join(inner, s) for s in subs}
        return {dirs[0]: inner}
    return {d: os.path.join(root, d) for d in dirs}


def load_resources(app_dir):
    """Read values/*.xml so @string/… and @dimen/… references can be resolved."""
    strings, dimens = {}, {}
    for cur, _, fnames in os.walk(app_dir):
        if not os.path.basename(cur).lower().startswith("values"):
            continue
        for f in fnames:
            if not f.lower().endswith(".xml"):
                continue
            try:
                tree = ET.parse(os.path.join(cur, f))
                for node in tree.getroot():
                    n = node.get("name")
                    if not n:
                        continue
                    if short_tag(node.tag) == "string":
                        strings.setdefault(n, "".join(node.itertext()).strip())
                    elif short_tag(node.tag) == "dimen":
                        dimens.setdefault(n, (node.text or "").strip())
            except Exception:
                continue
    return strings, dimens


def resolve(value, strings, dimens):
    if not value:
        return ""
    v = value.strip()
    if v.startswith("@string/"):
        return strings.get(v.split("/", 1)[1], "")
    if v.startswith("@dimen/"):
        return dimens.get(v.split("/", 1)[1], "")
    return v


# ─────────────────────────────────────────────────────────────────────────────
# 5. THE SCANNER
# ─────────────────────────────────────────────────────────────────────────────
TAG_FALLBACK_RX = re.compile(
    r"<([A-Za-z][\w.\-]*)((?:\s+[\w:.\-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*)\s*/?>")
ATTR_FALLBACK_RX = re.compile(r"([\w:.\-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def iter_elements(data):
    """Yield (tag, attrs) pairs. Falls back to regex if the XML is malformed."""
    try:
        root = ET.fromstring(data)
        for el in root.iter():
            if isinstance(el.tag, str):
                yield short_tag(el.tag), local_attrs(el.attrib), False
        return
    except Exception:
        pass
    text = data.decode("utf-8", errors="replace")
    for m in TAG_FALLBACK_RX.finditer(text):
        attrs = {}
        for a in ATTR_FALLBACK_RX.finditer(m.group(2) or ""):
            local = a.group(1).rsplit(":", 1)[-1]
            attrs.setdefault(local, a.group(2) if a.group(2) is not None else a.group(3))
        yield short_tag(m.group(1)), attrs, True


def tiny_dp(value):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:dp|dip)\s*", value or "")
    return f"{float(m.group(1)):g}dp" if m else None


def scan_file(path, rel, app, category, strings, dimens, stats):
    findings, seen = [], set()
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception:
        stats["unreadable"] += 1
        return findings
    if data[:4] == b"\x03\x00\x08\x00":              # binary AXML
        stats["binary"] += 1
        return findings
    screen = os.path.splitext(os.path.basename(rel))[0]
    stats["files"] += 1

    def add(code, details, tag, eid, idx, **extra):
        key = (code, idx)
        if key in seen:
            return
        seen.add(key)
        rule = RULES[code]
        loc = f"{rel} › {tag}" + (f"#{eid}" if eid else f" (element {idx})")
        findings.append({"app": app, "category": category, "code": code, "name": rule["name"],
                         "severity": rule["severity"], "details": details,
                         "location": loc, "screen": screen,
                         "tag": tag, "eid": eid, "extra": extra})

    try:
        for idx, (tag, attrs, fallback) in enumerate(iter_elements(data)):
            if fallback and idx == 0:
                stats["fallback"] += 1
            eid = attrs.get("id", "").split("/")[-1]
            tl = tag.lower()

            # visible text fields (resolved, normalised)
            texts = []
            for a in TEXT_ATTRS:
                if a in attrs:
                    val = normalize_text(resolve(attrs[a], strings, dimens) if attrs[a].startswith("@")
                                         else attrs[a])
                    if val:
                        texts.append((a, val))

            # ── keyword rules ──
            for code, rx_list in KEYWORD_RULES.items():
                hits, snippet = [], ""
                for kw, rx in rx_list:
                    for a, val in texts:
                        if rx.search(val):
                            hits.append(kw)
                            snippet = snippet or val
                            break
                if hits:
                    kws = ", ".join(f"'{k}'" for k in hits)
                    snip = snippet if len(snippet) <= 80 else snippet[:77] + "…"
                    add(code, f"Matched keyword {kws} in text: “{snip}”", tag, eid, idx,
                        kind="kw", hits=hits, snippet=snippet)

            # ── DP-04: tiny close buttons ──
            if tl in DP04_TAGS:
                dims = {}
                for a in RULES["DP-04"]["attributes"]:
                    n = tiny_dp(resolve(attrs.get(a.replace("layout_", "layout_"), ""), strings, dimens))
                    if n in RULES["DP-04"]["values"]:
                        dims[a] = n
                if dims:
                    hint = " ".join([eid, attrs.get("contentDescription", ""), attrs.get("text", ""),
                                     attrs.get("src", ""), attrs.get("srcCompat", "")])
                    hint = re.sub(r"[_\-/@.]", " ", re.sub(r"([a-z])([A-Z])", r"\1 \2", hint)).lower()
                    if (not STRICT_CLOSE_ONLY) or CLOSE_RX.search(hint):
                        what = " and ".join(f"{k} is set to tiny dimension ({v})" for k, v in dims.items())
                        add("DP-04", f"{what}; comfortable touch size is ~48dp", tag, eid, idx,
                            kind="dims", dims=dims)

            # ── DP-14: pre-ticked tracking switches ──
            if tl in DP14_TAGS:
                pre = (attrs.get("checked", "").lower() == "true"
                       or attrs.get("defaultValue", "").lower() == "true")
                if pre:
                    hay = [v for _, v in texts] + [re.sub(r"[_\-]", " ", eid), re.sub(r"[_\-]", " ", attrs.get("key", ""))]
                    hits = [kw for kw, rx in DP14_KEYWORDS if any(rx.search(h) for h in hay)]
                    if hits:
                        kws = ", ".join(f"'{k}'" for k in hits)
                        add("DP-14", f"Switched ON by default (checked=true) and matched keyword {kws}",
                            tag, eid, idx, kind="pre", hits=hits)
    except Exception:
        stats["unreadable"] += 1
    return findings


def scan_app(app, app_dir):
    category = app_category(app)
    strings, dimens = load_resources(app_dir)
    stats = defaultdict(int)
    findings = []
    for cur, dnames, fnames in os.walk(app_dir):
        dnames[:] = [d for d in dnames if d.lower() not in JUNK_PATH_PARTS]
        rel_dir = os.path.relpath(cur, app_dir).replace("\\", "/")
        parts = [] if rel_dir == "." else rel_dir.split("/")
        if any(p.lower().startswith(RESOURCE_DIR_PREFIXES) for p in parts):
            continue
        for f in sorted(fnames):
            if not f.lower().endswith(".xml") or f == "AndroidManifest.xml":
                continue
            rel = "/".join(parts + [f])
            findings += scan_file(os.path.join(cur, f), rel, app, category, strings, dimens, stats)
    findings.sort(key=lambda x: (SEV_RANK[x["severity"]], x["code"], x["location"]))
    return {"app": app, "category": category, "findings": findings,
            "rating": rate_app(findings), "stats": dict(stats)}


# ─────────────────────────────────────────────────────────────────────────────
# 6. REPORTING (CSV + HTML)
# ─────────────────────────────────────────────────────────────────────────────
def write_csv(results, path):
    cols = ["AppName", "Category", "ChecklistCode", "PatternName", "Severity",
            "Details", "Location", "SafetyRating"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:     # utf-8-sig: opens cleanly in Excel
        w = csv.writer(fh)
        w.writerow(cols)
        for r in results:
            if not r["findings"]:
                w.writerow([r["app"], r["category"], "", "No violations found", "None",
                            "No flagged design patterns in scanned layouts", "", r["rating"]])
            for f in r["findings"]:
                w.writerow([r["app"], r["category"], f["code"], f["name"], f["severity"],
                            f["details"], f["location"], r["rating"]])


def badge(sev):
    bg, fg = SEV_STYLE[sev]
    return (f"<span style='background:{bg};color:{fg};padding:2px 10px;border-radius:12px;"
            f"font-size:12px;font-weight:700;white-space:nowrap'>{sev} severity</span>")


def verdict_text(r):
    n_types = len({f["code"] for f in r["findings"]})
    n_high = len({f["code"] for f in r["findings"] if f["severity"] == "High"})
    if r["rating"] == "SAFE":
        return "No predefined indicators were detected in the XML screens checked; this is not a guarantee of safety."
    if r["rating"] == "MILD RISK":
        return (f"We found {n_types} minor kind{'s' if n_types != 1 else ''} of pressure tactic. "
                "Review the findings to understand the context.")
    extra = f", {n_high} of them involving money, ads or personal data" if n_high else ""
    return (f"We found {n_types} potential indicator categor{'ies' if n_types != 1 else 'y'}{extra}. "
            "Review the findings carefully; these are potential indicators, not proof of harmful behavior.")


def render_card(r):
    label, accent, soft = RATING_STYLE[r["rating"]]
    groups = defaultdict(list)
    for f in r["findings"]:
        groups[f["code"]].append(f)
    ordered = sorted(groups.items(), key=lambda kv: (SEV_RANK[RULES[kv[0]]["severity"]], kv[0]))

    parts = [f"""
<div style="background:#fff;border:1px solid #e0e0e0;border-left:8px solid {accent};border-radius:12px;
            padding:18px 20px;margin:16px 0;box-shadow:0 2px 6px rgba(0,0,0,.06)">
  <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between">
    <div>
      <div style="font-size:20px;font-weight:800;color:#1f2933">📱 {esc(r['app'])}</div>
      <div style="margin-top:4px"><span style="background:#eceff1;color:#455a64;padding:2px 10px;
           border-radius:12px;font-size:12px;font-weight:600">{esc(r['category'])}</span>
           <span style="color:#78909c;font-size:12px;margin-left:6px">{r['stats'].get('files', 0)} screens checked</span></div>
    </div>
    <div style="background:{soft};color:{accent};border:2px solid {accent};padding:8px 18px;
         border-radius:24px;font-weight:800;font-size:16px">{label}</div>
  </div>
  <p style="margin:12px 0 4px;color:#37474f;font-size:15px;line-height:1.5">{esc(verdict_text(r))}</p>"""]

    if ordered:
        parts.append(f"<div style='color:#607d8b;font-size:13px;margin-bottom:8px'>"
                     f"{len(ordered)} kind(s) of pattern · {len(r['findings'])} spot(s) in total. "
                     f"Tap a pattern to see exactly where it was found.</div>")
    for code, items in ordered:
        rule = RULES[code]
        n = len(items)
        li = ""
        for k, i in enumerate(items, 1):
            where, found = friendly_finding(i)
            li += (f"<li style='margin:12px 0;padding:10px 12px;background:#fff;border:1px solid #eceff1;"
                   f"border-radius:8px;list-style:none'>"
                   f"<div style='font-weight:700;color:#455a64;font-size:12px;margin-bottom:4px'>EXAMPLE {k} OF {n}</div>"
                   f"<div>📍 <b>Where:</b> {where}</div>"
                   f"<div style='margin-top:4px'>🔎 <b>What we found:</b> {found}</div>"
                   f"<div style='margin-top:6px;color:#90a4ae;font-size:11px'>🔧 For reference: "
                   f"<code style='background:#f1f3f5;padding:1px 4px;border-radius:4px'>{esc(i['location'])}</code></div></li>")
        parts.append(f"""
  <details style="border:1px solid #eceff1;border-radius:8px;padding:10px 14px;margin:8px 0;background:#fafbfc">
    <summary style="cursor:pointer;font-weight:700;color:#263238;font-size:15px">
      {esc(rule['name'])} &nbsp;{badge(rule['severity'])}
      <i style="font-weight:400;color:#78909c;font-size:13px"> ({n} occurrence{'s' if n != 1 else ''} detected)</i>
    </summary>
    <div style="margin-top:10px;padding:10px 12px;background:#fff;border-radius:8px;border:1px dashed #cfd8dc;
                color:#37474f;font-size:14px;line-height:1.5">
      <b>👪 What this means for your child:</b> {esc(PARENT_INFO[code])}</div>
    <ul style="padding:0;margin:10px 0 2px;color:#263238;font-size:14px;line-height:1.5">{li}</ul>
  </details>""")
    notes = []
    if r["stats"].get("binary"):
        notes.append(f"{r['stats']['binary']} file(s) were unreadable binary XML (decode the APK with apktool first)")
    if r["stats"].get("fallback"):
        notes.append(f"{r['stats']['fallback']} file(s) had formatting problems and were read in a simpler way")
    if r["stats"].get("unreadable"):
        notes.append(f"{r['stats']['unreadable']} file(s) could not be read")
    if notes:
        parts.append(f"<div style='color:#8d6e63;font-size:12px;margin-top:8px'>⚠️ {esc('; '.join(notes))}.</div>")
    parts.append("</div>")
    return "".join(parts)


def tile(num, label, color):
    return (f"<div style='flex:1;min-width:120px;background:#fff;border-radius:12px;padding:14px;"
            f"text-align:center;border-top:5px solid {color};box-shadow:0 1px 4px rgba(0,0,0,.08)'>"
            f"<div style='font-size:30px;font-weight:800;color:{color}'>{num}</div>"
            f"<div style='font-size:13px;color:#546e7a'>{label}</div></div>")


def build_html(results):
    counts = {k: sum(1 for r in results if r["rating"] == k) for k in RATING_STYLE}
    total_files = sum(r["stats"].get("files", 0) for r in results)
    now = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
    cards = "".join(render_card(r) for r in sorted(
        results, key=lambda r: (RATING_ORDER[r["rating"]], -len(r["findings"]), r["app"].lower())))

    legend = "".join(
        f"<li style='margin:4px 0'><b style='color:{RATING_STYLE[k][1]}'>{RATING_STYLE[k][0]}</b> – {t}</li>"
        for k, t in [("UNSAFE", "a high-severity indicator or at least 3 counted indicators were detected."),
                     ("MILD RISK", "1–2 counted indicators were detected, with no high-severity indicator."),
                     ("SAFE", "no predefined indicators were detected in the checked XML.")])
    advice = ""
    if counts["UNSAFE"] or counts["MILD RISK"]:
        advice = """
  <div style="background:#e3f2fd;border-radius:12px;padding:14px 18px;margin:16px 0;color:#0d47a1;font-size:14px;line-height:1.6">
    <b>💡 What you can do:</b> turn on a password/fingerprint for purchases in your app store, switch off
    personalised ads in your child’s device settings, check subscriptions regularly, and talk to your child about
    why games sometimes “beg” or rush them.</div>"""

    return f"""
<div style="font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f4f6f8;
            color:#263238;padding:22px;border-radius:16px;max-width:980px">
  <div style="font-size:26px;font-weight:800">🕵️ Children’s App Safety Report</div>
  <div style="color:#607d8b;font-size:13px;margin:4px 0 16px">Generated {now} · automated scan of app screen layouts</div>
  <div style="display:flex;flex-wrap:wrap;gap:12px">
    {tile(len(results), "apps scanned", "#455a64")}
    {tile(counts["UNSAFE"], "🔴 unsafe", "#c62828")}
    {tile(counts["MILD RISK"], "🟡 mild risk", "#f9a825")}
    {tile(counts["SAFE"], "🟢 safe", "#2e7d32")}
    {tile(total_files, "screens checked", "#6d4c41")}
  </div>
  <div style="background:#fff8e1;border-left:6px solid #f9a825;border-radius:12px;padding:12px 18px;margin:16px 0;
              font-size:14px;color:#4e342e;line-height:1.5">
    <b>💡 What are “dark patterns”?</b> Design features that may push children into spending money, sharing
    data, watching ads or staying longer than intended. This tool flags possible indicators in decoded app screens.</div>
  <div style="background:#fff;border-radius:12px;padding:12px 18px;margin:16px 0;font-size:14px;color:#37474f;line-height:1.5">
    <b>How to read this report</b><ul style="padding-left:20px;margin:6px 0 0">{legend}</ul>
    <div style="margin-top:8px;color:#546e7a">Each trick lists <b>examples</b>: <b>📍 Where</b> = the app screen and the
    exact button, picture or text involved; <b>🔎 What we found</b> = what is wrong with it, in plain words.</div></div>
  {advice}
  {cards}
  <div style="color:#78909c;font-size:12px;line-height:1.5;margin-top:14px">
    ℹ️ This is an automated check of the app’s screen designs only. It cannot see ads loaded from the internet,
    in-game events, or purchases decided on a company’s servers, so “SAFE” means “nothing found”, not a guarantee.
    A match on a word (for example “stars” or “please”) can occasionally be harmless — the exact text is shown so you can judge.</div>
</div>"""




# ─────────────────────────────────────────────────────────────────────────────
# STREAMLIT APPLICATION LAYER
# ─────────────────────────────────────────────────────────────────────────────
import io
import hashlib
import streamlit as st

st.set_page_config(page_title="Children's App Safety Audit", page_icon="🛡️", layout="wide", initial_sidebar_state="collapsed")

APP_CSS = """
<style>
.stApp {background: #f5f7fb; color:#1c2940;}
.block-container {max-width:1150px; padding-top:2rem; padding-bottom:3rem;}
.hero {padding:26px 30px;border-radius:20px;background:linear-gradient(115deg,#17324e,#276a78);color:white;margin-bottom:20px;}
.hero h1 {color:white;font-size:2.05rem;margin:0 0 6px;}
.hero p {color:#e0eef2;margin:0;}
.info-panel {background:#fff;border:1px solid #e4eaf0;border-radius:14px;padding:18px 22px;margin:12px 0;line-height:1.65;}
.status {padding:10px 14px;border-radius:12px;font-weight:700;display:inline-block;margin:6px 0;}
.status-safe {background:#e4f6e8;color:#166534;}
.status-mild {background:#fff3d4;color:#875500;}
.status-unsafe {background:#fee5e5;color:#a21b27;}
.small-note {font-size:.88rem;color:#586879;}
div[data-testid="stMetric"] {background:white;border:1px solid #e4eaf0;border-radius:14px;padding:15px;}
</style>
"""
st.markdown(APP_CSS, unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>🛡️ Children’s App Safety Audit</h1><p>Check Android apps for design tricks that may affect children.</p></div>', unsafe_allow_html=True)


def create_csv_bytes(results):
    buf = io.StringIO(newline="")
    cols = ["AppName", "Category", "ChecklistCode", "PatternName", "Severity", "Details", "Location", "SafetyRating"]
    writer = csv.writer(buf)
    writer.writerow(cols)
    for r in results:
        if not r["findings"]:
            writer.writerow([r["app"], r["category"], "", "No violations found", "None",
                             "No flagged design patterns in scanned layouts", "", r["rating"]])
        for f in r["findings"]:
            writer.writerow([r["app"], r["category"], f["code"], f["name"], f["severity"],
                             f["details"], f["location"], r["rating"]])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def create_html_bytes(results):
    page = build_html(results)
    doc = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<title>Children’s App Safety Report</title></head>"
           "<body style='margin:0;padding:16px;background:#e9edf1'>" + page + "</body></html>")
    return doc.encode("utf-8")


def audit_uploaded_files(uploaded_files, progress=None):
    results, messages, used_names = [], [], set()
    with tempfile.TemporaryDirectory(prefix="pediatric_streamlit_") as work:
        for index, upload in enumerate(uploaded_files):
            if progress is not None:
                progress.progress(index / max(1, len(uploaded_files)), text=f"Scanning {upload.name}…")
            try:
                if not upload.name.lower().endswith(".zip"):
                    messages.append(f"{upload.name}: only ZIP files are supported.")
                    continue
                raw = upload.getvalue()
                if len(raw) > MAX_ZIP_BYTES:
                    messages.append(f"{upload.name}: ZIP is larger than the {MAX_ZIP_BYTES // 1024 // 1024} MB limit.")
                    continue
                zp = os.path.join(work, f"input_{index}.zip")
                with open(zp, "wb") as f:
                    f.write(raw)
                dest = os.path.join(work, f"unpacked_{index}")
                os.makedirs(dest, exist_ok=True)
                n = safe_extract(zp, dest)
                if n == 0:
                    messages.append(f"{upload.name}: no readable XML entries were found. Decode the APK resources first.")
                    continue
                stem = os.path.splitext(os.path.basename(upload.name))[0]
                for app_name, app_dir in discover_apps(dest, stem).items():
                    result = scan_app(app_name, app_dir)
                    if not result["stats"].get("files", 0):
                        messages.append(f"{upload.name} / {app_name}: no scan-ready layout XML was found (binary or missing layouts may be the cause).")
                        continue
                    unique, suffix = app_name, 2
                    while unique in used_names:
                        unique, suffix = f"{app_name} ({suffix})", suffix + 1
                    used_names.add(unique)
                    result["app"] = unique
                    for finding in result["findings"]:
                        finding["app"] = unique
                    results.append(result)
                    if result["stats"].get("binary"):
                        messages.append(f"{unique}: {result['stats']['binary']} binary XML files were skipped; decode with Apktool.")
                    if result["stats"].get("fallback"):
                        messages.append(f"{unique}: {result['stats']['fallback']} malformed XML files used fallback parsing.")
                    if result["stats"].get("unreadable"):
                        messages.append(f"{unique}: {result['stats']['unreadable']} XML files could not be fully parsed.")
            except (zipfile.BadZipFile, zipfile.LargeZipFile):
                messages.append(f"{upload.name}: invalid or unsupported ZIP archive.")
            except (ValueError, OSError, RuntimeError, EOFError) as exc:
                messages.append(f"{upload.name}: scan skipped ({type(exc).__name__}: {str(exc)[:180]}).")
        if progress is not None:
            progress.progress(1.0, text="Scan finished")
    return results, messages


def display_results(results):
    counts = {rating: sum(r["rating"] == rating for r in results) for rating in ("SAFE", "MILD RISK", "UNSAFE")}
    total = sum(len(r["findings"]) for r in results)
    distinct = sum(len({f["code"] for f in r["findings"]}) for r in results)
    st.subheader("Audit overview")
    row1 = st.columns(3)
    for col, (label, value) in zip(row1, [("Apps scanned", len(results)), ("Raw occurrences", total), ("Distinct categories (across apps)", distinct)]):
        col.metric(label, value)
    row2 = st.columns(3)
    for col, (label, value) in zip(row2, [("🔴 Unsafe", counts["UNSAFE"]), ("🟡 Mild risk", counts["MILD RISK"]), ("🟢 Safe", counts["SAFE"])]):
        col.metric(label, value)
    st.caption("Distinct category count sums the number of different patterns in each app; the same category can appear in more than one app.")
    st.markdown("### App-by-app findings")
    for result in sorted(results, key=lambda r: (RATING_ORDER[r["rating"]], -len(r["findings"]), r["app"].lower())):
        count = len(result["findings"])
        distinct_count = len({f["code"] for f in result["findings"]})
        with st.expander(f"{RATING_STYLE[result['rating']][0]}  ·  {result['app']}  ·  {count} occurrence(s)", expanded=result["rating"] == "UNSAFE"):
            cls = {"SAFE":"safe", "MILD RISK":"mild", "UNSAFE":"unsafe"}[result["rating"]]
            st.markdown(f'<div class="status status-{cls}">{esc(result["rating"])}</div>', unsafe_allow_html=True)
            st.write(verdict_text(result))
            st.caption(f"Category: {result['category']} · XML screens checked: {result['stats'].get('files',0)} · Distinct pattern types: {distinct_count} · Raw occurrences: {count}")
            groups = defaultdict(list)
            for finding in result["findings"]:
                groups[finding["code"]].append(finding)
            for code in sorted(groups, key=lambda c: (SEV_RANK[RULES[c]["severity"]], c)):
                rule, items = RULES[code], groups[code]
                with st.expander(f"{rule['name']} · {rule['severity']} severity · {len(items)} occurrence(s)"):
                    st.markdown("**👪 What this means for your child**")
                    st.write(PARENT_INFO[code])
                    for number, finding in enumerate(items, 1):
                        where, found = friendly_finding(finding)
                        st.markdown(f"**Example {number} of {len(items)}**")
                        st.markdown("**📍 Where:** " + where, unsafe_allow_html=True)
                        st.markdown("**🔎 What we found:** " + found, unsafe_allow_html=True)
                        st.caption("🔧 For reference: " + finding["location"])
                        if number < len(items):
                            st.divider()
            if result["stats"].get("binary") or result["stats"].get("unreadable"):
                st.warning("Some files could not be fully analyzed. See scan notices for details.")
    st.markdown("### Download your reports")
    a, b = st.columns(2)
    a.download_button("⬇️ Download master CSV", create_csv_bytes(results), CSV_NAME, mime="text/csv", use_container_width=True)
    b.download_button("⬇️ Download parent-friendly HTML", create_html_bytes(results), HTML_NAME, mime="text/html", use_container_width=True)
    st.info("These are potential indicators from static XML rules, not proof of deceptive intent. SAFE means no predefined indicators were found in the scanned XML, not that an app is guaranteed safe.")


tutorial, scanner = st.tabs(["📘 Parent Tutorial Guide", "🔍 App Safety Audit Scanner & Dashboard"])
with tutorial:
    st.header("A simple guide: from Android app to safety report")
    st.write("You do not need to know how to code. Follow these steps on a computer, then upload your ZIP file in the Scanner tab.")
    st.markdown('<div class="info-panel"><b>First, what are dark patterns?</b><p>Some apps use design tricks that can push children to spend money, watch more ads, or share information. This tool looks for possible warning signs in the app’s screen files.</p></div>', unsafe_allow_html=True)

    st.subheader("Step 1 · Get the Android app file (APK)")
    st.write("An **APK** is the file used to package an Android app. You need the APK file to inspect the app’s screens. You do **not** need to install the app on your phone.")
    st.markdown("""**Where to look, in this order:**
- **The app developer's official website or GitHub Releases:** best when the developer offers an APK directly.
- **[F-Droid](https://f-droid.org/):** a source for free and open-source Android apps. It won't have every children's app.
- **[APKMirror](https://www.apkmirror.com/):** a third-party APK archive; check the publisher, version, and file details carefully.
- **[APKPure](https://apkpure.com/):** another third-party option, but use extra caution and verify the app and publisher. It is not guaranteed safe.

**Safety reminder:** Avoid random download links, advertisements pretending to be download buttons, and modified or “premium unlocked” APKs. Download only apps you are allowed to inspect. If you cannot find a trustworthy APK, do not download an unknown file just to complete the scan.""")

    st.subheader("Step 2 · Open the APK in JADX")
    st.write("**JADX-GUI** is a desktop program that lets you look inside an Android app file. Get it from the [official JADX GitHub releases](https://github.com/skylot/jadx/releases).")
    st.markdown("""1. Open **JADX-GUI** on your computer.
2. Choose **File → Open** (or drag the APK into the window) and select your `.apk` file.
3. Wait while JADX loads the app.
4. In the folder list on the left, expand **Resources**. Depending on the app and JADX version, you may see **`res` → `layout`** (and folders such as `layout-land` or `layout-v21`).
5. Open a `.xml` file in `layout`. **Can you read text such as `<LinearLayout` or `<Button` and attributes like `android:text`?** If yes, you have readable XML. If the layouts are missing or unreadable, continue to Step 3.""")
    st.info("JADX is useful for **finding and viewing** resources, but exporting files from JADX does not always give the complete, decoded XML structure needed for scanning. For the most reliable ZIP, use Step 3.")

    st.subheader("Step 3 · Decode the screen files with Apktool")
    st.write("**Apktool** is a separate desktop tool that turns many Android resource files into readable folders. This is the recommended way to prepare files for the scanner.")
    st.markdown("""1. Follow the installation instructions on the [official Apktool website](https://apktool.org/). You may need Java installed.
2. Put the APK in a folder on your computer. Rename it to `my_app.apk` to make the next step easier.
3. Open **Terminal** (macOS/Linux) or **Command Prompt / PowerShell** (Windows) in that folder.
4. Run the command below. If Apktool is not recognized, check its installation instructions and PATH setup.""")
    st.code("apktool d my_app.apk -o decoded_app", language="bash")
    st.write("When it finishes, open the new **`decoded_app`** folder. Inside **`res`**, look for **`layout`** (the app's screen layouts) and **`values`** (text and size definitions). Some apps use several `layout-*` folders. Keep them all.")
    st.caption("If the command fails, the APK may be incompatible, incomplete, or protected. The scanner cannot analyze an APK directly; it needs readable XML files.")

    st.subheader("Step 4 · Make your ZIP file")
    st.markdown("""1. Find the **`decoded_app`** folder created in Step 3.
2. Right-click it and choose **Compress / Send to → Compressed (zipped) folder** (the wording depends on your computer).
3. Check that the ZIP contains a path like **`decoded_app/res/layout/`** and **`decoded_app/res/values/`**. Do not zip only a screenshot or the original `.apk`.
4. Save the `.zip` file somewhere you can find it, such as Downloads or Desktop.""")

    st.subheader("Step 5 · Scan and read the report")
    st.markdown("""1. Open the **🔍 App Safety Audit Scanner & Dashboard** tab above.
2. Upload the ZIP file you just made.
3. Click **🔍 Scan Apps** and wait for the results.
4. Open the app's result card to read **What this means for your child**, **📍 Where**, and **🔎 What we found**.
5. Use the download buttons to save the report if you want.""")

    st.subheader("What do the results mean?")
    st.markdown("""- 🟢 **SAFE:** the scanner did not find any of its predefined warning signs in the files checked.
- 🟡 **MILD RISK:** it found one or two counted warning signs, with no high-severity finding.
- 🔴 **UNSAFE:** it found three or more counted warning signs, or at least one high-severity finding.

These labels are **screening results, not guarantees**. A green result does not prove an app is safe, and a red result does not prove the developer intended to mislead children.""")
    with st.expander("Optional: What this tool can and cannot check"):
        st.write("The scanner reads Android XML screen files. It cannot see everything that happens while a child uses an app, such as live ads, purchases, tracking, or screens created while the app runs. Some matching words may be harmless, and some problems may not appear in the files. The risk thresholds are rules defined for this research project; they are not a medically validated safety score.")

with scanner:
    st.header("Scan decoded Android layouts")
    st.write("Upload one or more ZIP archives containing decoded `.xml` layout files and, ideally, the matching `res/values` resource files.")
    uploads = st.file_uploader("Choose ZIP files", type=["zip"], accept_multiple_files=True, help="Multiple app folders inside a ZIP are supported.")
    with st.expander("⚙️ Advanced settings (optional — you can leave these unchanged)"):
        st.write("These options are mainly for researchers comparing scan results. **Parents can keep the recommended defaults.**")
        st.markdown("**How should repeated findings affect the app's safety rating?**")
        st.write("Imagine one app has **4 tiny close buttons**, but no other problems:")
        st.markdown("""- **Different pattern types (recommended):** counts this as **1 type** of problem → **MILD RISK**.
- **Every time a problem appears:** counts all **4 buttons** → **UNSAFE**.""")
        new_counts = st.radio(
            "Choose how to count findings for the safety rating",
            ["categories", "occurrences"],
            index=0,
            format_func=lambda v: "Different pattern types (recommended)" if v == "categories" else "Every occurrence (count repeated findings)",
            help="Only the rating calculation changes. The report still shows every detected location either way.",
        )
        st.caption("A single High-severity finding always gives an UNSAFE rating, whichever counting option you choose. All individual findings remain visible and are included in the CSV.")
        st.markdown("**Which small buttons should be flagged?**")
        new_strict = st.checkbox("Only check unusually small close, skip, or dismiss buttons (recommended)", value=True)
        st.write("**Checked:** focuses on tiny buttons used to close or skip something. **Unchecked:** also flags other tiny buttons, which may produce more false alarms.")
        st.caption("These settings apply when you click Scan Apps. They do not delete or rewrite any of the 15 detection rules.")
    if "audit" not in st.session_state:
        st.session_state.audit = None
    if st.button("🔍 Scan Apps", type="primary", disabled=not uploads):
        RATING_COUNTS = new_counts
        STRICT_CLOSE_ONLY = new_strict
        bar = st.progress(0, text="Preparing scan…")
        with st.spinner("Checking XML layouts and resolving resources…"):
            results, messages = audit_uploaded_files(uploads, bar)
        st.session_state.audit = {"results": results, "messages": messages, "counts": new_counts, "strict": new_strict}
    if st.session_state.audit is not None:
        audit = st.session_state.audit
        if audit["messages"]:
            with st.expander(f"Scan notices ({len(audit['messages'])})", expanded=not bool(audit["results"])):
                for message in audit["messages"]:
                    st.warning(message)
        if audit["results"]:
            RATING_COUNTS = audit["counts"]
            STRICT_CLOSE_ONLY = audit["strict"]
            display_results(audit["results"])
        else:
            st.error("No readable app layouts were scanned. Please upload decoded Android XML layouts, for example from Apktool.")
    else:
        st.info("Choose your ZIP file(s) and click Scan Apps to see your dashboard.")
