from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Any

HeadingType = Literal["body_heading", "appendix_heading", "toc_item", "table_title", "figure_title", "flowchart_section", "unknown"]
RegionType = Literal["toc", "body", "appendix", "table", "figure"]
ExtractionMethod = Literal["pymupdf", "vlm", "hybrid"]
VisualProminence = Literal["high", "normal"]


@dataclass
class Heading:
    text: str
    level: int
    number: str | None = None
    type: HeadingType = "unknown"
    confidence: float = 0.0
    visual_prominence: VisualProminence = "high"


@dataclass
class PageRegion:
    type: RegionType
    bbox: tuple[float, float, float, float] | None = None
    notes: str = ""


@dataclass
class PageStructure:
    page_no: int
    is_toc_page: bool = False
    is_appendix_page: bool = False
    is_table_continuation: bool = False
    headings: list[Heading] = field(default_factory=list)
    appendix_headings: list[Heading] = field(default_factory=list)
    table_titles: list[dict[str, Any]] = field(default_factory=list)
    regions: list[PageRegion] = field(default_factory=list)
    extraction_method: ExtractionMethod = "pymupdf"
    structure_confidence: float = 0.0
    notes: str = ""


@dataclass
class DocumentContext:
    pdf_path: str
    file_title: str
    total_pages: int
    toc_pages: list[int] = field(default_factory=list)
    page_structures: dict[int, PageStructure] = field(default_factory=dict)
    current_heading_stack: list[Heading] = field(default_factory=list)
    current_appendix: Heading | None = None
    previous_page_tail: str | None = None
    process_sections: list[Heading] = field(default_factory=list)


@dataclass
class PageContext:
    file_title: str
    page_no: int
    total_pages: int
    is_toc_page: bool = False
    known_headings_on_this_page: list[Heading] = field(default_factory=list)
    current_heading_stack: list[Heading] = field(default_factory=list)
    current_appendix: Heading | None = None
    page_regions: list[PageRegion] = field(default_factory=list)
    previous_page_tail: str | None = None
    is_flowchart_page: bool = False
    require_flowchart_structure: bool = False
    require_diagram_descriptions: bool = False
    require_figure_descriptions: bool = False


