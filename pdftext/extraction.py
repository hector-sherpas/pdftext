from functools import partial
from typing import List
from concurrent.futures import ProcessPoolExecutor
import math
import pypdfium2 as pdfium

from pdftext.inference import inference
from pdftext.model import get_model
from pdftext.pdf.chars import get_pdfium_chars
from pdftext.pdf.utils import unnormalize_bbox
from pdftext.postprocessing import merge_text, sort_blocks, postprocess_text, handle_hyphens
from pdftext.settings import settings


def _process_pdf(pdf, flatten_pdf):
    if isinstance(pdf, str):
        pdf = pdfium.PdfDocument(pdf)
    else:
        if not isinstance(pdf, pdfium.PdfDocument):
            raise TypeError("pdf must be a file path string or a PdfDocument object")

    # Must be called on the parent pdf, before the page was retrieved
    if flatten_pdf:
        pdf.init_forms()
    
    return pdf


def _get_page_range(pdf_doc, model, page_range):
    text_chars = get_pdfium_chars(pdf_doc, page_range)
    pages = inference(text_chars, model)
    return pages


def _get_pages(pdf_doc, model=None, page_range=None, flatten_pdf=False, workers=None):
    if model is None:
        model = get_model()

    if page_range is None:
        page_range = range(len(pdf_doc))

    if workers is not None:
        workers = min(workers, len(page_range) // settings.WORKER_PAGE_THRESHOLD) # It's inefficient to have too many workers, since we batch in inference

    if workers is None or workers <= 1:
        text_chars = get_pdfium_chars(pdf_doc, page_range, flatten_pdf)
        return inference(text_chars, model)

    func = partial(_get_page_range, pdf_doc, model)
    page_range = list(page_range)

    pages_per_worker = math.ceil(len(page_range) / workers)
    page_range_chunks = [page_range[i * pages_per_worker:(i + 1) * pages_per_worker] for i in range(workers)]

    with ProcessPoolExecutor(max_workers=workers) as executor:
        pages = list(executor.map(func, page_range_chunks))

    ordered_pages = [page for sublist in pages for page in sublist]

    return ordered_pages


def plain_text_output(pdf: str | pdfium.PdfDocument, sort=False, model=None, hyphens=False, page_range=None, flatten_pdf=False, workers=None) -> str:
    pdf_doc = _process_pdf(pdf, flatten_pdf)
    text = paginated_plain_text_output(pdf_doc, sort=sort, model=model, hyphens=hyphens, page_range=page_range, flatten_pdf=flatten_pdf, workers=workers)
    return "\n".join(text)


def paginated_plain_text_output(pdf_doc, sort=False, model=None, hyphens=False, page_range=None, flatten_pdf=False, workers=None) -> List[str]:
    pages = _get_pages(pdf_doc, model, page_range, flatten_pdf, workers=workers)
    text = []
    for page in pages:
        text.append(merge_text(page, sort=sort, hyphens=hyphens).strip())
    return text


def _process_span(span, page_width, page_height, keep_chars):
    span["bbox"] = unnormalize_bbox(span["bbox"], page_width, page_height)
    span["text"] = handle_hyphens(postprocess_text(span["text"]), keep_hyphens=True)
    if not keep_chars:
        del span["chars"]
    else:
        for char in span["chars"]:
            char["bbox"] = unnormalize_bbox(char["bbox"], page_width, page_height)


def dictionary_output(pdf: str | pdfium.PdfDocument, sort=False, model=None, page_range=None, flatten_pdf=False, keep_chars=False, workers=None):
    pdf_doc = _process_pdf(pdf, flatten_pdf)
    pages = _get_pages(pdf_doc, model, page_range, flatten_pdf, workers=workers)
    for page in pages:
        page_width, page_height = page["width"], page["height"]
        for block in page["blocks"]:
            for k in list(block.keys()):
                if k not in ["lines", "bbox"]:
                    del block[k]
            block["bbox"] = unnormalize_bbox(block["bbox"], page_width, page_height)
            for line in block["lines"]:
                for k in list(line.keys()):
                    if k not in ["spans", "bbox"]:
                        del line[k]
                line["bbox"] = unnormalize_bbox(line["bbox"], page_width, page_height)
                for span in line["spans"]:
                    _process_span(span, page_width, page_height, keep_chars)

        if sort:
            page["blocks"] = sort_blocks(page["blocks"])

    return pages
