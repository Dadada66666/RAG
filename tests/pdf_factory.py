"""Small redistributable PDFs generated in tests with pypdf only."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter
from pypdf._page import PageObject
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


def _font() -> DictionaryObject:
    return DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )


def _text_page(
    writer: PdfWriter, lines: tuple[tuple[float, float, str], ...]
) -> PageObject:
    page = writer.add_blank_page(width=612, height=792)
    font = writer._add_object(_font())
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        }
    )
    commands = ["BT", "/F1 12 Tf"]
    for x, y, text in lines:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"1 0 0 1 {x} {y} Tm ({safe}) Tj")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    return page


def _image_page(writer: PdfWriter) -> PageObject:
    page = writer.add_blank_page(width=612, height=792)
    image = DecodedStreamObject()
    image.set_data(bytes([255]) * 100)
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(10),
            NameObject("/Height"): NumberObject(10),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image_ref = writer._add_object(image)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Im0"): image_ref}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"q 500 0 0 700 50 40 cm /Im0 Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)
    return page


def _bilingual_page(writer: PdfWriter) -> PageObject:
    page = writer.add_blank_page(width=612, height=792)
    latin_font = writer._add_object(_font())
    cid_font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/CIDFontType0"),
                NameObject("/BaseFont"): NameObject("/STSong-Light"),
                NameObject("/CIDSystemInfo"): DictionaryObject(
                    {
                        NameObject("/Registry"): TextStringObject("Adobe"),
                        NameObject("/Ordering"): TextStringObject("GB1"),
                        NameObject("/Supplement"): NumberObject(4),
                    }
                ),
            }
        )
    )
    chinese_font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type0"),
                NameObject("/BaseFont"): NameObject("/STSong-Light"),
                NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
                NameObject("/DescendantFonts"): ArrayObject([cid_font]),
            }
        )
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): latin_font, NameObject("/FZ"): chinese_font}
            )
        }
    )
    chinese = "年度报告".encode("utf-16-be").hex().upper()
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 12 Tf 1 0 0 1 72 720 Tm (Annual report) Tj "
            f"/FZ 12 Tf 1 0 0 1 72 690 Tm <{chinese}> Tj ET"
        ).encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    return page


def write_tiny_pdf(
    path: Path,
    *,
    layout: str = "single",
) -> Path:
    writer = PdfWriter()
    if layout == "scanned":
        _image_page(writer)
    elif layout == "mixed":
        _text_page(writer, ((72, 720, "English and Chinese mixed fixture"),))
        _image_page(writer)
    elif layout == "rotated":
        _text_page(writer, ((72, 720, "Rotated"),)).rotate(90)
    elif layout == "two-column":
        _text_page(
            writer,
            ((40, 720, "Left column"), (330, 720, "Right column")),
        )
    elif layout == "bilingual":
        _bilingual_page(writer)
    elif layout in {"table", "merged-table"}:
        _text_page(
            writer,
            ((80, 700, "Metric"), (310, 700, "Value"), (80, 650, "Revenue")),
        )
    else:
        _text_page(writer, ((72, 720, "Born digital PDF"),))
    with path.open("wb") as target:
        writer.write(target)
    return path
