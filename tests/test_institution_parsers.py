from decimal import Decimal

from app.services.importers.bcc_pdf_importer import BccBankPdfImporter
from app.services.importers.bbva_pdf_importer import BbvaPdfImporter
from app.services.importers.bper_pdf_importer import BperPdfImporter
from app.services.importers.numia_pdf_importer import NumiaCardPdfImporter
from app.services.importers.paypal_pdf_importer import PaypalPdfImporter
from app.services.importers.satispay_pdf_importer import SatispayPdfImporter


def test_numia_card_signs_and_multiline_fx():
    text = """
    Numia S.p.A. Carta N. 0000 **** **** 0000
    DATA ACQUISTO DATA REGISTR. DESCRIZIONE DELLE OPERAZIONI IMPORTO IN EURO
    23/07/2026 24/07/2026 OPENAI *CHATGPT SUBSCR SAN FRANCISCO CA
    24,40 USD 21,41
    15/04/2026 15/04/2026 MGP*Vinted Vilnius LTU -2,60
    29/04/2026 29/04/2026 ADDEBITO IN C/C -1.333,29
    """
    rows = NumiaCardPdfImporter().parse_text(text)
    assert [r.amount for r in rows] == [Decimal("-21.41"), Decimal("2.60"), Decimal("1333.29")]


def test_paypal_rows_keep_transaction_ids_and_currency():
    text = """
    Cronologia transazioni - EUR
    Data Descrizione Nome \\ Email Lordo Tariffa Netto
    07/07/26 Pagamento Express Checkout
    ID: TESTPAYPAL000001
    Example Energy SRL
    merchant@example.test -40,68 0,00 -40,68
    07/07/26 Bonifico bancario sul conto PayPal
    ID: TESTPAYPAL000002 40,68 0,00 40,68
    Cronologia transazioni - USD
    29/07/26 Pagamento cumulativo
    ID: TESTPAYPAL000003
    Example Merchant
    service@example.test 0,01 0,00 0,01
    """
    rows = PaypalPdfImporter().parse_text(text)
    assert rows[0].amount == Decimal("-40.68")
    assert "TESTPAYPAL000001" in rows[0].description
    assert rows[1].amount == Decimal("40.68")
    assert rows[2].currency == "USD"


def test_satispay_transaction_amount_is_first_amount():
    text = """
    Lista Transazioni Satispay
    Data Transazione Importo Tipo Disponibilità Disponibilità dopo la transazione ID
    27 lug 2026
    00:16
    Ricarica Satispay
    Da (IT*****0000)
    150,00 € Dalla Banca
    Approvato
    150,00 € 150,00 € 00000000-0000-4000-8000-000000000001
    26 lug 2026
    10:27
    PagoPA -25,85 € PagoPA
    Approvato
    -25,85 € -25,85 € 00000000-0000-4000-8000-000000000002
    """
    rows = SatispayPdfImporter().parse_text(text)
    assert [r.amount for r in rows] == [Decimal("150.00"), Decimal("-25.85")]
    assert "00000000" in rows[0].description


def test_satispay_supports_pdf_replacement_currency_glyphs():
    text = """
    Lista Transazioni Satispay Disponibilità
    27 ago 2026 Example savings pocket -50,00� Deposito Risparmi -50,00� 25,00� 00000000-0000-4000-8000-000000000003
    00:09 Approvato
    """
    rows = SatispayPdfImporter().parse_text(text)
    assert rows[0].amount == Decimal("-50.00")


def test_numia_current_layout_has_purchase_and_posting_dates():
    text = """
    Credit MC
    Lista Movimenti
    Data registrazione Data acquisto Importo originale Valuta Commissioni Importo EURO
    31/08/2026 01/09/2026 EXAMPLE SHOP ROMA ITA -2.50 -2.50 0.00 EUR
    09:26:18
    30/08/2026 31/08/2026 EXAMPLE DIGITAL SERVICE IRL -9.99 -9.99 0.00 EUR
    11:14:11
    """
    rows = NumiaCardPdfImporter().parse_text(text)
    assert [row.amount for row in rows] == [Decimal("-2.50"), Decimal("-9.99")]
    assert rows[0].booked_on.isoformat() == "2026-09-01"
    assert rows[0].value_on.isoformat() == "2026-08-31"


def test_bcc_movimenti_globali_signed_amounts():
    text = """
    Banca di Credito Cooperativo di Roma Movimenti Globali
    EXAMPLE HOLDER, SECOND HOLDER Z0000000000000000000000 21/07/2026 21/07/2026 -120,00 Pagamenti diversi SDD Commerciale - Rich. Incasso SE Ricarica dell'app Satispay Example Wallet S.A. TESTREF0000000001
    EXAMPLE HOLDER, SECOND HOLDER Z0000000000000000000000 20/07/2026 20/07/2026 131,10 Bonifico a Vostro favore Bonifico a vs favore *DEMO BENEFIT
    """
    rows = BccBankPdfImporter().parse_text(text)
    assert [r.amount for r in rows] == [Decimal("-120.00"), Decimal("131.10")]


def test_bper_new_layout_sign_column():
    text = """
    ABI: 05387 BIC: BPMOIT22 XXX
    1/07/26 A 1.100,00 1/07/26 BONIFICO ISTANTANEO o/c: EXAMPLE HOLDER a favore di EXAMPLE RECIPIENT
    15/07/26 D 1.090,77 15/07/26 RATA PRESTITO Fin. TEST-LOAN-000001
    """
    rows = BperPdfImporter().parse_text(text)
    assert [r.amount for r in rows] == [Decimal("1100.00"), Decimal("-1090.77")]


def test_bper_current_relax_banking_layout_keeps_signed_amounts():
    text = """
    Conto Corrente: 00000
    Data operazione Data valuta Descrizione Entrate Uscite Note Categorie
    01/09/2026 01/09/2026 BONIFICO ISTANTANEO a favore di EXAMPLE RECIPIENT DEPOSITO 1.100,00 EUR Spese: 0,00 EUR -RIF. TEST 1.100,00€ Contabilizzato BONIFICO
    15/08/2026 15/08/2026 RATA PRESTITO Fin. TEST-LOAN Quota capitale 414,28 Interessi 673,74 -1.090,77€ Contabilizzato RATA FINANZIAMENTO
    """
    rows = BperPdfImporter().parse_text(text)
    assert [row.amount for row in rows] == [Decimal("1100.00"), Decimal("-1090.77")]


def test_bbva_rows_keep_value_date_and_wrapped_description():
    text = """
    Ultime transazioni
    Data Causale Importo Saldo
    31/08/2026 Example Shop -70,75 € 5.247,54 EUR
    Data valuta: 31/08/2026 Pagamento con carta
    30/08/2026 Bonifico eseguito -300,00 € 5.318,29 EUR
    Data valuta: 29/08/2026 Lavori di manutenzione
    urgenti
    1/1
    """
    rows = BbvaPdfImporter().parse_text(text)
    assert [row.amount for row in rows] == [Decimal("-70.75"), Decimal("-300.00")]
    assert rows[0].value_on.isoformat() == "2026-08-31"
    assert rows[1].value_on.isoformat() == "2026-08-29"
    assert rows[1].description == "Bonifico eseguito Lavori di manutenzione urgenti"
