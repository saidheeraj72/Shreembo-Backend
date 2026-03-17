"""Composed limit service."""

from .limit_crud import LimitCrudMixin
from .limit_rate_daily import LimitRateDailyMixin
from .limit_tokens import LimitTokenChecksMixin
from .limit_stats import LimitStatsMixin


class LimitService(
    LimitCrudMixin,
    LimitRateDailyMixin,
    LimitTokenChecksMixin,
    LimitStatsMixin,
):
    """Service for usage limit management and enforcement."""


from . import limit_crud as _limit_crud
from . import limit_rate_daily as _limit_rate_daily
from . import limit_tokens as _limit_tokens
from . import limit_stats as _limit_stats

_limit_crud.LimitService = LimitService
_limit_rate_daily.LimitService = LimitService
_limit_tokens.LimitService = LimitService
_limit_stats.LimitService = LimitService

limit_service = LimitService()
