from __future__ import annotations

from pathlib import Path

from src.config.constants import (
    HCBI_INDEX_NAME,
    POLICY_INDEX_NAME,
    SUPPORTING_DOCS_INDEX_NAME,
)
from src.ingestion.extractors import ExtractorFactory
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.ingestion.metadata_extractor import MetadataExtractor
from src.models.schemas import ExtractedDocument
from src.schemas.state import DocumentType
from src.utils.logger import get_logger

logger = get_logger(__name__)

_INDEX_BY_TYPE: dict[DocumentType, str] = {
    DocumentType.GLOBAL_POLICY:  HCBI_INDEX_NAME,
    DocumentType.TPI_POLICY:     POLICY_INDEX_NAME,
    DocumentType.SUPPORTING_DOC: SUPPORTING_DOCS_INDEX_NAME,
}

_POLICY_DOMAIN_DEFAULTS: dict[DocumentType, dict] = {
    DocumentType.TPI_POLICY: {"policy_domain": "THIRD_PARTY_INTERMEDIARY", "language": "English"},
}


class DocumentLoader:
    """
    Recursively walks a volume path, extracts text from every supported file,
    and enriches each ExtractedDocument with document-level ChunkMetadata fields:
      document_type, index_name, version, effective_date, policy_number, policy_domain,
      policy_name, policy_owner, country, language.

    Callers specify the document_type so the loader knows which index and defaults to apply.
    SRP: orchestrating extraction + metadata enrichment only — no chunking.
    """

    def __init__(
        self,
        ocr_provider: BaseOCRProvider,
        factory: ExtractorFactory | None = None,
        translator=None,
        settings=None,
    ) -> None:
        self._factory = factory or ExtractorFactory(ocr_provider)
        self._meta = MetadataExtractor()
        # Optional TranslationService — when provided, non-English extracted text is translated to
        # English before chunking/enrichment. None -> no translation (unchanged behaviour).
        self._translator = translator
        # Optional settings — enables saving each translated doc as a .docx to the translated-docs
        # Volume. None -> translation still happens in-memory, just not persisted.
        self._settings = settings

    def _maybe_translate(self, doc: ExtractedDocument, file_name: str) -> None:
        """Translate the document's extracted text to English in place (best-effort). English docs are
        detected and left untouched; a failure keeps the original text."""
        translator = self._translator
        if translator is None or not getattr(translator, "enabled", False):
            return
        original = doc.content or ""
        # Language detection scans the whole string (regex over every char); for very large extracts
        # (e.g. a 95M-char ledger) that scan alone takes minutes — log it so it is not mistaken for a hang.
        if len(original) > 1_000_000:
            logger.info("[Loader] Detecting language | file=%s | %d chars (large — may take a while)",
                        file_name, len(original))
        if not translator.needs_translation(original):
            return
        translated = translator.translate_text(original, label=file_name)
        if translated and translated != original:
            doc.content = translated
            doc.metadata["translated_to_english"] = True
            doc.metadata["source_language"] = "non-english"
            logger.info(
                "[Loader] Translated to English | file=%s | chars %d -> %d",
                file_name, len(original), len(translated),
            )
            # Persist the English translation as a .docx to the translated-docs Volume (best-effort).
            if self._settings is not None:
                try:
                    from src.pipeline.translated_docs import save_translated_doc

                    save_translated_doc(self._settings, file_name, translated)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Loader] Translated-doc save skipped | file=%s | %s", file_name, exc)

    def load_directory(
        self,
        root_path: str | Path,
        scope_id: str,
        document_type: DocumentType,
        doc_level_overrides: dict | None = None,
    ) -> list[ExtractedDocument]:
        """
        Recursively extract all supported files under root_path.
        Each returned ExtractedDocument has metadata enriched with ChunkMetadata fields.

        Args:
            root_path: Databricks volume path or local directory.
            scope_id: Scope identifier attached to every chunk.
            document_type: GLOBAL_POLICY | TPI_POLICY | SUPPORTING_DOC.
            doc_level_overrides: Optional dict to force-override any metadata field
                (e.g. country="US" for a country-specific policy folder).
        """
        root = Path(root_path)
        if not root.exists():
            raise FileNotFoundError(f"Volume path does not exist: {root}")

        logger.info(
            "[Loader] Scanning | path=%s | scope=%s | document_type=%s",
            root, scope_id, document_type.value,
        )

        all_files = [f for f in root.rglob("*") if f.is_file()]
        supported = [f for f in all_files if f.suffix.lower().lstrip(".") in self._factory.supported_extensions]
        skipped_count = len(all_files) - len(supported)

        logger.info(
            "[Loader] Files found=%d | supported=%d | unsupported=%d | scope=%s",
            len(all_files), len(supported), skipped_count, scope_id,
        )

        documents: list[ExtractedDocument] = []
        failed: list[str] = []

        for file_path in supported:
            try:
                extractor = self._factory.get(file_path)
                doc = extractor.extract(file_path, scope_id)
                self._maybe_translate(doc, file_path.name)
                self._enrich_metadata(doc, file_path, document_type, doc_level_overrides)
                documents.append(doc)
            except Exception as exc:
                logger.warning("[Loader] Extraction failed | file=%s | error=%s", file_path.name, exc)
                failed.append(file_path.name)

        logger.info(
            "[Loader] Complete | extracted=%d | failed=%d | scope=%s | path=%s",
            len(documents), len(failed), scope_id, root,
        )
        if failed:
            logger.warning("[Loader] Failed files: %s", failed)

        return documents

    def load_file(
        self,
        file_path: str | Path,
        scope_id: str,
        document_type: DocumentType,
        doc_level_overrides: dict | None = None,
    ) -> ExtractedDocument:
        path = Path(file_path)
        logger.info(
            "[Loader] Single file | file=%s | scope=%s | document_type=%s",
            path.name, scope_id, document_type.value,
        )
        extractor = self._factory.get(path)
        doc = extractor.extract(path, scope_id)
        self._maybe_translate(doc, path.name)
        self._enrich_metadata(doc, path, document_type, doc_level_overrides)
        return doc

    def _enrich_metadata(
        self,
        doc: ExtractedDocument,
        file_path: Path,
        document_type: DocumentType,
        overrides: dict | None,
    ) -> None:
        """
        Merge extracted + default + caller-override metadata into doc.metadata.
        Always sets document_type and index_name; the rest come from filename/header.
        """
        base_extra: dict = {
            "document_type": document_type.value,
            "index_name": _INDEX_BY_TYPE[document_type],
            **_POLICY_DOMAIN_DEFAULTS.get(document_type, {}),
        }
        if overrides:
            base_extra.update(overrides)

        enriched = self._meta.enrich(file_path, doc.content, extra=base_extra)
        doc.metadata.update(enriched)
        logger.debug(
            "[Loader] Metadata enriched | file=%s | keys=%s",
            file_path.name, list(enriched.keys()),
        )
