"""Provider-independent invoice model and the provider interface.

A sales document is turned into a `CanonicalInvoice`; each provider
adapter maps it to its own API (REST/JSON or SOAP/XML) and maps the
answers back to an `IssueResult`. Adapters never touch the database:
the service writes the outbox row before calling `issue()` and stores
whatever comes back.

Error classes tell the service what is safe to do next:

- `ProviderRejectedError`: the provider refused the request (validation,
  wrong series, expired contract...). Nothing was issued; the user can
  fix the data and issue again with a new key.
- `ProviderUnavailableError`: timeout, network error, HTTP 5xx – the
  invoice MAY have been issued. The service must look it up by the
  idempotency key (`find()`) before sending it again.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal

import httpx

# VAT rate codes of the app (see sales.common) and their meaning.
TAXABLE_RATES = ("0", "5", "8", "10")
NOT_SUBJECT = "KCT"  # not subject to VAT
NOT_DECLARED = "KKKNT"  # not required to declare and pay VAT

# Invoice states kept in the outbox (EInvoiceIssue.status):
# pending - written, the request is being sent;
# unknown - sent but the answer was lost: look it up before resending;
# signed  - number assigned and signed, tax authority code pending;
# issued  - has the tax authority code;
# failed  - refused before signing: fix the data and issue again.
PENDING = "pending"
UNKNOWN = "unknown"
SIGNED = "signed"
ISSUED = "issued"
FAILED = "failed"
ACTIVE_STATES = (PENDING, UNKNOWN, SIGNED, ISSUED)


class ProviderError(Exception):
    def __init__(self, message: str, *, code: str = "", raw=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.raw = raw


class ProviderRejectedError(ProviderError):
    """The request was refused; nothing was issued."""


class ProviderUnavailableError(ProviderError):
    """No usable answer; the invoice may or may not exist."""


@dataclass(frozen=True)
class ConfigField:
    """A provider setting shown on the configuration page."""

    name: str
    label: str
    secret: bool = False
    required: bool = True
    hint: str = ""
    placeholder: str = ""


@dataclass
class InvoiceLine:
    line_no: int
    name: str
    unit: str = ""
    code: str = ""
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    # Amount before VAT, after the line discount.
    amount: int = 0
    discount: int = 0
    # "0" | "5" | "8" | "10" | "KCT" | "KKKNT"
    vat_rate: str = "10"
    vat_amount: int = 0

    @property
    def gross(self) -> int:
        """Amount before the discount."""
        return self.amount + self.discount


@dataclass
class TaxGroup:
    vat_rate: str
    base: int
    vat: int


@dataclass
class OriginalInvoice:
    """The invoice an adjustment or replacement refers to."""

    template: str
    series: str
    number: str
    issue_date: date
    provider_ref: str = ""
    tax_authority_code: str = ""


@dataclass
class CanonicalInvoice:
    key: str  # idempotency key, unique per company and provider
    seller_tax_code: str
    template: str  # 1 = VAT invoice
    series: str  # e.g. C26TAA
    issue_date: date
    lines: list[InvoiceLine]
    buyer_legal_name: str = ""  # organization
    buyer_name: str = ""  # individual / contact person
    buyer_tax_code: str = ""
    buyer_address: str = ""
    buyer_email: str = ""
    buyer_code: str = ""
    payment_method: str = "TM/CK"
    currency: str = "VND"
    # original | adjust_down | adjust_up
    kind: str = "original"
    original: OriginalInvoice | None = None
    reason: str = ""
    amount_in_words: str = ""
    note: str = ""

    @property
    def total_before_tax(self) -> int:
        return sum(ln.amount for ln in self.lines)

    @property
    def total_discount(self) -> int:
        return sum(ln.discount for ln in self.lines)

    @property
    def total_vat(self) -> int:
        return sum(ln.vat_amount for ln in self.lines)

    @property
    def total(self) -> int:
        return self.total_before_tax + self.total_vat

    @property
    def tax_groups(self) -> list[TaxGroup]:
        groups: dict[str, TaxGroup] = {}
        for ln in self.lines:
            g = groups.setdefault(ln.vat_rate, TaxGroup(ln.vat_rate, 0, 0))
            g.base += ln.amount
            g.vat += ln.vat_amount
        return list(groups.values())

    @property
    def is_adjustment(self) -> bool:
        return self.kind in ("adjust_down", "adjust_up")

    def to_dict(self) -> dict:
        def conv(v):
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v

        return conv(asdict(self))


@dataclass
class IssueResult:
    status: str  # SIGNED | ISSUED
    number: str = ""
    template: str = ""
    series: str = ""
    issue_date: date | None = None
    provider_ref: str = ""  # provider transaction / invoice id
    lookup_code: str = ""  # code for the buyer's lookup page
    tax_authority_code: str = ""  # MCCQT
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderConfig:
    environment: str  # test | production
    base_url: str
    tax_code: str
    template: str
    series: str
    values: dict  # non-secret and decrypted secret fields


class EInvoiceProvider(ABC):
    """Adapter for one e-invoice provider."""

    code: str = ""
    name: str = ""
    # Shown on the settings page; values arrive in config.values.
    fields: tuple[ConfigField, ...] = ()
    default_urls: dict[str, str] = {}
    # The provider assigns the tax authority code later (poll status).
    async_tax_code: bool = True
    supports_adjustment: bool = True
    timeout: float = 45.0

    def __init__(
        self, config: ProviderConfig, client: httpx.Client | None = None
    ):
        self.config = config
        self.base_url = (
            config.base_url or self.default_urls.get(config.environment) or ""
        ).rstrip("/")
        self.client = client or httpx.Client(timeout=self.timeout)

    def value(self, name: str) -> str:
        return str(self.config.values.get(name) or "").strip()

    # -------------------------------------------------------- transport

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            resp = self.client.request(method, url, **kw)
        except httpx.TimeoutException as e:
            raise ProviderUnavailableError(
                "Hết thời gian chờ nhà cung cấp hóa đơn trả lời"
            ) from e
        except httpx.HTTPError as e:
            raise ProviderUnavailableError(
                f"Không kết nối được nhà cung cấp hóa đơn: {e}"
            ) from e
        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"Nhà cung cấp hóa đơn lỗi máy chủ (HTTP {resp.status_code})",
                raw=resp.text[:2000],
            )
        return resp

    # ---------------------------------------------------------- the API

    @abstractmethod
    def test_connection(self) -> str:
        """Check the credentials; return a short success message."""

    @abstractmethod
    def issue(self, invoice: CanonicalInvoice) -> IssueResult:
        """Create, sign and send the invoice to the tax authority."""

    @abstractmethod
    def find(self, invoice: CanonicalInvoice) -> IssueResult | None:
        """Look up an invoice by its idempotency key (None = absent)."""

    def status(
        self, invoice: CanonicalInvoice, result: IssueResult
    ) -> IssueResult:
        """Refresh the state of an issued invoice (tax authority code).

        Default: look it up again by key.
        """
        return self.find(invoice) or result

    @abstractmethod
    def download(
        self, invoice: CanonicalInvoice, result: IssueResult, kind: str
    ) -> bytes:
        """Return the invoice file: kind = "pdf" | "xml"."""


def vat_rate_label(rate: str) -> str:
    """Rate as printed on invoices: 10%, 8%, KCT, KKKNT."""
    return f"{rate}%" if rate in TAXABLE_RATES else rate
