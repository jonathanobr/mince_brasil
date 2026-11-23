# src/config.py
"""
Configuracao centralizada e helpers cartograficos / editoriais v2 do spin-off `minerais_criticos_brasil`.
Politica Nacional de Minerais Criticos e Estrategicos (Lei nº 15.506/2026, Decretos nº 13.118 e 13.146/2026,
Pleno CIMCE 05/11/2026 — 27 minerais em 3 Classes de Prioridade).
"""

from __future__ import annotations

import json
import logging
import textwrap
import unicodedata
from pathlib import Path
from typing import Optional

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from matplotlib.offsetbox import AnchoredOffsetbox, TextArea, VPacker
from pyproj import Transformer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEV_ROOT = PROJECT_ROOT.parent
SRC_DIR = PROJECT_ROOT / "src"
DATA_DIR = PROJECT_ROOT / "data"
DATA_RAW = DATA_DIR / "raw"
ANM_RAW_DIR = DATA_RAW / "anm"
ANM_RAW_METADATA_PATH = DATA_RAW / "anm_metadata.json"
DATA_DOCS = DATA_DIR / "docs"
DATA_CURATED = DATA_DIR / "curated"
DATA_PROCESSED = DATA_DIR / "processed"
BASEMAPS_PROCESSED = DATA_PROCESSED / "basemaps"
ONTOLOGY_PATH = DATA_CURATED / "cimce_2026_ontology.json"

# Fallbacks opcionais somente leitura para workspace local com repositorios irmaos
SHARED_ANM_SHP = DEV_ROOT / "terras_raras_brasil" / "data" / "raw" / "anm" / "BRASIL.shp"
SHARED_ANM_META = DEV_ROOT / "terras_raras_brasil" / "data" / "raw" / "anm_metadata.json"
SHARED_BASEMAPS_DIR = DEV_ROOT / "terras_raras_brasil" / "data" / "processed" / "basemaps"
SHARED_SOCIOENV_DIR = DEV_ROOT / "terras_raras_brasil" / "data" / "processed"
SHARED_MCS2026_CSV = DEV_ROOT / "recursos" / "data" / "MCS2026_Commodities_Data.csv"

OUTPUT_DIR = PROJECT_ROOT / "output"
FIGURES_PNG_DIR = OUTPUT_DIR / "figures" / "png"
FIGURES_SVG_DIR = OUTPUT_DIR / "figures" / "svg"
TABLES_DIR = OUTPUT_DIR / "tables"
TABLES_CSV_DIR = TABLES_DIR / "csv"
TABLES_MD_DIR = TABLES_DIR / "markdown"
TABLES_TEX_DIR = TABLES_DIR / "latex"
EXCEL_TABLES_PATH = TABLES_DIR / "minerais_criticos_brasil_tabelas.xlsx"
REPORT_DIR = OUTPUT_DIR / "relatorio"
AUDIT_REPORT_PATH = OUTPUT_DIR / "quality_audit_report.json"

for _d in (
    DATA_RAW,
    ANM_RAW_DIR,
    DATA_CURATED,
    DATA_PROCESSED,
    BASEMAPS_PROCESSED,
    FIGURES_PNG_DIR,
):
    _d.mkdir(parents=True, exist_ok=True)


def resolve_anm_shp() -> Path:
    """Resolve BRASIL.shp localmente primeiro, com fallback ao workspace irmao se existir."""
    local_shp = ANM_RAW_DIR / "BRASIL.shp"
    if local_shp.exists():
        return local_shp
    if SHARED_ANM_SHP.exists():
        return SHARED_ANM_SHP
    raise FileNotFoundError(
        f"Shapefile ANM nao encontrado em {local_shp}. "
        "Execute 'python src/fetch_sources.py' ou 'python src/run_all.py --fetch' para baixar da ANM/SIGMINE."
    )


def resolve_anm_meta() -> Path | None:
    """Retorna metadados brutos da ANM se disponiveis localmente ou no fallback irmao."""
    for p in (ANM_RAW_METADATA_PATH, SHARED_ANM_META):
        if p.exists():
            return p
    return None


def resolve_basemaps_dir() -> Path:
    """Resolve diretorio de basemaps GeoParquet (local primeiro, fallback irmao se existir)."""
    required = ("brazil_states_ibge2025.parquet", "brazil_eez.parquet", "south_america_countries.parquet")
    if all((BASEMAPS_PROCESSED / fn).exists() for fn in required):
        return BASEMAPS_PROCESSED
    if all((SHARED_BASEMAPS_DIR / fn).exists() for fn in required):
        return SHARED_BASEMAPS_DIR
    raise FileNotFoundError(
        f"Basemaps GeoParquet ausentes em {BASEMAPS_PROCESSED}. "
        "Execute 'python src/fetch_sources.py' ou 'python src/run_all.py --fetch' para baixar IBGE/VLIZ/Natural Earth."
    )


def resolve_socioenv_dir() -> Path | None:
    """Retorna diretorio com camadas socioambientais (Biomas/UCs/TIs) se presentes; caso contrario None."""
    required = ("socioenv_biomes.parquet", "socioenv_ucs.parquet", "socioenv_tis.parquet")
    for candidate in (DATA_PROCESSED, SHARED_SOCIOENV_DIR):
        if all((candidate / fn).exists() for fn in required):
            return candidate
    return None

DPI = 600

IBGE_ALBERS_CRS = (
    "+proj=aea +lat_0=-12 +lon_0=-54 +lat_1=-2 +lat_2=-22 "
    "+x_0=5000000 +y_0=10000000 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs"
)
SIRGAS_2000_GEO = "EPSG:4674"
WGS84_GEO = "EPSG:4326"

CLASS_ORDER = [
    "Classe I — Prioridade Alta",
    "Classe II — Prioridade Média",
    "Classe III — Prioridade Baixa",
]

CLASS_COLORS = {
    "Classe I — Prioridade Alta": "#B91C1C",
    "Classe II — Prioridade Média": "#D97706",
    "Classe III — Prioridade Baixa": "#1D4ED8",
}

STAGE_ORDER = [
    "Concessão de Lavra",
    "Requerimento de Lavra",
    "Direito de Requerer a Lavra",
    "Autorização de Pesquisa",
    "Requerimento de Pesquisa / Disponibilidade / Outros",
]

STAGE_COLORS = {
    "Concessão de Lavra": "#B91C1C",
    "Requerimento de Lavra": "#EA580C",
    "Direito de Requerer a Lavra": "#D97706",
    "Autorização de Pesquisa": "#2563EB",
    "Requerimento de Pesquisa / Disponibilidade / Outros": "#64748B",
}

BLOCK_COLORS = {
    "BRICS+": "#7C3AED",
    "Demais Sul Global": "#0D9488",
    "Norte Global": "#1E3A8A",
    "Outros USGS": "#CBD5E1",
}

BIOME_COLORS = {
    "Amazônia": "#D1FAE5",
    "Cerrado": "#FEF3C7",
    "Caatinga": "#FFEDD5",
    "Mata Atlântica": "#DCFCE7",
    "Pampa": "#E0E7FF",
    "Pantanal": "#CFFAFE",
}

UF_TO_REGION = {
    "AC": "Norte", "AM": "Norte", "AP": "Norte", "PA": "Norte", "RO": "Norte", "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste", "PB": "Nordeste",
    "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MS": "Centro-Oeste", "MT": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}

UF_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MG": "Minas Gerais", "MS": "Mato Grosso do Sul", "MT": "Mato Grosso",
    "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí", "PR": "Paraná",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RO": "Rondônia", "RR": "Roraima",
    "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "SE": "Sergipe", "SP": "São Paulo",
    "TO": "Tocantins",
}


def load_ontology() -> dict:
    with open(ONTOLOGY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_anm_snapshot_meta() -> dict:
    for p in (DATA_PROCESSED / "anm_snapshot_meta.json", ANM_RAW_METADATA_PATH, SHARED_ANM_META):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {
        "snapshot_label": "nov/2026",
        "snapshot_abnt": "nov. 2026",
        "total_anm_rows": 270994,
        "garimpo_lic_count": 19090,
    }


def get_anm_snapshot_label() -> str:
    return str(get_anm_snapshot_meta().get("snapshot_label", "nov/2026"))


def get_anm_snapshot_abnt() -> str:
    return str(get_anm_snapshot_meta().get("snapshot_abnt", "nov. 2026"))


def fmt_ptbr(value: float, decimals: int = 1) -> str:
    if value is None or not np.isfinite(value):
        return "--"
    s = f"{float(value):,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_int_ptbr(value: float | int) -> str:
    if value is None or not np.isfinite(float(value)):
        return "--"
    return f"{int(round(float(value))):,}".replace(",", ".")


def normalize_text(val: object) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in {"nan", "none", "null", ""}:
        return ""
    s_nfkd = unicodedata.normalize("NFKD", s)
    s_ascii = "".join(ch for ch in s_nfkd if not unicodedata.combining(ch))
    return " ".join(s_ascii.upper().split())


def classify_anm_stage(fase: object) -> str:
    f = normalize_text(fase)
    if "CONCESSAO DE LAVRA" in f or "MANIFESTO DE MINA" in f:
        return "Concessão de Lavra"
    if f == "REQUERIMENTO DE LAVRA" or ("REQUERIMENTO" in f and "LAVRA" in f and "GARIMPEIRA" not in f):
        return "Requerimento de Lavra"
    if "DIREITO DE REQUERER A LAVRA" in f:
        return "Direito de Requerer a Lavra"
    if "AUTORIZACAO DE PESQUISA" in f:
        return "Autorização de Pesquisa"
    return "Requerimento de Pesquisa / Disponibilidade / Outros"


def setup_editorial_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.edgecolor": "#94A3B8",
        "axes.linewidth": 0.7,
        "axes.facecolor": "#FFFFFF",
        "figure.facecolor": "#FFFFFF",
        "savefig.facecolor": "#FFFFFF",
        "figure.dpi": DPI,
        "savefig.dpi": DPI,
        "savefig.bbox": "tight",
        "svg.fonttype": "path",
    })


def format_map_axes_and_scalebar(
    ax,
    crs_str: str = IBGE_ALBERS_CRS,
    scale_pos: str = "bottom_right",
    north_pos: tuple[float, float] = (0.955, 0.945),
    candidates_km: Optional[list[int]] = None,
    show_crs_box: bool = True,
    crs_datum: str = "SIRGAS 2000",
    crs_proj: str = "Albers (IBGE)",
    crs_fontsize: float = 6.5,
) -> None:
    """Formata eixos (°W/°S ou °'W/°'S), seta Norte, barra de escala e quadro interno Sistema de Referencia."""
    try:
        inv_tf = Transformer.from_crs(crs_str, WGS84_GEO, always_xy=True)
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        y_mid = 0.5 * (ylim[0] + ylim[1])
        x_mid = 0.5 * (xlim[0] + xlim[1])
        lon_lo, _ = inv_tf.transform(xlim[0], y_mid)
        lon_hi, _ = inv_tf.transform(xlim[1], y_mid)
        _, lat_lo = inv_tf.transform(x_mid, ylim[0])
        _, lat_hi = inv_tf.transform(x_mid, ylim[1])
        lon_span = abs(lon_hi - lon_lo) if np.isfinite(lon_hi - lon_lo) else 10.0
        lat_span = abs(lat_hi - lat_lo) if np.isfinite(lat_hi - lat_lo) else 10.0

        def _fmt_coord(val: float, hemi: str, span: float) -> str:
            if span < 1.5:
                abs_v = abs(val)
                deg = int(abs_v)
                mins = int(round((abs_v - deg) * 60.0))
                if mins == 60:
                    deg += 1
                    mins = 0
                return f"{deg}°{mins:02d}'{hemi}"
            return f"{abs(val):.0f}°{hemi}"

        def fmt_lon(x_val, _pos):
            try:
                lon, _ = inv_tf.transform(x_val, y_mid)
                if not np.isfinite(lon) or abs(lon) > 180:
                    return ""
                hemi = "W" if lon < -0.05 else ("E" if lon > 0.05 else "")
                return _fmt_coord(lon, hemi, lon_span)
            except Exception:
                return ""

        def fmt_lat(y_val, _pos):
            try:
                _, lat = inv_tf.transform(x_mid, y_val)
                if not np.isfinite(lat) or abs(lat) > 90:
                    return ""
                hemi = "S" if lat < -0.05 else ("N" if lat > 0.05 else "")
                return _fmt_coord(lat, hemi, lat_span)
            except Exception:
                return ""

        ax.xaxis.set_major_formatter(ticker.FuncFormatter(fmt_lon))
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(fmt_lat))
        ax.tick_params(axis="both", labelsize=7.8, colors="#334155")
        ax.set_xlabel("Longitude", fontsize=8.4, color="#334155", labelpad=5)
        ax.set_ylabel("Latitude", fontsize=8.4, color="#334155", labelpad=5)
    except Exception as exc:
        logging.debug("Aviso ao formatar graus nos eixos: %s", exc)

    ax.text(
        north_pos[0], north_pos[1], "N\n▲",
        transform=ax.transAxes, ha="center", va="center",
        fontsize=8.2, fontweight="bold", color="#0F172A",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="white", edgecolor="#94A3B8", alpha=0.90),
        zorder=25,
    )

    crs_ax_bbox = None
    if show_crs_box:
        try:
            line1 = TextArea("Sistema de referência:", textprops=dict(size=crs_fontsize, weight="bold", color="#0F172A"))
            line2 = TextArea(f"{crs_datum}    {crs_proj}", textprops=dict(size=crs_fontsize, color="#1E293B"))
            vbox = VPacker(children=[line1, line2], align="left", pad=0, sep=1.5)
            ob = AnchoredOffsetbox(loc="lower right", pad=0.32, borderpad=0.45, child=vbox, frameon=True)
            ob.patch.set_boxstyle("square,pad=0.28")
            ob.patch.set_facecolor("white")
            ob.patch.set_edgecolor("#334155")
            ob.patch.set_linewidth(0.8)
            ob.patch.set_alpha(0.95)
            ob.set_zorder(26)
            ax.add_artist(ob)
            fig = ax.figure
            fig.canvas.draw()
            crs_ax_bbox = ob.get_window_extent(fig.canvas.get_renderer()).transformed(ax.transAxes.inverted())
        except Exception as exc:
            logging.debug("Aviso ao desenhar quadro Sistema de referencia: %s", exc)

    try:
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        dx = xlim[1] - xlim[0]
        dy = ylim[1] - ylim[0]
        target_m = dx * 0.16
        opts = candidates_km or [25, 50, 100, 150, 200, 250, 500, 1000]
        best_km = min(opts, key=lambda k: abs(k * 1000.0 - target_m))
        bar_m = best_km * 1000.0

        if scale_pos == "bottom_right":
            if crs_ax_bbox is not None:
                crs_right_x = xlim[0] + dx * float(crs_ax_bbox.x1)
                crs_center_x = xlim[0] + dx * 0.5 * float(crs_ax_bbox.x0 + crs_ax_bbox.x1)
                x0 = min(crs_center_x - bar_m / 2.0, crs_right_x - bar_m)
                x0 = max(x0, xlim[0] + dx * 0.04)
                y0 = ylim[0] + dy * (float(crs_ax_bbox.y1) + 0.024)
            else:
                x0 = xlim[1] - dx * 0.05 - bar_m
                y0 = ylim[0] + dy * 0.045
        elif scale_pos == "bottom_left":
            x0 = xlim[0] + dx * 0.045
            y0 = ylim[0] + dy * 0.045
        else:
            x0 = xlim[0] + dx * 0.50 - bar_m / 2.0
            y0 = ylim[0] + dy * 0.045

        ax.plot([x0, x0 + bar_m], [y0, y0], color="#0F172A", linewidth=2.2, zorder=25)
        ax.plot([x0, x0], [y0 - dy * 0.006, y0 + dy * 0.006], color="#0F172A", linewidth=1.6, zorder=25)
        ax.plot([x0 + bar_m, x0 + bar_m], [y0 - dy * 0.006, y0 + dy * 0.006], color="#0F172A", linewidth=1.6, zorder=25)
        ax.text(
            x0 + bar_m / 2.0, y0 + dy * 0.011, f"0 — {best_km} km",
            ha="center", va="bottom", fontsize=7.1, fontweight="bold", color="#0F172A",
            bbox=dict(boxstyle="round,pad=0.16", facecolor="white", edgecolor="#CBD5E1", alpha=0.90),
            zorder=25,
        )
    except Exception as exc:
        logging.debug("Aviso ao desenhar barra de escala: %s", exc)


def _wrap_footer_lines_to_px(fig, raw_paragraphs: list[str], target_px: float, fontsize: float, est_chars: int) -> str:
    renderer = fig.canvas.get_renderer()
    w_chars = max(75, est_chars)
    best_txt = ""
    for _ in range(8):
        wrapped = [textwrap.fill(p, width=w_chars, break_long_words=False) for p in raw_paragraphs if p]
        cand_txt = "\n".join(wrapped)
        tmp = fig.text(0.0, 0.0, cand_txt, fontsize=fontsize, linespacing=1.22, va="bottom", ha="left", alpha=0.0)
        bb = tmp.get_window_extent(renderer)
        tmp.remove()
        if bb.width <= target_px:
            best_txt = cand_txt
            if bb.width < target_px * 0.88 and any("\n" in p for p in wrapped):
                w_chars = int(w_chars * (target_px * 0.96 / max(bb.width, 1.0)))
                continue
            break
        ratio = (target_px * 0.96) / max(bb.width, 1.0)
        new_w = max(65, int(w_chars * ratio) - 1)
        if new_w >= w_chars:
            new_w = w_chars - 2
        w_chars = new_w
        best_txt = cand_txt
    if not best_txt:
        wrapped = [textwrap.fill(p, width=w_chars, break_long_words=False) for p in raw_paragraphs if p]
        best_txt = "\n".join(wrapped)
    return best_txt


def _render_full_width_footer(
    fig,
    title_h1: str,
    title_h2: str,
    sources_str: str,
    crs_str: str = "",
    caveat_str: str = "",
    fontsize: float = 6.6,
    min_span_frac: float = 0.78,
) -> None:
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    main_axes = [a for a in fig.get_axes() if a.get_axes_locator() is None] or fig.get_axes()
    inv_fig = fig.transFigure.inverted()
    pos_x0, pos_x1, bottom_ys = [], [], []
    for a in main_axes:
        p = a.get_position()
        pos_x0.append(float(p.x0))
        pos_x1.append(float(p.x1))
        tb = a.get_tightbbox(renderer)
        if tb is not None:
            bottom_ys.append(float(tb.transformed(inv_fig).y0))
        leg = a.get_legend()
        if leg is not None and leg.get_visible():
            lbb = leg.get_window_extent(renderer).transformed(inv_fig)
            if lbb.y0 < float(p.y0) + 0.04:
                bottom_ys.append(float(lbb.y0))
    for fl in getattr(fig, "legends", []):
        if fl.get_visible():
            fbb = fl.get_window_extent(renderer).transformed(inv_fig)
            if fbb.y0 < 0.35:
                bottom_ys.append(float(fbb.y0))

    x_left = min(pos_x0) if pos_x0 else 0.06
    x_right = max(pos_x1) if pos_x1 else 0.95
    if (x_right - x_left) < min_span_frac:
        mid = 0.5 * (x_left + x_right)
        x_left = max(0.04, mid - min_span_frac / 2.0)
        x_right = min(0.96, x_left + min_span_frac)
        x_left = max(0.04, x_right - min_span_frac)

    min_y = min(bottom_ys) if bottom_ys else 0.10
    fig_w_px = fig.get_figwidth() * fig.dpi
    fig_h_px = fig.get_figheight() * fig.dpi
    pad_x_fig = 8.0 / fig_w_px
    pad_y_fig = 5.5 / fig_h_px
    gap_fig = 8.5 / fig_h_px
    avail_px = max((x_right - x_left - 2.0 * pad_x_fig) * fig_w_px, 240.0)
    est_chars = int((avail_px / fig.dpi) / (fontsize * 0.50 / 72.0))

    paragraphs = [f"{title_h1.upper()} — {title_h2}", f"Fontes: {sources_str}"]
    if crs_str:
        paragraphs.append(f"Escopo: {crs_str}")
    if caveat_str:
        paragraphs.append(f"Nota: {caveat_str}")

    footer_text = _wrap_footer_lines_to_px(fig, paragraphs, avail_px, fontsize, est_chars)
    txt_y_top = min_y - gap_fig - pad_y_fig
    txt_obj = fig.text(
        x_left + pad_x_fig, txt_y_top, footer_text,
        fontsize=fontsize, color="#1E293B", linespacing=1.22, va="top", ha="left", zorder=20,
    )
    fig.canvas.draw()
    tbb = txt_obj.get_window_extent(fig.canvas.get_renderer()).transformed(inv_fig)
    rect = mpatches.FancyBboxPatch(
        (x_left, float(tbb.y0) - pad_y_fig), x_right - x_left, (float(tbb.y1) - float(tbb.y0)) + 2.0 * pad_y_fig,
        boxstyle="square,pad=0", transform=fig.transFigure,
        facecolor="#F8FAFC", edgecolor="#CBD5E1", linewidth=0.65, alpha=0.96, zorder=19, clip_on=False,
    )
    fig.add_artist(rect)


def add_cartographic_footer(ax, title_h1: str, title_h2: str, sources_str: str, crs_str: str = "", caveat_str: str = "") -> None:
    _render_full_width_footer(ax.figure, title_h1=title_h1, title_h2=title_h2, sources_str=sources_str, crs_str=crs_str, caveat_str=caveat_str)


def add_figure_footer(fig, title_h1: str, title_h2: str, sources_str: str, crs_str: str = "", caveat_str: str = "") -> None:
    _render_full_width_footer(fig, title_h1=title_h1, title_h2=title_h2, sources_str=sources_str, crs_str=crs_str, caveat_str=caveat_str)


def save_figure_dual(fig, filename_stem: str, save_svg: bool = True) -> tuple[Path, Path | None]:
    png_path = FIGURES_PNG_DIR / f"{filename_stem}_600dpi.png"
    svg_path = FIGURES_SVG_DIR / f"{filename_stem}.svg"
    if png_path.exists():
        try:
            png_path.unlink()
        except Exception:
            pass
    fig.savefig(str(png_path), dpi=DPI, bbox_inches="tight", facecolor="white")
    out_svg: Path | None = None
    if save_svg:
        FIGURES_SVG_DIR.mkdir(parents=True, exist_ok=True)
        if svg_path.exists():
            try:
                svg_path.unlink()
            except Exception:
                pass
        fig.savefig(str(svg_path), bbox_inches="tight", facecolor="white")
        out_svg = svg_path
    plt.clf()
    plt.close("all")
    return png_path, out_svg
