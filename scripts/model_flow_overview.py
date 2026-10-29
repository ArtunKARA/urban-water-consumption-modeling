"""Vector poster of every research model flow, derived from the shared plan."""

from __future__ import annotations

import hashlib
import json
import math
import textwrap
from dataclasses import dataclass
from xml.etree.ElementTree import Element, SubElement, tostring

from model_flow_validation import model_levels


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def render_overview(plan: dict, contract_digest: str | None = None) -> str:
    fields = plan["fields"]
    source_order = [sid for sid, source in plan["sources"].items() if source["kind"] == "external"]
    levels = model_levels(plan)
    primary = {
        sid: [fid for fid, f in fields.items() if f["source"] == sid and "derived_from" not in f]
        for sid in source_order
    }
    columns = {sid: max(1, math.ceil(len(primary[sid]) / 5)) for sid in source_order}
    group_widths = {sid: columns[sid] * 184 + 16 for sid in source_order}
    width = sum(group_widths.values()) + (len(source_order) - 1) * 24 + 80
    raw_bottom = (
        236 + max(math.ceil(len(primary[sid]) / columns[sid]) for sid in source_order) * 120
    )
    derived_y = raw_bottom + 150
    model_y = derived_y + 310
    output_y = model_y + 174
    height = output_y + 296 + 174 * max(levels.values())
    svg = Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": str(width),
            "height": str(height),
            "viewBox": f"0 0 {width} {height}",
            "role": "img",
            "aria-labelledby": "poster-title poster-description",
        },
    )
    SubElement(svg, "title", {"id": "poster-title"}).text = "Projenin toplu model akışları"
    SubElement(svg, "desc", {"id": "poster-description"}).text = (
        f"Bütün kaynaklar, {len(fields)} alan ve {len(plan['models'])} model/analiz aynı grafikte. "
        "Yorum verileri eğitim düğümüne değil sonuç düğümüne bağlanır. "
        "Ara çıktılar üreten modelden tüketen modele taşınır. "
        "Bölgesel uzantı bağımsız bölge verisine bağlıdır. Eğitim yapılmamıştır."
    )
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    SubElement(svg, "metadata").text = json.dumps(
        {
            "plan_sha256": digest,
            "contract_sha256": contract_digest,
            "field_ids": list(fields),
            "model_ids": [m["id"] for m in plan["models"]],
            "source_ids": list(plan["sources"]),
            "training_executed": False,
        }
    )
    SubElement(svg, "style").text = """
      text { font-family: 'Segoe UI', Arial, sans-serif; fill:#20313f; }
      .node rect { stroke:#b8c6cf; stroke-width:1.2; }
      .source rect { fill:#fff0f2; }
      .field rect { fill:#edf8ef; }
      .derived rect { fill:#e3f4e8; }
      .model rect { fill:#fff0dc; }
      .output rect { fill:#eaf2ff; }
      .edge { fill:none; stroke-width:1.35; }
      .feature { stroke:#536b7b; opacity:.48; }
      .context { stroke:#b37b33; opacity:.42; stroke-dasharray:7 5; }
      .gate { stroke:#7e8b95; opacity:.4; stroke-dasharray:2 5; }
      .review { stroke:#4979bb; opacity:.42; stroke-dasharray:9 4 2 4; }
      .origin { stroke:#89969d; opacity:.38; }
      .derivation { stroke:#4d8064; opacity:.4; stroke-dasharray:3 4; }
    """
    SubElement(svg, "rect", {"width": str(width), "height": str(height), "fill": "#ffffff"})
    defs = SubElement(svg, "defs")
    for role, color in [
        ("feature", "#536b7b"),
        ("context", "#b37b33"),
        ("gate", "#7e8b95"),
        ("review", "#4979bb"),
        ("origin", "#89969d"),
        ("derivation", "#4d8064"),
    ]:
        marker = SubElement(
            defs,
            "marker",
            {
                "id": f"arrow-{role}",
                "viewBox": "0 0 10 10",
                "refX": "8",
                "refY": "5",
                "markerWidth": "6",
                "markerHeight": "6",
                "orient": "auto",
            },
        )
        SubElement(marker, "path", {"d": "M 0 0 L 10 5 L 0 10 z", "fill": color})
    edges = SubElement(svg, "g", {"id": "connections"})
    nodes = SubElement(svg, "g", {"id": "nodes"})
    box_ids: dict[Box, str] = {}

    def text(
        x: float,
        y: float,
        value: str,
        size: int = 16,
        parent: Element = nodes,
        anchor: str = "start",
        weight: int = 400,
    ) -> None:
        SubElement(
            parent,
            "text",
            {
                "x": str(x),
                "y": str(y),
                "font-size": str(size),
                "text-anchor": anchor,
                "font-weight": str(weight),
            },
        ).text = value

    def wrapped(
        box: Box,
        value: str,
        start: float,
        chars: int,
        size: int = 16,
        parent: Element = nodes,
        center: bool = False,
    ) -> None:
        for i, line in enumerate(textwrap.wrap(value, chars, break_long_words=False)):
            text(
                box.cx if center else box.x + 12,
                box.y + start + i * (size + 5),
                line,
                size,
                parent,
                "middle" if center else "start",
            )

    def node(key: str, box: Box, kind: str, title: str) -> Element:
        box_ids[box] = key
        group = SubElement(nodes, "g", {"id": key, "class": f"node {kind}", "data-kind": kind})
        SubElement(group, "title").text = title
        SubElement(
            group,
            "rect",
            {
                "x": str(box.x),
                "y": str(box.y),
                "width": str(box.w),
                "height": str(box.h),
                "rx": "11",
            },
        )
        return group

    def edge(
        start: Box,
        target: Box,
        role: str,
        fid: str,
        mid: str,
        raw: bool = False,
        side: bool = False,
    ) -> None:
        if side:
            # Review records arrive at the result, outside the fit-model column.
            lane_x = start.x + start.w + 6
            end_x = target.x - 9
            d = (
                f"M {start.x + start.w} {start.cy} H {lane_x} V {raw_bottom + 65} "
                f"C {lane_x} {raw_bottom + 100}, {end_x} {target.y - 60}, "
                f"{end_x} {target.cy} H {target.x}"
            )
        elif raw:
            lane_x = start.x + start.w + 6
            d = (
                f"M {start.x + start.w} {start.cy} H {lane_x} V {raw_bottom + 35} "
                f"C {lane_x} {raw_bottom + 85}, {target.cx} {target.y - 70}, "
                f"{target.cx} {target.y}"
            )
        else:
            d = (
                f"M {start.cx} {start.y + start.h} "
                f"C {start.cx} {start.y + start.h + 70}, "
                f"{target.cx} {target.y - 70}, {target.cx} {target.y}"
            )
        SubElement(
            edges,
            "path",
            {
                "class": f"edge {role}",
                "d": d,
                "marker-end": f"url(#arrow-{role})",
                "data-field": fid,
                "data-model": mid,
                "data-role": role,
                "data-from": box_ids[start],
                "data-to": box_ids[target],
            },
        )

    text(40, 46, "Kentsel su araştırması · Toplu model akış haritası", 29, weight=500)
    text(40, 74, "Kaynak → alan → türetilen özellik → model / analiz → çıktı", 17)
    text(width - 40, 45, "v0.1 · Planlama", 16, anchor="end")
    text(width - 40, 70, "Eğitimler çalıştırılmadı", 14, anchor="end")
    for i, (name, fill) in enumerate(
        [
            ("Kaynak", "#f9d9e0"),
            ("Veri / özellik", "#ccebd5"),
            ("Model / analiz", "#ffe0b1"),
            ("Çıktı", "#d0e1ff"),
        ]
    ):
        x = 40 + i * 230
        SubElement(
            nodes,
            "rect",
            {"x": str(x), "y": "93", "width": "18", "height": "18", "rx": "4", "fill": fill},
        )
        text(x + 26, 108, name, 14)
    source_boxes: dict[str, Box] = {}
    field_boxes: dict[str, Box] = {}
    x = 40
    for sid in source_order:
        group_w = group_widths[sid]
        source = Box(x + 8, 142, group_w - 16, 64)
        source_boxes[sid] = source
        group = node("source-" + sid, source, "source", plan["sources"][sid]["status"])
        wrapped(
            source,
            plan["sources"][sid]["label"],
            26,
            max(18, int((source.w - 24) / 8.5)),
            parent=group,
            center=True,
        )
        for index, fid in enumerate(primary[sid]):
            row, col = divmod(index, columns[sid])
            box = Box(x + 16 + col * 184, 236 + row * 120, 166, 106)
            field_boxes[fid] = box
            fgroup = node("field-" + fid, box, "field", fields[fid]["label"])
            fgroup.set("data-field-id", fid)
            text(box.x + 12, box.y + 20, fid, 12, fgroup, weight=500)
            wrapped(box, fields[fid]["label"], 43, 19, 15, fgroup)
            rail_x = box.x - 7
            d = (
                f"M {source.cx} {source.y + source.h} V {source.y + source.h + 16} "
                f"H {rail_x} V {box.cy} H {box.x}"
            )
            SubElement(
                edges,
                "path",
                {
                    "d": d,
                    "class": "edge origin",
                    "marker-end": "url(#arrow-origin)",
                    "data-source": sid,
                    "data-field": fid,
                    "data-from": "source-" + sid,
                    "data-to": "field-" + fid,
                },
            )
        x += group_w + 24

    text(40, raw_bottom + 118, "ÖLÇÜMLERDEN VE KAYITLARDAN TÜRETİLEN ÖZELLİKLER", 14, weight=500)
    derived_ids = [fid for fid, field in fields.items() if "derived_from" in field]
    derived_w = (width - 80 - 24 * (len(derived_ids) - 1)) / len(derived_ids) if derived_ids else 0
    for i, fid in enumerate(derived_ids):
        box = Box(40 + i * (derived_w + 24), derived_y, derived_w, 110)
        field_boxes[fid] = box
        group = node("field-" + fid, box, "derived", fields[fid]["definition"])
        group.set("data-field-id", fid)
        text(box.x + 12, box.y + 20, fid + " · türetilir", 12, group, weight=500)
        wrapped(box, fields[fid]["label"], 44, max(20, int((box.w - 24) / 8)), 15, group)
    for fid in derived_ids:
        for ancestor in fields[fid]["derived_from"]:
            start, target = field_boxes[ancestor], field_boxes[fid]
            if ancestor in derived_ids:
                # Same-layer derivations use a channel below the feature boxes.
                y = derived_y + 130 + derived_ids.index(fid) * 4
                d = f"M {start.cx} {start.y + start.h} V {y} H {target.cx} V {target.y + target.h}"
                SubElement(
                    edges,
                    "path",
                    {
                        "d": d,
                        "class": "edge derivation",
                        "marker-end": "url(#arrow-derivation)",
                        "data-parent": ancestor,
                        "data-derived": fid,
                        "data-from": "field-" + ancestor,
                        "data-to": "field-" + fid,
                    },
                )
            else:
                edge(start, target, "derivation", ancestor, "", raw=True)

    model_boxes: dict[str, Box] = {}
    output_boxes: dict[str, Box] = {}
    column_w = (width - 80 - 24 * (len(plan["models"]) - 1)) / len(plan["models"])
    text(40, model_y - 34, "MODELLER VE ANALİZLER", 14, weight=500)
    for i, model in enumerate(plan["models"]):
        mid = model["id"]
        offset = 174 * levels[mid]
        box = Box(40 + i * (column_w + 24), model_y + offset, column_w, 108)
        out = Box(box.x, output_y + offset, box.w, 144)
        model_boxes[mid], output_boxes[mid] = box, out
        group = node("model-" + mid, box, "model", model["algorithm"])
        text(box.cx, box.y + 24, mid, 16, group, "middle", 500)
        wrapped(box, model["label"], 48, max(22, int((box.w - 24) / 9)), 17, group, True)
        method = {
            "learned": "İlk öğrenilmiş model adayı",
            "rules": "Durum / geçiş kuralları",
            "analytical": "Referansa bağlı hesap",
            "conditional": "Ek bölge verisine bağlı",
        }
        summary = model.get("experiment_design", {}).get("label", method[model["kind"]])
        text(box.cx, box.y + 95, summary, 12, group, "middle")
        ogroup = node("output-" + mid, out, "output", " · ".join(model["outputs"]))
        produced = [fid for fid, field in fields.items() if field.get("producer_model") == mid]
        if produced:
            ogroup.set("data-field-ids", json.dumps(produced))
            if len(produced) == 1:
                ogroup.set("data-field-id", produced[0])
            for fid in produced:
                field_boxes[fid] = out
        text(out.x + 12, out.y + 23, mid + " çıktısı", 13, ogroup, weight=500)
        wrapped(out, model["question"], 49, max(23, int((out.w - 24) / 8.3)), 16, ogroup)
        edge(box, out, "feature", "", mid)
    for model in plan["models"]:
        mid = model["id"]
        for fid, role in model["inputs"]:
            start = field_boxes[fid]
            if "producer_model" in fields[fid]:
                target = output_boxes[mid] if role == "review" else model_boxes[mid]
                SubElement(
                    edges,
                    "path",
                    {
                        "d": f"M {start.x + start.w} {start.cy} "
                        f"C {start.x + start.w + 12} {start.cy}, "
                        f"{target.x - 12} {target.cy}, {target.x} {target.cy}",
                        "class": f"edge {role}",
                        "marker-end": f"url(#arrow-{role})",
                        "data-field": fid,
                        "data-model": mid,
                        "data-role": role,
                        "data-from": box_ids[start],
                        "data-to": box_ids[target],
                    },
                )
            else:
                edge(
                    start,
                    output_boxes[mid] if role == "review" else model_boxes[mid],
                    role,
                    fid,
                    mid,
                    raw=fid not in derived_ids,
                    side=role == "review",
                )
    footer = height - 90
    for i, (role, title) in enumerate(
        [
            ("feature", "Temel özellik"),
            ("context", "Aday bağlam"),
            ("gate", "Uygunluk kontrolü"),
            ("review", "Yorum / değerlendirme → sonuç"),
            ("derivation", "Alanlar arası türetme"),
        ]
    ):
        x = 40 + i * ((width - 80) / 5)
        SubElement(nodes, "path", {"d": f"M {x} {footer} H {x + 34}", "class": f"edge {role}"})
        text(x + 44, footer + 5, title, 13)
    text(
        40,
        height - 45,
        "Onaylar ve kimlikler kontrol/eşleştirme içindir. Bildirimler kesin olay etiketi değildir. "
        "Yorum hatları eğitim modellerine girmez.",
        14,
    )
    text(
        40,
        height - 20,
        "Ara model çıktıları ayrı fiziksel kaynak değildir. "
        "Bölgesel uzantı bağımsız veri gerektirir. "
        "Hava kontrolleri yalnız ilgili varyant içindir. Kaynak: model-flow-map.v0.1.json",
        14,
    )
    if set(field_boxes) != set(fields):
        raise ValueError("Overview must represent every field exactly once")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(svg, encoding="unicode") + "\n"
