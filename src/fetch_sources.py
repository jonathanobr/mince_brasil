# src/fetch_sources.py
"""
Etapa 0 (autonoma): Download oficial das fontes publicas necessarias para gerar
os mapas do projeto `mince_brasil` (`minerais_criticos_brasil`) sem depender de
outros repositorios locais:
  1. ANM / SIGMINE (BRASIL.zip -> data/raw/anm/BRASIL.shp + anm_metadata.json)
  2. IBGE Malha Estadual 2025 (BR_UF_2025.zip -> data/processed/basemaps/brazil_states_ibge2025.parquet)
  3. Marine Regions / VLIZ (EEZ Brasil / Amazonia Azul -> data/processed/basemaps/brazil_eez.parquet)
  4. Natural Earth 110m (limites sul-americanos -> data/processed/basemaps/south_america_countries.parquet)

Registra URL, SHA-256 e timestamp UTC em `data/raw/download_manifest.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geopandas as gpd
import pyogrio
import requests
import urllib3
from shapely.geometry import box

from src.config import (
    ANM_RAW_DIR,
    ANM_RAW_METADATA_PATH,
    BASEMAPS_PROCESSED,
    DATA_RAW,
    SIRGAS_2000_GEO,
    WGS84_GEO,
)

logging.basicConfig(level=logging.INFO, format="[FETCH CIMCE] %(levelname)s: %(message)s")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MANIFEST_PATH = DATA_RAW / "download_manifest.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MinceBrasil/1.0",
    "Accept": "application/json, application/octet-stream, */*",
}
_PT_MONTHS_SHORT = {
    1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
    7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez",
}

ANM_URLS = [
    "https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/BRASIL.zip",
    "https://app.anm.gov.br/dadosabertos/SIGMINE/PROCESSOS_MINERARIOS/BRASIL.zip",
]
IBGE_UF_URL = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
    "malhas_municipais/municipio_2025/Brasil/BR_UF_2025.zip"
)
VLIZ_EEZ_URL = (
    "https://geo.vliz.be/geoserver/MarineRegions/ows"
    "?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=MarineRegions:eez&cql_filter=iso_sov1=%27BRA%27&outputFormat=application/json"
)
NE_COUNTRIES_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_110m_admin_0_countries.geojson"
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_stream(url: str, dest: Path, timeout: int = 240) -> tuple[bool, str | None, str]:
    verify_ssl = True
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
        r.raise_for_status()
    except requests.exceptions.SSLError:
        if ".gov.br" in url:
            verify_ssl = False
            logging.warning("SSLError em %s; tentando verify=False para host .gov.br.", url)
            r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True, verify=False)
            r.raise_for_status()
        else:
            raise

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 512):
            if chunk:
                f.write(chunk)
    return verify_ssl, r.headers.get("Last-Modified"), str(r.url)


def _format_pt_month_labels(dt: datetime) -> tuple[str, str]:
    mon = _PT_MONTHS_SHORT[dt.month]
    abnt = "maio" if mon == "mai" else f"{mon}."
    return f"{mon}/{dt.year}", f"{abnt} {dt.year}"


def fetch_anm_shapefile() -> dict[str, Any]:
    """Baixa e extrai BRASIL.zip oficial da ANM/SIGMINE para data/raw/anm/."""
    ANM_RAW_DIR.mkdir(parents=True, exist_ok=True)
    zip_dest = ANM_RAW_DIR / "BRASIL.zip"
    last_err: Exception | None = None

    for url in ANM_URLS:
        try:
            logging.info("[ANM SIGMINE] Baixando %s...", url)
            verify_ssl, http_last_mod, final_url = _download_stream(url, zip_dest, timeout=300)
            zip_size = zip_dest.stat().st_size
            if zip_size < 50_000_000:
                raise RuntimeError(f"BRASIL.zip suspeito/incompleto ({zip_size} bytes)")
            with open(zip_dest, "rb") as f:
                if f.read(4) != b"PK\x03\x04":
                    raise RuntimeError("Cabecalho ZIP invalido em BRASIL.zip")

            zip_sha256 = _sha256_file(zip_dest)
            shp_zip_dt: datetime | None = None
            extracted: list[str] = []
            with zipfile.ZipFile(zip_dest, "r") as zf:
                members = [m for m in zf.infolist() if not m.is_dir() and Path(m.filename).name.upper().startswith("BRASIL.")]
                if not members:
                    raise RuntimeError("BRASIL.shp nao encontrado em BRASIL.zip")
                for m in members:
                    base_name = Path(m.filename).name
                    out_p = ANM_RAW_DIR / base_name
                    with zf.open(m, "r") as src_f, open(out_p, "wb") as dst_f:
                        while True:
                            buf = src_f.read(1024 * 1024)
                            if not buf:
                                break
                            dst_f.write(buf)
                    extracted.append(base_name)
                    if base_name.upper() == "BRASIL.SHP":
                        shp_zip_dt = datetime(*m.date_time, tzinfo=timezone.utc)

            shp_path = ANM_RAW_DIR / "BRASIL.shp"
            info_shp = pyogrio.read_info(shp_path)
            total_rows = int(info_shp.get("features", 0))
            if total_rows < 200_000:
                raise RuntimeError(f"Contagem inesperadamente baixa no shapefile ANM: {total_rows}")

            http_dt: datetime | None = None
            if http_last_mod:
                try:
                    http_dt = parsedate_to_datetime(http_last_mod)
                    if http_dt.tzinfo is None:
                        http_dt = http_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    http_dt = None

            dl_dt = datetime.now(timezone.utc)
            ref_dt = http_dt or shp_zip_dt or dl_dt
            snapshot_label, snapshot_abnt = _format_pt_month_labels(ref_dt)
            meta = {
                "source": "ANM / SIGMINE (BRASIL.zip)",
                "url": url,
                "final_url": final_url,
                "ssl_verified": verify_ssl,
                "file_zip": "data/raw/anm/BRASIL.zip",
                "file_shp": "data/raw/anm/BRASIL.shp",
                "extracted_files": sorted(extracted),
                "zip_size_bytes": int(zip_size),
                "shp_size_bytes": int(shp_path.stat().st_size),
                "sha256": zip_sha256,
                "last_modified_http": http_last_mod,
                "downloaded_at_utc": dl_dt.isoformat(),
                "total_anm_rows": total_rows,
                "snapshot_date_iso": ref_dt.date().isoformat(),
                "snapshot_label": snapshot_label,
                "snapshot_abnt": snapshot_abnt,
            }
            ANM_RAW_METADATA_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info("[ANM SIGMINE] OK: %s registros (%s)", f"{total_rows:,}", snapshot_label)
            return meta
        except Exception as exc:
            last_err = exc
            logging.warning("[ANM SIGMINE] Falha em %s: %s", url, exc)

    raise RuntimeError(f"[ANM SIGMINE] Todas as URLs falharam: {last_err}")


def fetch_basemaps() -> dict[str, Any]:
    """Baixa e converte basemaps (IBGE UF 2025, Marine Regions EEZ e Natural Earth) para GeoParquet."""
    raw_bm = DATA_RAW / "basemaps"
    raw_bm.mkdir(parents=True, exist_ok=True)
    BASEMAPS_PROCESSED.mkdir(parents=True, exist_ok=True)
    out_meta: dict[str, Any] = {}

    # 1. IBGE UF 2025
    uf_zip = raw_bm / "BR_UF_2025.zip"
    logging.info("[IBGE UF 2025] Baixando %s...", IBGE_UF_URL)
    _download_stream(IBGE_UF_URL, uf_zip, timeout=180)
    with zipfile.ZipFile(uf_zip, "r") as zf:
        shps = [n for n in zf.namelist() if n.lower().endswith(".shp")]
        if not shps:
            raise RuntimeError("Nenhum .shp encontrado em BR_UF_2025.zip")
        gdf_uf = gpd.read_file(f"zip://{uf_zip}!{shps[0]}")
    gdf_uf = gdf_uf.set_crs(SIRGAS_2000_GEO) if gdf_uf.crs is None else gdf_uf.to_crs(SIRGAS_2000_GEO)
    gdf_uf["geometry"] = gdf_uf.geometry.make_valid()
    gdf_uf = gdf_uf.rename(columns={"SIGLA_UF": "uf", "NM_UF": "uf_name", "NM_REGIAO": "region", "AREA_KM2": "area_ibge_km2"})
    gdf_uf["uf"] = gdf_uf["uf"].astype(str).str.strip().str.upper()
    cols = [c for c in ["CD_UF", "uf", "uf_name", "region", "area_ibge_km2", "geometry"] if c in gdf_uf.columns]
    uf_pq = BASEMAPS_PROCESSED / "brazil_states_ibge2025.parquet"
    gdf_uf[cols].to_parquet(uf_pq)
    out_meta["ibge_uf_2025"] = {"url": IBGE_UF_URL, "sha256": _sha256_file(uf_zip), "rows": int(len(gdf_uf))}

    # 2. Amazonia Azul (VLIZ Marine Regions)
    eez_json = raw_bm / "brazil_eez.geojson"
    logging.info("[VLIZ EEZ] Baixando %s...", VLIZ_EEZ_URL)
    _download_stream(VLIZ_EEZ_URL, eez_json, timeout=120)
    gdf_eez = gpd.read_file(eez_json)
    gdf_eez = (gdf_eez.set_crs(WGS84_GEO) if gdf_eez.crs is None else gdf_eez).to_crs(SIRGAS_2000_GEO)
    gdf_eez["geometry"] = gdf_eez.geometry.make_valid()
    eez_pq = BASEMAPS_PROCESSED / "brazil_eez.parquet"
    gdf_eez.to_parquet(eez_pq)
    out_meta["brazil_eez"] = {"url": VLIZ_EEZ_URL, "sha256": _sha256_file(eez_json), "rows": int(len(gdf_eez))}

    # 3. Paises Sul-Americanos (Natural Earth 110m com pre-recorte WGS84 anti-wrapping)
    ne_json = raw_bm / "ne_110m_countries.geojson"
    logging.info("[Natural Earth] Baixando %s...", NE_COUNTRIES_URL)
    _download_stream(NE_COUNTRIES_URL, ne_json, timeout=90)
    gdf_ne = gpd.read_file(ne_json)
    gdf_ne = gdf_ne.set_crs(WGS84_GEO) if gdf_ne.crs is None else gdf_ne.to_crs(WGS84_GEO)
    clip_poly = box(-115.0, -60.0, -15.0, 28.0)
    gdf_ne["geometry"] = gdf_ne.geometry.make_valid().intersection(clip_poly)
    gdf_ne = gdf_ne[~gdf_ne.geometry.is_empty & gdf_ne.geometry.notna()].to_crs(SIRGAS_2000_GEO)
    sa_pq = BASEMAPS_PROCESSED / "south_america_countries.parquet"
    gdf_ne.to_parquet(sa_pq)
    out_meta["south_america"] = {"url": NE_COUNTRIES_URL, "sha256": _sha256_file(ne_json), "rows": int(len(gdf_ne))}

    return out_meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Download oficial das bases necessarias para mince_brasil (Figuras 1–5).")
    parser.add_argument("--only-anm", action="store_true", help="Baixa apenas o shapefile da ANM/SIGMINE.")
    parser.add_argument("--only-basemaps", action="store_true", help="Baixa apenas os basemaps IBGE/VLIZ/Natural Earth.")
    args = parser.parse_args()

    sources: dict[str, Any] = {}
    if not args.only_basemaps:
        sources["anm_sigmine"] = fetch_anm_shapefile()
    if not args.only_anm:
        sources.update(fetch_basemaps())

    manifest = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "sources": sources}
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Manifesto salvo em %s", MANIFEST_PATH)


if __name__ == "__main__":
    main()
