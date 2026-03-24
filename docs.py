import io
import logging

logger = logging.getLogger(__name__)

DOCS_PATH = "/CalebBot/documents"
MAX_CHARS = 60000


def list_documents(dbx) -> tuple[list[str], str | None]:
    try:
        result = dbx.files_list_folder(DOCS_PATH)
        return sorted(e.name for e in result.entries if hasattr(e, "name")), None
    except Exception as e:
        logger.error("Dropbox list error: %s", e)
        return [], f"Error listing documents: {e}"


def fetch_and_parse(dbx, filename: str) -> str:
    from dropbox.exceptions import ApiError
    path = f"{DOCS_PATH}/{filename}"
    try:
        _, res = dbx.files_download(path)
    except ApiError:
        return f"File not found: {filename}"
    except Exception as e:
        logger.error("Dropbox download error for %s: %s", path, e)
        return f"Download error: {e}"

    name_lower = filename.lower()
    if name_lower.endswith(".pdf"):
        return _parse_pdf(res.content, filename)
    elif name_lower.endswith((".txt", ".md", ".csv")):
        try:
            return res.content.decode("utf-8", errors="replace")[:MAX_CHARS]
        except Exception as e:
            return f"Parse error: {e}"
    else:
        return f"Unsupported file type: {filename}"


def _parse_pdf(content: bytes, filename: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"[Page {i + 1}]\n{text}")
        full_text = "\n\n".join(pages)
        if len(full_text) > MAX_CHARS:
            full_text = (
                full_text[:MAX_CHARS]
                + f"\n\n[Truncated at {MAX_CHARS} characters — "
                f"{len(reader.pages)} pages total]"
            )
        return full_text or "No extractable text found in PDF."
    except Exception as e:
        return f"PDF parse error: {e}"
