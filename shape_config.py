"""Shape-parameter resolution for procedural 3D model generation.

Every organism gets a cell + colony shape, via a fallback chain so 3D
generation never silently does nothing:

  1. a per-organism custom override stored in organisms.shape_config (JSON)
  2. a built-in config for well-known (genus, species) pairs (current names
     and common synonyms)
  3. a generic default inferred from the Gram stain, or a plain default

Colony entries (see scene3d.colony_layout) describe a plate: colour, how many
colonies, their radius range in millimetres on a 90 mm plate, a profile, a
texture_style (granular/dry/glossy/mucoid) and an edge style (entire/undulate/
rhizoid/filamentous). Sizes are typical for a plate read after the usual
incubation; colours are illustrative.

Cell entries give a shape in micrometres (kind + radius/length or rx/ry/rz), a
typical arrangement and a size note. Kinds: coccus, rod, coccobacillus,
curved_rod, spiral and ellipsoid (yeast). Arrangements: single, pair, chain,
tetrad, cluster, packet, palisade and budding.

The cell is worked out in layers, and the last one that applies wins:
  1. a plain default, or one inferred from the Gram stain
  2. a guess from the name (Streptococcus has "coccus" in it)
  3. the typical shape of the genus (GENUS_CELLS)
  4. a built-in entry for the species (BUILTIN_SHAPES)
  5. words in the strain's morphology notes ("cocci in clusters")
  6. the shape and arrangement recorded with the latest Gram stain
  7. a correction made to the 3D cell model on the strain page
The result says which layer decided it (cell["basis"]), so the page can say so.
The cell colour follows the strain's recorded Gram result for bacteria
(crystal-violet purple for positive, safranin pink-red for negative); yeasts,
which are not Gram stained in practice, are drawn cream.
"""
import json

GRAM_POSITIVE_COLOR = (0.47, 0.27, 0.57)   # crystal violet retained: purple
GRAM_NEGATIVE_COLOR = (0.83, 0.35, 0.42)   # safranin counterstain: pink/red
GRAM_VARIABLE_COLOR = (0.65, 0.33, 0.50)
YEAST_COLOR = (0.92, 0.88, 0.76)

_SACCHAROMYCES = dict(
    cell=dict(kind="ellipsoid", rx=3.0, ry=2.8, rz=3.5, arrangement="budding",
              size_um="about 5-10 µm", color=YEAST_COLOR),
    colony=dict(profile="convex", n_colonies=34, colony_radius_mm=(1.0, 2.5),
                texture_style="glossy", edge="entire", color=(0.93, 0.90, 0.80)))

_L_BREVIS = dict(
    cell=dict(kind="rod", radius=0.4, length=2.2, arrangement="chain",
              size_um="about 0.7-1 x 2-4 µm", color=GRAM_POSITIVE_COLOR),
    colony=dict(profile="raised", n_colonies=50, colony_radius_mm=(0.5, 1.3),
                texture_style="glossy", edge="entire", color=(0.92, 0.90, 0.84)))

BUILTIN_SHAPES = {
    ("Saccharomyces", "cerevisiae"): _SACCHAROMYCES,
    ("Saccharomyces", "pastorianus"): dict(
        cell=dict(kind="ellipsoid", rx=3.0, ry=2.8, rz=3.4, arrangement="budding",
                  size_um="about 5-10 µm", color=YEAST_COLOR),
        colony=dict(profile="raised", n_colonies=32, colony_radius_mm=(1.0, 2.2),
                    texture_style="glossy", edge="entire", color=(0.90, 0.88, 0.84))),
    ("Brettanomyces", "spp."): dict(
        cell=dict(kind="ellipsoid", rx=1.8, ry=1.7, rz=3.6, arrangement="budding",
                  size_um="about 2-4 x 4-8 µm, often ogival", color=(0.86, 0.80, 0.70)),
        colony=dict(profile="raised", n_colonies=40, colony_radius_mm=(0.6, 1.6),
                    texture_style="dry", edge="entire", color=(0.84, 0.78, 0.68))),
    ("Lactobacillus", "brevis"): _L_BREVIS,          # former name
    ("Levilactobacillus", "brevis"): _L_BREVIS,      # current name (Zheng et al. 2020)
    ("Pediococcus", "damnosus"): dict(
        cell=dict(kind="coccus", radius=0.5, arrangement="tetrad",
                  size_um="cocci about 1 µm, in pairs and tetrads", color=GRAM_POSITIVE_COLOR),
        colony=dict(profile="convex", n_colonies=70, colony_radius_mm=(0.3, 0.8),
                    texture_style="glossy", edge="entire", color=(0.91, 0.89, 0.83))),
    ("Acetobacter", "spp."): dict(
        cell=dict(kind="rod", radius=0.35, length=1.6, arrangement="pair",
                  size_um="about 0.6-0.8 x 1-4 µm", color=GRAM_NEGATIVE_COLOR),
        colony=dict(profile="raised", n_colonies=40, colony_radius_mm=(0.5, 1.5),
                    texture_style="glossy", edge="entire", color=(0.88, 0.85, 0.76))),
    ("Bacillus", "subtilis"): dict(
        cell=dict(kind="rod", radius=0.4, length=2.4, arrangement="chain",
                  size_um="about 0.7-0.8 x 2-3 µm", color=GRAM_POSITIVE_COLOR),
        colony=dict(profile="flat", n_colonies=10, colony_radius_mm=(2.5, 6.0),
                    texture_style="dry", edge="undulate", color=(0.86, 0.82, 0.70))),
}

_COLONY_DEFAULTS = dict(profile="raised", n_colonies=40, colony_radius_mm=(1.0, 2.5),
                        texture_style="glossy", edge="entire",
                        color=(0.87, 0.84, 0.76))

GENERIC_DEFAULT = dict(
    cell=dict(kind="coccus", radius=0.6, arrangement="single", size_um="unknown",
              color=(0.75, 0.72, 0.68)),
    colony=dict(_COLONY_DEFAULTS),
)

import re

# Default dimensions (µm) for each kind of cell.
CELL_DIMENSIONS = {
    "coccus": dict(radius=0.5, size_um="about 1 µm across"),
    "rod": dict(radius=0.4, length=2.0, size_um="about 0.8 x 2-3 µm"),
    "coccobacillus": dict(radius=0.4, length=0.5, size_um="about 0.8 x 1-1.5 µm"),
    "curved_rod": dict(radius=0.3, length=1.8, size_um="about 0.5 x 1.5-3 µm, comma shaped"),
    "spiral": dict(radius=0.18, length=8.5, size_um="about 0.3 x 5-10 µm, helical"),
    "ellipsoid": dict(rx=3.0, ry=2.8, rz=3.5, size_um="about 5-10 µm"),
}

# What each choice is called on the strain page.
SHAPE_LABELS = [
    ("coccus", "Coccus (round)"),
    ("rod", "Rod (bacillus)"),
    ("coccobacillus", "Coccobacillus (short rod)"),
    ("curved_rod", "Curved rod (vibrio)"),
    ("spiral", "Spiral (spirillum)"),
    ("ellipsoid", "Yeast (oval)"),
]
ARRANGEMENT_LABELS = [
    ("single", "Single cells"),
    ("pair", "Pairs (diplo-)"),
    ("chain", "Chains (strepto-)"),
    ("tetrad", "Tetrads (groups of four)"),
    ("cluster", "Clusters (staphylo-)"),
    ("packet", "Packets of eight (sarcinae)"),
    ("palisade", "Palisades (side by side)"),
    ("budding", "Budding"),
]
ARRANGEMENTS_FOR = {
    "coccus": ("single", "pair", "chain", "tetrad", "cluster", "packet"),
    "rod": ("single", "pair", "chain", "palisade"),
    "coccobacillus": ("single", "pair", "chain"),
    "curved_rod": ("single", "pair"),
    "spiral": ("single",),
    "ellipsoid": ("single", "budding"),
}

# Typical cell shape and arrangement by genus, from standard descriptions
# (Bergey's Manual and the genus descriptions in LPSN). A genus here sets the
# shape for every species in it unless the species has its own entry above.
_G = lambda kind, arr, **dims: dict(kind=kind, arrangement=arr, **dims)
GENUS_CELLS = {
    # Gram-positive cocci
    "Staphylococcus": _G("coccus", "cluster"),
    "Micrococcus": _G("coccus", "tetrad"),
    "Kocuria": _G("coccus", "cluster"),
    "Streptococcus": _G("coccus", "chain"),
    "Lactococcus": _G("coccus", "pair"),
    "Enterococcus": _G("coccus", "pair"),
    "Pediococcus": _G("coccus", "tetrad"),
    "Tetragenococcus": _G("coccus", "tetrad"),
    "Aerococcus": _G("coccus", "tetrad"),
    "Leuconostoc": _G("coccus", "chain", size_um="about 0.5-0.7 x 0.7-1.2 µm, lens shaped"),
    "Oenococcus": _G("coccus", "chain", size_um="about 0.5-0.7 x 0.7-1.2 µm"),
    "Sarcina": _G("coccus", "packet", radius=1.0, size_um="about 2 µm, in packets of eight"),
    "Deinococcus": _G("coccus", "tetrad"),
    "Peptostreptococcus": _G("coccus", "chain"),
    # Gram-negative cocci
    "Neisseria": _G("coccus", "pair"),
    "Megasphaera": _G("coccus", "pair", radius=0.8, size_um="about 1.2-2 µm, in pairs"),
    "Veillonella": _G("coccus", "pair", radius=0.3, size_um="about 0.5 µm"),
    # Gram-positive rods
    "Bacillus": _G("rod", "chain", radius=0.4, length=2.4, size_um="about 0.7-1 x 2-4 µm"),
    "Paenibacillus": _G("rod", "chain"),
    "Geobacillus": _G("rod", "chain"),
    "Lysinibacillus": _G("rod", "chain"),
    "Clostridium": _G("rod", "pair", radius=0.45, length=3.0, size_um="about 0.5-1.5 x 3-8 µm"),
    "Clostridioides": _G("rod", "pair", radius=0.45, length=3.0),
    "Listeria": _G("rod", "single", radius=0.3, length=1.2, size_um="about 0.5 x 1-2 µm"),
    "Corynebacterium": _G("rod", "palisade", radius=0.35, length=1.6),
    "Mycobacterium": _G("rod", "single", radius=0.25, length=2.5),
    "Bifidobacterium": _G("rod", "single"),
    "Propionibacterium": _G("rod", "single"),
    "Cutibacterium": _G("rod", "single"),
    "Lactobacillus": _G("rod", "chain"),
    "Lactiplantibacillus": _G("rod", "single"),
    "Lacticaseibacillus": _G("rod", "chain"),
    "Levilactobacillus": _G("rod", "chain"),
    "Limosilactobacillus": _G("rod", "single"),
    "Ligilactobacillus": _G("rod", "chain"),
    "Latilactobacillus": _G("rod", "chain"),
    "Lentilactobacillus": _G("rod", "chain"),
    "Secundilactobacillus": _G("rod", "chain"),
    "Fructilactobacillus": _G("rod", "chain"),
    "Companilactobacillus": _G("rod", "chain"),
    "Loigolactobacillus": _G("rod", "chain"),
    "Pectinatus": _G("curved_rod", "single", size_um="about 0.7 x 2-30 µm, slightly curved"),
    "Weissella": _G("coccobacillus", "chain"),
    # Gram-negative rods
    "Escherichia": _G("rod", "single", radius=0.5, length=1.5, size_um="about 1 x 2 µm"),
    "Shigella": _G("rod", "single", radius=0.5, length=1.5),
    "Salmonella": _G("rod", "single", radius=0.5, length=1.5),
    "Klebsiella": _G("rod", "single", radius=0.5, length=1.4),
    "Enterobacter": _G("rod", "single", radius=0.5, length=1.4),
    "Citrobacter": _G("rod", "single", radius=0.5, length=1.4),
    "Serratia": _G("rod", "single", radius=0.4, length=1.2),
    "Proteus": _G("rod", "single", radius=0.4, length=1.6),
    "Yersinia": _G("coccobacillus", "single"),
    "Pseudomonas": _G("rod", "single", radius=0.35, length=1.6, size_um="about 0.5-1 x 1.5-5 µm"),
    "Acetobacter": _G("rod", "pair", radius=0.35, length=1.6),
    "Gluconobacter": _G("rod", "pair", radius=0.35, length=1.6),
    "Gluconacetobacter": _G("rod", "pair", radius=0.35, length=1.6),
    "Komagataeibacter": _G("rod", "pair", radius=0.35, length=1.6),
    "Zymomonas": _G("rod", "pair", radius=0.6, length=3.0),
    "Pantoea": _G("rod", "single", radius=0.4, length=1.4),
    "Rahnella": _G("rod", "single", radius=0.4, length=1.4),
    "Obesumbacterium": _G("rod", "single", radius=0.5, length=1.4),
    "Xanthomonas": _G("rod", "single", radius=0.3, length=1.4),
    "Agrobacterium": _G("rod", "single"),
    "Rhizobium": _G("rod", "single"),
    "Burkholderia": _G("rod", "single"),
    "Legionella": _G("rod", "single", radius=0.3, length=2.0),
    "Bacteroides": _G("rod", "single"),
    "Aeromonas": _G("rod", "single"),
    "Acinetobacter": _G("coccobacillus", "pair"),
    "Moraxella": _G("coccobacillus", "pair"),
    "Haemophilus": _G("coccobacillus", "single", radius=0.25, length=0.4),
    "Brucella": _G("coccobacillus", "single", radius=0.3, length=0.4),
    "Bordetella": _G("coccobacillus", "single", radius=0.3, length=0.4),
    "Pasteurella": _G("coccobacillus", "single"),
    # Curved and spiral cells
    "Vibrio": _G("curved_rod", "single"),
    "Desulfovibrio": _G("curved_rod", "single"),
    "Campylobacter": _G("spiral", "single", radius=0.15, length=4.0, size_um="about 0.2-0.5 x 0.5-5 µm"),
    "Helicobacter": _G("spiral", "single", radius=0.25, length=4.0, size_um="about 0.5 x 2-5 µm"),
    "Spirillum": _G("spiral", "single"),
    "Aquaspirillum": _G("spiral", "single"),
    "Magnetospirillum": _G("spiral", "single"),
    # Yeasts
    "Saccharomyces": _G("ellipsoid", "budding"),
    "Brettanomyces": _G("ellipsoid", "budding", rx=1.8, ry=1.7, rz=3.6,
                        size_um="about 2-4 x 4-8 µm, often ogival"),
    "Dekkera": _G("ellipsoid", "budding", rx=1.8, ry=1.7, rz=3.6,
                  size_um="about 2-4 x 4-8 µm, often ogival"),
    "Pichia": _G("ellipsoid", "budding"),
    "Candida": _G("ellipsoid", "budding", rx=2.0, ry=1.9, rz=2.6, size_um="about 3-6 µm"),
    "Torulaspora": _G("ellipsoid", "budding", rx=2.0, ry=2.0, rz=2.3, size_um="about 3-5 µm"),
    "Kluyveromyces": _G("ellipsoid", "budding"),
    "Lachancea": _G("ellipsoid", "budding"),
    "Hanseniaspora": _G("ellipsoid", "budding", rx=1.6, ry=1.5, rz=3.2,
                        size_um="about 2-4 x 5-8 µm, lemon shaped"),
    "Zygosaccharomyces": _G("ellipsoid", "budding"),
    "Debaryomyces": _G("ellipsoid", "budding", rx=2.0, ry=2.0, rz=2.3),
    "Wickerhamomyces": _G("ellipsoid", "budding"),
    "Metschnikowia": _G("ellipsoid", "budding"),
    "Kazachstania": _G("ellipsoid", "budding"),
    "Rhodotorula": _G("ellipsoid", "budding"),
    "Cryptococcus": _G("ellipsoid", "budding"),
    "Schizosaccharomyces": _G("rod", "single", radius=1.8, length=8.0,
                              size_um="about 3-4 x 7-14 µm, divides by fission"),
}

# Name parts that give the shape away when the genus is not in the table.
_NAME_HINTS = [
    (re.compile(r"coccobacill", re.I), "coccobacillus"),
    (re.compile(r"spirill|spira|spiro", re.I), "spiral"),
    (re.compile(r"vibrio", re.I), "curved_rod"),
    (re.compile(r"coccus$|cocci$|coccus\b", re.I), "coccus"),
    (re.compile(r"bacill|bacter|bacterium", re.I), "rod"),
    (re.compile(r"myces$|candida|yeast", re.I), "ellipsoid"),
]

# Words in the morphology notes. The shape words are checked in order, so
# "coccobacilli" is not read as "cocci".
_NOTE_SHAPES = [
    (re.compile(r"cocco-?bacill", re.I), "coccobacillus"),
    (re.compile(r"\bspiral|\bhelical|\bspirill|\bspirochaet|\bcorkscrew", re.I), "spiral"),
    (re.compile(r"\bcurved|\bcomma|\bvibrio", re.I), "curved_rod"),
    (re.compile(r"\bcocci\b|\bcoccus\b|\bcoccoid|\bdiplococc|\bstreptococc|\bstaphylococc", re.I), "coccus"),
    (re.compile(r"\brods?\b|\bbacilli\b|\brod-shaped|\bbacillary", re.I), "rod"),
    (re.compile(r"\byeast|\bbudding|\boval cells", re.I), "ellipsoid"),
]
_NOTE_ARRANGEMENTS = [
    (re.compile(r"\bpackets?\b|\bsarcina|\bcuboid", re.I), "packet"),
    (re.compile(r"\btetrads?\b", re.I), "tetrad"),
    (re.compile(r"\bclusters?\b|\bgrape|\bstaphylo", re.I), "cluster"),
    (re.compile(r"\bpalisades?\b", re.I), "palisade"),
    (re.compile(r"\bchains?\b|\bstrepto", re.I), "chain"),
    (re.compile(r"\bpairs?\b|\bdiplo", re.I), "pair"),
    (re.compile(r"\bbudding|\bbuds\b", re.I), "budding"),
    (re.compile(r"\bsingly\b|\bsingle cells?\b", re.I), "single"),
]


def _cell_of(kind, arrangement=None, base=None, **dims):
    """A cell dict of this kind: dimensions from `dims`, else from `base` when it
    is the same kind, else the defaults; the arrangement if it suits the kind."""
    cell = dict(CELL_DIMENSIONS[kind])
    if base is not None and base.get("kind") == kind:
        cell.update({k: v for k, v in base.items() if k not in ("arrangement", "basis")})
    cell.update(dims)
    cell["kind"] = kind
    allowed = ARRANGEMENTS_FOR[kind]
    if arrangement in allowed:
        cell["arrangement"] = arrangement
    elif base is not None and base.get("arrangement") in allowed:
        cell["arrangement"] = base["arrangement"]
    else:
        cell["arrangement"] = "budding" if kind == "ellipsoid" else "single"
    return cell


def note_hints(text):
    """(shape, arrangement) found in free-text morphology notes, or Nones."""
    text = text or ""
    shape = next((k for rx, k in _NOTE_SHAPES if rx.search(text)), None)
    arr = next((a for rx, a in _NOTE_ARRANGEMENTS if rx.search(text)), None)
    return shape, arr


def describe_cell(cell):
    """'rods in chains', 'cocci in clusters', 'single oval yeast cells, budding'."""
    noun = {"coccus": "cocci", "rod": "rods", "coccobacillus": "coccobacilli",
            "curved_rod": "curved rods", "spiral": "spiral cells",
            "ellipsoid": "yeast cells"}[cell["kind"]]
    arr = cell.get("arrangement", "single")
    return {"single": f"single {noun}", "pair": f"{noun} in pairs",
            "chain": f"{noun} in chains", "tetrad": f"{noun} in tetrads",
            "cluster": f"{noun} in clusters", "packet": f"{noun} in packets of eight",
            "palisade": f"{noun} in palisades", "budding": f"budding {noun}"}.get(arr, noun)


def _gram_color(gram):
    g = (gram or "").lower()
    if g.startswith("pos"):
        return GRAM_POSITIVE_COLOR
    if g.startswith("neg"):
        return GRAM_NEGATIVE_COLOR
    if g.startswith("var"):
        return GRAM_VARIABLE_COLOR
    return None


def _copy(cfg):
    return dict(cell=dict(cfg["cell"]), colony=dict(cfg["colony"]))


def _get(org, key):
    try:
        return org[key] if key in org.keys() else None
    except (AttributeError, IndexError, KeyError):
        return None


def resolve_shape_config(org):
    """org: a sqlite3.Row or dict with genus/species/gram_stain and, optionally,
    shape_config, morphology_notes, cell_shape and cell_arrangement."""
    gram = (_get(org, "gram_stain") or "").lower()
    genus = (_get(org, "genus") or "").strip()
    species = (_get(org, "species") or "").strip()
    cfg = None
    custom = _get(org, "shape_config")
    if custom:
        try:
            c = json.loads(custom)
            if "cell" in c and "colony" in c:
                c["colony"] = dict(_COLONY_DEFAULTS, **c["colony"])
                c["cell"].setdefault("basis", "custom")
                cfg = c
        except (ValueError, TypeError):
            cfg = None

    if cfg is None:
        # Layer 1: the Gram stain, or a plain default
        if gram.startswith("neg"):
            cfg = dict(cell=_cell_of("rod", "single", radius=0.4, length=1.8, size_um="unknown"),
                       colony=dict(_COLONY_DEFAULTS, profile="raised", n_colonies=40,
                                   colony_radius_mm=(0.8, 2.0), texture_style="mucoid"))
            cfg["cell"]["basis"] = "gram"
        elif gram.startswith("pos"):
            cfg = dict(cell=_cell_of("coccus", "pair", size_um="unknown"),
                       colony=dict(_COLONY_DEFAULTS, profile="convex", n_colonies=50,
                                   colony_radius_mm=(0.5, 1.5), texture_style="glossy"))
            cfg["cell"]["basis"] = "gram"
        else:
            cfg = _copy(GENERIC_DEFAULT)
            cfg["cell"]["basis"] = "default"
        # Layer 2: the name
        hint = next((k for rx, k in _NAME_HINTS if rx.search(genus)), None)
        if hint and hint != cfg["cell"]["kind"]:
            cfg["cell"] = _cell_of(hint)
            cfg["cell"]["basis"] = "name"
        elif hint:
            cfg["cell"]["basis"] = "name"
        # Layer 3: the genus
        g = GENUS_CELLS.get(genus[:1].upper() + genus[1:].lower() if genus else "")
        if g:
            dims = {k: v for k, v in g.items() if k not in ("kind", "arrangement")}
            cfg["cell"] = _cell_of(g["kind"], g["arrangement"], **dims)
            cfg["cell"]["basis"] = "genus"
        # Layer 4: the species
        key = (genus, species)
        if key in BUILTIN_SHAPES:
            cfg = _copy(BUILTIN_SHAPES[key])
            cfg["cell"]["basis"] = "species"

    # Layer 5: the lab's own morphology notes
    shape, arr = note_hints(_get(org, "morphology_notes"))
    if shape or (arr and arr in ARRANGEMENTS_FOR.get(cfg["cell"]["kind"], ())):
        cfg["cell"] = _cell_of(shape or cfg["cell"]["kind"], arr, base=cfg["cell"])
        cfg["cell"]["basis"] = "notes"

    # Layer 6: what the lab saw with its latest Gram stain
    seen_kind = _get(org, "observed_shape")
    seen_arr = _get(org, "observed_arrangement")
    if seen_kind in CELL_DIMENSIONS:
        # an arrangement not recorded is the plain one, not an earlier guess
        seen_arr = seen_arr or ("budding" if seen_kind == "ellipsoid" else "single")
        cfg["cell"] = _cell_of(seen_kind, seen_arr, base=cfg["cell"])
        cfg["cell"]["basis"] = "gram_record"
        cfg["cell"]["basis_date"] = _get(org, "observed_date")

    # Layer 7: a correction made to the cell model on the strain page
    chosen_kind = _get(org, "cell_shape")
    chosen_arr = _get(org, "cell_arrangement")
    if chosen_kind in CELL_DIMENSIONS or (chosen_arr and chosen_arr in
                                         ARRANGEMENTS_FOR.get(cfg["cell"]["kind"], ())):
        kind = chosen_kind if chosen_kind in CELL_DIMENSIONS else cfg["cell"]["kind"]
        cfg["cell"] = _cell_of(kind, chosen_arr, base=cfg["cell"])
        cfg["cell"]["basis"] = "chosen"

    # The strain's own Gram record decides the colour of a bacterial cell; yeast
    # cells keep their cream colour.
    cell = cfg["cell"]
    if cell.get("kind") == "ellipsoid" or genus == "Schizosaccharomyces":
        if cell.get("basis") != "species" or "color" not in cell:
            cell["color"] = YEAST_COLOR
    else:
        col = _gram_color(gram)
        if col is not None:
            cell["color"] = col
        else:
            cell.setdefault("color", GENERIC_DEFAULT["cell"]["color"])
    return cfg


BASIS_TEXT = {
    "chosen": "corrected on this page",
    "gram_record": "as recorded with the latest Gram stain",
    "notes": "taken from the morphology notes",
    "species": "typical for this species",
    "genus": "typical for the genus",
    "name": "guessed from the name",
    "gram": "guessed from the Gram result only",
    "default": "not known for this organism",
    "custom": "set in the shape settings",
}
