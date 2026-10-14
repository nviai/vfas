"""Request/response schemas of the public API /api/v1."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorBody(BaseModel):
    message: str
    type: str
    code: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class MeCompany(BaseModel):
    id: int
    name: str
    tax_code: str | None


class MeUser(BaseModel):
    id: int
    email: str
    full_name: str


class MeOut(BaseModel):
    user: MeUser
    company: MeCompany
    role: str | None
    scopes: list[str]


class AccountOut(BaseModel):
    code: str
    name: str
    parent_code: str | None
    level: int
    kind: str
    normal_balance: str
    postable: bool
    partner_mode: str
    needs_bank: bool
    active: bool


class PartnerOut(BaseModel):
    id: int
    code: str
    name: str
    is_customer: bool
    is_supplier: bool
    is_employee: bool
    tax_code: str | None
    address: str | None
    phone: str | None
    email: str | None
    active: bool


class PartnerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    code: str = ""
    is_customer: bool = False
    is_supplier: bool = False
    is_employee: bool = False
    tax_code: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    contact_person: str = ""
    bank_account: str = ""
    bank_name: str = ""
    payment_terms_days: int = Field(0, ge=0, le=3650)
    note: str = ""


class ItemOut(BaseModel):
    id: int
    code: str
    name: str
    unit: str
    kind: str
    vat_rate: str
    sale_price: str
    purchase_price: str
    active: bool


class LineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debit_account: str = Field(min_length=1, max_length=20, examples=["6422"])
    credit_account: str = Field(min_length=1, max_length=20, examples=["1111"])
    amount: int = Field(
        gt=0, le=10**15, description="Số tiền (đồng, số nguyên)"
    )
    description: str = ""
    debit_partner_id: int | None = None
    credit_partner_id: int | None = None
    debit_bank_account_id: int | None = None
    credit_bank_account_id: int | None = None


class LineOut(BaseModel):
    line_no: int
    debit_account: str
    credit_account: str
    amount: int
    description: str
    debit_partner_id: int | None
    credit_partner_id: int | None


class EntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: str = Field(
        "PK",
        description="Loại chứng từ: PK (phiếu kế toán), PT, PC, BC, BN…",
        max_length=8,
    )
    date: date
    doc_date: date | None = None
    description: str = Field("", max_length=500)
    contact_name: str = Field("", max_length=200)
    contact_address: str = Field("", max_length=300)
    partner_id: int | None = None
    ref_no: str = Field("", max_length=100)
    lines: list[LineIn] = Field(min_length=1, max_length=500)


class EntryOut(BaseModel):
    id: int
    doc_type: str
    number: str | None
    date: date
    doc_date: date | None
    status: Literal["draft", "posted", "cancelled"]
    description: str
    contact_name: str
    partner_id: int | None
    total: int
    url: str
    lines: list[LineOut] | None = None


class EntryList(BaseModel):
    data: list[EntryOut]
    limit: int
    offset: int


class BalanceOut(BaseModel):
    account: str
    date: date
    partner_id: int | None
    total_debit: int
    total_credit: int
    balance: int = Field(description="Nợ − Có (dương = dư Nợ)")


class TrialBalanceRow(BaseModel):
    account: str
    name: str | None
    opening: int
    debit: int
    credit: int
    closing: int


class TrialBalanceOut(BaseModel):
    start: date
    end: date
    rows: list[TrialBalanceRow]
    total_debit: int
    total_credit: int


class ToolCallOut(BaseModel):
    name: str
    ok: bool
    result: Any


# ------------------------------------------------- OpenAI compatibility


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str | list[dict] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "vfas-agent"
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    stream_options: dict | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    user: str | None = None
