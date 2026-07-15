import kagglehub
from kagglehub import KaggleDatasetAdapter
from sqlalchemy import inspect
from sqlalchemy.engine import Engine


def download_datasets(engine: Engine) -> None:
    dataset_names: list[str] = [
        "chip_companies_financials",
        "fab_capacity",
        "export_controls",
        "chip_prices",
        "ai_chip_market",
    ]

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    for dataset_name in dataset_names:
        if dataset_name in existing:
            continue
        dataframe = kagglehub.dataset_load(
            KaggleDatasetAdapter.PANDAS,
            "sergionefedov/global-semiconductor-industry-2010-2026",
            f"{dataset_name}.csv",
        )
        dataframe.to_sql(dataset_name, engine, index=False, if_exists="replace")
