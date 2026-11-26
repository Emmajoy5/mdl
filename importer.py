"""Bulk import of an existing strain list (CSV or Excel) into the catalog.

Pure parsing/validation logic with no Flask or database dependency, so it can
be tested on its own. app.py owns the web flow and the actual INSERTs.

A lab's existing spreadsheet will never match this app's column names exactly,
so the pipeline is: parse the file -> guess which column is which from its
header (the admin can correct any guess on the preview page) -> turn each row
into a normalized record with errors/warnings -> flag likely duplicates -> only
then write anything.
"""
import csv
import io
import re
from datetime import date, datetime

MAX_ROWS = 5000

# target key -> (label shown in the mapping dropdown, header spellings we recognize).
# Spellings are compared after normalize_header(): lowercase, letters/digits only.
FIELDS = {
    "genus": ("Genus", ["genus"]),
    "species": ("Species", ["species", "speciesepithet", "sp"]),
    "strain": ("Strain", ["strain", "strainid", "strainname", "isolate", "isolatename"]),
    "full_name": ("Full organism name (genus + species + strain in one column)",
                  ["organism", "organismname", "fullname", "name", "speciesname",
                   "scientificname", "taxon", "microorganism", "microbe"]),
    "ncbi_taxonomy_id": ("NCBI taxonomy ID", ["ncbi", "ncbiid", "ncbitaxonomyid", "taxid", "taxonomyid"]),
    "accessions": ("Accession(s), e.g. \"NCYC 505; ATCC 18824\"",
                   ["accession", "accessions", "accessionnumber", "accessionnumbers",
                    "culturecollection", "culturecollectionid", "crossreference",
                    "crossreferences", "catalognumber", "catalogue", "catalogueno",
                    "atccno", "refno"]),
    "tags": ("Tags (comma or semicolon separated)", ["tags", "tag", "keywords", "labels"]),
    "notes": ("Notes", ["notes", "note", "comments", "comment", "remarks", "description"]),
    "collection_type": ("Collection type (library / validation / reference / other)",
                        ["collectiontype", "type", "category", "collection"]),
    "gram_stain": ("Gram stain", ["gram", "gramstain", "gramreaction", "gramresult"]),
    "viability_category": ("Viability category (periodic / stable / longTerm / unknown)",
                           ["viability", "viabilitycategory", "storagecategory", "revivalcategory"]),
    "revival_interval_days": ("Revival interval (days)",
                              ["revivalinterval", "revivalintervaldays", "revivalevery",
                               "subcultureinterval", "subcultureintervaldays", "reviveevery",
                               "intervaldays", "interval"]),
    "last_revival_date": ("Last revival date",
                          ["lastrevival", "lastrevivaldate", "lastrevived", "lastsubcultured",
                           "lastsubculture", "lastsubculturedate", "lastrefresh", "lastrenewed"]),
    "media": ("Growth media", ["media", "medium", "growthmedia", "growthmedium", "agar"]),
    "oxygen_requirement": ("Oxygen requirement", ["oxygen", "oxygenrequirement", "atmosphere", "aerobic"]),
    "optimal_temp_c": ("Optimal temperature (°C)", ["temp", "temperature", "optimaltemp",
                                                    "optimaltemperature", "growthtemp", "incubationtemp"]),
    "morphology_notes": ("Morphology notes", ["morphology", "morphologynotes", "colonymorphology",
                                              "cellmorphology"]),
    "storage_location": ("Storage location", ["storage", "storagelocation", "location"]),
    "storage_freezer": ("Storage: freezer / fridge", ["freezer", "fridge", "storagefreezer", "unit"]),
    "storage_rack": ("Storage: rack / shelf", ["rack", "shelf", "storagerack", "tower"]),
    "storage_box": ("Storage: box", ["box", "storagebox", "cryobox", "plate"]),
    "storage_position": ("Storage: position in box (e.g. A3)", ["position", "storageposition", "well", "slot", "cell"]),
    "storage_format": ("Storage format", ["storageformat", "format", "preservation", "preservationmethod"]),
    "storage_temp_c": ("Storage temperature (°C)", ["storagetemp", "storagetemperature"]),
    "date_stored": ("Date stored", ["datestored", "stored", "storedon", "dateofstorage",
                                    "dateadded", "dateacquired", "acquired", "received", "datereceived"]),
    "source_lab": ("Source lab / supplier (a lab or company the sample came from)",
                   ["sourcelab", "supplier", "source", "receivedfrom", "obtainedfrom", "origin",
                    "provider", "donor"]),
    "online_reference": ("Online reference database (sample source is a database)",
                         ["onlinereference", "database", "referencedatabase", "onlinedatabase"]),
    "role": ("Brewing role (fermentation / souring / spoilage / conditioning / flavor / other)",
             ["role", "brewingrole", "function", "use"]),
    "spoilage_risk": ("Spoilage risk (none / low / medium / high)",
                      ["spoilage", "spoilagerisk", "risk"]),
    "added_by": ("Added by", ["addedby", "enteredby", "createdby", "technician", "analyst", "owner"]),
}

REQUIRED_NOTE = "Genus and species are required (or one full-name column that contains both)."

COLLECTION_TYPES = ["library", "validation", "reference", "other"]
VIABILITY = {"periodic": "periodic", "stable": "stable", "longterm": "longTerm",
             "long term": "longTerm", "long-term": "longTerm", "unknown": "unknown"}
ROLES = ["fermentation", "souring", "spoilage", "conditioning", "flavor", "other"]
RISKS = ["none", "low", "medium", "high"]


CUSTOM_TYPES = ["text", "number", "date", "choice"]


def normalize_custom(field_type, choices, raw):
    """Validate/normalize one custom-field value. Returns (value, problem):
    value is a clean string or None (empty/invalid); problem is a message when
    a non-empty entry had to be dropped."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if field_type == "number":
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            return None, f"\"{raw}\" isn't a number"
        return (str(int(n)) if n.is_integer() else str(n)), None
    if field_type == "date":
        d = parse_date(raw)
        return (d, None) if d else (None, f"\"{raw}\" isn't a date (try YYYY-MM-DD)")
    if field_type == "choice":
        for c in choices or []:
            if c.lower() == raw.lower():
                return c, None
        return None, f"\"{raw}\" isn't one of: {', '.join(choices or [])}"
    return raw[:500], None


def normalize_header(h):
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


# --------------------------------------------------------------------------- #
# Reading the file
# --------------------------------------------------------------------------- #
class ImportFileError(Exception):
    """A problem with the uploaded file itself, worded for the person uploading."""


def parse_file(filename, data):
    """Return (headers, rows): headers is a list of str, rows a list of lists of
    str (every cell converted to text; empty cells are ''). Supports .csv/.tsv/
    .txt and .xlsx (first sheet)."""
    name = (filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        grid = _read_xlsx(data)
    elif name.endswith((".csv", ".tsv", ".txt")):
        grid = _read_csv(data)
    elif name.endswith(".xls"):
        raise ImportFileError("Old .xls files aren't supported. In Excel, use File > Save As > "
                              "Excel Workbook (.xlsx) or CSV, then upload that.")
    else:
        raise ImportFileError("Upload a .csv or .xlsx file.")

    # Leading/trailing blank lines are dropped; blank lines in the middle are
    # kept (and skipped later) so row numbers shown in the preview match the
    # numbers the person sees in their spreadsheet.
    def blank(row):
        return not any(str(c).strip() for c in row)
    while grid and blank(grid[0]):
        grid.pop(0)
    while grid and blank(grid[-1]):
        grid.pop()
    if len(grid) < 2:
        raise ImportFileError("The file needs a header row plus at least one data row.")
    headers = [str(c).strip() for c in grid[0]]
    width = len(headers)
    rows = [[str(c).strip() for c in (row + [""] * width)[:width]] for row in grid[1:]]
    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"That file has {len(rows)} rows; the limit is {MAX_ROWS} per import. "
                              f"Split it into smaller files.")
    return headers, rows


def _read_csv(data):
    for enc in ("utf-8-sig", "cp1252"):  # Excel's "CSV" is often Windows-1252, not UTF-8
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportFileError("Couldn't read that file's text encoding. Save it as CSV UTF-8 and retry.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(data):
    try:
        import openpyxl
    except ImportError:  # pragma: no cover
        raise ImportFileError("Excel import needs the openpyxl package; use CSV instead.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportFileError("That doesn't look like a valid .xlsx file.")
    ws = wb.worksheets[0]
    grid = []
    for row in ws.iter_rows(values_only=True):
        cells = []
        for v in row:
            if v is None:
                cells.append("")
            elif isinstance(v, datetime):
                cells.append(v.date().isoformat())
            elif isinstance(v, date):
                cells.append(v.isoformat())
            elif isinstance(v, float) and v.is_integer():
                cells.append(str(int(v)))  # 505.0 -> 505, not "505.0"
            else:
                cells.append(str(v))
        grid.append(cells)
    wb.close()
    return grid


# --------------------------------------------------------------------------- #
# Column mapping
# --------------------------------------------------------------------------- #
def auto_map(headers, custom=None):
    """Guess a target field for each header: returns a list the same length as
    headers holding a FIELDS key or None. Each target is claimed by at most one
    column (the first that matches), so two columns never both feed "genus"."""
    lookup = {}
    for key, (_label, names) in FIELDS.items():
        for n in names:
            lookup.setdefault(n, key)
        lookup.setdefault(key.replace("_", ""), key)
    for key, name in (custom or {}).items():
        lookup.setdefault(normalize_header(name), key)
    used = set()
    mapping = []
    for h in headers:
        key = lookup.get(normalize_header(h))
        if key and key not in used:
            used.add(key)
            mapping.append(key)
        else:
            mapping.append(None)
    return mapping


# --------------------------------------------------------------------------- #
# Turning rows into records
# --------------------------------------------------------------------------- #
_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d %b %Y",
                 "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d"]


def parse_date(text):
    """Day-first for ambiguous numeric dates (03/04/2025 is 3 April). Returns an
    ISO date string or None."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _norm_gram(v):
    raw = v.strip().lower()
    if raw in ("+", "gram+", "gram +", "g+"):
        return "positive"
    if raw in ("-", "gram-", "gram -", "g-", "\u2212", "gram\u2212"):
        return "negative"
    s = re.sub(r"[\s_\-]+", " ", raw).strip()
    if s in ("positive", "pos", "gram positive", "gram pos"):
        return "positive"
    if s in ("negative", "neg", "gram negative", "gram neg"):
        return "negative"
    if s in ("variable", "var", "gram variable"):
        return "variable"
    if s in ("not done", "nd", "n/a", "na", "none", "not applicable"):
        return "not_done"
    return None


def _split_full_name(full):
    parts = full.split()
    if len(parts) == 1:
        return parts[0], "", ""
    # "S. cerevisiae" style abbreviations are kept as the genus initial; a spreadsheet
    # that abbreviates will be flagged by the admin at preview time, not guessed at here.
    return parts[0], parts[1], " ".join(parts[2:])


def parse_accessions(text):
    """"NCYC 505; ATCC 18824" -> [("NCYC", "505"), ("ATCC", "18824")]. Entries
    that don't split into collection + number are returned as problems."""
    pairs, problems = [], []
    for chunk in re.split(r"[;\n,|]+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^([A-Za-z][A-Za-z\-\.]*)\s*[:#]?\s*(\S.*)$", chunk)
        if m:
            pairs.append((m.group(1).upper().rstrip("."), m.group(2).strip()))
        else:
            problems.append(chunk)
    return pairs, problems


def build_records(rows, mapping, custom_fields=None):
    """Return one record per row: {row_number, values, accessions, tags,
    errors, warnings}. `values` holds normalized organism fields ready to
    INSERT; `row_number` is the 1-based spreadsheet row (header = row 1)."""
    idx = {key: i for i, key in enumerate(mapping) if key}
    records = []
    for n, row in enumerate(rows, start=2):
        def get(key):
            i = idx.get(key)
            return row[i].strip() if i is not None and i < len(row) else ""

        if not any(row):
            continue  # a blank line inside the data; its number is skipped, not reused
        errors, warnings = [], []
        genus, species, strain = get("genus"), get("species"), get("strain")
        full = get("full_name")
        if (not genus or not species) and full:
            g, s, st = _split_full_name(full)
            genus, species = genus or g, species or s
            strain = strain or st
        if not genus or not species:
            errors.append(REQUIRED_NOTE if not (genus or species or full)
                          else "Species is missing." if genus else "Genus is missing.")

        v = dict(genus=genus, species=species, strain=strain or None,
                 full_name=" ".join(x for x in (genus, species, strain) if x))
        v["notes"] = get("notes") or None
        v["ncbi_taxonomy_id"] = get("ncbi_taxonomy_id") or None
        v["media"] = get("media") or None
        v["oxygen_requirement"] = get("oxygen_requirement") or None
        v["optimal_temp_c"] = get("optimal_temp_c") or None
        v["morphology_notes"] = get("morphology_notes") or None
        v["storage_location"] = get("storage_location") or None
        for k in ("storage_freezer", "storage_rack", "storage_box", "storage_position"):
            v[k] = get(k) or None
        v["storage_format"] = get("storage_format") or None
        v["storage_temp_c"] = get("storage_temp_c") or None
        v["added_by"] = get("added_by") or None
        v["source_lab"] = get("source_lab") or None
        v["online_reference"] = get("online_reference") or None

        ct = get("collection_type").lower()
        if ct and ct not in COLLECTION_TYPES:
            warnings.append(f"Collection type \"{get('collection_type')}\" isn't one of "
                            f"{', '.join(COLLECTION_TYPES)}; imported as library.")
            ct = ""
        v["collection_type"] = ct or "library"

        gram_raw = get("gram_stain")
        v["gram_stain"] = _norm_gram(gram_raw) if gram_raw else None
        if gram_raw and not v["gram_stain"]:
            warnings.append(f"Gram stain \"{gram_raw}\" wasn't recognized (use positive / negative / "
                            f"variable / not done); left blank.")

        via_raw = get("viability_category")
        via = VIABILITY.get(via_raw.lower()) if via_raw else None
        if via_raw and not via:
            warnings.append(f"Viability category \"{via_raw}\" wasn't recognized; set to unknown.")
        v["viability_category"] = via or "unknown"

        interval_raw = get("revival_interval_days")
        interval = None
        if interval_raw:
            m = re.match(r"^\s*(\d+)\s*(d|day|days)?\s*$", interval_raw.lower())
            if m and int(m.group(1)) > 0:
                interval = int(m.group(1))
            else:
                warnings.append(f"Revival interval \"{interval_raw}\" isn't a whole number of days; left blank.")
        v["revival_interval_days"] = interval
        if v["viability_category"] == "periodic" and not interval:
            warnings.append("Periodic strain with no revival interval: the dashboard will flag it "
                            "until you set one.")
        if interval and v["viability_category"] != "periodic":
            v["viability_category"] = "periodic"
            warnings.append("A revival interval was given, so this strain was set to periodic.")

        for key in ("last_revival_date", "date_stored"):
            raw = get(key)
            parsed = parse_date(raw)
            if raw and not parsed:
                warnings.append(f"Couldn't read \"{raw}\" as a date (try YYYY-MM-DD); left blank.")
            if parsed and parsed > date.today().isoformat():
                warnings.append(f"\"{raw}\" is in the future; left blank.")
                parsed = None
            v[key] = parsed

        role = get("role").lower()
        if role and role not in ROLES:
            warnings.append(f"Role \"{get('role')}\" isn't one of {', '.join(ROLES)}; left blank.")
            role = ""
        v["role"] = role or None
        risk = get("spoilage_risk").lower()
        if risk and risk not in RISKS:
            warnings.append(f"Spoilage risk \"{get('spoilage_risk')}\" isn't one of "
                            f"{', '.join(RISKS)}; left blank.")
            risk = ""
        v["spoilage_risk"] = risk or None

        accessions, problems = parse_accessions(get("accessions"))
        for p in problems:
            warnings.append(f"Accession \"{p}\" needs a collection and a number (like NCYC 505); skipped.")
        tags = [t.strip() for t in re.split(r"[,;]", get("tags")) if t.strip()]
        custom = {}
        for key, fdef in (custom_fields or {}).items():
            raw = get(key)
            val, problem = normalize_custom(fdef["field_type"], fdef["choices"], raw)
            if problem:
                warnings.append(f"{fdef['name']}: {problem}; left blank.")
            if val is not None:
                custom[key] = val

        if v["source_lab"] and v["online_reference"]:
            warnings.append("Both a source lab and an online reference were given; using the source lab.")
            v["online_reference"] = None

        records.append(dict(row_number=n, values=v, accessions=accessions, tags=tags, custom=custom,
                            errors=errors, warnings=warnings, duplicate_of=None))
    return records


def mark_duplicates(records, existing_accessions, existing_strains):
    """Flag rows that already exist in the catalog or repeat an earlier row in
    this same file. `existing_accessions` maps (COLLECTION, number) lowercased
    -> collection_id; `existing_strains` maps (genus, species, strain) lowercased
    -> collection_id. Sets record["duplicate_of"] to a short human reason."""
    seen_acc, seen_strain = {}, {}
    for rec in records:
        if rec["errors"]:
            continue
        v = rec["values"]
        skey = (v["genus"].lower(), v["species"].lower(), (v["strain"] or "").lower())
        reason = None
        for cc, num in rec["accessions"]:
            akey = (cc.lower(), num.lower())
            if akey in existing_accessions:
                reason = f"{cc} {num} is already in your catalog as {existing_accessions[akey]}"
                break
            if akey in seen_acc:
                reason = f"{cc} {num} already appears on row {seen_acc[akey]} of this file"
                break
        if reason is None and skey in existing_strains:
            reason = f"already in your catalog as {existing_strains[skey]}"
        if reason is None and skey in seen_strain:
            reason = f"same strain as row {seen_strain[skey]} of this file"
        if reason:
            rec["duplicate_of"] = reason
        else:
            for cc, num in rec["accessions"]:
                seen_acc.setdefault((cc.lower(), num.lower()), rec["row_number"])
            seen_strain.setdefault(skey, rec["row_number"])
    return records


def template_csv():
    """A starter file with the recommended columns and two example rows."""
    cols = ["Genus", "Species", "Strain", "Accessions", "Tags", "Gram stain", "Collection type",
            "Viability category", "Revival interval (days)", "Last revival date", "Media",
            "Storage location", "Source lab", "Notes"]
    rows = [
        ["Saccharomyces", "cerevisiae", "Ale-01", "NCYC 505; ATCC 18824", "ale, top-fermenting",
         "", "library", "periodic", "180", "2026-03-14", "YPD agar", "Freezer 2, Box 4, A3",
         "Central QC Lab", "House ale strain"],
        ["Levilactobacillus", "brevis", "", "DSM 20054", "sour, spoilage", "positive", "library",
         "stable", "", "", "MRS agar", "Freezer 1, Box 2, B7", "", ""],
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue()
