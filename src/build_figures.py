# src/build_figures.py
"""
Etapa 4: Atlas Cartografico e Analitico do Brasil — 27 Minerais Criticos e Estrategicos (CIMCE 2026)
12 figuras cientificas a 600 DPI (PNG + SVG), em SIRGAS 2000 / Albers IBGE, com:
  - Quadro interno Sistema de referencia, barra de escala, seta Norte e Amazonia Azul (ZEE)
  - Rodape compacto em largura plena (crs_str="") e leg.set_zorder(30)
  - Escala linear rigorosa e formatacao numérica PT-BR individualizada
"""

from __future__ import annotations

import argparse
from functools import cached_property
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geopandas as gpd
import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer

from src.config import (
    BIOME_COLORS,
    BLOCK_COLORS,
    CLASS_COLORS,
    CLASS_ORDER,
    DATA_PROCESSED,
    IBGE_ALBERS_CRS,
    STAGE_COLORS,
    add_cartographic_footer,
    add_figure_footer,
    fmt_int_ptbr,
    fmt_ptbr,
    format_map_axes_and_scalebar,
    get_anm_snapshot_label,
    get_anm_snapshot_meta,
    load_ontology,
    resolve_basemaps_dir,
    resolve_socioenv_dir,
    save_figure_dual,
    setup_editorial_style,
)

logging.basicConfig(level=logging.INFO, format="[BUILD FIGURES 27] %(levelname)s: %(message)s")

# Complexos Nucleares Oficiais INB / CNEN (Uranio* — monopolio federal CF arts. 21 e 177)
INB_URANIUM_COMPLEXES = [
    ("Caetité / Lagoa Real (BA) [Ativa]", -42.26, -13.84),
    ("Santa Quitéria / Itataia (CE)", -39.77, -4.56),
    ("Caldas (MG)", -46.48, -21.92),
    ("Espinharas (PB)", -37.35, -6.85),
    ("Amorinópolis (GO)", -51.10, -16.62),
    ("Figueira (PR)", -50.40, -23.85),
]


def _clean_albers(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    g = gdf.to_crs(IBGE_ALBERS_CRS).copy()
    g["geometry"] = shapely.force_2d(g.geometry).make_valid()
    return g[~g.geometry.is_empty & g.geometry.notna()].copy()


class CimceAtlasData:
    """Container com carregamento sob demanda para bases vetoriais e sumarios do CIMCE 2026."""

    def __init__(self, save_svg: bool = True) -> None:
        self.save_svg = save_svg
        self.ont = load_ontology()
        self.anm_label = get_anm_snapshot_label()
        self.mineral_colors = {m["name"]: m["color"] for m in self.ont["minerals"]}
        self.mineral_meta = {m["name"]: m for m in self.ont["minerals"]}
        self._tf = Transformer.from_crs("EPSG:4674", IBGE_ALBERS_CRS, always_xy=True)

    @cached_property
    def _basemaps_dir(self) -> Path:
        return resolve_basemaps_dir()

    @cached_property
    def _socioenv_dir(self) -> Path:
        sd = resolve_socioenv_dir()
        if sd is None:
            raise FileNotFoundError("Camadas socioambientais (Biomas/UCs/TIs) ausentes para figuras socioambientais.")
        return sd

    @cached_property
    def states_alb(self) -> gpd.GeoDataFrame:
        return _clean_albers(gpd.read_parquet(self._basemaps_dir / "brazil_states_ibge2025.parquet"))

    @cached_property
    def eez_alb(self) -> gpd.GeoDataFrame:
        return _clean_albers(gpd.read_parquet(self._basemaps_dir / "brazil_eez.parquet"))

    @cached_property
    def sa_alb(self) -> gpd.GeoDataFrame:
        return _clean_albers(gpd.read_parquet(self._basemaps_dir / "south_america_countries.parquet"))

    @cached_property
    def biomes_alb(self) -> gpd.GeoDataFrame:
        return _clean_albers(gpd.read_parquet(self._socioenv_dir / "socioenv_biomes.parquet"))

    @cached_property
    def ucs_alb(self) -> gpd.GeoDataFrame:
        return _clean_albers(gpd.read_parquet(self._socioenv_dir / "socioenv_ucs.parquet"))

    @cached_property
    def tis_alb(self) -> gpd.GeoDataFrame:
        return _clean_albers(gpd.read_parquet(self._socioenv_dir / "socioenv_tis.parquet"))

    @cached_property
    def ind_gdf(self) -> gpd.GeoDataFrame:
        return gpd.read_parquet(DATA_PROCESSED / "anm_cimce_industrial.parquet")

    @cached_property
    def ind_df(self) -> pd.DataFrame:
        ix, iy = self._tf.transform(self.ind_gdf["longitude"].values, self.ind_gdf["latitude"].values)
        df = pd.DataFrame(self.ind_gdf.drop(columns=["geometry"]))
        df["x_alb"] = ix
        df["y_alb"] = iy
        return df

    @cached_property
    def gar_df(self) -> pd.DataFrame:
        gar_gdf = gpd.read_parquet(DATA_PROCESSED / "anm_cimce_garimpo_licenciamento.parquet")
        gx, gy = self._tf.transform(gar_gdf["longitude"].values, gar_gdf["latitude"].values)
        df = pd.DataFrame(gar_gdf.drop(columns=["geometry"]))
        df["x_alb"] = gx
        df["y_alb"] = gy
        return df

    @cached_property
    def gar_count(self) -> int:
        gar_path = DATA_PROCESSED / "anm_cimce_garimpo_licenciamento.parquet"
        if gar_path.exists():
            return len(self.gar_df)
        return int(get_anm_snapshot_meta().get("garimpo_lic_count", 0))

    @cached_property
    def conc_alb(self) -> gpd.GeoDataFrame:
        conc = _clean_albers(self.ind_gdf[self.ind_gdf["stage_tier"] == "Concessão de Lavra"].copy())
        cx, cy = self._tf.transform(conc["longitude"].values, conc["latitude"].values)
        conc["x_alb"] = cx
        conc["y_alb"] = cy
        return conc

    @cached_property
    def inb_pts(self) -> pd.DataFrame:
        ux, uy = self._tf.transform([p[1] for p in INB_URANIUM_COMPLEXES], [p[2] for p in INB_URANIUM_COMPLEXES])
        return pd.DataFrame({
            "label": [p[0] for p in INB_URANIUM_COMPLEXES],
            "x_alb": ux,
            "y_alb": uy,
        })

    @cached_property
    def msum(self) -> pd.DataFrame:
        return pd.read_parquet(DATA_PROCESSED / "cimce_mineral_summary.parquet")

    @cached_property
    def uf_sum(self) -> pd.DataFrame:
        return pd.read_parquet(DATA_PROCESSED / "cimce_uf_summary.parquet")

    @cached_property
    def mcs(self) -> pd.DataFrame:
        return pd.read_parquet(DATA_PROCESSED / "cimce_mcs2026_global_27.parquet")

    @cached_property
    def socio(self) -> pd.DataFrame:
        return pd.read_parquet(DATA_PROCESSED / "cimce_socioenv_summary.parquet")

    @cached_property
    def sens(self) -> pd.DataFrame:
        return pd.read_parquet(DATA_PROCESSED / "anm_cimce_sensitivity_excluded.parquet")


def _national_bounds(data: CimceAtlasData, margin: float = 0.05) -> tuple[tuple[float, float], tuple[float, float]]:
    sb = data.states_alb.total_bounds
    eb = data.eez_alb.total_bounds
    xmin = min(sb[0], eb[0])
    ymin = min(sb[1], eb[1])
    xmax = min(max(sb[2], eb[2]), 7_550_000.0)
    ymax = max(sb[3], eb[3])
    dx = xmax - xmin
    dy = ymax - ymin
    return (xmin - dx * margin, xmax + dx * margin), (ymin - dy * margin, ymax + dy * margin)


def _draw_national_basemap(ax, data: CimceAtlasData, show_biomes: bool = False) -> None:
    xlim, ylim = _national_bounds(data)
    data.sa_alb.plot(ax=ax, color="#F1F5F9", edgecolor="#CBD5E1", linewidth=0.5, zorder=1)
    if show_biomes:
        for bname, bcol in BIOME_COLORS.items():
            sub_b = data.biomes_alb[data.biomes_alb["Bioma"] == bname]
            if not sub_b.empty:
                sub_b.plot(ax=ax, color=bcol, edgecolor="none", alpha=0.80, zorder=2, rasterized=True)
        data.states_alb.plot(ax=ax, color="none", edgecolor="#475569", linewidth=0.65, zorder=5)
    else:
        data.states_alb.plot(ax=ax, color="#FAFAFA", edgecolor="#94A3B8", linewidth=0.6, zorder=2)

    data.eez_alb.plot(
        ax=ax, color="#E0F2FE", edgecolor="#0284C7",
        linewidth=0.85, linestyle="--", alpha=0.45, zorder=2,
    )
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.grid(True, linestyle=":", linewidth=0.4, color="#CBD5E1", alpha=0.65, zorder=0)


def _plot_inb_complexes(ax, data: CimceAtlasData, zorder: int = 16) -> None:
    ax.scatter(
        data.inb_pts["x_alb"], data.inb_pts["y_alb"],
        marker="^", s=68, color="#15803D", edgecolor="#052E16", linewidths=0.75,
        zorder=zorder,
    )


def fig_01_national_3_classes(data: CimceAtlasData) -> None:
    fig, ax = plt.subplots(figsize=(11.5, 11.2))
    fig.subplots_adjust(left=0.07, right=0.98, top=0.925, bottom=0.145)
    _draw_national_basemap(ax, data)

    counts_cls = {}
    for z, cls_name in ((6, CLASS_ORDER[2]), (8, CLASS_ORDER[1]), (10, CLASS_ORDER[0])):
        sub = data.ind_df[data.ind_df["cimce_class"] == cls_name]
        counts_cls[cls_name] = len(sub)
        ax.scatter(
            sub["x_alb"], sub["y_alb"],
            s=4.2 if z == 6 else (5.0 if z == 8 else 5.8),
            color=CLASS_COLORS[cls_name], alpha=0.68 if z == 6 else 0.78,
            edgecolors="none", zorder=z, rasterized=True,
        )

    _plot_inb_complexes(ax, data, zorder=15)
    format_map_axes_and_scalebar(ax, IBGE_ALBERS_CRS, scale_pos="bottom_right", candidates_km=[500, 1000], show_crs_box=True)

    handles = [
        mlines.Line2D([], [], color=CLASS_COLORS[c], marker="o", linestyle="None", markersize=6.2,
                      label=f"{c.replace(' — Prioridade ', ' — ')} ({fmt_int_ptbr(counts_cls[c])})")
        for c in CLASS_ORDER
    ]
    handles.append(mlines.Line2D([], [], color="#15803D", marker="^", markeredgecolor="#052E16",
                                 linestyle="None", markersize=6.8, label="Complexos Nucleares INB/CNEN (6)"))
    handles.append(mpatches.Patch(facecolor="#E0F2FE", edgecolor="#0284C7", linestyle="--", label="Amazônia Azul (ZEE)"))
    leg = ax.legend(
        handles=handles, title="Escopo CIMCE 2026 — Regime Industrial",
        loc="lower left", fontsize=7.5, title_fontsize=8.2, frameon=True, facecolor="white", framealpha=0.95,
    )
    leg.set_zorder(30)

    ax.set_title(
        "Minerais Críticos e Estratégicos no Brasil: As 3 Classes do CIMCE (27 Commodities)",
        fontsize=12.6, fontweight="bold", pad=12, color="#0F172A",
    )
    tot_ind = fmt_int_ptbr(len(data.ind_df))
    tot_gar = fmt_int_ptbr(data.gar_count)
    add_cartographic_footer(
        ax,
        title_h1="POLÍTICA NACIONAL DE MINERAIS CRÍTICOS E ESTRATÉGICOS (LEI Nº 15.506/2026)",
        title_h2=f"Distribuição territorial de {tot_ind} processos ANM em regime industrial/pesquisa segundo as Classes I (Alta), II (Média) e III (Baixa).",
        sources_str=f"ANM/SIGMINE ({data.anm_label}); Pleno CIMCE (05/11/2026); INB/CNEN (Urânio*); IBGE Malha UF 2025; Marine Regions (Amazônia Azul).",
        crs_str="",
        caveat_str=f"Exclui {tot_gar} títulos de garimpo/licenciamento, quartzito ornamental/brita e dolomito agrícola (segregados em camada própria).",
    )
    save_figure_dual(fig, "fig_01_mapa_nacional_3_classes_cimce", save_svg=data.save_svg)


def fig_02_concessoes_lavra(data: CimceAtlasData) -> None:
    fig, ax = plt.subplots(figsize=(11.5, 11.2))
    fig.subplots_adjust(left=0.07, right=0.98, top=0.925, bottom=0.145)
    _draw_national_basemap(ax, data)

    conc = data.conc_alb
    counts_c = {}
    for z, cls_name in ((6, CLASS_ORDER[2]), (8, CLASS_ORDER[1]), (10, CLASS_ORDER[0])):
        sub = conc[conc["cimce_class"] == cls_name].copy()
        counts_c[cls_name] = len(sub)
        if not sub.empty:
            sub_simp = sub.copy()
            sub_simp["geometry"] = sub_simp.geometry.simplify(250.0)
            sub_simp.plot(ax=ax, color=CLASS_COLORS[cls_name], edgecolor="#0F172A", linewidth=0.45, alpha=0.88, zorder=z, rasterized=True)
            ax.scatter(
                sub["x_alb"], sub["y_alb"], s=14.0, color=CLASS_COLORS[cls_name],
                edgecolor="#0F172A", linewidths=0.35, alpha=0.92, zorder=z + 1, rasterized=True,
            )

    _plot_inb_complexes(ax, data, zorder=16)

    # Anotacoes distritais dos principais polos produtores em concessao de lavra
    tf = Transformer.from_crs("EPSG:4674", IBGE_ALBERS_CRS, always_xy=True)
    callouts = [
        ("Pitinga (AM)\nSn / Ta / Nb / ETR", (-60.05, -0.75), (-66.2, 2.0), "#1D4ED8"),
        ("Carajás (PA)\nFe / Cu / Mn / Ni", (-50.25, -6.05), (-50.8, 1.0), "#B91C1C"),
        ("Seridó (RN/PB) & Taquari (SE)\nW / K / Ti", (-36.8, -8.5), (-32.5, -4.2), "#D97706"),
        ("Brumado & Campo Formoso (BA)\nMagnesita / Cromita / V", (-41.1, -12.6), (-33.5, -10.5), "#D97706"),
        ("Minaçu & Catalão (GO)\nETR / Nb / Ni / P", (-48.2, -15.6), (-58.6, -13.5), "#B91C1C"),
        ("Vale do Jequitinhonha (MG)\nLítio / Grafita", (-42.2, -16.8), (-33.6, -15.6), "#B91C1C"),
        ("Quadrilátero Ferrífero & Araxá (MG)\nFe / Au / Nb / P", (-44.8, -19.8), (-34.2, -21.5), "#B91C1C"),
    ]
    for txt, (px, py), (tx, ty), col in callouts:
        pxa, pya = tf.transform(px, py)
        txa, tya = tf.transform(tx, ty)
        ax.annotate(
            txt, xy=(pxa, pya), xytext=(txa, tya),
            ha="center", va="center", fontsize=6.6, fontweight="bold", color="#0F172A",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor=col, linewidth=0.9, alpha=0.94),
            arrowprops=dict(arrowstyle="-", color="#334155", linewidth=0.75),
            zorder=22,
        )

    format_map_axes_and_scalebar(ax, IBGE_ALBERS_CRS, scale_pos="bottom_right", candidates_km=[500, 1000], show_crs_box=True)

    handles = [
        mlines.Line2D([], [], color=CLASS_COLORS[c], marker="o", markeredgecolor="#0F172A", markeredgewidth=0.5,
                      linestyle="None", markersize=6.4,
                      label=f"{c.replace(' — Prioridade ', ' — ')} ({fmt_int_ptbr(counts_c[c])})")
        for c in CLASS_ORDER
    ]
    handles.append(mlines.Line2D([], [], color="#15803D", marker="^", markeredgecolor="#052E16",
                                 linestyle="None", markersize=6.8, label="Complexos Nucleares INB/CNEN (6)"))
    handles.append(mpatches.Patch(facecolor="#E0F2FE", edgecolor="#0284C7", linestyle="--", label="Amazônia Azul (ZEE)"))
    leg = ax.legend(
        handles=handles, title=f"Concessões de Lavra Outorgadas ({fmt_int_ptbr(len(conc))})",
        loc="lower left", fontsize=7.4, title_fontsize=8.2, frameon=True, facecolor="white", framealpha=0.95,
    )
    leg.set_zorder(30)

    ax.set_title(
        "Parque Produtivo Outorgado: Concessões de Lavra nos 27 Minerais CIMCE",
        fontsize=12.6, fontweight="bold", pad=12, color="#0F172A",
    )
    add_cartographic_footer(
        ax,
        title_h1="CONCESSÕES DE LAVRA E MANIFESTOS DE MINA EM REGIME INDUSTRIAL",
        title_h2=f"Localização dos {fmt_int_ptbr(len(conc))} títulos ativos em fase de lavra outorgada frente ao acervo total de {fmt_int_ptbr(len(data.ind_df))} processos industriais.",
        sources_str=f"ANM/SIGMINE ({data.anm_label}); INB/CNEN (6 complexos de Urânio*); IBGE Malha UF 2025; Marine Regions (Amazônia Azul).",
        crs_str="",
        caveat_str="Exclui requerimentos, autorizações de pesquisa e garimpos; polígonos em lavra exibem forte concentração em MG, PA, BA e GO.",
    )
    save_figure_dual(fig, "fig_02_mapa_concessoes_lavra_grande_porte", save_svg=data.save_svg)


def _plot_class_map(data: CimceAtlasData, class_name: str, stem: str, title: str, h2: str, caveat: str, include_inb: bool = False) -> None:
    fig, ax = plt.subplots(figsize=(11.5, 11.2))
    fig.subplots_adjust(left=0.07, right=0.98, top=0.925, bottom=0.145)
    _draw_national_basemap(ax, data)

    class_minerals = [m for m in data.ont["minerals"] if m["cimce_class"] == class_name]
    sub_all = data.ind_df[data.ind_df["cimce_class"] == class_name]

    # Plotar mais abundantes por baixo e mais raros por cima
    ordered_for_z = sorted(
        class_minerals,
        key=lambda m: int((sub_all["cimce_mineral"] == m["name"]).sum()),
        reverse=True,
    )
    for i, m in enumerate(ordered_for_z):
        sub_m = sub_all[sub_all["cimce_mineral"] == m["name"]]
        if sub_m.empty:
            continue
        cnt = len(sub_m)
        sz = 4.2 if cnt > 5000 else (5.8 if cnt > 1000 else 10.5)
        ax.scatter(
            sub_m["x_alb"], sub_m["y_alb"],
            s=sz, color=m["color"], alpha=0.72 if cnt > 3000 else 0.88,
            edgecolors="#0F172A" if cnt < 400 else "none", linewidths=0.25 if cnt < 400 else 0.0,
            zorder=5 + i, rasterized=True,
        )

    if include_inb:
        _plot_inb_complexes(ax, data, zorder=22)

    format_map_axes_and_scalebar(ax, IBGE_ALBERS_CRS, scale_pos="bottom_right", candidates_km=[500, 1000], show_crs_box=True)

    handles = []
    for m in class_minerals:
        cnt = int((sub_all["cimce_mineral"] == m["name"]).sum())
        tag = " (ANM + 6 INB)" if m["name"] == "Urânio*" else ""
        handles.append(
            mlines.Line2D([], [], color=m["color"], marker="o", linestyle="None", markersize=6.0,
                          label=f"{m['order']}. {m['name']} ({fmt_int_ptbr(cnt)}){tag}")
        )
    if include_inb:
        handles.append(
            mlines.Line2D([], [], color="#15803D", marker="^", markeredgecolor="#052E16",
                          linestyle="None", markersize=6.4, label="Complexos Nucleares INB/CNEN (6)")
        )
    handles.append(mpatches.Patch(facecolor="#E0F2FE", edgecolor="#0284C7", linestyle="--", label="Amazônia Azul (ZEE)"))
    leg = ax.legend(
        handles=handles, title=class_name,
        loc="lower left", fontsize=7.0, title_fontsize=7.8, frameon=True, facecolor="white", framealpha=0.95,
    )
    leg.set_zorder(30)

    ax.set_title(title, fontsize=12.4, fontweight="bold", pad=12, color="#0F172A")
    add_cartographic_footer(
        ax,
        title_h1=f"DETALHAMENTO ESPACIAL — {class_name.upper()}",
        title_h2=h2,
        sources_str=f"ANM/SIGMINE ({data.anm_label}); Pleno CIMCE (05/11/2026); IBGE Malha UF 2025; Marine Regions (Amazônia Azul).",
        crs_str="",
        caveat_str=caveat,
    )
    save_figure_dual(fig, stem, save_svg=data.save_svg)


def fig_03_classe1(data: CimceAtlasData) -> None:
    sub = data.ind_df[data.ind_df["cimce_class"] == CLASS_ORDER[0]]
    _plot_class_map(
        data,
        class_name=CLASS_ORDER[0],
        stem="fig_03_mapa_classe1_prioridade_alta",
        title="CIMCE Classe I — Prioridade Alta (9 Minerais de Transição Energética e Nuclear)",
        h2=f"Espacialização de {fmt_int_ptbr(len(sub))} processos ANM em regime industrial nos 9 minerais prioritários de fomento (FGAM/PFMCE).",
        caveat="Silício restringe Quartzo/Quartzito ao USO industrial/metalúrgico; Urânio* combina títulos ANM com 6 complexos INB sob monopólio da União.",
        include_inb=True,
    )


def fig_04_classe2(data: CimceAtlasData) -> None:
    sub = data.ind_df[data.ind_df["cimce_class"] == CLASS_ORDER[1]]
    _plot_class_map(
        data,
        class_name=CLASS_ORDER[1],
        stem="fig_04_mapa_classe2_prioridade_media",
        title="CIMCE Classe II — Prioridade Média (8 Minerais de Ligas, Refino e Fertilizantes)",
        h2=f"Distribuição territorial de {fmt_int_ptbr(len(sub))} processos ANM industriais em Tungstênio, Cromo, Vanádio, Tântalo, Titânio, Fosfato, Potássio e Enxofre.",
        caveat="Enxofre possui escassos títulos minerários primários na ANM porque sua oferta industrial advém sobretudo como subproduto do refino de petróleo/gás e sulfetos.",
        include_inb=False,
    )


def fig_05_classe3(data: CimceAtlasData) -> None:
    sub = data.ind_df[data.ind_df["cimce_class"] == CLASS_ORDER[2]]
    _plot_class_map(
        data,
        class_name=CLASS_ORDER[2],
        stem="fig_05_mapa_classe3_prioridade_baixa",
        title="CIMCE Classe III — Prioridade Baixa (10 Minerais de Base, Preciosos, Nióbio e Tório*)",
        h2=f"Mapeamento de {fmt_int_ptbr(len(sub))} processos industriais na Classe III (inclui Nióbio, Ferro, Alumínio, Ouro industrial, Estanho e Tório*).",
        caveat="Ouro e Estanho exibem somente processos industriais (garimpos segregados na Fig. 06); Magnésio exclui dolomito agrícola; Tório* tem 0 títulos primários (monopólio federal / monazita).",
        include_inb=False,
    )


def fig_06_industrial_vs_garimpo(data: CimceAtlasData) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18.2, 9.2))
    fig.subplots_adjust(left=0.045, right=0.985, top=0.885, bottom=0.145, wspace=0.13)

    # Painel A: Regime Industrial
    _draw_national_basemap(ax1, data)
    for z, c in ((6, CLASS_ORDER[2]), (8, CLASS_ORDER[1]), (10, CLASS_ORDER[0])):
        s = data.ind_df[data.ind_df["cimce_class"] == c]
        ax1.scatter(s["x_alb"], s["y_alb"], s=3.2, color=CLASS_COLORS[c], alpha=0.68, edgecolors="none", zorder=z, rasterized=True)
    format_map_axes_and_scalebar(ax1, IBGE_ALBERS_CRS, scale_pos="bottom_right", candidates_km=[500, 1000], show_crs_box=True)
    h1 = [
        mlines.Line2D([], [], color=CLASS_COLORS[c], marker="o", linestyle="None", markersize=5.8,
                      label=f"{c.split(' — ')[0]} ({fmt_int_ptbr(int((data.ind_df['cimce_class'] == c).sum()))})")
        for c in CLASS_ORDER
    ]
    h1.append(mpatches.Patch(facecolor="#E0F2FE", edgecolor="#0284C7", linestyle="--", label="Amazônia Azul (ZEE)"))
    leg1 = ax1.legend(handles=h1, title=f"Regime Industrial ({fmt_int_ptbr(len(data.ind_df))})",
                      loc="lower left", fontsize=7.0, title_fontsize=7.6, frameon=True, facecolor="white", framealpha=0.95)
    leg1.set_zorder(30)
    ax1.set_title("A. Regime Industrial / Pesquisa Mineral (27 Minerais)", fontsize=11.0, fontweight="bold", pad=8, color="#0F172A")

    # Painel B: Garimpo / Licenciamento segregado
    _draw_national_basemap(ax2, data)
    gar_au = data.gar_df[data.gar_df["cimce_mineral"] == "Ouro"]
    gar_sn = data.gar_df[data.gar_df["cimce_mineral"] == "Estanho"]
    gar_ot = data.gar_df[~data.gar_df["cimce_mineral"].isin(["Ouro", "Estanho"])]
    ax2.scatter(gar_au["x_alb"], gar_au["y_alb"], s=3.4, color="#CA8A04", alpha=0.65, edgecolors="none", zorder=7, rasterized=True)
    ax2.scatter(gar_sn["x_alb"], gar_sn["y_alb"], s=4.0, color="#0284C7", alpha=0.72, edgecolors="none", zorder=8, rasterized=True)
    ax2.scatter(gar_ot["x_alb"], gar_ot["y_alb"], s=5.0, color="#7C3AED", alpha=0.80, edgecolors="none", zorder=9, rasterized=True)
    format_map_axes_and_scalebar(ax2, IBGE_ALBERS_CRS, scale_pos="bottom_right", candidates_km=[500, 1000], show_crs_box=True)
    h2 = [
        mlines.Line2D([], [], color="#CA8A04", marker="o", linestyle="None", markersize=5.8,
                      label=f"Ouro — Garimpo/Lic. ({fmt_int_ptbr(len(gar_au))})"),
        mlines.Line2D([], [], color="#0284C7", marker="o", linestyle="None", markersize=5.8,
                      label=f"Estanho — Garimpo/Lic. ({fmt_int_ptbr(len(gar_sn))})"),
        mlines.Line2D([], [], color="#7C3AED", marker="o", linestyle="None", markersize=5.8,
                      label=f"Demais Minerais CIMCE ({fmt_int_ptbr(len(gar_ot))})"),
        mpatches.Patch(facecolor="#E0F2FE", edgecolor="#0284C7", linestyle="--", label="Amazônia Azul (ZEE)"),
    ]
    leg2 = ax2.legend(handles=h2, title=f"Camada Segregada ({fmt_int_ptbr(len(data.gar_df))})",
                      loc="lower left", fontsize=7.0, title_fontsize=7.6, frameon=True, facecolor="white", framealpha=0.95)
    leg2.set_zorder(30)
    ax2.set_title("B. Garimpo, Licenciamento e Registro de Extração (Segregado)", fontsize=11.0, fontweight="bold", pad=8, color="#0F172A")

    fig.suptitle(
        "Segregação Ontológica na ANM: Regime Industrial versus Garimpo e Licenciamento (27 Minerais CIMCE)",
        fontsize=13.5, fontweight="bold", y=0.955, color="#0F172A",
    )
    add_figure_footer(
        fig,
        title_h1="HIGIENE ONTOLÓGICA DE REGIMES MINERÁRIOS NA ANM",
        title_h2=f"Separação espacial entre os {fmt_int_ptbr(len(data.ind_df))} títulos industriais/de pesquisa (Painel A) e os {fmt_int_ptbr(len(data.gar_df))} registros de lavra garimpeira, licenciamento e extração (Painel B).",
        sources_str=f"ANM/SIGMINE ({data.anm_label}); IBGE Malha UF 2025; Marine Regions (Amazônia Azul).",
        crs_str="",
        caveat_str="Ouro e Estanho concentram 94,2% dos títulos na camada de garimpo/licenciamento (sobretudo no Tapajós/PA, MT, RO e AP); a segregação evita distorções analíticas em séries industriais.",
    )
    save_figure_dual(fig, "fig_06_mapa_industrial_vs_garimpo_segregacao")


def fig_07_socioambiental(data: CimceAtlasData) -> None:
    fig, ax = plt.subplots(figsize=(11.5, 11.2))
    fig.subplots_adjust(left=0.07, right=0.98, top=0.925, bottom=0.145)
    _draw_national_basemap(ax, data, show_biomes=True)

    ucs_us = data.ucs_alb[data.ucs_alb["group"] == "US"]
    ucs_pi = data.ucs_alb[data.ucs_alb["group"] == "PI"]
    if not ucs_us.empty:
        ucs_us.plot(ax=ax, color="#86EFAC", edgecolor="#16A34A", linewidth=0.25, alpha=0.45, zorder=3, rasterized=True)
    if not ucs_pi.empty:
        ucs_pi.plot(ax=ax, color="#15803D", edgecolor="#052E16", linewidth=0.35, alpha=0.58, zorder=4, rasterized=True)
    if not data.tis_alb.empty:
        data.tis_alb.plot(ax=ax, color="#FDE047", edgecolor="#CA8A04", linewidth=0.35, alpha=0.58, zorder=4, rasterized=True)

    for z, c in ((7, CLASS_ORDER[2]), (8, CLASS_ORDER[1]), (9, CLASS_ORDER[0])):
        s = data.ind_df[data.ind_df["cimce_class"] == c]
        ax.scatter(s["x_alb"], s["y_alb"], s=2.8, color=CLASS_COLORS[c], alpha=0.62, edgecolors="none", zorder=z, rasterized=True)

    format_map_axes_and_scalebar(ax, IBGE_ALBERS_CRS, scale_pos="bottom_right", candidates_km=[500, 1000], show_crs_box=True)

    handles = [mpatches.Patch(facecolor=BIOME_COLORS[b], edgecolor="#94A3B8", label=b) for b in ("Amazônia", "Cerrado", "Caatinga", "Mata Atlântica", "Pampa", "Pantanal")]
    handles.extend([
        mpatches.Patch(facecolor="#15803D", edgecolor="#052E16", alpha=0.7, label="UCs Proteção Integral (CNUC)"),
        mpatches.Patch(facecolor="#86EFAC", edgecolor="#16A34A", alpha=0.7, label="UCs Uso Sustentável (CNUC)"),
        mpatches.Patch(facecolor="#FDE047", edgecolor="#CA8A04", alpha=0.7, label="Terras Indígenas (FUNAI)"),
    ])
    for c in CLASS_ORDER:
        handles.append(mlines.Line2D([], [], color=CLASS_COLORS[c], marker="o", linestyle="None", markersize=5.4, label=c.split(" — ")[0]))
    handles.append(mpatches.Patch(facecolor="#E0F2FE", edgecolor="#0284C7", linestyle="--", label="Amazônia Azul (ZEE)"))

    leg = ax.legend(
        handles=handles, title="Camadas Socioambientais e Classes CIMCE",
        loc="lower left", ncol=2, fontsize=6.4, title_fontsize=7.3, frameon=True, facecolor="white", framealpha=0.95,
    )
    leg.set_zorder(30)

    ax.set_title(
        "Interface Socioambiental dos 27 Minerais CIMCE: Biomas, UCs e Terras Indígenas",
        fontsize=12.4, fontweight="bold", pad=12, color="#0F172A",
    )
    tot_row = data.socio[(data.socio["scope"] == "Total 27 Minerais (Regime Industrial)") & (data.socio["dimension"] == "Total Geral")].iloc[0]
    add_cartographic_footer(
        ax,
        title_h1="SOBREPOSIÇÃO TERRITORIAL DO REGIME INDUSTRIAL CIMCE EM ÁREAS PROTEGIDAS",
        title_h2=f"Pegada líquida nacional de {fmt_ptbr(tot_row['net_overlap_ha'] / 1e6, 2)} Mha (vs. {fmt_ptbr(tot_row['gross_overlap_ha'] / 1e6, 2)} Mha brutos) sobre 6 Biomas, 1.934 UCs e 624 TIs.",
        sources_str=f"ANM/SIGMINE ({data.anm_label}); IBGE Biomas 1:250k (2019) & UFs 2025; CNUC/MMA (set/2019); FUNAI (mar/2021); Marine Regions (Amazônia Azul).",
        crs_str="",
        caveat_str="A ocorrência de polígono minerário (sobretudo requerimento/pesquisa) sobre UC ou TI não implica autorização de lavra nem supressão ambiental.",
    )
    save_figure_dual(fig, "fig_07_mapa_socioambiental_biomas_ucs_tis")


def fig_08_regulatory_funnel(data: CimceAtlasData) -> None:
    df = data.msum.sort_values("order", ascending=False).reset_index(drop=True)  # order=1 no topo
    y = np.arange(len(df))
    labels = [f"{int(r['order'])}. {r['cimce_mineral']} {'[MCTI]' if r['in_mcti_recursos_14'] else '[Novo]'}" for _, r in df.iterrows()]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16.8, 10.8), gridspec_kw={"width_ratios": [1.32, 1.0], "wspace": 0.36})
    fig.subplots_adjust(left=0.145, right=0.975, top=0.885, bottom=0.125)

    conc_v = df["concessao_lavra"].values
    pre_v = df["requerimento_lavra"].values + df["direito_requerer_lavra"].values
    pesq_v = df["autorizacao_pesquisa"].values + df["req_pesquisa_disp_outros"].values
    tot_v = df["industrial_total"].values

    ax1.barh(y, conc_v, height=0.68, color="#B91C1C", label="Concessão de Lavra")
    ax1.barh(y, pre_v, left=conc_v, height=0.68, color="#EA580C", label="Pré-Lavra (Req. / Dir. Req.)")
    ax1.barh(y, pesq_v, left=conc_v + pre_v, height=0.68, color="#2563EB", label="Pesquisa / Disp. / Outros")
    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=7.6, fontweight="bold")
    for idx_y, tick_lbl in enumerate(ax1.get_yticklabels()):
        tick_lbl.set_color(CLASS_COLORS[df.loc[idx_y, "cimce_class"]])

    max_tot = max(float(tot_v.max()), 1.0)
    ax1.set_xlim(0, max_tot * 1.24)
    ax1.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: fmt_int_ptbr(v)))
    ax1.set_xlabel("Processos ANM em Regime Industrial (Escala Linear)", fontsize=8.8, fontweight="bold")
    ax1.set_title("A. Acervo Cadastral Industrial por Estágio Regulatório", fontsize=10.6, fontweight="bold", pad=8)
    ax1.legend(loc="lower right", fontsize=7.4, frameon=True, facecolor="white", framealpha=0.95)
    ax1.grid(True, axis="x", linestyle="--", alpha=0.35)
    for i, r in df.iterrows():
        suffix = " (Monop. União)" if r["cimce_mineral"] in ("Urânio*", "Tório*") else (" (Subproduto†)" if r["cimce_mineral"] == "Enxofre" else "")
        ax1.text(tot_v[i] + max_tot * 0.015, i, f"{fmt_int_ptbr(tot_v[i])}{suffix}", va="center", ha="left", fontsize=6.6, fontweight="bold", color="#1E293B")

    # Painel B: Concessoes de Lavra
    bar_cols = [CLASS_COLORS[c] for c in df["cimce_class"]]
    ax2.barh(y, conc_v, height=0.68, color=bar_cols, alpha=0.90)
    ax2.set_yticks(y)
    ax2.set_yticklabels([f"{int(r['order'])}. {r['cimce_mineral']}" for _, r in df.iterrows()], fontsize=7.6, fontweight="bold")
    for idx_y, tick_lbl in enumerate(ax2.get_yticklabels()):
        tick_lbl.set_color(CLASS_COLORS[df.loc[idx_y, "cimce_class"]])
    max_conc = max(float(conc_v.max()), 1.0)
    ax2.set_xlim(0, max_conc * 1.28)
    ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: fmt_int_ptbr(v)))
    ax2.set_xlabel("Concessões de Lavra / Manifestos de Mina Outorgados (Escala Linear)", fontsize=8.8, fontweight="bold")
    ax2.set_title("B. Maturidade Produtiva: Concessões Outorgadas por Classe", fontsize=10.6, fontweight="bold", pad=8)
    ax2.grid(True, axis="x", linestyle="--", alpha=0.35)
    h_cls = [mpatches.Patch(color=CLASS_COLORS[c], label=c.replace(" — Prioridade ", " (") + ")") for c in CLASS_ORDER]
    ax2.legend(handles=h_cls, loc="lower right", fontsize=7.2, frameon=True, facecolor="white", framealpha=0.95)
    for i, r in df.iterrows():
        pct = (r["concessao_lavra"] / r["industrial_total"] * 100.0) if r["industrial_total"] > 0 else 0.0
        ax2.text(conc_v[i] + max_conc * 0.015, i, f"{fmt_int_ptbr(conc_v[i])} ({fmt_ptbr(pct, 1)}%)", va="center", ha="left", fontsize=6.6, fontweight="bold", color="#1E293B")

    # Linhas divisoras entre Classes III (0..9), II (10..17) e I (18..26)
    for ax_p in (ax1, ax2):
        ax_p.axhline(9.5, color="#475569", linestyle="--", linewidth=0.9, alpha=0.7)
        ax_p.axhline(17.5, color="#475569", linestyle="--", linewidth=0.9, alpha=0.7)

    fig.suptitle(
        "Funil Regulatório ANM nos 27 Minerais CIMCE 2026: Estágios Industriais e Concessões",
        fontsize=13.5, fontweight="bold", y=0.952, color="#0F172A",
    )
    add_figure_footer(
        fig,
        title_h1="MATURIDADE REGULATÓRIA E CONCENTRAÇÃO DE ATIVOS EM FASE DE PESQUISA",
        title_h2=f"Comparação entre os {fmt_int_ptbr(int(tot_v.sum()))} processos industriais e as {fmt_int_ptbr(int(conc_v.sum()))} Concessões de Lavra outorgadas nas Classes I–III.",
        sources_str=f"ANM/SIGMINE ({data.anm_label}); Deliberação Pleno CIMCE (05/11/2026); INB/CNEN.",
        crs_str="",
        caveat_str="[MCTI]=14 minerais presentes na lista `recursos`; [Novo]=13 adicionados pelo CIMCE 2026. *Urânio/Tório: monopólio federal (CF arts. 21 e 177). †Enxofre: oferta industrial via subproduto.",
    )
    save_figure_dual(fig, "fig_08_funil_regulatorio_27_minerais")


def fig_09_global_positioning_brazil(data: CimceAtlasData) -> None:
    df = data.mcs.sort_values("order", ascending=False).reset_index(drop=True)
    y = np.arange(len(df))
    h = 0.37

    fig, ax = plt.subplots(figsize=(14.8, 11.4))
    fig.subplots_adjust(left=0.19, right=0.975, top=0.90, bottom=0.125)

    ax.barh(y + h / 2, df["brazil_res_pct"], height=h, color="#7C3AED", alpha=0.92, label="% das Reservas Globais Lavráveis (2025)")
    ax.barh(y - h / 2, df["brazil_prod_pct"], height=h, color="#0D9488", alpha=0.92, label="% da Produção Mineral Mundial (2025)")

    ylabels = [
        f"{int(r['order'])}. {r['cimce_mineral']}  [{'MCTI-14' if r['in_mcti_recursos_14'] else 'CIMCE+'}]"
        for _, r in df.iterrows()
    ]
    ax.set_yticks(y)
    ax.set_yticklabels(ylabels, fontsize=7.9, fontweight="bold")
    for idx_y, t in enumerate(ax.get_yticklabels()):
        t.set_color(CLASS_COLORS[df.loc[idx_y, "cimce_class"]])

    ax.axhline(9.5, color="#475569", linestyle="--", linewidth=0.9, alpha=0.7)
    ax.axhline(17.5, color="#475569", linestyle="--", linewidth=0.9, alpha=0.7)
    ax.set_xlim(0, 114)
    ax.set_xlabel("Participação do Brasil no Total Mundial (%) — Escala Linear", fontsize=9.2, fontweight="bold")
    ax.grid(True, axis="x", linestyle="--", alpha=0.35)
    ax.legend(loc="lower right", fontsize=8.0, frameon=True, facecolor="white", framealpha=0.95)

    for i, r in df.iterrows():
        rp = float(r["brazil_res_pct"])
        pp = float(r["brazil_prod_pct"])
        r_tag = f"{fmt_ptbr(rp, 1)}%" if rp > 0 else f"0% ({'Abundante' if 'Abundante' in str(r['br_res_status']) else 'Other/N/D'})"
        p_tag = f"{fmt_ptbr(pp, 1)}%" if pp > 0 else ("Sem prod. primária" if r["cimce_mineral"] == "Tório*" else "0% (Other/N/D)")
        ax.text(rp + 1.0, i + h / 2, r_tag, va="center", ha="left", fontsize=6.4, fontweight="bold", color="#5B21B6")
        ax.text(pp + 1.0, i - h / 2, p_tag, va="center", ha="left", fontsize=6.4, fontweight="bold", color="#0F766E")

    # Etiquetas de classe na margem interna direita
    for y_box, cl_lbl in ((22.5, "CLASSE I\nAlta (1–9)"), (13.5, "CLASSE II\nMédia (10–17)"), (4.5, "CLASSE III\nBaixa (18–27)")):
        ax.text(112.0, y_box, cl_lbl, ha="right", va="center", fontsize=8.2, fontweight="bold",
                color="#334155", bbox=dict(boxstyle="round,pad=0.3", facecolor="#F8FAFC", edgecolor="#CBD5E1"))

    ax.set_title(
        "Posicionamento Global do Brasil nos 27 Minerais CIMCE 2026: Reservas versus Produção",
        fontsize=13.0, fontweight="bold", pad=12, color="#0F172A",
    )
    add_figure_footer(
        fig,
        title_h1="ASSIMETRIA ENTRE DOTAÇÃO DE RESERVAS E PRODUÇÃO MINERAL CORRENTE",
        title_h2="Participação percentual do Brasil frente aos totais mundiais em cada um dos 27 minerais das Classes I, II e III do CIMCE.",
        sources_str="USGS Mineral Commodity Summaries (MCS 2026); suplementação auditada ANM/SGB; WNA / OECD-NEA & IAEA Red Book e INB/CNEN (Urânio*, Tório*).",
        crs_str="",
        caveat_str="'Other/N/D' indica commodities em que o Brasil não consta desagregado no MCS 2026 sem suplemento internacional equivalente (reportado 0% sem imputação arbitrária). 'Abundante': Silício (quartzo/sílica abundante globalmente).",
    )
    save_figure_dual(fig, "fig_09_posicionamento_global_brasil_mcs2026")


def fig_10_global_blocks(data: CimceAtlasData) -> None:
    df = data.mcs.sort_values("order", ascending=False).reset_index(drop=True)
    y = np.arange(len(df))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(17.8, 11.4), gridspec_kw={"wspace": 0.34})
    fig.subplots_adjust(left=0.135, right=0.98, top=0.875, bottom=0.125)

    for ax_p, prefix, title_p, top_c_col, top_p_col, is_res in (
        (ax1, "prod", "A. Produção Mineral Mundial (2025)", "top1_prod_country", "top1_prod_pct", False),
        (ax2, "res", "B. Reservas Globais Lavráveis (2025)", "top1_res_country", "top1_res_pct", True),
    ):
        b1 = df[f"brics_{prefix}_pct"].values
        b2 = df[f"demais_sul_{prefix}_pct"].values
        b3 = df[f"norte_{prefix}_pct"].values
        b4 = df[f"outros_{prefix}_pct"].values
        ax_p.barh(y, b1, height=0.68, color=BLOCK_COLORS["BRICS+"], label="BRICS+ Expandido (11 Membros)")
        ax_p.barh(y, b2, left=b1, height=0.68, color=BLOCK_COLORS["Demais Sul Global"], label="Demais Sul Global")
        ax_p.barh(y, b3, left=b1 + b2, height=0.68, color=BLOCK_COLORS["Norte Global"], label="Norte Global")
        ax_p.barh(y, b4, left=b1 + b2 + b3, height=0.68, color=BLOCK_COLORS["Outros USGS"], label="Outros USGS / Residual / N/D")
        ax_p.set_yticks(y)
        ax_p.set_yticklabels([f"{int(r['order'])}. {r['cimce_mineral']}" for _, r in df.iterrows()], fontsize=7.8, fontweight="bold")
        for idx_y, tick_lbl in enumerate(ax_p.get_yticklabels()):
            tick_lbl.set_color(CLASS_COLORS[df.loc[idx_y, "cimce_class"]])
        ax_p.axhline(9.5, color="#475569", linestyle="--", linewidth=0.9, alpha=0.7)
        ax_p.axhline(17.5, color="#475569", linestyle="--", linewidth=0.9, alpha=0.7)
        ax_p.set_xlim(0, 148)
        ax_p.set_xticks([0, 25, 50, 75, 100])
        ax_p.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=8.0)
        ax_p.set_title(title_p, fontsize=10.8, fontweight="bold", pad=8)
        ax_p.grid(True, axis="x", linestyle="--", alpha=0.35)

        pt_country = {
            "Australia": "Austrália", "South Africa": "África do Sul", "Congo (Kinshasa)": "R.D. Congo",
            "Kazakhstan": "Cazaquistão", "Indonesia": "Indonésia", "Morocco": "Marrocos",
            "Russia": "Rússia", "Brazil": "Brasil", "Canada": "Canadá", "India": "Índia",
            "Guinea": "Guiné", "United States": "EUA", "Chile": "Chile", "China": "China", "Peru": "Peru",
        }
        for i, r in df.iterrows():
            c1 = pt_country.get(str(r[top_c_col]), str(r[top_c_col]))
            p1 = float(r[top_p_col])
            if p1 <= 0.0:
                lbl_r = "N/D (Sem prod. primária)" if not is_res and r["cimce_mineral"] == "Tório*" else "Abundante (N/D)"
            else:
                lbl_r = f"{c1} ({fmt_ptbr(p1, 1)}%)"
            ax_p.text(101.5, i, lbl_r, va="center", ha="left", fontsize=6.5, fontweight="bold", color="#1E293B")

    handles, labels_h = ax1.get_legend_handles_labels()
    fig.legend(handles, labels_h, loc="upper center", bbox_to_anchor=(0.54, 0.922), ncol=4, fontsize=8.2, frameon=True, facecolor="white", framealpha=0.95)
    fig.suptitle(
        "Concentração Geopolítica Global nos 27 Minerais CIMCE 2026 (BRICS+, Sul Global e Norte Global)",
        fontsize=13.5, fontweight="bold", y=0.96, color="#0F172A",
    )
    add_figure_footer(
        fig,
        title_h1="GEOPOLÍTICA DAS CADEIAS CRÍTICAS DO CIMCE NO TABULEIRO MUNDIAL",
        title_h2="Partição percentual da produção anual e das reservas globais por bloco geopolítico, com indicação do líder mundial individual em cada mineral.",
        sources_str="USGS Mineral Commodity Summaries (MCS 2026); suplementação ANM/SGB; WNA / OECD-NEA & IAEA Red Book e INB/CNEN (Urânio*, Tório*).",
        crs_str="",
        caveat_str="BRICS+ inclui os 11 membros plenos (BRA, RUS, CHN, IND, ZAF, IRN, EGY, IDN, SAU, ETH, ARE). Tório* não possui produção comercial primária; Silício possui reservas de quartzo abundantes sem quantificação numérica global.",
    )
    save_figure_dual(fig, "fig_10_concentracao_global_blocos_geopoliticos")


def fig_11_temporal_and_ufs(data: CimceAtlasData) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(17.0, 7.8), gridspec_kw={"wspace": 0.24})
    fig.subplots_adjust(left=0.065, right=0.975, top=0.86, bottom=0.175)

    # Painel A: Coortes temporais por Classe
    cohorts = ["≤1999", "2000–2009", "2010–2019", "2020–2022", "2023", "2024", "2025", "2026"]
    df_t = data.ind_df.copy()
    fy = pd.to_numeric(df_t["first_year"], errors="coerce")
    def _co(y_val):
        if pd.isna(y_val): return "Sem Ano"
        yi = int(y_val)
        if yi <= 1999: return "≤1999"
        if yi <= 2009: return "2000–2009"
        if yi <= 2019: return "2010–2019"
        if yi <= 2022: return "2020–2022"
        return str(yi)
    df_t["coorte"] = fy.map(_co)
    ct = pd.crosstab(df_t["coorte"], df_t["cimce_class"]).reindex(index=cohorts, columns=CLASS_ORDER, fill_value=0)
    x = np.arange(len(cohorts))
    bottom = np.zeros(len(cohorts))
    for cl in CLASS_ORDER:
        vals = ct[cl].values
        ax1.bar(x, vals, bottom=bottom, width=0.64, color=CLASS_COLORS[cl], label=cl.split(" — ")[0], alpha=0.90)
        bottom += vals
    ax1.set_xticks(x)
    ax1.set_xticklabels(cohorts, fontsize=8.0, fontweight="bold")
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: fmt_int_ptbr(v)))
    ax1.set_ylim(0, float(bottom.max()) * 1.16)
    ax1.set_ylabel("Processos ANM Industriais (Escala Linear)", fontsize=8.8, fontweight="bold")
    ax1.set_title("A. Coortes de Protocolo ANM por Classe CIMCE (≤1999–2026)", fontsize=10.6, fontweight="bold", pad=8)
    ax1.grid(True, axis="y", linestyle="--", alpha=0.35)
    ax1.legend(loc="upper left", fontsize=7.6, frameon=True, facecolor="white", framealpha=0.95)
    for i, tot in enumerate(bottom):
        ax1.text(i, tot + float(bottom.max()) * 0.015, fmt_int_ptbr(tot), ha="center", va="bottom", fontsize=7.0, fontweight="bold", color="#0F172A")

    boom_n = int(ct.loc[["2023", "2024", "2025", "2026"]].values.sum())
    boom_pct = boom_n / max(int(ct.values.sum()), 1) * 100.0
    ax1.plot([3.65, 7.35], [10_900, 10_900], color="#B91C1C", linewidth=1.3, linestyle="-", zorder=12)
    ax1.plot([3.65, 3.65], [10_200, 10_900], color="#B91C1C", linewidth=1.3, zorder=12)
    ax1.plot([7.35, 7.35], [10_200, 10_900], color="#B91C1C", linewidth=1.3, zorder=12)
    ax1.annotate(
        f"Ciclo 2023–2026:\n{fmt_int_ptbr(boom_n)} proc. ({fmt_ptbr(boom_pct, 1)}%)",
        xy=(5.5, 11_100), xytext=(5.5, 19_200),
        ha="center", va="bottom", fontsize=7.6, fontweight="bold", color="#7F1D1D",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#FEF2F2", edgecolor="#B91C1C"),
        arrowprops=dict(arrowstyle="->", color="#B91C1C", linewidth=1.1),
    )

    # Painel B: Top 15 UFs
    uf15 = data.uf_sum.sort_values("industrial_total", ascending=False).head(15).iloc[::-1].reset_index(drop=True)
    yu = np.arange(len(uf15))
    c1v = uf15["classe_1"].values
    c2v = uf15["classe_2"].values
    c3v = uf15["classe_3"].values
    ax2.barh(yu, c1v, height=0.68, color=CLASS_COLORS[CLASS_ORDER[0]], label="Classe I (Alta)", alpha=0.90)
    ax2.barh(yu, c2v, left=c1v, height=0.68, color=CLASS_COLORS[CLASS_ORDER[1]], label="Classe II (Média)", alpha=0.90)
    ax2.barh(yu, c3v, left=c1v + c2v, height=0.68, color=CLASS_COLORS[CLASS_ORDER[2]], label="Classe III (Baixa)", alpha=0.90)
    ax2.set_yticks(yu)
    ax2.set_yticklabels([f"{r['uf']} ({r['region']})" for _, r in uf15.iterrows()], fontsize=8.0, fontweight="bold")
    max_uf = max(float(uf15["industrial_total"].max()), 1.0)
    ax2.set_xlim(0, max_uf * 1.34)
    ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: fmt_int_ptbr(v)))
    ax2.set_xlabel("Processos Industriais ANM (Escala Linear)", fontsize=8.8, fontweight="bold")
    ax2.set_title("B. Top 15 UFs por Volume Cadastral e Concessões de Lavra", fontsize=10.6, fontweight="bold", pad=8)
    ax2.grid(True, axis="x", linestyle="--", alpha=0.35)
    for i, r in uf15.iterrows():
        ax2.text(
            float(r["industrial_total"]) + max_uf * 0.015, i,
            f"{fmt_int_ptbr(r['industrial_total'])} | {fmt_int_ptbr(r['concessao_lavra'])} conc.",
            va="center", ha="left", fontsize=6.7, fontweight="bold", color="#1E293B",
        )

    fig.suptitle(
        "Dinâmica Temporal (≤1999–2026) e Concentração Federativa nos 27 Minerais CIMCE",
        fontsize=13.5, fontweight="bold", y=0.955, color="#0F172A",
    )
    add_figure_footer(
        fig,
        title_h1="EXPANSÃO RECENTE DE PROTOCOLOS E CONCENTRAÇÃO ESTADUAL NA ANM",
        title_h2="Evolução histórica por ano de protocolo (Painel A) e liderança das 15 principais Unidades da Federação (Painel B).",
        sources_str=f"ANM/SIGMINE ({data.anm_label}; campo ANO de protocolo original).",
        crs_str="",
        caveat_str=f"Minas Gerais, Pará e Bahia lideram o acervo industrial em regime CIMCE; processos recentes (2023–2026) representam {fmt_ptbr(boom_pct, 1)}% do estoque analisado em Classes I–III.",
    )
    save_figure_dual(fig, "fig_11_dinamica_temporal_e_ranking_ufs")


def fig_12_socioenv_and_exclusions(data: CimceAtlasData) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(17.4, 8.2), gridspec_kw={"wspace": 0.44})
    fig.subplots_adjust(left=0.13, right=0.975, top=0.86, bottom=0.175)

    # Painel A: Bruta vs Liquida por Bioma/UC/TI (Total 27 Industrial)
    sub_s = data.socio[
        (data.socio["scope"] == "Total 27 Minerais (Regime Industrial)")
        & (data.socio["dimension"] != "Total Geral")
    ].copy()
    sub_s = sub_s.sort_values("net_overlap_ha", ascending=True).reset_index(drop=True)
    y1 = np.arange(len(sub_s))
    h1 = 0.37
    gross_m = sub_s["gross_overlap_ha"] / 1e6
    net_m = sub_s["net_overlap_ha"] / 1e6
    ax1.barh(y1 + h1 / 2, gross_m, height=h1, color="#94A3B8", label="Área Bruta Sobreposta (Mha)")
    ax1.barh(y1 - h1 / 2, net_m, height=h1, color="#0D9488", label="Pegada Líquida Dissolvida (Mha)")
    ax1.set_yticks(y1)
    ax1.set_yticklabels(sub_s["category"], fontsize=7.6, fontweight="bold")
    max_g = max(float(gross_m.max()), 1.0)
    ax1.set_xlim(0, max_g * 1.36)
    ax1.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: fmt_ptbr(v, 1)))
    ax1.set_xlabel("Milhões de Hectares (Mha — Escala Linear em IBGE Albers)", fontsize=8.6, fontweight="bold")
    ax1.set_title("A. Exposição Socioambiental: Bruta vs. Líquida Dissolvida", fontsize=10.4, fontweight="bold", pad=8)
    ax1.legend(loc="lower right", fontsize=7.4, frameon=True, facecolor="white", framealpha=0.95)
    ax1.grid(True, axis="x", linestyle="--", alpha=0.35)
    for i, r in sub_s.iterrows():
        gm = r["gross_overlap_ha"] / 1e6
        nm = r["net_overlap_ha"] / 1e6
        ax1.text(gm + max_g * 0.015, i + h1 / 2, f"{fmt_ptbr(gm, 2)} Mha", va="center", ha="left", fontsize=6.4, color="#475569", fontweight="bold")
        ax1.text(nm + max_g * 0.015, i - h1 / 2, f"{fmt_ptbr(nm, 2)} Mha ({fmt_ptbr(r['pct_net_footprint'], 1)}%)", va="center", ha="left", fontsize=6.4, color="#0F766E", fontweight="bold")

    # Painel B: Auditoria de Exclusoes Ontologicas e Garimpo Segregado
    gar_au = data.gar_df[data.gar_df["cimce_mineral"] == "Ouro"]
    gar_sn = data.gar_df[data.gar_df["cimce_mineral"] == "Estanho"]
    gar_ot = data.gar_df[~data.gar_df["cimce_mineral"].isin(["Ouro", "Estanho"])]
    buckets = [
        ("Garimpo / Lic. — Ouro", len(gar_au), float(gar_au["area_ha"].sum()), "#CA8A04"),
        ("Garimpo / Lic. — Estanho", len(gar_sn), float(gar_sn["area_ha"].sum()), "#0284C7"),
        ("Garimpo / Lic. — Outros CIMCE", len(gar_ot), float(gar_ot["area_ha"].sum()), "#7C3AED"),
    ]
    for _, sr in data.sens.iterrows():
        buckets.append((str(sr["sensitivity_bucket"]), int(sr["excluded_count"]), float(sr["excluded_area_ha"]), "#DC2626"))

    bdf = pd.DataFrame(buckets, columns=["bucket", "count", "area_ha", "color"]).sort_values("count", ascending=True).reset_index(drop=True)
    y2 = np.arange(len(bdf))
    ax2.barh(y2, bdf["count"], height=0.62, color=bdf["color"], alpha=0.88)
    ax2.set_yticks(y2)
    ax2.set_yticklabels(bdf["bucket"], fontsize=7.4, fontweight="bold")
    max_b = max(float(bdf["count"].max()), 1.0)
    ax2.set_xlim(0, max_b * 1.54)
    ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: fmt_int_ptbr(v)))
    ax2.set_xlabel("Quantidade de Processos ANM Segregados / Expurgados (Escala Linear)", fontsize=8.6, fontweight="bold")
    ax2.set_title("B. Auditoria Ontológica: Garimpos Segregados e Exclusões", fontsize=10.4, fontweight="bold", pad=8)
    ax2.grid(True, axis="x", linestyle="--", alpha=0.35)
    h_ex = [
        mpatches.Patch(color="#CA8A04", label="Garimpo / Licenciamento (Camada Própria)"),
        mpatches.Patch(color="#DC2626", label="Substância Não Industrial / Falso Positivo (Expurgado)"),
    ]
    ax2.legend(handles=h_ex, loc="lower right", fontsize=7.0, frameon=True, facecolor="white", framealpha=0.95)
    for i, r in bdf.iterrows():
        area_str = f"{fmt_ptbr(r['area_ha'] / 1e6, 2)} Mha" if r["area_ha"] >= 1e6 else f"{fmt_int_ptbr(r['area_ha'])} ha"
        ax2.text(r["count"] + max_b * 0.015, i, f"{fmt_int_ptbr(r['count'])} proc. ({area_str})", va="center", ha="left", fontsize=6.5, fontweight="bold", color="#1E293B")

    fig.suptitle(
        "Perfil Socioambiental e Auditoria de Higiene Ontológica no Cadastro CIMCE 2026",
        fontsize=13.5, fontweight="bold", y=0.955, color="#0F172A",
    )
    add_figure_footer(
        fig,
        title_h1="CONTABILIDADE LÍQUIDA DISSOLVIDA E TRILHA DE EXCLUSÕES NA ANM",
        title_h2="Sobreposição dupla em IBGE Albers para os 27 minerais industriais (Painel A) e reconciliação de processos garimpeiros/excluídos (Painel B).",
        sources_str=f"ANM/SIGMINE ({data.anm_label}); IBGE Biomas 1:250k (2019); CNUC/MMA (set/2019); FUNAI (mar/2021).",
        crs_str="",
        caveat_str="A exclusão de quartzito ornamental/brita e dolomito/calcário agrícola previne contaminação de Silício (Classe I) e Magnésio (Classe III) por cadeias da construção civil e calagem.",
    )
    save_figure_dual(fig, "fig_12_perfil_socioambiental_e_auditoria_exclusoes")


FIGURE_REGISTRY = {
    1: fig_01_national_3_classes,
    2: fig_02_concessoes_lavra,
    3: fig_03_classe1,
    4: fig_04_classe2,
    5: fig_05_classe3,
    6: fig_06_industrial_vs_garimpo,
    7: fig_07_socioambiental,
    8: fig_08_regulatory_funnel,
    9: fig_09_global_positioning_brazil,
    10: fig_10_global_blocks,
    11: fig_11_temporal_and_ufs,
    12: fig_12_socioenv_and_exclusions,
}
PUBLISHED_FIGURES = [1, 2, 3, 4, 5]


def main(figures: list[int] | None = None, png_only: bool = False) -> None:
    setup_editorial_style()
    if figures is None:
        has_full = (
            resolve_socioenv_dir() is not None
            and (DATA_PROCESSED / "cimce_mcs2026_global_27.parquet").exists()
            and (DATA_PROCESSED / "cimce_socioenv_summary.parquet").exists()
        )
        selected = sorted(FIGURE_REGISTRY.keys()) if has_full else PUBLISHED_FIGURES
    else:
        selected = figures

    logging.info("Gerando figuras CIMCE 2026 (%s | png_only=%s)...", selected, png_only)
    data = CimceAtlasData(save_svg=not png_only)
    for fid in selected:
        fn = FIGURE_REGISTRY[fid]
        logging.info("Gerando fig_%02d (%s)...", fid, fn.__name__)
        fn(data)
    logging.info("Geracao de figuras concluida com sucesso!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera mapas e figuras 600 DPI do CIMCE 2026.")
    parser.add_argument("--figures", nargs="+", type=int, choices=sorted(FIGURE_REGISTRY.keys()), help="IDs das figuras (padrao: 1 2 3 4 5 em clone enxuto).")
    parser.add_argument("--all", action="store_true", help="Gera todas as 12 figuras do atlas completo.")
    parser.add_argument("--png-only", action="store_true", help="Salva apenas PNG 600 DPI (sem SVG).")
    args = parser.parse_args()
    figs = sorted(FIGURE_REGISTRY.keys()) if args.all else args.figures
    main(figures=figs, png_only=args.png_only)
