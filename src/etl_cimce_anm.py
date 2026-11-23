# src/etl_cimce_anm.py
"""
Etapa 1: ETL geoespacial dos 27 Minerais Criticos e Estrategicos do CIMCE/PNMCE 2026
a partir do shapefile ANM/SIGMINE (nov/2026) com segregacao ontologica estrita:
  - Regime industrial vs. Garimpo / Licenciamento (Ouro, Estanho, etc.)
  - Silicio restrito a rotas industriais/metalurgicas (expurgo de quartzito ornamental/brita)
  - Magnesio restrito a magnesita/Mg (expurgo de dolomito/calcario agricola)
  - Expurgo de CITRINO e OURO PIGMENTO
  - Sobreposicao socioambiental dupla (Bruta vs. Liquida Dissolvida em IBGE Albers)
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely import STRtree

from src.config import (
    CLASS_ORDER,
    DATA_PROCESSED,
    IBGE_ALBERS_CRS,
    UF_TO_REGION,
    classify_anm_stage,
    load_ontology,
    normalize_text,
    resolve_anm_meta,
    resolve_anm_shp,
    resolve_socioenv_dir,
)

logging.basicConfig(level=logging.INFO, format="[ETL CIMCE ANM] %(levelname)s: %(message)s")


def _clean_albers(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = gdf.to_crs(IBGE_ALBERS_CRS).copy()
    out["geometry"] = shapely.force_2d(out.geometry).make_valid()
    return out[~out.geometry.is_empty & out.geometry.notna()].copy()


def _extract_event_year(val: object) -> float | None:
    if not val:
        return None
    m = re.search(r"EM\s+\d{2}/\d{2}/(19\d{2}|20\d{2})", str(val))
    if not m:
        m = re.search(r"\b(19\d{2}|20\d{2})\b", str(val))
    return float(m.group(1)) if m else None


def classify_cimce_dataframe(raw_df: pd.DataFrame, ont: dict) -> tuple[list[tuple[int, str, str, int, bool]], pd.DataFrame]:
    """Aplica higiene ontologica CIMCE 2026 (exact lookup, filtro USO Silicio e exclusoes)."""
    subs_s = raw_df["SUBS"].map(normalize_text)
    uso_s = raw_df["USO"].map(normalize_text)

    exact_lookup: dict[str, dict] = {}
    uso_lookup: dict[str, dict] = {}
    for m in ont["minerals"]:
        for s in m["anm_subs_exact"]:
            exact_lookup[normalize_text(s)] = m
        for s in m.get("anm_subs_with_uso_filter", []):
            uso_lookup[normalize_text(s)] = m
    silicon_uso_ok = {normalize_text(u) for u in ont["silicon_industrial_uso_allowed"]}

    sens_rows = []
    for bucket_name, spec in ont["sensitivity_exclusions"].items():
        subs_set = {normalize_text(x) for x in spec["subs"]}
        mask_b = subs_s.isin(subs_set)
        if "Silício" in bucket_name:
            mask_b = mask_b & ~uso_s.isin(silicon_uso_ok)
        sub_b = raw_df[mask_b]
        sens_rows.append({
            "sensitivity_bucket": bucket_name,
            "excluded_count": int(len(sub_b)),
            "excluded_area_ha": round(float(pd.to_numeric(sub_b["AREA_HA"], errors="coerce").fillna(0.0).sum()), 1),
            "rule_reason": spec.get("reason", ""),
        })
    df_sens = pd.DataFrame(sens_rows)

    records_meta: list[tuple[int, str, str, int, bool]] = []
    for idx, subs_n, uso_n in zip(raw_df.index, subs_s, uso_s):
        meta = exact_lookup.get(subs_n)
        if meta is None and subs_n in uso_lookup and uso_n in silicon_uso_ok:
            meta = uso_lookup[subs_n]
        if meta is not None:
            records_meta.append((
                idx,
                meta["name"],
                meta["cimce_class"],
                int(meta["order"]),
                bool(meta["in_mcti_recursos_14"]),
            ))
    return records_meta, df_sens


def run_etl_anm(skip_socioenv: bool = False) -> None:
    ont = load_ontology()
    anm_shp = resolve_anm_shp()
    anm_meta_path = resolve_anm_meta()

    snap_meta: dict = {}
    if anm_meta_path is not None and anm_meta_path.exists():
        snap_meta = json.loads(anm_meta_path.read_text(encoding="utf-8"))

    logging.info("Lendo shapefile ANM oficial (%s)...", anm_shp)
    raw = gpd.read_file(anm_shp)
    logging.info("Registros brutos ANM: %d", len(raw))

    raw["subs_norm"] = raw["SUBS"].map(normalize_text)
    raw["uso_norm"] = raw["USO"].map(normalize_text)
    raw["fase_norm"] = raw["FASE"].map(normalize_text)
    raw["uf_norm"] = raw["UF"].map(normalize_text)

    records_meta, df_sens = classify_cimce_dataframe(raw, ont)
    df_sens.to_parquet(DATA_PROCESSED / "anm_cimce_sensitivity_excluded.parquet", index=False)
    logging.info("Auditoria de exclusoes ontologicas salva:\n%s", df_sens.to_string(index=False))

    matched_idx = [r[0] for r in records_meta]
    cimce = raw.loc[matched_idx].copy()
    cimce["cimce_mineral"] = [r[1] for r in records_meta]
    cimce["cimce_class"] = [r[2] for r in records_meta]
    cimce["cimce_order"] = [r[3] for r in records_meta]
    cimce["in_mcti_recursos_14"] = [r[4] for r in records_meta]
    cimce["stage_tier"] = cimce["FASE"].map(classify_anm_stage)

    raw_id = cimce["ID"].astype(str).str.strip().str.strip("{}")
    dsp = cimce["DSProcesso"].astype(str).str.strip()
    proc = cimce["PROCESSO"].astype(str).str.strip()
    cimce["anm_id"] = raw_id.where(raw_id.ne("") & raw_id.ne("nan"), dsp.where(dsp.ne(""), proc))
    cimce["processo"] = dsp.where(dsp.ne("") & dsp.ne("None"), proc)
    cimce["area_ha"] = pd.to_numeric(cimce["AREA_HA"], errors="coerce").fillna(0.0)
    cimce["first_year"] = pd.to_numeric(cimce["ANO"], errors="coerce")
    cimce.loc[(cimce["first_year"] < 1900) | (cimce["first_year"] > 2026), "first_year"] = pd.NA
    cimce["ult_evento_year"] = cimce["ULT_EVENTO"].map(_extract_event_year)
    cimce["uf"] = cimce["uf_norm"].where(cimce["uf_norm"].isin(UF_TO_REGION.keys()), "ND")
    cimce["region"] = cimce["uf"].map(UF_TO_REGION).fillna("Não Definida")

    # Filtrar dominio geografico Brasil valido e projetar em Albers para metricas
    logging.info("Saneando geometrias em IBGE Albers (%d processos CIMCE casados)...", len(cimce))
    cimce_alb = _clean_albers(cimce)
    cent_wgs = gpd.GeoSeries(cimce_alb.geometry.centroid, crs=IBGE_ALBERS_CRS).to_crs("EPSG:4326")
    cimce_alb["longitude"] = cent_wgs.x.round(6)
    cimce_alb["latitude"] = cent_wgs.y.round(6)
    valid_geo = cimce_alb["longitude"].between(-75.0, -28.0) & cimce_alb["latitude"].between(-35.0, 6.5)
    cimce_alb = cimce_alb[valid_geo].copy()
    cimce_alb["area_albers_ha"] = (cimce_alb.geometry.area / 10_000.0).round(2)

    # Segregacao estrita de regime: Industrial vs. Garimpo / Licenciamento
    gar_pat = "|".join(ont["industrial_regime_exclude_patterns"])
    is_gar = cimce_alb["fase_norm"].str.contains(gar_pat, regex=True, na=False)
    gar_alb = cimce_alb[is_gar].copy()
    ind_alb = cimce_alb[~is_gar].copy()
    ind_alb["regime_group"] = "Regime Industrial / Pesquisa"
    gar_alb["regime_group"] = "Garimpo / Licenciamento / Extração"

    logging.info("Segregacao de regime: Industrial=%d | Garimpo/Licenciamento=%d", len(ind_alb), len(gar_alb))

    keep_cols = [
        "anm_id", "processo", "uf", "region", "fase", "stage_tier", "regime_group",
        "cimce_mineral", "cimce_class", "cimce_order", "in_mcti_recursos_14",
        "subs_norm", "uso_norm", "NOME", "area_ha", "area_albers_ha",
        "first_year", "ult_evento_year", "latitude", "longitude", "geometry",
    ]
    # Padronizar nomes de colunas
    rename_map = {"FASE": "fase", "NOME": "operator_raw"}
    ind_alb = ind_alb.rename(columns=rename_map)
    gar_alb = gar_alb.rename(columns=rename_map)
    out_cols = [c if c != "NOME" else "operator_raw" for c in keep_cols]

    ind_alb[out_cols].to_crs("EPSG:4674").to_parquet(DATA_PROCESSED / "anm_cimce_industrial.parquet")
    gar_alb[out_cols].to_crs("EPSG:4674").to_parquet(DATA_PROCESSED / "anm_cimce_garimpo_licenciamento.parquet")

    snap_meta.update({
        "total_anm_rows": int(len(raw)),
        "industrial_count": int(len(ind_alb)),
        "garimpo_lic_count": int(len(gar_alb)),
        "snapshot_label": snap_meta.get("snapshot_label", "nov/2026"),
        "snapshot_abnt": snap_meta.get("snapshot_abnt", "nov. 2026"),
    })
    (DATA_PROCESSED / "anm_snapshot_meta.json").write_text(
        json.dumps(snap_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Resumo por Mineral x Estagio
    m_rows = []
    for m_meta in ont["minerals"]:
        m_name = m_meta["name"]
        sub_i = ind_alb[ind_alb["cimce_mineral"] == m_name]
        sub_g = gar_alb[gar_alb["cimce_mineral"] == m_name]
        conc_n = int((sub_i["stage_tier"] == "Concessão de Lavra").sum())
        req_l_n = int((sub_i["stage_tier"] == "Requerimento de Lavra").sum())
        dir_r_n = int((sub_i["stage_tier"] == "Direito de Requerer a Lavra").sum())
        aut_p_n = int((sub_i["stage_tier"] == "Autorização de Pesquisa").sum())
        out_p_n = int((sub_i["stage_tier"] == "Requerimento de Pesquisa / Disponibilidade / Outros").sum())
        m_rows.append({
            "order": int(m_meta["order"]),
            "cimce_mineral": m_name,
            "cimce_class": m_meta["cimce_class"],
            "in_mcti_recursos_14": bool(m_meta["in_mcti_recursos_14"]),
            "industrial_total": int(len(sub_i)),
            "garimpo_lic_total": int(len(sub_g)),
            "concessao_lavra": conc_n,
            "requerimento_lavra": req_l_n,
            "direito_requerer_lavra": dir_r_n,
            "autorizacao_pesquisa": aut_p_n,
            "req_pesquisa_disp_outros": out_p_n,
            "industrial_area_ha": round(float(sub_i["area_ha"].sum()), 1),
            "garimpo_area_ha": round(float(sub_g["area_ha"].sum()), 1),
            "special_regime": m_meta.get("special_regime") or "",
        })
    pd.DataFrame(m_rows).to_parquet(DATA_PROCESSED / "cimce_mineral_summary.parquet", index=False)

    def _fast_union(geoms, batch_size: int = 250):
        arr = [g for g in geoms if g is not None and not g.is_empty]
        if not arr:
            return shapely.Polygon()
        while len(arr) > batch_size:
            nxt = []
            for i in range(0, len(arr), batch_size):
                chunk = arr[i:i + batch_size]
                try:
                    nxt.append(shapely.unary_union(chunk))
                except shapely.errors.GEOSException:
                    nxt.append(shapely.unary_union(chunk, grid_size=2.0))
            arr = nxt
        try:
            return shapely.unary_union(arr)
        except shapely.errors.GEOSException:
            return shapely.unary_union(arr, grid_size=2.0)

    # Dissolucao unica por (UF, Classe) -> reutilizada para UFs, Classes e Total Brasil
    logging.info("Dissolvendo pegadas liquidas por UF e Classe CIMCE...")
    uf_class_geoms: dict[tuple[str, str], shapely.Geometry] = {}
    uf_rows = []
    for uf, g in cimce_alb.groupby("uf"):
        gi = g[~g.index.isin(gar_alb.index)].sort_values(["longitude", "latitude"])
        gg = g[g.index.isin(gar_alb.index)]
        cls_u_list = []
        cls_cnts = {}
        for cl in CLASS_ORDER:
            g_cl = gi[gi["cimce_class"] == cl]
            cls_cnts[cl] = int(len(g_cl))
            u_cl = _fast_union(g_cl.geometry.values) if not g_cl.empty else shapely.Polygon()
            uf_class_geoms[(uf, cl)] = u_cl
            if not u_cl.is_empty:
                cls_u_list.append(u_cl)
        conc_n = int((gi["stage_tier"] == "Concessão de Lavra").sum())
        net_ha = float(_fast_union(cls_u_list).area / 10_000.0) if cls_u_list else 0.0
        uf_rows.append({
            "uf": uf,
            "region": UF_TO_REGION.get(uf, "Não Definida"),
            "industrial_total": int(len(gi)),
            "garimpo_lic_total": int(len(gg)),
            "classe_1": cls_cnts[CLASS_ORDER[0]],
            "classe_2": cls_cnts[CLASS_ORDER[1]],
            "classe_3": cls_cnts[CLASS_ORDER[2]],
            "concessao_lavra": conc_n,
            "industrial_gross_ha": round(float(gi["area_ha"].sum()), 1),
            "industrial_net_ha": round(net_ha, 1),
        })
    df_uf = pd.DataFrame(uf_rows).sort_values("industrial_total", ascending=False)
    df_uf.to_parquet(DATA_PROCESSED / "cimce_uf_summary.parquet", index=False)

    socioenv_dir = None if skip_socioenv else resolve_socioenv_dir()
    if socioenv_dir is None:
        logging.info(
            "Camadas socioambientais (Biomas/UCs/TIs) ausentes ou ignoradas — pulando overlay socioambiental (modo publicado Figuras 1–5)."
        )
        return

    # Sobreposicao socioambiental exata (Bruta vs. Liquida Dissolvida em IBGE Albers) por Classe CIMCE
    logging.info("Calculando sobreposicoes socioambientais (Biomas, UCs, TIs) com indices STRtree/prepare...")
    biomes_alb = _clean_albers(gpd.read_parquet(socioenv_dir / "socioenv_biomes.parquet"))
    ucs_alb = _clean_albers(gpd.read_parquet(socioenv_dir / "socioenv_ucs.parquet"))
    tis_alb = _clean_albers(gpd.read_parquet(socioenv_dir / "socioenv_tis.parquet"))
    ucs_pi_gdf = ucs_alb[ucs_alb["group"] == "PI"].copy()
    ucs_us_gdf = ucs_alb[ucs_alb["group"] == "US"].copy()

    class_unions: dict[str, shapely.Geometry] = {
        cl: _fast_union([geom for (u, c), geom in uf_class_geoms.items() if c == cl and not geom.is_empty])
        for cl in CLASS_ORDER
    }
    total_union = _fast_union(list(class_unions.values()))

    def _atomic_polygons(geom) -> np.ndarray:
        if geom is None or geom.is_empty:
            return np.empty(0, dtype=object)
        parts = []
        stack = [geom]
        while stack:
            g = stack.pop()
            if g is None or g.is_empty:
                continue
            if g.geom_type == "Polygon":
                parts.append(g)
            elif hasattr(g, "geoms"):
                stack.extend(g.geoms)
        return np.asarray(parts, dtype=object)

    def _safe_inter_area_ha(g1, g2) -> float:
        try:
            return float(shapely.intersection(g1, g2).area / 10_000.0)
        except shapely.errors.GEOSException:
            return float(shapely.intersection(shapely.set_precision(g1, 2.0), shapely.set_precision(g2, 2.0)).area / 10_000.0)

    def _safe_vec_inter_areas_ha(geoms_arr: np.ndarray, mask_geom) -> np.ndarray:
        if len(geoms_arr) == 0:
            return np.empty(0, dtype=float)
        try:
            return shapely.area(shapely.intersection(geoms_arr, mask_geom)) / 10_000.0
        except shapely.errors.GEOSException:
            m_prec = shapely.set_precision(mask_geom, 2.0)
            return np.asarray([_safe_inter_area_ha(g, m_prec) for g in geoms_arr], dtype=float)

    def _safe_local_union(geom_arr: np.ndarray):
        try:
            return shapely.unary_union(geom_arr)
        except shapely.errors.GEOSException:
            return shapely.unary_union(geom_arr, grid_size=2.0)

    def _overlay_single_polygon(proc_gdf: gpd.GeoDataFrame, scope_parts: np.ndarray, scope_tree: STRtree | None, poly_geom) -> tuple[int, float, float]:
        if proc_gdf.empty or poly_geom is None or poly_geom.is_empty:
            return 0, 0.0, 0.0
        shapely.prepare(poly_geom)

        # Bruta (cnt, g_ha)
        cand_idx = list(proc_gdf.sindex.query(poly_geom, predicate="intersects"))
        cnt = 0
        g_ha = 0.0
        if cand_idx:
            sub = proc_gdf.iloc[cand_idx]
            sub_geoms = np.asarray(sub.geometry.values, dtype=object)
            sub_areas = sub["area_albers_ha"].to_numpy(dtype=float)
            inside = shapely.contains_properly(poly_geom, sub_geoms)
            in_areas = sub_areas[inside]
            pos_in = in_areas > 0.01
            cnt += int(pos_in.sum())
            g_ha += float(in_areas[pos_in].sum())

            border_geoms = sub_geoms[~inside]
            if len(border_geoms) > 0:
                b_areas = _safe_vec_inter_areas_ha(border_geoms, poly_geom)
                pos_b = b_areas > 0.01
                cnt += int(pos_b.sum())
                g_ha += float(b_areas[pos_b].sum())

        # Liquida dissolvida (n_ha)
        n_ha = 0.0
        if scope_tree is not None and len(scope_parts) > 0:
            p_idx = np.asarray(scope_tree.query(poly_geom, predicate="intersects"), dtype=int)
            if len(p_idx) > 0:
                cand_parts = scope_parts[p_idx]
                p_in = shapely.contains_properly(poly_geom, cand_parts)
                if p_in.any():
                    n_ha += float(shapely.area(cand_parts[p_in]).sum() / 10_000.0)
                p_b = cand_parts[~p_in]
                if len(p_b) > 0:
                    n_ha += float(_safe_vec_inter_areas_ha(p_b, poly_geom).sum())
        return cnt, g_ha, n_ha

    def _prepare_layer_index(layer_gdf: gpd.GeoDataFrame):
        geoms = np.asarray(layer_gdf.geometry.values, dtype=object)
        shapely.prepare(geoms)
        return geoms, STRtree(geoms)

    ucs_pi_idx = _prepare_layer_index(ucs_pi_gdf)
    ucs_us_idx = _prepare_layer_index(ucs_us_gdf)
    ucs_all_idx = _prepare_layer_index(ucs_alb)
    tis_all_idx = _prepare_layer_index(tis_alb)

    def _overlay_polygon_layer(proc_gdf: gpd.GeoDataFrame, scope_parts: np.ndarray, l_geoms: np.ndarray, l_tree: STRtree) -> tuple[int, float, float]:
        if proc_gdf.empty or len(l_geoms) == 0:
            return 0, 0.0, 0.0

        # Bruta (cnt, g_ha)
        p_geoms = np.asarray(proc_gdf.geometry.values, dtype=object)
        p_areas = proc_gdf["area_albers_ha"].to_numpy(dtype=float)
        in_idx, tr_idx = l_tree.query(p_geoms, predicate="intersects")
        groups: dict[int, list[int]] = defaultdict(list)
        for pi, lj in zip(in_idx, tr_idx):
            groups[int(pi)].append(int(lj))

        cnt = 0
        g_ha = 0.0
        for pi, ljs in groups.items():
            pg = p_geoms[pi]
            if len(ljs) == 1:
                lg = l_geoms[ljs[0]]
                if shapely.contains_properly(lg, pg):
                    a_ha = float(p_areas[pi])
                else:
                    a_ha = _safe_inter_area_ha(pg, lg)
            else:
                local_u = _safe_local_union(l_geoms[ljs])
                a_ha = _safe_inter_area_ha(pg, local_u)
            if a_ha > 0.01:
                cnt += 1
                g_ha += a_ha

        # Liquida dissolvida (n_ha)
        n_ha = 0.0
        if len(scope_parts) > 0:
            s_in, s_tr = l_tree.query(scope_parts, predicate="intersects")
            s_groups: dict[int, list[int]] = defaultdict(list)
            for si, lj in zip(s_in, s_tr):
                s_groups[int(si)].append(int(lj))
            for si, ljs in s_groups.items():
                sp = scope_parts[si]
                if len(ljs) == 1:
                    lg = l_geoms[ljs[0]]
                    if shapely.contains_properly(lg, sp):
                        n_ha += float(sp.area / 10_000.0)
                    else:
                        n_ha += _safe_inter_area_ha(sp, lg)
                else:
                    local_u = _safe_local_union(l_geoms[ljs])
                    n_ha += _safe_inter_area_ha(sp, local_u)

        return cnt, g_ha, n_ha

    scopes = [("Total 27 Minerais (Regime Industrial)", ind_alb, total_union)]
    for cl in CLASS_ORDER:
        scopes.append((cl, ind_alb[ind_alb["cimce_class"] == cl], class_unions[cl]))

    socio_rows = []
    for scope_lbl, s_gdf, scope_u in scopes:
        logging.info("  Overlay socioambiental: %s (%d poligonos)...", scope_lbl, len(s_gdf))
        scope_parts = _atomic_polygons(scope_u)
        scope_tree = STRtree(scope_parts) if len(scope_parts) > 0 else None
        gross_tot = float(s_gdf["area_albers_ha"].sum())
        net_tot = float(scope_u.area / 10_000.0)
        socio_rows.append({
            "scope": scope_lbl, "dimension": "Total Geral", "category": "Brasil (Todos Processos)",
            "process_count": int(len(s_gdf)),
            "gross_overlap_ha": round(gross_tot, 1),
            "net_overlap_ha": round(net_tot, 1),
            "pct_net_footprint": 100.0,
        })

        for _, br in biomes_alb.iterrows():
            b_geom = br.geometry
            cnt, g_ha, n_ha = _overlay_single_polygon(s_gdf, scope_parts, scope_tree, b_geom)
            socio_rows.append({
                "scope": scope_lbl, "dimension": "Bioma IBGE", "category": str(br["Bioma"]),
                "process_count": cnt, "gross_overlap_ha": round(g_ha, 1),
                "net_overlap_ha": round(n_ha, 1),
                "pct_net_footprint": round(n_ha / max(net_tot, 1e-6) * 100.0, 2),
            })

        for cat_lbl, (l_geoms, l_tree) in (
            ("UCs Proteção Integral (PI)", ucs_pi_idx),
            ("UCs Uso Sustentável (US)", ucs_us_idx),
            ("Total UCs (União PI + US)", ucs_all_idx),
            ("Total Terras Indígenas (FUNAI)", tis_all_idx),
        ):
            cnt, g_ha, n_ha = _overlay_polygon_layer(s_gdf, scope_parts, l_geoms, l_tree)
            dim_lbl = "Terras Indígenas (FUNAI)" if "Terras Indígenas" in cat_lbl else "Unidades de Conservação (CNUC)"
            socio_rows.append({
                "scope": scope_lbl, "dimension": dim_lbl, "category": cat_lbl,
                "process_count": cnt, "gross_overlap_ha": round(g_ha, 1),
                "net_overlap_ha": round(n_ha, 1),
                "pct_net_footprint": round(n_ha / max(net_tot, 1e-6) * 100.0, 2),
            })

    pd.DataFrame(socio_rows).to_parquet(DATA_PROCESSED / "cimce_socioenv_summary.parquet", index=False)
    logging.info("ETL ANM CIMCE 2026 concluido com sucesso!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETL ANM/SIGMINE para os 27 minerais do CIMCE 2026.")
    parser.add_argument("--skip-socioenv", action="store_true", help="Nao executa o cruzamento com Biomas/UCs/TIs.")
    args = parser.parse_args()
    run_etl_anm(skip_socioenv=args.skip_socioenv)
