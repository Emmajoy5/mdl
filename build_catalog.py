"""Build the microbial catalog SQLite DB + a self-contained searchable dashboard.

This is a standalone CLI companion to app.py -- useful for a quick static
dashboard.html you can open without running the Flask server. The live app
(app.py) is the primary tool; this covers the same seed data via a simpler,
read-only view.

Usage:  python build_catalog.py
Outputs: microbe_catalog.db, dashboard.html
"""
import json
import sqlite3
from pathlib import Path

from seed_data import build_seed_rows

DB_PATH = "microbe_catalog.db"
DASHBOARD_PATH = "dashboard.html"


def init_db(conn: sqlite3.Connection):
    schema = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)


def seed_db(conn: sqlite3.Connection):
    orgs, accs, tgs, rels = build_seed_rows()
    cur = conn.cursor()
    for o in orgs:
        cur.execute(
            """INSERT INTO organisms
               (collection_id, genus, species, strain, full_name, ncbi_taxonomy_id, notes,
                collection_type, added_by, viability_category, revival_interval_days, last_revival_date)
               VALUES (:collection_id, :genus, :species, :strain, :full_name, :ncbi_taxonomy_id, :notes,
                       :collection_type, :added_by, :viability_category, :revival_interval_days, :last_revival_date)""",
            o,
        )
    for a in accs:
        cur.execute(
            """INSERT INTO accessions (organism_id, culture_collection, accession_number)
               VALUES (:organism_id, :culture_collection, :accession_number)""",
            a,
        )
    for t in tgs:
        cur.execute(
            """INSERT INTO tags (organism_id, tag) VALUES (:organism_id, :tag)""", t
        )
    for r in rels:
        cur.execute(
            """INSERT INTO brewing_relevance
               (organism_id, role, attenuation_min, attenuation_max, temp_range_c,
                flavor_compounds, off_flavor_potential, spoilage_risk, application_notes)
               VALUES (:organism_id, :role, :attenuation_min, :attenuation_max, :temp_range_c,
                       :flavor_compounds, :off_flavor_potential, :spoilage_risk, :application_notes)""",
            r,
        )
    conn.commit()


def load_catalog(conn: sqlite3.Connection, tenant_id=None) -> dict:
    """Flatten the catalog into a JSON-friendly list for the dashboard. With a
    tenant_id, only that lab's strains (a multi-lab database needs one)."""
    cur = conn.cursor()
    sql = """SELECT organism_id, collection_id, genus, species, strain, full_name,
                    ncbi_taxonomy_id, target_status, collection_type, notes
             FROM organisms {where} ORDER BY collection_id"""
    if tenant_id is not None:
        cur.execute(sql.format(where="WHERE tenant_id=?"), (tenant_id,))
    else:
        cur.execute(sql.format(where=""))
    orgs = cur.fetchall()
    data = []
    for row in orgs:
        oid, cid, genus, species, strain, full_name, ncbi, status, ctype, notes = row
        accs = [f"{c} {n}" for (c, n) in cur.execute(
            "SELECT culture_collection, accession_number FROM accessions WHERE organism_id=? ORDER BY culture_collection", (oid,)
        ).fetchall()]
        tags = [t for (t,) in cur.execute(
            "SELECT tag FROM tags WHERE organism_id=? ORDER BY tag", (oid,)
        ).fetchall()]
        rel = cur.execute(
            "SELECT role, attenuation_min, attenuation_max, temp_range_c, flavor_compounds,"
            " off_flavor_potential, spoilage_risk, application_notes FROM brewing_relevance WHERE organism_id=?",
            (oid,),
        ).fetchone()
        model_rows = cur.execute(
            "SELECT COUNT(*), SUM(is_print_ready) FROM models WHERE organism_id=?", (oid,)
        ).fetchone()
        img_rows = cur.execute(
            "SELECT COUNT(*) FROM plate_images WHERE organism_id=?", (oid,)
        ).fetchone()
        data.append({
            "collection_id": cid,
            "full_name": full_name,
            "genus": genus,
            "species": species,
            "strain": strain,
            "ncbi": ncbi,
            "status": status,
            "collection_type": ctype,
            "notes": notes,
            "accessions": accs,
            "tags": tags,
            "role": rel[0] if rel else "",
            "attenuation": _fmt_range(rel[1], rel[2]) if rel else "",
            "temp_range": rel[3] if rel else "",
            "flavor": rel[4] if rel else "",
            "off_flavor": rel[5] if rel else "",
            "spoilage_risk": rel[6] if rel else "",
            "application": rel[7] if rel else "",
            "model_count": model_rows[0] if model_rows else 0,
            "print_ready": (model_rows[1] or 0) if model_rows else 0,
            "image_count": img_rows[0] if img_rows else 0,
        })
    return {"records": data,
            "stats": {
                "species": len(data),
                "print_ready": sum(1 for d in data if d["print_ready"] > 0),
                "models": sum(d["model_count"] for d in data),
                "images": sum(d["image_count"] for d in data),
            }}


def _fmt_range(a, b):
    if a is None and b is None:
        return ""
    if a is None:
        return f"≤{b:g}"
    if b is None:
        return f"≥{a:g}"
    return f"{a:g} to {b:g}%"


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Microbial Digital Library | Catalog</title>
<style>
  :root { --bg:#0f1420; --card:#1a2130; --line:#2a3347; --text:#e6ebf5; --muted:#8b96ab;
          --accent:#5bc0de; --good:#4cc38a; --warn:#f0b429; --bad:#f05252; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         background:var(--bg); color:var(--text); line-height:1.5; }
  header { padding:22px 28px 14px; border-bottom:1px solid var(--line); }
  header h1 { margin:0 0 4px; font-size:20px; }
  header p { margin:0; color:var(--muted); font-size:13px; }
  .stats { display:flex; gap:12px; padding:14px 28px; flex-wrap:wrap; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 16px; min-width:110px; }
  .stat b { display:block; font-size:22px; }
  .stat span { color:var(--muted); font-size:12px; }
  .controls { display:flex; gap:10px; padding:4px 28px 16px; flex-wrap:wrap; }
  input, select { background:var(--card); border:1px solid var(--line); color:var(--text);
                  border-radius:8px; padding:8px 12px; font-size:14px; }
  #search { flex:1; min-width:220px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:16px; padding:6px 28px 40px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }
  .card h3 { margin:0 0 2px; font-size:16px; }
  .cid { color:var(--accent); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
  .meta { color:var(--muted); font-size:12px; margin:6px 0; }
  .row { display:flex; justify-content:space-between; font-size:13px; padding:3px 0; border-top:1px dashed var(--line); }
  .row b { font-weight:600; color:var(--muted); }
  .tag { display:inline-block; background:#223047; color:#a9b6cf; border-radius:6px; padding:1px 8px; font-size:11px; margin:2px 3px 0 0; }
  .badge { font-size:11px; font-weight:600; border-radius:6px; padding:1px 7px; }
  .b-good { background:rgba(76,195,138,.15); color:var(--good); }
  .b-warn { background:rgba(240,180,41,.15); color:var(--warn); }
  .b-bad  { background:rgba(240,82,82,.15); color:var(--bad); }
  .b-muted{ background:#223047; color:#8b96ab; }
  .empty { color:var(--muted); padding:30px; text-align:center; }
  footer { color:var(--muted); font-size:12px; padding:0 28px 30px; }
</style>
</head>
<body>
<header>
  <h1>Microbial Digital Library</h1>
  <p>Brewing-relevant microbes: searchable catalog. Illustrative seed data; confirm against culture-collection records.</p>
</header>
<div class="stats" id="stats"></div>
<div class="controls">
  <input id="search" type="search" placeholder="Search name, ID, tag, flavor, notes...">
  <select id="f-genus"><option value="">All genera</option></select>
  <select id="f-type"><option value="">All collection types</option></select>
  <select id="f-role"><option value="">All roles</option></select>
  <select id="f-risk"><option value="">All spoilage risks</option></select>
  <select id="f-print"><option value="">Any print status</option>
      <option value="ready">Print-ready</option><option value="none">Not yet printable</option></select>
</div>
<div class="grid" id="grid"></div>
<div class="empty" id="empty" hidden>No records match.</div>
<footer>Generated by build_catalog.py. The live app (app.py) is the primary tool; this is a static snapshot.</footer>

<script>
const DATA = __DATA__;
const esc = s => (s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function riskBadge(r){ const m={none:'b-muted',low:'b-good',medium:'b-warn',high:'b-bad'}; return `<span class="badge ${m[r]||'b-muted'}">${esc(r||'n/a')}</span>`; }
function printBadge(d){ return d.print_ready>0 ? `<span class="badge b-good">print-ready (${d.print_ready})</span>` : `<span class="badge b-muted">not printable</span>`; }
function typeBadge(t){ return t==='validation' ? `<span class="badge b-warn">validation</span>` : `<span class="badge b-muted">${esc(t||'library')}</span>`; }

function card(d){
  const accs = (d.accessions||[]).map(a=>`<span class="tag">${esc(a)}</span>`).join('');
  const tags = (d.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join('');
  return `<div class="card">
    <div class="cid">${esc(d.collection_id)} ${typeBadge(d.collection_type)}</div>
    <h3>${esc(d.full_name)}</h3>
    <div class="meta">${esc(d.genus)} ${esc(d.species)}${d.strain?' · '+esc(d.strain):''}${d.ncbi?' · NCBI txid '+esc(d.ncbi):''} · ${esc(d.status)}</div>
    <div class="row"><b>Brewing role</b><span>${esc(d.role||'n/a')}</span></div>
    <div class="row"><b>Temp range</b><span>${esc(d.temp_range||'n/a')} °C</span></div>
    <div class="row"><b>Attenuation</b><span>${esc(d.attenuation||'n/a')}</span></div>
    <div class="row"><b>Flavor</b><span>${esc(d.flavor||'n/a')}</span></div>
    <div class="row"><b>Off-flavor</b><span>${esc(d.off_flavor||'n/a')}</span></div>
    <div class="row"><b>Spoilage risk</b><span>${riskBadge(d.spoilage_risk)}</span></div>
    <div class="row"><b>Assets</b><span>${d.model_count} model(s) · ${d.image_count} image(s) · ${printBadge(d)}</span></div>
    ${d.application?`<div class="row"><b>Notes</b><span>${esc(d.application)}</span></div>`:''}
    ${accs?`<div class="meta">${accs}</div>`:''}
    ${tags?`<div class="meta">${tags}</div>`:''}
  </div>`;
}

function render(){
  const q = document.getElementById('search').value.toLowerCase();
  const g = document.getElementById('f-genus').value;
  const ty = document.getElementById('f-type').value;
  const r = document.getElementById('f-role').value;
  const k = document.getElementById('f-risk').value;
  const p = document.getElementById('f-print').value;
  let rows = DATA.records.filter(d => {
    if(g && d.genus!==g) return false;
    if(ty && d.collection_type!==ty) return false;
    if(r && d.role!==r) return false;
    if(k && d.spoilage_risk!==k) return false;
    if(p==='ready' && d.print_ready<=0) return false;
    if(p==='none' && d.print_ready>0) return false;
    if(q){
      const hay = [d.collection_id,d.full_name,d.genus,d.species,d.strain,(d.tags||[]).join(' '),
                   d.role,d.flavor,d.off_flavor,d.notes,d.application,(d.accessions||[]).join(' ')].join(' ').toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  document.getElementById('grid').innerHTML = rows.map(card).join('');
  document.getElementById('empty').hidden = rows.length>0;
}

function init(){
  const st = DATA.stats||{};
  const stats = document.getElementById('stats');
  stats.innerHTML = `<div class="stat"><b>${st.species||0}</b><span>species</span></div>
    <div class="stat"><b>${st.models||0}</b><span>3D models</span></div>
    <div class="stat"><b>${st.print_ready||0}</b><span>print-ready species</span></div>
    <div class="stat"><b>${st.images||0}</b><span>plate images</span></div>`;
  const gen = [...new Set(DATA.records.map(d=>d.genus))].sort();
  const types = [...new Set(DATA.records.map(d=>d.collection_type))].sort();
  const roles = [...new Set(DATA.records.map(d=>d.role).filter(Boolean))].sort();
  const risks = [...new Set(DATA.records.map(d=>d.spoilage_risk).filter(Boolean))].sort();
  const fill = (id,vals)=>{ const s=document.getElementById(id); vals.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;s.appendChild(o);}); };
  fill('f-genus',gen); fill('f-type',types); fill('f-role',roles); fill('f-risk',risks);
  ['search','f-genus','f-type','f-role','f-risk','f-print'].forEach(id=>document.getElementById(id).addEventListener('input',render));
  render();
}
document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>
"""


def main():
    import sys
    conn = sqlite3.connect(DB_PATH)
    multi_lab = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tenants'").fetchone() is not None
    if multi_lab:
        # The app's own database: never create or seed tables here, and never
        # put several labs' strains in one page (old databases can hold several).
        if len(sys.argv) >= 3 and sys.argv[1] == "--tenant":
            tid = int(sys.argv[2])
        else:
            ids = [r[0] for r in conn.execute("SELECT tenant_id FROM tenants")]
            if len(ids) != 1:
                conn.close()
                sys.exit("This database holds several labs. Run: python build_catalog.py --tenant <lab id>")
            tid = ids[0]
        data = load_catalog(conn, tid)
    else:
        init_db(conn)
        seed_db(conn)
        data = load_catalog(conn)
    conn.close()
    html = DASHBOARD_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    Path(DASHBOARD_PATH).write_text(html, encoding="utf-8")
    print(f"Wrote {DB_PATH} with {data['stats']['species']} species.")
    print(f"Wrote {DASHBOARD_PATH} ({len(html):,} bytes).")


if __name__ == "__main__":
    main()
