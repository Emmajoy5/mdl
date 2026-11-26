"""Seed data for the Microbial Digital Library.

Values for brewing relevance are representative/illustrative and strain-specific;
treat them as placeholders to be confirmed against culture-collection records.
"""
from datetime import date, timedelta

_TODAY = date.today()


def _days_ago(n):
    return (_TODAY - timedelta(days=n)).isoformat()


def _collection_id(genus: str, species: str, n: int) -> str:
    g = (genus[:3] or "XXX").upper()
    s = ("SPP" if species.lower() in ("spp.", "spp", "sp.", "sp") else species[:3]).upper()
    return f"MDL-{g}{s}-{n:03d}"


# Each organism: genus/species/strain/full_name/ncbi_id/notes/accessions/tags/relevance
# plus library/validation collection_type and viability-tracking fields.
SEED_ORGANISMS = [
    dict(
        genus="Saccharomyces", species="cerevisiae", strain=None,
        full_name="Saccharomyces cerevisiae",
        ncbi_id=4932,
        notes="Primary ale / top-fermenting yeast; the workhorse of brewing.",
        collection_type="library",
        gram_stain="not_done",  # yeast -- Gram stain isn't routinely used for ID here
        accessions=[("NCYC", "505"), ("ATCC", "18824")],
        tags=["ale", "fermentation", "top-fermenting", "ethanol"],
        relevance=dict(role="fermentation", attenuation_min=70.0, attenuation_max=85.0,
                       temp_range_c="18-24",
                       flavor_compounds="ethanol, CO2, esters, higher alcohols",
                       off_flavor_potential="high ester/fusel if over-pitched or hot",
                       spoilage_risk="none",
                       application_notes="Standard ale strain; flocculation varies by strain."),
        added_by="Lab import", viability_category="stable",
        revival_interval_days=None, last_revival_date=None,
    ),
    dict(
        genus="Saccharomyces", species="pastorianus", strain=None,
        full_name="Saccharomyces pastorianus",
        ncbi_id=27292,
        notes="Lager / bottom-fermenting yeast (hybrid of S. cerevisiae x S. eubayanus).",
        collection_type="library",
        gram_stain="not_done",
        accessions=[("NCYC", "679")],
        tags=["lager", "fermentation", "bottom-fermenting", "cold"],
        relevance=dict(role="fermentation", attenuation_min=68.0, attenuation_max=80.0,
                       temp_range_c="8-14",
                       flavor_compounds="clean profile, low esters, sulfur (H2S) in some strains",
                       off_flavor_potential="diacetyl if diacetyl rest skipped",
                       spoilage_risk="none",
                       application_notes="Cold fermentation; longer maturation typical."),
        added_by="Lab import", viability_category="stable",
        revival_interval_days=None, last_revival_date=None,
    ),
    dict(
        genus="Brettanomyces", species="spp.", strain=None,
        full_name="Brettanomyces spp. (e.g. B. bruxellensis)",
        ncbi_id=None,
        notes="Wild yeast; contributes funk/brett character and is a classic spoilage organism.",
        collection_type="library",
        gram_stain="not_done",
        accessions=[("NCYC", "928")],
        tags=["wild", "sour", "spoilage", "4-ethylphenol", "funk"],
        relevance=dict(role="spoilage", attenuation_min=None, attenuation_max=None,
                       temp_range_c="15-30",
                       flavor_compounds="4-ethylphenol, 4-ethylguaiacol, acetic acid, ethyl acetate",
                       off_flavor_potential="barnyard/band-aid (phenolic) at threshold",
                       spoilage_risk="high",
                       application_notes="Valued in lambic/sour and barrel-aged beers; spoilage in clean beer."),
        added_by="Lab import", viability_category="periodic",
        # Seeded to land in the amber "due soon" early-warning band (within
        # DUE_SOON_WINDOW_DAYS) relative to whenever the app/tests actually
        # run, rather than a fixed calendar date that would drift out of
        # that band as real time passes.
        revival_interval_days=180, last_revival_date=_days_ago(180 - 4),
    ),
    dict(
        genus="Levilactobacillus", species="brevis", strain=None,
        full_name="Levilactobacillus brevis",
        ncbi_id=1580,
        notes="Lactic-acid bacterium (formerly Lactobacillus brevis; reclassified by Zheng et al. 2020); "
              "used for souring and a common spoilage organism.",
        collection_type="library",
        gram_stain="positive",
        accessions=[("DSM", "20054")],
        tags=["sour", "lactic acid", "spoilage", "kettle-sour"],
        relevance=dict(role="souring", attenuation_min=None, attenuation_max=None,
                       temp_range_c="15-40",
                       flavor_compounds="lactic acid, diacetyl",
                       off_flavor_potential="lactic sourness, diacetyl",
                       spoilage_risk="high",
                       application_notes="Common in kettle-souring and mixed fermentations."),
        added_by="Lab import", viability_category="stable",
        revival_interval_days=None, last_revival_date=None,
    ),
    dict(
        genus="Pediococcus", species="damnosus", strain=None,
        full_name="Pediococcus damnosus",
        ncbi_id=51663,
        notes="Lactic-acid bacterium; causes 'sick beer' (ropiness) and diacetyl in spoilage.",
        collection_type="library",
        gram_stain="positive",
        accessions=[("DSM", "20331")],
        tags=["sour", "spoilage", "diacetyl", "ropiness", "exopolysaccharide"],
        relevance=dict(role="spoilage", attenuation_min=None, attenuation_max=None,
                       temp_range_c="10-35",
                       flavor_compounds="lactic acid, diacetyl, exopolysaccharide (rope)",
                       off_flavor_potential="diacetyl, ropiness ('sick beer')",
                       spoilage_risk="high",
                       application_notes="A major beer-spoilage concern; rarely used deliberately."),
        added_by="Lab import", viability_category="stable",
        revival_interval_days=None, last_revival_date=None,
    ),
    dict(
        genus="Acetobacter", species="spp.", strain=None,
        full_name="Acetobacter spp. (e.g. A. aceti)",
        ncbi_id=None,
        notes="Acetic-acid bacterium; oxidizes ethanol to acetic acid (vinegar).",
        collection_type="library",
        gram_stain="negative",
        accessions=[("ATCC", "15973")],
        tags=["acetic acid", "vinegar", "spoilage", "aerobic"],
        relevance=dict(role="spoilage", attenuation_min=None, attenuation_max=None,
                       temp_range_c="20-30",
                       flavor_compounds="acetic acid, ethyl acetate, acetaldehyde",
                       off_flavor_potential="vinegar sourness, solvent/ester off-notes",
                       spoilage_risk="high",
                       application_notes="Obligate aerobe; spoilage via oxygen ingress."),
        # acetic acid bacteria lose viability readily in storage, so they are
        # checked on a schedule rather than treated as stable
        added_by="Lab import", viability_category="periodic",
        revival_interval_days=60, last_revival_date=_days_ago(41),
    ),
    dict(
        genus="Bacillus", species="subtilis", strain="ATCC 6633",
        full_name="Bacillus subtilis ATCC 6633",
        ncbi_id=1423,
        notes="Validation/QC panel strain: biological indicator used for lab validation, "
             "not part of general brewing stock.",
        collection_type="validation",
        gram_stain="positive",
        accessions=[("ATCC", "6633")],
        tags=["validation", "biological indicator", "QC", "spore-former"],
        relevance=dict(role="other", attenuation_min=None, attenuation_max=None,
                       temp_range_c="30-37",
                       flavor_compounds="n/a",
                       off_flavor_potential="n/a",
                       spoilage_risk="none",
                       application_notes="Used as a validation/QC reference strain, not pitched into product."),
        added_by="Lab import", viability_category="periodic",
        # Seeded to land overdue (not just "due"), relative to whenever the
        # app/tests actually run -- see the _days_ago() note above.
        revival_interval_days=90, last_revival_date=_days_ago(90 + 23),
    ),
]


def build_seed_rows():
    """Return (organisms, accessions, tags, relevance) row lists for insertion."""
    orgs, accs, tgs, rels = [], [], [], []
    for i, o in enumerate(SEED_ORGANISMS, start=1):
        cid = _collection_id(o["genus"], o["species"], i)
        orgs.append(dict(
            collection_id=cid, genus=o["genus"], species=o["species"],
            strain=o["strain"], full_name=o["full_name"], ncbi_taxonomy_id=o["ncbi_id"],
            notes=o["notes"], collection_type=o["collection_type"],
            gram_stain=o.get("gram_stain"),
            added_by=o["added_by"], viability_category=o["viability_category"],
            revival_interval_days=o["revival_interval_days"],
            last_revival_date=o["last_revival_date"],
        ))
        for cc, num in o["accessions"]:
            accs.append(dict(organism_id=i, culture_collection=cc, accession_number=num))
        for t in o["tags"]:
            tgs.append(dict(organism_id=i, tag=t))
        rels.append(dict(organism_id=i, **o["relevance"]))
    return orgs, accs, tgs, rels
