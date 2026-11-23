# tests/test_cimce_integrity.py
"""
Suite automatizada de integridade ontologica, funcional e cartografica
do repositorio publicado `mince_brasil` (Minerais Criticos e Estrategicos / CIMCE 2026).
Compativel com clone limpo (GitHub Actions CI) e execucao local.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from src import build_figures, etl_cimce_anm, fetch_sources, run_all
from src.config import (
    CLASS_ORDER,
    FIGURES_PNG_DIR,
    PROJECT_ROOT,
    classify_anm_stage,
    fmt_int_ptbr,
    fmt_ptbr,
    load_ontology,
    normalize_text,
)

PUBLISHED_FIGURE_STEMS = [
    "fig_01_mapa_nacional_3_classes_cimce",
    "fig_02_mapa_concessoes_lavra_grande_porte",
    "fig_03_mapa_classe1_prioridade_alta",
    "fig_04_mapa_classe2_prioridade_media",
    "fig_05_mapa_classe3_prioridade_baixa",
]


def test_ontology_27_minerals_structure():
    ont = load_ontology()
    mins = ont["minerals"]
    assert len(mins) == 27
    assert [m["order"] for m in mins] == list(range(1, 28))
    assert sum(1 for m in mins if m["cimce_class"] == CLASS_ORDER[0]) == 9
    assert sum(1 for m in mins if m["cimce_class"] == CLASS_ORDER[1]) == 8
    assert sum(1 for m in mins if m["cimce_class"] == CLASS_ORDER[2]) == 10
    assert sum(1 for m in mins if m["in_mcti_recursos_14"]) == 14
    assert sum(1 for m in mins if not m["in_mcti_recursos_14"]) == 13
    for m in mins:
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", m["color"])


def test_ontological_hygiene_and_regime_rules():
    ont = load_ontology()
    synthetic = pd.DataFrame([
        {"SUBS": "TERRAS RARAS", "USO": "INDUSTRIAL", "AREA_HA": 100.0},
        {"SUBS": "MINÉRIO DE LÍTIO", "USO": "NÃO INFORMADO", "AREA_HA": 200.0},
        {"SUBS": "QUARTZO", "USO": "INDUSTRIAL", "AREA_HA": 50.0},
        {"SUBS": "QUARTZITO", "USO": "REVESTIMENTO", "AREA_HA": 300.0},
        {"SUBS": "DOLOMITO", "USO": "CORRETIVO DE SOLO", "AREA_HA": 150.0},
        {"SUBS": "CITRINO", "USO": "GEMA", "AREA_HA": 20.0},
        {"SUBS": "OURO PIGMENTO", "USO": "PIGMENTO", "AREA_HA": 10.0},
        {"SUBS": "TITANITA", "USO": "COLECAO", "AREA_HA": 15.0},
        {"SUBS": "NIÓBIO", "USO": "METALURGIA", "AREA_HA": 500.0},
    ])

    records, sens = etl_cimce_anm.classify_cimce_dataframe(synthetic, ont)
    matched_names = [r[1] for r in records]
    assert matched_names == ["Terras Raras", "Lítio", "Silício", "Nióbio"]
    assert len(sens) == 5

    sens_map = dict(zip(sens["sensitivity_bucket"], sens["excluded_count"]))
    assert sens_map["Silício (Quartzito/Quartzo não industrial)"] == 1
    assert sens_map["Magnésio (Dolomito/Calcário Magnesiano)"] == 1
    assert sens_map["Terras Raras (Citrino falso positivo)"] == 1
    assert sens_map["Ouro (Ouro Pigmento)"] == 1
    assert sens_map["Titânio (Titanita gema/coleção)"] == 1

    assert classify_anm_stage("CONCESSÃO DE LAVRA") == "Concessão de Lavra"
    assert classify_anm_stage("AUTORIZAÇÃO DE PESQUISA") == "Autorização de Pesquisa"
    assert normalize_text("Minério de Cério") == "MINERIO DE CERIO"
    assert fmt_ptbr(1234.56, 1) == "1.234,6"
    assert fmt_int_ptbr(270994) == "270.994"


@pytest.mark.parametrize("stem", PUBLISHED_FIGURE_STEMS)
def test_published_5_figures_600dpi(stem: str):
    png = FIGURES_PNG_DIR / f"{stem}_600dpi.png"
    assert png.exists(), f"Figura publicada ausente: {png}"
    assert png.stat().st_size > 50_000
    with Image.open(png) as im:
        w, h = im.size
        dpi = im.info.get("dpi", (0, 0))
        assert dpi[0] >= 599 and dpi[1] >= 599
        assert 0.55 <= (w / max(h, 1)) <= 2.55
        arr = np.asarray(im.convert("RGB"), dtype=np.float32)
        assert float(arr[::16, ::16].var()) > 100.0


def test_repo_metadata_licenses_and_modules():
    for rel in (
        "LICENSE",
        "output/LICENSE",
        "NOTICE.md",
        "CITATION.cff",
        "README.md",
        "pyproject.toml",
        "requirements.txt",
    ):
        p = PROJECT_ROOT / rel
        assert p.exists(), f"Arquivo institucional ausente: {p}"
        assert p.stat().st_size > 50

    assert "MIT License" in (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "CC BY 4.0" in (PROJECT_ROOT / "output" / "LICENSE").read_text(encoding="utf-8")

    readme_txt = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "C:\\Users\\jonat" not in readme_txt
    assert "OneDrive" not in readme_txt
    for stem in PUBLISHED_FIGURE_STEMS:
        assert f"{stem}_600dpi.png" in readme_txt

    assert build_figures.PUBLISHED_FIGURES == [1, 2, 3, 4, 5]
    assert len(fetch_sources.ANM_URLS) >= 1
    assert callable(run_all.main)
