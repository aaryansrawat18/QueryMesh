import os
import shutil
import uuid

import pandas as pd
import requests

from utils.guard import assert_url_allowed, jail_path
from utils.sandbox import run_isolated
from utils.trace import span

_FORMATS = {"csv": ".csv", "json": ".json", "parquet": ".parquet"}


def _require_worker() -> None:
    if os.environ.get("QUERYMESH_WORKER") != "1":
        raise RuntimeError("ETL tools run only in the worker process")


class ETLTools:

    def extract_load(self, url: str, output_folder: str, format: str):
        """Fetch an allowlisted URL and write it under data/extract or data/transform."""
        _require_worker()
        try:
            with span("tool.extract"):
                return self._extract(url, output_folder, format)
        except (ValueError, requests.exceptions.RequestException, KeyError, OSError) as exc:
            return f"Failed to extract data: {exc}"

    def _extract(self, url: str, output_folder: str, format: str):
        assert_url_allowed(url)
        folder = jail_path(output_folder)
        if format not in _FORMATS:
            return f"Unsupported format: {format}"
        folder.mkdir(parents=True, exist_ok=True)
        response = requests.get(url, timeout=15, allow_redirects=False)
        if 300 <= response.status_code < 400:
            return "Failed to extract data: redirects are not followed"
        response.raise_for_status()
        data = response.json()
        filename = folder / f"extracted_data{_FORMATS[format]}"
        df = pd.json_normalize(data["results"])
        if format == "csv":
            df.to_csv(filename, index=False)
        elif format == "json":
            df.to_json(filename, orient="records", lines=True)
        else:
            df.to_parquet(filename, index=False)
        return f"Data successfully extracted and saved to {filename}"

    def prepare_transform(self, file_path: str, output_folder: str):
        """Copy the source file into a fresh job directory and return a preview."""
        _require_worker()
        source = jail_path(file_path)
        folder = jail_path(output_folder)
        workdir = folder / str(uuid.uuid4())
        workdir.mkdir(parents=True, exist_ok=True)
        local_name = f"input{source.suffix.lower()}"
        shutil.copyfile(source, workdir / local_name)
        return workdir, local_name, self._preview(workdir / local_name)

    def _preview(self, file_path):
        extension = file_path.suffix.lower()
        if extension == ".csv":
            frame = pd.read_csv(file_path)
        elif extension == ".json":
            frame = pd.read_json(file_path, lines=True)
        elif extension == ".parquet":
            frame = pd.read_parquet(file_path)
        else:
            raise ValueError(f"Unsupported file format: {extension}")
        return str(frame.head(3))

    def execute_code(self, code: str, workdir) -> str:
        """Run model Python in a child process, not in the API interpreter."""
        _require_worker()
        try:
            root = jail_path(_relative(workdir))
        except ValueError as exc:
            return f"Failed to execute code: {exc}"
        with span("tool.python"):
            return run_isolated(code, root)


def _relative(path) -> str:
    from pathlib import Path

    project = Path(__file__).resolve().parents[1]
    return Path(path).resolve().relative_to(project).as_posix()
