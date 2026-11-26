"""The evaluation questionnaire: what is asked, how answers are checked, and how
they are turned into numbers for a report or a paper.

No web code lives here. app.py passes in a submitted form and gets back
cleaned answers, and passes in stored rows and gets back summary figures.
Keeping it separate makes the questionnaire easy to read, change and test.

The usability block is the System Usability Scale (SUS), a ten item
questionnaire published by John Brooke in 1996 and widely used in software
evaluation. Its scoring is fixed: odd items score (answer minus 1), even items
score (5 minus answer), the total is multiplied by 2.5 to give 0 to 100.
Cite it as: Brooke J. SUS: a quick and dirty usability scale. In: Usability
Evaluation in Industry. London: Taylor and Francis; 1996. pp 189-194.
"""
import json
import statistics

MAX_TEXT = 2000

AGREE = ("Strongly disagree", "Strongly agree")


def _choices(*labels):
    """Choices whose stored value is a short, stable key made from the label."""
    out = []
    for label in labels:
        if isinstance(label, tuple):
            out.append(label)
        else:
            key = "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_")
            while "__" in key:
                key = key.replace("__", "_")
            out.append((key, label))
    return out


# Legacy quick fields, kept exactly as they were so older rows still read.
EASE_CHOICES = [
    ("very_easy", "Very easy"), ("easy", "Easy"), ("ok", "OK"),
    ("difficult", "Difficult"), ("very_difficult", "Very difficult"),
]
FEATURE_CHOICES = [
    ("catalog", "Strain catalog and search"),
    ("upload_3d", "Plate upload and 3D models"),
    ("viability", "Viability and revival tracking"),
    ("gram_stain", "Gram stain tracking"),
    ("export", "Data export"),
    ("other", "Something else"),
]

# Features rated for usefulness in section D.
FEATURE_RATING_ITEMS = [
    ("f_catalog", "Strain catalog and search"),
    ("f_species", "Strain pages (details, accessions, tags)"),
    ("f_storage", "Storage location and storage map"),
    ("f_labels", "Printed QR labels"),
    ("f_revival", "Revival tracking and due dates"),
    ("f_gram", "Gram stain history"),
    ("f_plates", "Plate photos and conditions"),
    ("f_models", "3D colony models"),
    ("f_import", "Import from Excel or CSV"),
    ("f_export", "Export of your data"),
    ("f_shared", "Shared lab with join codes"),
    ("f_phone", "Using it on a phone"),
]
USEFULNESS_CHOICES = [("not_used", "Did not use"), ("1", "1 Not useful"), ("2", "2"),
                      ("3", "3 Somewhat useful"), ("4", "4"), ("5", "5 Very useful")]

SUS_ITEMS = [
    "I think that I would like to use this system frequently.",
    "I found the system unnecessarily complex.",
    "I thought the system was easy to use.",
    "I think that I would need the support of a technical person to be able to use this system.",
    "I found the various functions in this system were well integrated.",
    "I thought there was too much inconsistency in this system.",
    "I would imagine that most people would learn to use this system very quickly.",
    "I found the system very cumbersome to use.",
    "I felt very confident using the system.",
    "I needed to learn a lot of things before I could get going with this system.",
]


def _q(key, label, kind, **kw):
    d = dict(key=key, label=label, kind=kind)
    d.update(kw)
    return d


SECTIONS = [
    dict(id="about", title="A. About you and your lab",
         intro="Skip anything you prefer not to answer.",
         questions=[
             _q("role", "Your main role", "choice", choices=_choices(
                 "Lab technician", "Quality control analyst", "Lab manager",
                 "Researcher or scientist", "Student", "Brewer or production staff", "Other")),
             _q("experience", "Years of experience working with microbial cultures", "choice", choices=_choices(
                 "Less than 1 year", "1 to 3 years", "4 to 10 years", "More than 10 years")),
             _q("lab_type", "Type of lab", "choice", choices=_choices(
                 "Brewery or beverage quality control", "Academic research", "Teaching lab",
                 "Other industry lab", "Culture collection or biobank", "Other")),
             _q("collection_size", "Roughly how many strains does your lab keep?", "choice", choices=_choices(
                 "Fewer than 50", "50 to 199", "200 to 999", "1000 or more")),
             _q("team_size", "How many people in your lab use this app?", "choice", choices=_choices(
                 "Just me", "2 to 5", "6 to 15", "More than 15")),
             _q("usage_duration", "How long have you used it?", "choice", choices=_choices(
                 "Less than 1 week", "1 to 4 weeks", "1 to 3 months", "More than 3 months")),
             _q("usage_frequency", "How often do you use it?", "choice", choices=_choices(
                 "Every day", "Every week", "Every month", "Rarely")),
             _q("devices", "Which devices do you use it on? (tick all that apply)", "multi", choices=_choices(
                 "Desktop or laptop", "Phone", "Tablet")),
         ]),
    dict(id="before", title="B. Before and after",
         intro="How you kept track of cultures before, and what has changed. Honest estimates are fine. "
               "Leave a box empty if you do not know.",
         questions=[
             _q("previous_methods", "How did you keep track of cultures before? (tick all that apply)", "multi",
                choices=_choices("Paper notebook or labels", "A spreadsheet", "Another software package",
                                 "Memory and informal notes", "Nothing organised")),
             _q("prev_find_min", "Before: minutes it usually took to find one specific strain", "number",
                min=0, max=1440, unit="minutes"),
             _q("now_find_min", "Now: minutes it usually takes to find one specific strain", "number",
                min=0, max=1440, unit="minutes"),
             _q("prev_entry_min", "Before: minutes it took to record one new strain", "number",
                min=0, max=1440, unit="minutes"),
             _q("now_entry_min", "Now: minutes it takes to record one new strain", "number",
                min=0, max=1440, unit="minutes"),
             _q("hours_saved_week", "Hours per week the app saves your lab (enter 0 if none)", "number",
                min=0, max=500, unit="hours per week"),
         ]),
    dict(id="impact", title="C. What the app caught, and how it behaved",
         intro="Think about the time you have used it. Whole numbers, or leave empty if you do not know.",
         questions=[
             _q("mixups_caught", "Times the app showed a tube that was missing, misplaced or booked in the same position as another",
                "number", min=0, max=100000, integer=True, unit="times"),
             _q("overdue_caught", "Cultures you found were overdue for revival because of the app",
                "number", min=0, max=100000, integer=True, unit="cultures"),
             _q("found_lost", "Strains you could not find before and have now found by recording their location",
                "number", min=0, max=100000, integer=True, unit="strains"),
             _q("speed", "The app was fast enough for my work", "likert", scale=AGREE),
             _q("errors_seen", "How often did you see an error message or a page that would not open?", "choice",
                choices=_choices("Never", "Once or twice", "Several times", "Often")),
             _q("data_problem", "Did the app ever lose a record you had saved, or change one without you asking?", "choice",
                choices=_choices("No", "Yes, once", "Yes, more than once"),
                help="A 3D model that looks wrong is not lost data; tell us about it in the next box."),
             _q("problem_details", "If anything went wrong, what happened?", "text",
                help="What you were doing, what you expected, what you saw."),
         ]),
    dict(id="features", title="D. How useful was each feature?",
         intro="Choose \"Did not use\" for anything you have not tried.",
         layout="table",
         questions=[_q(key, label, "usefulness", choices=USEFULNESS_CHOICES) for key, label in FEATURE_RATING_ITEMS]),
    dict(id="sus", title="E. Usability scale",
         intro="Ten short statements (the System Usability Scale). For each, choose how much you agree, "
               "from 1 (strongly disagree) to 5 (strongly agree).",
         layout="likert",
         questions=[_q("sus_%d" % (i + 1), text, "likert", scale=AGREE) for i, text in enumerate(SUS_ITEMS)]),
    dict(id="overall", title="F. Overall",
         intro="",
         questions=[
             _q("nps", "How likely are you to recommend this app to another lab? (0 means not at all, 10 means certainly)",
                "choice", choices=[(str(i), str(i)) for i in range(11)]),
             _q("continue_use", "Do you plan to keep using it?", "choice", choices=_choices(
                 "Yes, definitely", "Probably", "Not sure", "Probably not", "No")),
             _q("hosting_pref", "How would you prefer to run it?", "choice", choices=_choices(
                 "Install it myself on a lab computer", "Use a hosted version run by someone else",
                 "Either would suit me", "Not sure")),
             _q("best_thing", "What is the best thing about it?", "text"),
             _q("worst_thing", "What is the most frustrating thing about it?", "text"),
         ]),
]

ALL_QUESTIONS = [q for s in SECTIONS for q in s["questions"]]
QUESTION_BY_KEY = {q["key"]: q for q in ALL_QUESTIONS}


def _allowed(q):
    return {k for k, _ in q.get("choices", [])}


def _number(raw, q):
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return None, None
    try:
        val = float(raw)
    except ValueError:
        return None, "%s: please enter a number." % q["label"]
    if val != val or val in (float("inf"), float("-inf")):
        return None, "%s: please enter a number." % q["label"]
    lo, hi = q.get("min", 0), q.get("max", 100000)
    if val < lo or val > hi:
        return None, "%s: enter a value between %g and %g." % (q["label"], lo, hi)
    if q.get("integer"):
        if val != int(val):
            return None, "%s: please enter a whole number." % q["label"]
        val = int(val)
    else:
        val = round(val, 2)
    return val, None


def parse_answers(form):
    """Check the optional questions. Returns (answers, errors). Empty answers
    are left out; invalid ones are reported, never guessed."""
    answers, errors = {}, []
    for q in ALL_QUESTIONS:
        key, kind = q["key"], q["kind"]
        if kind == "multi":
            vals = [v for v in form.getlist(key) if v in _allowed(q)]
            if vals:
                answers[key] = list(dict.fromkeys(vals))
        elif kind in ("choice", "usefulness"):
            v = (form.get(key) or "").strip()
            if not v:
                continue
            if v in _allowed(q):
                answers[key] = v
            else:
                errors.append("%s: choose one of the listed answers." % q["label"])
        elif kind == "likert":
            v = (form.get(key) or "").strip()
            if not v:
                continue
            if v in ("1", "2", "3", "4", "5"):
                answers[key] = int(v)
            else:
                errors.append("%s: choose a value from 1 to 5." % q["label"])
        elif kind == "number":
            val, err = _number(form.get(key), q)
            if err:
                errors.append(err)
            elif val is not None:
                answers[key] = val
        elif kind == "text":
            v = (form.get(key) or "").strip()
            if v:
                answers[key] = v[:MAX_TEXT]
    return answers, errors


def sus_score(answers):
    """SUS score from 0 to 100, or None unless all ten items were answered."""
    total = 0
    for i in range(1, 11):
        v = answers.get("sus_%d" % i)
        if not isinstance(v, int) or not 1 <= v <= 5:
            return None
        total += (v - 1) if i % 2 == 1 else (5 - v)
    return round(total * 2.5, 1)


def sus_band(score):
    """Plain description of a SUS score. 68 is the commonly quoted average."""
    if score is None:
        return ""
    if score >= 85:
        return "excellent"
    if score >= 73:
        return "good"
    if score >= 68:
        return "above average"
    if score >= 51:
        return "below average"
    return "poor"


def nps_group(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v >= 9:
        return "promoter"
    if v >= 7:
        return "passive"
    return "detractor"


def loads(text):
    try:
        data = json.loads(text) if text else {}
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def dumps(answers):
    return json.dumps(answers, ensure_ascii=False, sort_keys=True)


def label_for(key, value):
    q = QUESTION_BY_KEY.get(key)
    if not q:
        return str(value)
    if q["kind"] == "multi":
        labels = dict(q["choices"])
        return "; ".join(labels.get(v, v) for v in (value or []))
    if q["kind"] in ("choice", "usefulness"):
        return dict(q["choices"]).get(value, str(value))
    return value


def _stats(values):
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    return dict(n=len(vals), mean=round(statistics.mean(vals), 2),
                median=round(statistics.median(vals), 2), low=vals[0], high=vals[-1],
                total=round(sum(vals), 2),
                sd=round(statistics.stdev(vals), 2) if len(vals) > 1 else None)


def summarize(rows):
    """Figures for the summary page. rows are dicts with the stored columns."""
    parsed = []
    for r in rows:
        a = loads(r.get("answers"))
        parsed.append((r, a))
    n = len(parsed)
    out = dict(n=n)
    out["consented"] = sum(1 for r, _ in parsed if r.get("consent_research"))
    out["rating"] = _stats([r["rating"] for r, _ in parsed])
    out["sus"] = _stats([r["sus_score"] for r, _ in parsed if r.get("sus_score") is not None])
    if out["sus"]:
        out["sus"]["band"] = sus_band(out["sus"]["mean"])
    groups = [nps_group(a.get("nps")) for _, a in parsed if a.get("nps") is not None]
    if groups:
        m = len(groups)
        pro, det = groups.count("promoter"), groups.count("detractor")
        out["nps"] = dict(n=m, promoters=pro, passives=groups.count("passive"), detractors=det,
                          score=round(100.0 * (pro - det) / m))
    else:
        out["nps"] = None

    # numeric questions
    nums = {}
    for q in ALL_QUESTIONS:
        if q["kind"] == "number":
            st = _stats([a.get(q["key"]) for _, a in parsed])
            if st:
                nums[q["key"]] = dict(label=q["label"], unit=q.get("unit", ""), **st)
    out["numbers"] = nums

    # before and after, only for people who gave both numbers
    pairs = {}
    for name, before, after in (("find", "prev_find_min", "now_find_min"),
                                ("entry", "prev_entry_min", "now_entry_min")):
        diffs = [(a[before], a[after]) for _, a in parsed
                 if isinstance(a.get(before), (int, float)) and isinstance(a.get(after), (int, float))]
        if diffs:
            b = statistics.mean(x for x, _ in diffs)
            af = statistics.mean(y for _, y in diffs)
            pairs[name] = dict(n=len(diffs), before=round(b, 2), after=round(af, 2),
                               change_pct=round(100.0 * (af - b) / b) if b else None)
    out["pairs"] = pairs

    # choice and multi answers
    choice_tables = []
    for q in ALL_QUESTIONS:
        if q["kind"] in ("choice", "multi") and q["key"] != "nps":
            counts, answered = {}, 0
            for _, a in parsed:
                v = a.get(q["key"])
                if v in (None, "", []):
                    continue
                answered += 1
                for item in (v if isinstance(v, list) else [v]):
                    counts[item] = counts.get(item, 0) + 1
            if answered:
                labels = dict(q["choices"])
                rows_ = [(labels.get(k, k), counts.get(k, 0)) for k, _ in q["choices"] if counts.get(k, 0)]
                choice_tables.append(dict(key=q["key"], label=q["label"], n=answered, rows=rows_))
    out["choices"] = choice_tables

    # feature usefulness
    feats = []
    for key, label in FEATURE_RATING_ITEMS:
        vals, not_used = [], 0
        for _, a in parsed:
            v = a.get(key)
            if v == "not_used":
                not_used += 1
            elif v in ("1", "2", "3", "4", "5"):
                vals.append(int(v))
        if vals or not_used:
            feats.append(dict(label=label, n=len(vals), not_used=not_used,
                              mean=round(statistics.mean(vals), 2) if vals else None))
    out["features"] = feats

    likert = {}
    for q in ALL_QUESTIONS:
        if q["kind"] == "likert" and not q["key"].startswith("sus_"):
            st = _stats([a.get(q["key"]) for _, a in parsed])
            if st:
                likert[q["key"]] = dict(label=q["label"], **st)
    out["likert"] = likert

    texts = []
    for r, a in parsed:
        for key, title in (("best_thing", "Best thing"), ("worst_thing", "Most frustrating"),
                           ("problem_details", "What went wrong")):
            if a.get(key):
                texts.append(dict(title=title, text=a[key], when=r.get("submitted_at", "")))
    out["texts"] = texts
    return out


SNAPSHOT_COLUMNS = ["app_version", "strains_count", "plates_count", "revival_checks_count",
                    "strains_with_location", "days_in_use"]


def export_rows(rows, consent_only=True):
    """Anonymous, one row per response, for a spreadsheet or statistics package.
    Names and email addresses are never included."""
    cols = (["response_no", "submitted_date", "consent_research", "overall_rating", "ease_of_use",
             "most_used_feature"] + SNAPSHOT_COLUMNS + ["sus_score", "improvement_comment"]
            + [q["key"] for q in ALL_QUESTIONS])
    out = []
    n = 0
    for r in rows:
        if consent_only and not r.get("consent_research"):
            continue
        n += 1
        a = loads(r.get("answers"))
        row = dict(response_no=n, submitted_date=(r.get("submitted_at") or "")[:10],
                   consent_research="yes" if r.get("consent_research") else "no",
                   overall_rating=r.get("rating"),
                   ease_of_use=dict(EASE_CHOICES).get(r.get("ease"), r.get("ease")),
                   most_used_feature=dict(FEATURE_CHOICES).get(r.get("most_used"), r.get("most_used")),
                   sus_score=r.get("sus_score"), improvement_comment=r.get("improvement"))
        for c in SNAPSHOT_COLUMNS:
            row[c] = r.get(c)
        for q in ALL_QUESTIONS:
            if q["key"] in a:
                row[q["key"]] = label_for(q["key"], a[q["key"]])
        out.append(row)
    return cols, out


def data_dictionary():
    """One row per column: where it comes from and what it means."""
    rows = [
        ("response_no", "Export", "Running number. Not linked to any person.", "number", ""),
        ("submitted_date", "Automatic", "Date the response was sent.", "date", ""),
        ("consent_research", "Overall", "Person agreed their anonymous answers can be used in research.", "yes/no", ""),
        ("overall_rating", "Overall", "Overall rating of the app.", "1 to 5", "1 very poor, 5 excellent"),
        ("ease_of_use", "Overall", "How easy it was to do what they came to do.", "choice",
         "; ".join(l for _, l in EASE_CHOICES)),
        ("most_used_feature", "Overall", "Feature used most.", "choice", "; ".join(l for _, l in FEATURE_CHOICES)),
        ("app_version", "Automatic", "Version of the app when the response was sent.", "text", ""),
        ("strains_count", "Automatic", "Strains in the lab's catalog when the response was sent.", "count", ""),
        ("plates_count", "Automatic", "Plate photos in the catalog when the response was sent.", "count", ""),
        ("revival_checks_count", "Automatic", "Revival checks recorded when the response was sent.", "count", ""),
        ("strains_with_location", "Automatic", "Strains with a freezer, rack, box or position recorded.", "count", ""),
        ("days_in_use", "Automatic", "Days between the first strain entered and the response.", "days", ""),
        ("sus_score", "Usability scale", "System Usability Scale score, computed only when all ten items were answered.",
         "0 to 100", "68 is the commonly quoted average"),
        ("improvement_comment", "Overall", "Free text: something that would make the app more useful.", "text", ""),
    ]
    sect = {q["key"]: s["title"] for s in SECTIONS for q in s["questions"]}
    for q in ALL_QUESTIONS:
        kind = q["kind"]
        if kind == "number":
            allowed = "%g to %g %s" % (q.get("min", 0), q.get("max", 100000), q.get("unit", ""))
        elif kind == "likert":
            allowed = "1 to 5 (1 %s, 5 %s)" % (q["scale"][0].lower(), q["scale"][1].lower())
        elif kind in ("choice", "multi", "usefulness"):
            allowed = "; ".join(l for _, l in q["choices"])
        else:
            allowed = ""
        rows.append((q["key"], sect[q["key"]], q["label"],
                     {"number": "number", "likert": "1 to 5", "choice": "choice", "multi": "several choices",
                      "usefulness": "choice", "text": "text"}[kind], allowed))
    return ["column", "section", "question_or_meaning", "type", "allowed_answers"], rows
