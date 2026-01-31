import json
import math
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Dict, List, Tuple

import streamlit as st

try:
    from fpdf import FPDF
except ImportError:  # pragma: no cover - optional dependency in UI
    FPDF = None


DATA_DIR = "data"


# -----------------------------
# Utils
# -----------------------------

def load_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return fallback


def format_currency(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# -----------------------------
# Core calculation
# -----------------------------

@dataclass
class LineItem:
    category: str
    name: str
    qty: float
    unit: str
    unit_price: float
    total: float
    notes: str = ""


@dataclass
class PackageOption:
    size_l: float
    price: float


@dataclass
class PackageSelection:
    packages: List[Tuple[PackageOption, int]]
    total_liters: float
    total_cost: float


@dataclass
class QuoteResult:
    items: List[LineItem]
    days: float
    material_cost: float
    labor_cost: float
    subtotal: float
    final_price: float
    price_m2: float


# -----------------------------
# Calculation helpers
# -----------------------------

def optimize_packages(liters_needed: float, options: List[PackageOption]) -> PackageSelection:
    if liters_needed <= 0 or not options:
        return PackageSelection([], 0.0, 0.0)

    options = sorted(options, key=lambda opt: opt.size_l)
    smallest = options[0]
    max_small = math.ceil(liters_needed / smallest.size_l) + 4

    best_cost = float("inf")
    best_combo: List[Tuple[PackageOption, int]] = []
    best_liters = 0.0

    for qty_small in range(max_small + 1):
        liters_from_small = qty_small * smallest.size_l
        remaining = max(liters_needed - liters_from_small, 0)
        combo = [(smallest, qty_small)]

        if remaining > 0 and len(options) > 1:
            largest = options[-1]
            qty_large = math.ceil(remaining / largest.size_l)
            combo.append((largest, qty_large))
        elif remaining > 0:
            combo = [(smallest, math.ceil(liters_needed / smallest.size_l))]

        total_liters = sum(opt.size_l * qty for opt, qty in combo)
        total_cost = sum(opt.price * qty for opt, qty in combo)

        if total_liters >= liters_needed and total_cost < best_cost:
            best_cost = total_cost
            best_combo = combo
            best_liters = total_liters

    packages = [(opt, qty) for opt, qty in best_combo if qty > 0]
    return PackageSelection(packages, best_liters, best_cost)


def calc_paint(
    area_m2: float,
    coats: int,
    yield_m2_per_l: float,
    loss_pct: float,
    packaging: List[PackageOption],
    name: str,
    category: str,
) -> LineItem:
    liters = (area_m2 / yield_m2_per_l) * coats
    liters *= (1.0 + loss_pct)

    selection = optimize_packages(liters, packaging)
    total = selection.total_cost

    if selection.packages:
        pack_desc = ", ".join(
            f"{qty}x {opt.size_l}L" for opt, qty in selection.packages
        )
    else:
        pack_desc = "0"

    return LineItem(
        category,
        name,
        sum(qty for _, qty in selection.packages),
        f"kits ({pack_desc})",
        (total / max(sum(qty for _, qty in selection.packages), 1)),
        total,
        notes=f"{liters:.1f} L estimados com perdas",
    )


def calc_texture(
    area_m2: float,
    consumption_kg_m2: float,
    loss_pct: float,
    bag_kg: float,
    price_bag: float,
    name: str,
) -> LineItem:
    kg = area_m2 * consumption_kg_m2 * (1.0 + loss_pct)
    bags = math.ceil(kg / bag_kg)
    total = bags * price_bag
    return LineItem(
        "Textura",
        name,
        bags,
        f"saco ({bag_kg} kg)",
        price_bag,
        total,
        notes=f"{kg:.1f} kg estimados com perdas",
    )


def calc_supply_by_rate(
    area_m2: float,
    rate_per_m2: float,
    pack_size: float,
    pack_unit: str,
    pack_price: float,
    name: str,
    category: str,
) -> LineItem:
    qty_needed = area_m2 * rate_per_m2
    packs = math.ceil(qty_needed / pack_size)
    total = packs * pack_price
    return LineItem(
        category,
        name,
        packs,
        f"un ({pack_size} {pack_unit})",
        pack_price,
        total,
        notes=f"{qty_needed:.1f} {pack_unit} estimados",
    )


def calc_days(area_m2: float, prod_m2_day_per_worker: float, workers: int, factor: float) -> float:
    base = area_m2 / (prod_m2_day_per_worker * max(workers, 1))
    return base / max(factor, 1e-6)


# -----------------------------
# PDF Generation
# -----------------------------

def build_pdf(
    scope_text: str,
    items: List[LineItem],
    days: float,
    price_m2: float,
    total_price: float,
) -> bytes:
    if FPDF is None:
        raise RuntimeError("Dependência FPDF não encontrada. Instale fpdf2 para gerar PDF.")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", style="B", size=18)
    pdf.cell(0, 10, "FF Flausino Ferreira Pinturas", ln=True, align="C")

    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 8, f"Data: {date.today().strftime('%d/%m/%Y')}", ln=True)

    pdf.ln(4)
    pdf.set_font("Helvetica", style="B", size=12)
    pdf.cell(0, 8, "Escopo", ln=True)
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, scope_text or "--")

    pdf.ln(2)
    pdf.set_font("Helvetica", style="B", size=12)
    pdf.cell(0, 8, "Materiais", ln=True)
    pdf.set_font("Helvetica", size=10)

    for item in items:
        pdf.multi_cell(
            0,
            6,
            f"- {item.name}: {item.qty:.0f} {item.unit} ({format_currency(item.total)})",
        )

    pdf.ln(2)
    pdf.set_font("Helvetica", style="B", size=12)
    pdf.cell(0, 8, "Prazo", ln=True)
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 6, f"{days:.1f} dias", ln=True)

    pdf.ln(2)
    pdf.set_font("Helvetica", style="B", size=12)
    pdf.cell(0, 8, "Valores", ln=True)
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 6, f"Valor por m²: {format_currency(price_m2)}", ln=True)
    pdf.cell(0, 6, f"Total: {format_currency(total_price)}", ln=True)

    return pdf.output(dest="S").encode("latin-1")


# -----------------------------
# Streamlit UI
# -----------------------------

st.set_page_config(page_title="Orçador de Fachada", layout="wide")

materials = load_json(f"{DATA_DIR}/materials.json", fallback={})
productivity = load_json(f"{DATA_DIR}/productivity.json", fallback={})
defaults = load_json(f"{DATA_DIR}/defaults.json", fallback={})

st.title("Orçador Completo de Fachada")

with st.sidebar:
    st.header("Parâmetros gerais")
    workers = st.number_input("Nº de profissionais", 1, 20, 2)
    cost_worker_day = st.number_input(
        "Custo por profissional / dia (R$)", 0.0, 5000.0, 250.0, step=10.0
    )
    transport_day_total = st.number_input(
        "Transporte total / dia (R$)", 0.0, 2000.0, 20.0, step=5.0
    )
    food_day_total = st.number_input(
        "Alimentação total / dia (R$)", 0.0, 5000.0, 50.0, step=10.0
    )

    equip_cost = st.number_input(
        "Equipamentos/locações (R$) - total obra", 0.0, 200000.0, 0.0, step=50.0
    )
    indirect_fixed = st.number_input(
        "Indiretos fixos (R$) - total obra", 0.0, 200000.0, 0.0, step=50.0
    )

    risk_pct = st.slider("Reserva de risco (%)", 0, 40, 10) / 100.0
    margin_pct = st.slider("Margem / BDI (%)", 0, 60, 20) / 100.0

st.subheader("Presets de serviço")

preset_labels = [preset["label"] for preset in defaults.get("presets", [])]
selected_preset = st.selectbox("Selecione um preset", ["Personalizado"] + preset_labels)

preset_map = {preset["label"]: preset for preset in defaults.get("presets", [])}

selected_preset_data = preset_map.get(selected_preset, {})

col1, col2 = st.columns(2)

with col1:
    st.subheader("Áreas")
    area_paint = st.number_input(
        "Área para pintura externa (m²)", 0.0, 200000.0, 0.0, step=10.0
    )
    area_texture = st.number_input(
        "Área para textura rolada (m²)", 0.0, 200000.0, 0.0, step=10.0
    )
    area_grafiato = st.number_input(
        "Área para grafiato (m²)", 0.0, 200000.0, 0.0, step=10.0
    )
    area_sealer = st.number_input(
        "Área para selador (m²)", 0.0, 200000.0, 0.0, step=10.0
    )
    area_waterproof = st.number_input(
        "Área para impermeabilizante (m²)", 0.0, 200000.0, 0.0, step=10.0
    )
    area_removal = st.number_input(
        "Área para remoção (m²)", 0.0, 200000.0, 0.0, step=10.0
    )

with col2:
    st.subheader("Demãos e fatores")
    coats_paint = st.number_input("Demãos de tinta", 1, 5, 2)
    coats_sealer = st.number_input("Demãos de selador", 1, 3, 1)
    coats_waterproof = st.number_input("Demãos de impermeabilizante", 1, 4, 2)

    factor_height = st.slider("Fator altura/acesso (1 = normal)", 0.50, 1.20, 0.90)
    factor_climate = st.slider("Fator clima/vento (1 = ideal)", 0.50, 1.20, 0.95)

if selected_preset_data:
    st.info("Preset aplicado: ajustes automáticos de consumo e produtividade.")

items: List[LineItem] = []

materials_map: Dict[str, dict] = {m["id"]: m for m in materials.get("items", [])}
productivity_map: Dict[str, dict] = {
    p["id"]: p for p in productivity.get("items", [])
}

factor_total = factor_height * factor_climate

if area_paint > 0:
    paint_cfg = materials_map["tinta_externa"]
    items.append(
        calc_paint(
            area_m2=area_paint,
            coats=coats_paint,
            yield_m2_per_l=paint_cfg["yield_m2_per_l"],
            loss_pct=paint_cfg["loss_pct"],
            packaging=[PackageOption(**pkg) for pkg in paint_cfg["packages"]],
            name=paint_cfg["label"],
            category="Pintura",
        )
    )

if area_sealer > 0:
    sealer_cfg = materials_map["selador"]
    items.append(
        calc_paint(
            area_m2=area_sealer,
            coats=coats_sealer,
            yield_m2_per_l=sealer_cfg["yield_m2_per_l"],
            loss_pct=sealer_cfg["loss_pct"],
            packaging=[PackageOption(**pkg) for pkg in sealer_cfg["packages"]],
            name=sealer_cfg["label"],
            category="Preparação",
        )
    )

if area_texture > 0:
    texture_cfg = materials_map["textura_rolada"]
    items.append(
        calc_texture(
            area_m2=area_texture,
            consumption_kg_m2=texture_cfg["consumption_kg_m2"],
            loss_pct=texture_cfg["loss_pct"],
            bag_kg=texture_cfg["bag_kg"],
            price_bag=texture_cfg["price_bag"],
            name=texture_cfg["label"],
        )
    )

if area_grafiato > 0:
    grafiato_cfg = materials_map["grafiato"]
    items.append(
        calc_texture(
            area_m2=area_grafiato,
            consumption_kg_m2=grafiato_cfg["consumption_kg_m2"],
            loss_pct=grafiato_cfg["loss_pct"],
            bag_kg=grafiato_cfg["bag_kg"],
            price_bag=grafiato_cfg["price_bag"],
            name=grafiato_cfg["label"],
        )
    )

if area_waterproof > 0:
    waterproof_cfg = materials_map["impermeabilizante"]
    items.append(
        calc_paint(
            area_m2=area_waterproof,
            coats=coats_waterproof,
            yield_m2_per_l=waterproof_cfg["yield_m2_per_l"],
            loss_pct=waterproof_cfg["loss_pct"],
            packaging=[PackageOption(**pkg) for pkg in waterproof_cfg["packages"]],
            name=waterproof_cfg["label"],
            category="Impermeabilização",
        )
    )

if area_removal > 0:
    removal_cfg = materials_map["remocao"]
    items.append(
        calc_supply_by_rate(
            area_m2=area_removal,
            rate_per_m2=removal_cfg["rate_per_m2"],
            pack_size=removal_cfg["pack_size"],
            pack_unit=removal_cfg["pack_unit"],
            pack_price=removal_cfg["pack_price"],
            name=removal_cfg["label"],
            category="Remoção",
        )
    )

area_base_for_supplies = max(
    area_paint,
    area_texture,
    area_grafiato,
    area_sealer,
    area_waterproof,
    area_removal,
    0.0,
)
if area_base_for_supplies > 0:
    for supply in materials.get("supplies", []):
        items.append(
            calc_supply_by_rate(
                area_base_for_supplies,
                rate_per_m2=supply["rate_per_m2"],
                pack_size=supply["pack_size"],
                pack_unit=supply["pack_unit"],
                pack_price=supply["pack_price"],
                name=supply["label"],
                category="Insumos",
            )
        )

# Prazo

days = 0.0
if area_sealer > 0:
    days += calc_days(
        area_sealer,
        prod_m2_day_per_worker=productivity_map["selador"]["m2_day_worker"],
        workers=workers,
        factor=factor_total,
    )
if area_texture > 0:
    days += calc_days(
        area_texture,
        prod_m2_day_per_worker=productivity_map["textura_rolada"]["m2_day_worker"],
        workers=workers,
        factor=factor_total,
    )
if area_grafiato > 0:
    days += calc_days(
        area_grafiato,
        prod_m2_day_per_worker=productivity_map["grafiato"]["m2_day_worker"],
        workers=workers,
        factor=factor_total,
    )
if area_paint > 0:
    days += calc_days(
        area_paint,
        prod_m2_day_per_worker=productivity_map["pintura_externa"]["m2_day_worker"],
        workers=workers,
        factor=factor_total,
    )
if area_waterproof > 0:
    days += calc_days(
        area_waterproof,
        prod_m2_day_per_worker=productivity_map["impermeabilizante"]["m2_day_worker"],
        workers=workers,
        factor=factor_total,
    )
if area_removal > 0:
    days += calc_days(
        area_removal,
        prod_m2_day_per_worker=productivity_map["remocao"]["m2_day_worker"],
        workers=workers,
        factor=factor_total,
    )

material_cost = sum(i.total for i in items)
labor_day_cost = (workers * cost_worker_day) + transport_day_total + food_day_total
labor_cost = days * labor_day_cost
subtotal = material_cost + labor_cost + equip_cost + indirect_fixed
subtotal_risk = subtotal * (1.0 + risk_pct)
final_price = subtotal_risk * (1.0 + margin_pct)

area_for_m2_price = max(
    area_paint,
    area_texture,
    area_grafiato,
    area_sealer,
    area_waterproof,
    area_removal,
    1.0,
)
price_m2 = final_price / area_for_m2_price

quote = QuoteResult(
    items=items,
    days=days,
    material_cost=material_cost,
    labor_cost=labor_cost,
    subtotal=subtotal,
    final_price=final_price,
    price_m2=price_m2,
)

st.divider()
st.subheader("Resultado")

left, right = st.columns([2, 1])
with left:
    st.write("**Materiais e insumos**")
    if items:
        st.dataframe([i.__dict__ for i in items], use_container_width=True)
    else:
        st.caption("Preencha as áreas para gerar materiais.")

with right:
    st.metric("Custo materiais (R$)", format_currency(material_cost))
    st.metric("Dias estimados", f"{days:.1f}")
    st.metric("Custo mão de obra (R$)", format_currency(labor_cost))
    st.metric("Subtotal (R$)", format_currency(subtotal))
    st.metric("Preço final (R$)", format_currency(final_price))
    st.metric("Preço recomendado (R$/m²)", format_currency(price_m2))

st.subheader("Gerar PDF")

scope_text = st.text_area("Escopo", value=selected_preset_data.get("scope", ""))

pdf_buffer = None
pdf_error = None
if st.button("Gerar PDF"):
    try:
        pdf_content = build_pdf(
            scope_text=scope_text,
            items=items,
            days=days,
            price_m2=price_m2,
            total_price=final_price,
        )
        pdf_buffer = BytesIO(pdf_content)
    except RuntimeError as exc:
        pdf_error = str(exc)

if pdf_error:
    st.error(pdf_error)
elif pdf_buffer:
    st.download_button(
        label="Baixar PDF",
        data=pdf_buffer,
        file_name="orcamento_ff_flausino_ferreira.pdf",
        mime="application/pdf",
    )

st.caption(
    "Catálogo JSON carregado em data/materials.json e data/productivity.json. "
    "Presets em data/defaults.json."
)
