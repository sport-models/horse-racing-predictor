"""v0.5.8 optional bookmaker-promotion recording & cash accounting.

A 'money back as bonus' offer is an ELIGIBILITY marker until manually confirmed;
bonus credits are promotional value and are never booked as cash payouts.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP

NONE = "none"
REFUND = "refund_as_bonus"
BONUS = "bonus_used"
MODES = {NONE, REFUND, BONUS}
DISPLAY = {
    "Standard cash bet": NONE,
    "Bet returned as bonus bet (offer)": REFUND,
    "Bonus bet used": BONUS,
}


def dec(value):
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def cash_stake(bet):
    total = dec(bet.get("total_stake") if bet.get("total_stake") is not None else
                (dec(bet.get("win_stake")) + dec(bet.get("place_stake"))))
    if bet.get("promo_mode", NONE) != BONUS:
        return float(total)
    return float(total - dec(bet.get("bonus_win_stake")) - dec(bet.get("bonus_place_stake")))


def validate_promotion(bet_type, promo_mode, win_stake, place_stake, bonus_win, bonus_place):
    if promo_mode not in MODES:
        return False, "Unknown promotion type"
    w, p, bw, bp = map(dec, (win_stake, place_stake, bonus_win, bonus_place))
    if min(w, p, bw, bp) < 0 or bw > w or bp > p:
        return False, "Bonus-funded amounts must fit within the corresponding stakes"
    if promo_mode != BONUS and (bw != 0 or bp != 0):
        return False, "Only bonus-bet-used wagers may have bonus-funded stakes"
    if promo_mode == BONUS and bw + bp <= 0:
        return False, "Enter how much of the stake was paid using a bonus bet"
    if bet_type == "Win" and p != 0:
        return False, "Win-only cannot have a place stake"
    if bet_type == "Place" and w != 0:
        return False, "Place-only cannot have a win stake"
    if bet_type == "Each Way" and (w <= 0 or p <= 0):
        return False, "Each-way requires win and place components"
    return True, "OK"


def calculated_cash_payout(bet, position, paying_places=None, scratched=False):
    """Bookmaker cash payout (includes cash stake return, excludes bonus stake return).

    Fully voided bet: refund only cash components. Reissued bonus value is tracked
    outside cash, and must be confirmed by the bookmaker.
    """
    if scratched:
        return round(cash_stake(bet), 2)
    if not isinstance(position, int) or position <= 0:
        return 0.0
    win_stake, place_stake = dec(bet.get("win_stake")), dec(bet.get("place_stake"))
    b_win, b_place = (dec(bet.get("bonus_win_stake")), dec(bet.get("bonus_place_stake"))) \
        if bet.get("promo_mode", NONE) == BONUS else (Decimal("0"), Decimal("0"))
    win_odds, place_odds = dec(bet.get("win_odds")), dec(bet.get("place_odds"))
    out = Decimal("0")
    kind = bet.get("bet_type")
    if position == 1 and kind in ("Win", "Each Way"):
        out += (win_stake - b_win) * win_odds + b_win * (win_odds - 1)
    if kind in ("Place", "Each Way") and paying_places is not None and position <= int(paying_places):
        out += (place_stake - b_place) * place_odds + b_place * (place_odds - 1)
    return float(out.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def cash_profit(bet, payout=None):
    if payout is None:
        payout = bet.get("total_return") or 0
    return float(dec(payout) - dec(cash_stake(bet)))


def cash_stake_metrics(bets):
    """Settled cash performance. Bonuses earned are reported separately, never cash.
    """
    settled = [b for b in bets if b.get("status") == "Settled"]
    outlay = sum(cash_stake(b) for b in settled)
    cash_returns = sum(float(b.get("total_return") or 0) for b in settled)
    bonus_used = sum(float(dec(b.get("bonus_win_stake")) + dec(b.get("bonus_place_stake")))
                     for b in settled if b.get("promo_mode") == BONUS)
    bonus_credits = sum(float(b.get("bonus_credit_value") or 0) for b in bets
                        if b.get("status") in ("Settled", "Void"))
    return {"settled": len(settled), "cash_staked": outlay, "cash_return": cash_returns,
            "cash_profit": cash_returns - outlay, "roi_pct": 100 * (cash_returns - outlay) / outlay if outlay else None,
            "bonus_staked": bonus_used, "bonus_credits_confirmed": bonus_credits}
