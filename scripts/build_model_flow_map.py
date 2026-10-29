"""Build the research planning diagrams from one local, validated map.

This does not ingest data or train models. Run with --check to detect stale views.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from model_flow_overview import render_overview
from model_flow_validation import ROLES, load_json, validate, verify_exports

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "contracts"
COLORS = {
    "source": "fill:#fff1f2,stroke:#be123c,color:#881337",
    "data": "fill:#f0fdf4,stroke:#15803d,color:#14532d",
    "model": "fill:#fff7ed,stroke:#c2410c,color:#7c2d12",
    "output": "fill:#eff6ff,stroke:#1d4ed8,color:#1e3a8a",
}


def label(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def markdown(plan: dict) -> str:
    lines = [
        "# Projenin kaynak–alan–model–sonuç haritası",
        "",
        "Üretilmiş görünüm: `model-flow-map.v0.1.json` dosyasını düzenleyip",
        "`python scripts/build_model_flow_map.py` çalıştırın. Eğitim yapılmış değildir.",
        "",
        "Kırmızı: kaynak · Yeşil: veri · Turuncu: model/analiz · Mavi: sonuç.",
        "Düz ok: temel özellik · Kesikli ok: aday bağlam veya uygunluk kontrolü.",
        "Yorum/değerlendirme alanları modele girdi oku çizilmeden ayrıca listelenir.",
        "",
        "## Kaynaklar ve model planındaki alanlar",
        "",
    ]
    for sid, source in plan["sources"].items():
        lines += [f"### {source['label']}", "", source["status"], ""]
        for fid, field in plan["fields"].items():
            if field["source"] == sid:
                provenance = field.get(
                    "schema_ref", "Türetilir: " + ", ".join(field.get("derived_from", []))
                )
                lines.append(f"- `{fid}` — {field['label']}. `{provenance}`")
        lines.append("")
    for model in plan["models"]:
        lines += [f"## {model['id']} — {model['label']}", "", model["question"], ""]
        lines += ["```mermaid", "flowchart TB"]
        source_ids = list(
            dict.fromkeys(
                plan["fields"][fid]["source"] for fid, role in model["inputs"] if role != "review"
            )
        )
        for sid in source_ids:
            source_label = label(plan["sources"][sid]["label"])
            lines.append(f'    s_{sid}["KAYNAK: {source_label}"]')
        field_ids = []
        for fid, role in model["inputs"]:
            if role == "review":
                continue
            field_ids.append(fid)
            field = plan["fields"][fid]
            lines.append(f'    s_{field["source"]} --> f_{fid}["{fid}: {label(field["label"])}"]')
            edge = "-->" if role == "feature" else f"-.->|{ROLES[role]}|"
            lines.append(f"    f_{fid} {edge} model")
        lines += [
            f'    model["{model["id"]}: {label(model["label"])}"]',
            f'    model --> output["{label(model["question"])}"]',
        ]
        for name, colors in COLORS.items():
            lines.append(f"    classDef {name} {colors};")
        lines += [
            f"    class {','.join('s_' + sid for sid in source_ids)} source;",
            f"    class {','.join('f_' + fid for fid in field_ids)} data;",
            "    class model model;",
            "    class output output;",
            "```",
            "",
        ]
        for key, title in [
            ("stage", "Aşama"),
            ("algorithm", "İlk yöntem adayı"),
            ("baseline", "Karşılaştırma"),
            ("target", "Hedef"),
            ("metrics", "Değerlendirme"),
            ("split", "Veri bölmesi"),
            ("requires", "Gerekli veri"),
            ("open", "Açık karar"),
        ]:
            lines += [f"**{title}:** {model[key]}", ""]
        if model.get("documentation"):
            lines += [f"[Yöntemin resmi belgeleri]({model['documentation']})", ""]
        if design := model.get("experiment_design"):
            lines += [f"**Kararlaştırılan deney:** {design['label']}", ""]
        lines += ["**Varyantlar ve koşullu kontroller:**", ""]
        for variant in model["variants"]:
            contexts = ", ".join(variant["context_fields"]) or "yok"
            gates = ", ".join(variant["gate_fields"]) or "ek kontrol yok"
            lines += [f"- {variant['label']}: aday bağlam `{contexts}`; `{gates}`."]
        lines += [
            "",
            "Temel kontroller: " + ", ".join(model["base_gates"]) + ". "
            "Varsa kullanılan ek kanıt: " + (", ".join(model["optional_gates"]) or "yok") + ".",
            "",
        ]
        reviews = [fid for fid, role in model["inputs"] if role == "review"]
        if reviews:
            lines += [
                "**Eğitim girdisi olmayan yorum/değerlendirme alanları:** "
                + ", ".join(f"`{fid}`" for fid in reviews)
                + ".",
                "",
            ]
    lines += ["## Ortak koşullar", ""]
    lines += [f"- {rule}" for rule in plan["global_controls"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--fragment", type=Path, help="Optional second fragment copy for preview")
    args = parser.parse_args()
    plan = load_json(DOCS / "model-flow-map.v0.1.json")
    contract = load_json(DOCS / plan["contract"])
    validate(plan, contract)
    contract_digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    data = json.dumps({**plan, "contract_sha256": contract_digest}, ensure_ascii=False).replace(
        "</", "<\\/"
    )
    template = (DOCS / "model-flow-view.template.html").read_text(encoding="utf-8")
    if template.count("__MODEL_FLOW_DATA__") != 1:
        raise ValueError("Expected exactly one data placeholder")
    fragment = template.replace("__MODEL_FLOW_DATA__", data)
    page = (DOCS / "model-flow-page.template.html").read_text(encoding="utf-8")
    if page.count("__MODEL_FLOW_FRAGMENT__") != 1:
        raise ValueError("Expected exactly one page fragment placeholder")
    artifacts = {
        DOCS / "model-flow-map.fragment.html": fragment,
        DOCS / "model-flow-map.md": markdown(plan),
        DOCS / "model-flow-map.html": page.replace("__MODEL_FLOW_FRAGMENT__", fragment),
        DOCS / "model-flow-overview.svg": render_overview(plan, contract_digest),
    }
    if args.fragment:
        artifacts[args.fragment.resolve()] = fragment
    for path, content in artifacts.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise ValueError(f"Stale or missing generated view: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    try:
        verify_exports(DOCS, artifacts[DOCS / "model-flow-overview.svg"])
    except ValueError:
        if args.check:
            raise
        print("PNG/PDF exports need regeneration: node scripts/export_model_flow_graph.cjs")
    print(
        f"OK: {len(plan['sources'])} sources, {len(plan['fields'])} fields, "
        f"{len(plan['models'])} flows; views {'checked' if args.check else 'built'}."
    )


if __name__ == "__main__":
    main()
