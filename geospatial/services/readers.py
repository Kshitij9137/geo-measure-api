"""Format readers.

Each reader turns an uploaded file into a normalised ``GeoDataFrame`` (one row per feature,
CRS attached). A dict registry maps ``FileType`` -> reader class, so adding a format means
adding one class and one registry entry.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import geopandas as gpd
import pandas as pd
from django.conf import settings
from pyogrio import list_layers
from pyogrio.errors import DataLayerError, DataSourceError, GeometryError

from geospatial.exceptions import EmptyDatasetError, MissingCRSError, UnreadableFileError

from .archive import extract_zip_safely, find_shapefile
from .types import FileType

logger = logging.getLogger(__name__)

# Column holding the source layer name (KML folders); stripped from properties by the processor.
LAYER_COLUMN = "__layer__"
_READ_ERRORS = (DataSourceError, DataLayerError, GeometryError, ValueError)


class GeoFileReader(ABC):
    file_type: FileType

    @abstractmethod
    def read(self, path: Path, workdir: Path) -> gpd.GeoDataFrame:
        """Read ``path`` into a GeoDataFrame. ``workdir`` is scratch space owned by the caller."""


class KMLReader(GeoFileReader):
    """KML is always WGS84 (EPSG:4326) by specification, so it carries no CRS declaration.

    GDAL exposes every ``<Folder>``/``<Document>`` as a separate layer and ``read_file`` only
    reads the first one, so we read *all* layers.
    """

    file_type = FileType.KML

    def read(self, path: Path, workdir: Path) -> gpd.GeoDataFrame:
        try:
            layers = [str(name) for name, _ in list_layers(path)]
            frames = []
            for layer in layers:
                frame = gpd.read_file(path, layer=layer, engine="pyogrio")
                if frame.empty:
                    continue
                frame[LAYER_COLUMN] = layer
                frames.append(frame)
        except _READ_ERRORS as exc:
            raise UnreadableFileError(f"The KML file could not be parsed: {exc}") from exc

        if not frames:
            raise EmptyDatasetError()

        combined = pd.concat(frames, ignore_index=True)
        gdf = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
        logger.info("Read %d KML feature(s) from %d layer(s)", len(gdf), len(frames))
        return gdf


class ShapefileReader(GeoFileReader):
    file_type = FileType.SHAPEFILE

    def read(self, path: Path, workdir: Path) -> gpd.GeoDataFrame:
        files = extract_zip_safely(
            path,
            workdir / "extracted",
            max_files=settings.ZIP_MAX_FILES,
            max_total_bytes=settings.ZIP_MAX_UNCOMPRESSED_BYTES,
        )
        shp = find_shapefile(files)
        try:
            gdf = gpd.read_file(shp, engine="pyogrio")
        except _READ_ERRORS as exc:
            raise UnreadableFileError(f"The Shapefile could not be read: {exc}") from exc

        if gdf.crs is None:
            raise MissingCRSError(
                "The Shapefile has no CRS definition (missing or empty .prj file); "
                "include the .prj so measurements can be calculated reliably."
            )
        logger.info("Read %d Shapefile feature(s) from %s", len(gdf), shp.name)
        return gdf


READERS: dict[FileType, type[GeoFileReader]] = {
    FileType.KML: KMLReader,
    FileType.SHAPEFILE: ShapefileReader,
}


def get_reader(file_type: FileType) -> GeoFileReader:
    return READERS[file_type]()
