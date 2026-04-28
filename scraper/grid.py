from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

BRAZIL_BBOX = (-33.8, -74.0, 5.3, -34.7)
KM_PER_DEG_LAT = 111.0
MIN_CELL_KM = 0.5  # floor for recursive subdivision — can be overridden per call


@dataclass(frozen=True)
class Cell:
    cell_id: str
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    parent_id: str | None = None

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_lat + self.max_lat) / 2.0, (self.min_lon + self.max_lon) / 2.0)

    @property
    def width_km(self) -> float:
        mid_lat = (self.min_lat + self.max_lat) / 2.0
        return (self.max_lon - self.min_lon) * KM_PER_DEG_LAT * math.cos(math.radians(mid_lat))

    @property
    def height_km(self) -> float:
        return (self.max_lat - self.min_lat) * KM_PER_DEG_LAT

    @property
    def size_km(self) -> float:
        return min(self.width_km, self.height_km)

    def contains_point(self, lat: float, lon: float) -> bool:
        return (self.min_lat <= lat <= self.max_lat
                and self.min_lon <= lon <= self.max_lon)

    def zoom_for_maps(self) -> int:
        km = max(self.size_km, 0.5)
        if km >= 40:
            return 10
        if km >= 20:
            return 11
        if km >= 10:
            return 12
        if km >= 5:
            return 13
        if km >= 2:
            return 14
        return 15

    def to_geojson(self) -> dict:
        return {
            "type": "Feature",
            "properties": {"cell_id": self.cell_id, "parent_id": self.parent_id},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [self.min_lon, self.min_lat],
                    [self.max_lon, self.min_lat],
                    [self.max_lon, self.max_lat],
                    [self.min_lon, self.max_lat],
                    [self.min_lon, self.min_lat],
                ]],
            },
        }


def _make_id(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> str:
    return f"{min_lat:.5f}_{min_lon:.5f}_{max_lat:.5f}_{max_lon:.5f}"


def load_brazil_polygon(geojson_path: Path) -> BaseGeometry:
    data = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    if data.get("type") == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in data["features"]]
        if len(geoms) == 1:
            return geoms[0]
        from shapely.ops import unary_union
        return unary_union(geoms)
    if data.get("type") == "Feature":
        return shape(data["geometry"])
    return shape(data)


def generate_initial_cells(km: float = 25.0) -> list[Cell]:
    deg_lat = km / KM_PER_DEG_LAT
    min_lat, min_lon, max_lat, max_lon = BRAZIL_BBOX
    cells: list[Cell] = []
    lat = min_lat
    while lat < max_lat:
        nlat = min(lat + deg_lat, max_lat)
        mid_lat = (lat + nlat) / 2.0
        deg_lon = km / (KM_PER_DEG_LAT * max(math.cos(math.radians(mid_lat)), 0.1))
        lon = min_lon
        while lon < max_lon:
            nlon = min(lon + deg_lon, max_lon)
            cells.append(Cell(
                cell_id=_make_id(lat, lon, nlat, nlon),
                min_lat=lat, min_lon=lon, max_lat=nlat, max_lon=nlon,
            ))
            lon = nlon
        lat = nlat
    return cells


def clip_to_polygon(cells: Iterable[Cell], polygon: BaseGeometry) -> list[Cell]:
    prepared = prep(polygon)
    return [c for c in cells if prepared.intersects(box(c.min_lon, c.min_lat, c.max_lon, c.max_lat))]


def subdivide(cell: Cell, min_km: float = MIN_CELL_KM) -> list[Cell]:
    if cell.size_km <= min_km:
        return []
    mid_lat = (cell.min_lat + cell.max_lat) / 2.0
    mid_lon = (cell.min_lon + cell.max_lon) / 2.0
    quads = [
        (cell.min_lat, cell.min_lon, mid_lat, mid_lon),
        (cell.min_lat, mid_lon, mid_lat, cell.max_lon),
        (mid_lat, cell.min_lon, cell.max_lat, mid_lon),
        (mid_lat, mid_lon, cell.max_lat, cell.max_lon),
    ]
    return [
        Cell(cell_id=_make_id(a, b, c, d), min_lat=a, min_lon=b, max_lat=c, max_lon=d, parent_id=cell.cell_id)
        for (a, b, c, d) in quads
    ]
