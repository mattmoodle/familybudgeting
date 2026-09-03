from pathlib import Path

from app.services.importers.base import StatementImporter
from app.services.importers.bcc_pdf_importer import BccBankPdfImporter
from app.services.importers.bbva_pdf_importer import BbvaPdfImporter
from app.services.importers.bper_pdf_importer import BperPdfImporter
from app.services.importers.csv_importer import CsvImporter
from app.services.importers.numia_pdf_importer import NumiaCardPdfImporter
from app.services.importers.paypal_pdf_importer import PaypalPdfImporter
from app.services.importers.pdf_importer import PdfImporter
from app.services.importers.pdf_utils import extract_pdf_text
from app.services.importers.satispay_pdf_importer import SatispayPdfImporter
from app.services.importers.xlsx_importer import XlsxImporter

PDF_IMPORTERS = (
    BbvaPdfImporter,
    PaypalPdfImporter,
    SatispayPdfImporter,
    NumiaCardPdfImporter,
    BccBankPdfImporter,
    BperPdfImporter,
)


def get_importer(path: Path) -> StatementImporter:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return CsvImporter()
    if suffix in {".xlsx", ".xlsm"}:
        return XlsxImporter()
    if suffix == ".pdf":
        text = extract_pdf_text(path)
        for importer_cls in PDF_IMPORTERS:
            if importer_cls.matches(text):
                return importer_cls()
        return PdfImporter()
    raise ValueError(f"Unsupported file format: {suffix}")
