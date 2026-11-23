# src/run_all.py
"""
Orquestrador CLI do projeto `mince_brasil` (`minerais_criticos_brasil`, CIMCE 2026).
Uso:
  python src/run_all.py [--fetch] [--skip-etl] [--only-figures] [--figures 1 2 3 4 5] [--png-only]
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import build_figures, etl_cimce_anm, fetch_sources

logging.basicConfig(level=logging.INFO, format="[RUN ALL CIMCE] %(levelname)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline cartografico PNMCE / CIMCE 2026 (Figuras 1–5).")
    parser.add_argument("--fetch", action="store_true", help="Baixa ANM/SIGMINE e basemaps IBGE/VLIZ/Natural Earth antes do ETL.")
    parser.add_argument("--skip-etl", action="store_true", help="Reutiliza parquets ja processados em data/processed/.")
    parser.add_argument("--only-figures", action="store_true", help="Gera apenas as figuras 600 DPI.")
    parser.add_argument("--figures", nargs="+", type=int, default=build_figures.PUBLISHED_FIGURES, help="IDs das figuras a gerar (padrao: 1 2 3 4 5).")
    parser.add_argument("--png-only", action="store_true", help="Gera apenas PNG 600 DPI (sem SVG).")
    parser.add_argument("--skip-socioenv", action="store_true", help="Pula cruzamento socioambiental caso camadas locais existam.")
    parser.add_argument("--full", action="store_true", help="Executa pipeline local estendido (requer modulos locais opcionais).")
    args = parser.parse_args()

    if args.only_figures:
        build_figures.main(figures=args.figures, png_only=args.png_only)
        return

    if args.fetch:
        logging.info("Etapa 0: Download oficial ANM/SIGMINE e basemaps IBGE/VLIZ/Natural Earth...")
        fetch_sources.fetch_anm_shapefile()
        fetch_sources.fetch_basemaps()

    if args.full:
        try:
            etl_mcs = importlib.import_module("src.etl_mcs_global_cimce")
            build_tables = importlib.import_module("src.build_tables")
            build_dp = importlib.import_module("src.build_data_paper")
            audit_p = importlib.import_module("src.audit_pipeline")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Modo --full requer os modulos locais completos (tabelas, MCS 2026 e Data Paper) nao publicados no repositorio enxuto."
            ) from exc
        if not args.skip_etl:
            etl_mcs.run_etl_mcs()
            etl_cimce_anm.run_etl_anm(skip_socioenv=args.skip_socioenv)
        build_tables.build_all_tables()
        build_figures.main(figures=sorted(build_figures.FIGURE_REGISTRY.keys()), png_only=args.png_only)
        build_dp.main()
        audit_p.main()
        return

    if not args.skip_etl:
        logging.info("Etapa 1/2: ETL geoespacial ANM/SIGMINE (27 minerais CIMCE, segregacao industrial)...")
        etl_cimce_anm.run_etl_anm(skip_socioenv=args.skip_socioenv)
    else:
        logging.info("Etapa 1/2: Pulando ETL (--skip-etl).")

    logging.info("Etapa 2/2: Geracao dos mapas selecionados (600 DPI)...")
    build_figures.main(figures=args.figures, png_only=args.png_only)
    logging.info("Pipeline mince_brasil concluido com sucesso!")


if __name__ == "__main__":
    main()
