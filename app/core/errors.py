"""Shared exception types."""


class BusinessError(Exception):
    """Business rule violation (negative cash, locked period, ...).

    The message is shown to the user.
    """

    def __init__(
        self, message: str, code: str = "business", details: dict | None = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class NegativeBalanceError(BusinessError):
    def __init__(
        self,
        message: str,
        *,
        date,
        line_no: int,
        balance: int,
        voucher_number: str | None = None,
    ):
        super().__init__(
            message,
            code="negative_balance",
            details={
                "date": date,
                "line_no": line_no,
                "balance": balance,
                "voucher_number": voucher_number,
            },
        )
        self.date = date
        self.line_no = line_no
        self.balance = balance


class PeriodLockedError(BusinessError):
    def __init__(self, message: str):
        super().__init__(message, code="period_locked")


class PermissionDeniedError(Exception):
    def __init__(
        self, message: str = "Bạn không có quyền thực hiện thao tác này"
    ):
        super().__init__(message)
        self.message = message


class LoginRequiredError(Exception):
    pass


class NotFoundError(Exception):
    def __init__(self, message: str = "Không tìm thấy"):
        super().__init__(message)
        self.message = message
