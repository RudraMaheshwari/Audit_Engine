"""Pinpoint why an .xlsx or .pptx triggers Excel/PowerPoint's 'repair' prompt.

Opens the file as a zip and, for every relevant XML part, reports:
  * whether the XML is well-formed,
  * any run text over the 32,767-char limit (with a preview),
  * empty rich-text runs (xlsx) / raw newlines inside a run (pptx),
  * any illegal XML control characters.

Usage:  python scripts/diagnose_xlsx.py "C:\\path\\to\\file.xlsx"   (or a .pptx deck)
"""

import re
import sys
import zipfile
from xml.dom.minidom import parseString

_LIMIT = 32767
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")
_XL_T = re.compile(r"<t\b[^>]*>(.*?)</t>", re.DOTALL)
_PP_T = re.compile(r"<a:t\b[^>]*>(.*?)</a:t>", re.DOTALL)
_EMPTY_RUN = re.compile(r"<r>(?:<rPr\b.*?</rPr>)?<t\b[^>]*>(?:</t>|/>)", re.DOTALL)
# Empty PowerPoint run <a:r>[<a:rPr …>]<a:t …></a:t></a:r> — the most common pptx 'repair' cause.
_PP_EMPTY_RUN = re.compile(
    r"<a:r>(?:<a:rPr\b[^>]*/>|<a:rPr\b(?:(?!</?a:r\b).)*?</a:rPr>)?"
    r"(?:<a:t\b[^>]*></a:t>|<a:t\b[^>]*/>)</a:r>",
    re.DOTALL,
)
# A table row and its grid, to check cell-count vs column-count (a mismatch makes PowerPoint repair).
_TBL = re.compile(r"<a:tbl>.*?</a:tbl>", re.DOTALL)
_GRIDCOL = re.compile(r"<a:gridCol\b")
_TR = re.compile(r"<a:tr\b.*?</a:tr>", re.DOTALL)
_TC = re.compile(r"<a:tc\b")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python scripts/diagnose_xlsx.py <file.xlsx|file.pptx>")
        return
    path = sys.argv[1]
    is_pptx = path.lower().endswith(".pptx")
    t_re = _PP_T if is_pptx else _XL_T
    problems = 0
    with zipfile.ZipFile(path) as z:
        all_xml = [n for n in z.namelist() if n.endswith((".xml", ".rels"))]
        if is_pptx:
            content_parts = [n for n in all_xml if (
                n.startswith("ppt/slides/") or n.startswith("ppt/slideLayouts/")
                or n.startswith("ppt/slideMasters/") or n.startswith("ppt/notesSlides/"))]
        else:
            content_parts = [n for n in all_xml
                             if "worksheets/sheet" in n or n.endswith("sharedStrings.xml")]

        # 1) Well-formedness of EVERY xml/rels part (catches structural breakage anywhere, incl.
        #    presentation.xml / [Content_Types].xml / tableStyles.xml, not just slides).
        for name in all_xml:
            xml = z.read(name).decode("utf-8", "ignore")
            try:
                parseString(xml)
            except Exception as exc:
                problems += 1
                print(f"[MALFORMED XML] {name} -> {exc}")

        # 2) Content-level checks on the text-bearing parts.
        for name in content_parts:
            xml = z.read(name).decode("utf-8", "ignore")

            for t in (t for t in t_re.findall(xml) if len(t) > _LIMIT):
                problems += 1
                print(f"[OVER 32767]   {name} -> run text len={len(t):,} | preview: {t[:90]!r}…")

            if is_pptx:
                nl = [t for t in t_re.findall(xml) if "\n" in t or "\r" in t]
                if nl:
                    problems += 1
                    print(f"[NEWLINE RUN]  {name} -> {len(nl)} run(s) with a raw line break (corrupts pptx)")
                empties = len(_PP_EMPTY_RUN.findall(xml))
                if empties:
                    problems += 1
                    print(f"[EMPTY RUNS]   {name} -> {empties} empty <a:r> run(s) (PowerPoint removes these)")
                for ti, tbl in enumerate(_TBL.findall(xml), start=1):
                    n_cols = len(_GRIDCOL.findall(tbl))
                    for ri, tr in enumerate(_TR.findall(tbl), start=1):
                        n_cells = len(_TC.findall(tr))
                        if n_cells != n_cols:
                            problems += 1
                            print(f"[TABLE MISMATCH] {name} -> table {ti} row {ri}: "
                                  f"{n_cells} cell(s) vs {n_cols} grid column(s)")
            else:
                empties = len(_EMPTY_RUN.findall(xml))
                if empties:
                    problems += 1
                    print(f"[EMPTY RUNS]   {name} -> {empties} empty rich-text run(s)")

            if _ILLEGAL.search(xml):
                problems += 1
                print(f"[ILLEGAL CHR]  {name} -> contains C0/C1 control characters")

    print("-" * 60)
    print("No problems found — file looks clean." if problems == 0
          else f"{problems} problem(s) found (see above).")


if __name__ == "__main__":
    main()
